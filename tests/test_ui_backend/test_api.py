import pytest
from fastapi.testclient import TestClient
from pathlib import Path


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("VSM_DATA_DIR", str(tmp_path))
    import importlib

    import src.ui_backend.main as m

    importlib.reload(m)
    return TestClient(m.app)


@pytest.fixture()
def client_small_limit(tmp_path, monkeypatch):
    """Backend avec une limite d'upload réduite (1 Mo) via VSM_MAX_UPLOAD_MB."""
    monkeypatch.setenv("VSM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VSM_MAX_UPLOAD_MB", "1")
    import importlib

    import src.ui_backend.main as m

    importlib.reload(m)
    return TestClient(m.app)


def _login(client):
    client.post(
        "/auth/bootstrap",
        json={"username": "doc", "password": "mot-de-passe-fort!", "role": "medecin"},
    )
    r = client.post(
        "/auth/login", json={"username": "doc", "password": "mot-de-passe-fort!"}
    )
    assert r.status_code == 200
    return {"X-CSRF-Token": r.json()["csrf"]}


def _process_and_wait(client, headers, doc_id, **body):
    """Lance un traitement asynchrone et attend sa fin (avec délai max)."""
    import time

    r = client.post(f"/documents/{doc_id}/process", headers=headers, json=body)
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    deadline = time.time() + 120
    while time.time() < deadline:
        job = client.get(f"/documents/process/{job_id}", headers=headers).json()
        if job["status"] == "done":
            return job["result"]
        if job["status"] == "error":
            raise AssertionError(f"traitement en échec : {job['error']}")
        time.sleep(0.5)
    raise AssertionError("délai dépassé pour le traitement asynchrone")


def test_requires_auth(client):
    assert client.get("/documents").status_code == 401


def test_csrf_required(client):
    _login(client)
    # Les méthodes qui changent l'état exigent toujours le token CSRF.
    assert client.post("/documents/upload").status_code == 403  # csrf manquant


def test_csrf_not_required_on_reads(client):
    # Lecture seule (GET) : cookie SameSite=Strict suffit — nécessaire pour
    # l'export HTML ouvert dans un nouvel onglet (navigation sans header).
    _login(client)
    assert client.get("/documents").status_code == 200


def test_csrf_wrong_token_rejected(client):
    _login(client)
    r = client.post("/documents/upload", headers={"X-CSRF-Token": "mauvais-token"})
    assert r.status_code == 403


def test_full_flow(client, tmp_path):
    headers = _login(client)
    from pathlib import Path

    synth = (
        Path(__file__).resolve().parents[2] / "data" / "synthetic" / "cas_001_clean.png"
    )
    if not synth.exists():
        pytest.skip("dataset non généré")
    up = client.post(
        "/documents/upload",
        headers=headers,
        files={"file": ("cas.png", synth.read_bytes(), "image/png")},
    )
    assert up.status_code == 200
    doc_id = up.json()["document_id"]
    body = _process_and_wait(
        client,
        headers,
        doc_id,
        engine="tesseract",
        anonymize_mode="pseudo",
    )
    assert body["pii_detected_count"] >= 3
    vsm_id = body["vsm_id"]
    val = client.post(
        f"/vsm/{vsm_id}/validate", headers=headers, json={"statut": "signe"}
    )
    assert val.status_code == 200 and "signature" in val.json()
    audit = client.get("/audit", headers=headers)
    assert audit.json()["chain_valid"] is True
    dele = client.delete(f"/documents/{doc_id}", headers=headers)
    assert dele.json()["deleted_documents"] == 1


def test_process_async_flow(client, tmp_path):
    # Le POST /process répond immédiatement (job_id) ; l'état évolue
    # processing → done ; le VSM est visible dès la fin (sans reconnexion).
    headers = _login(client)
    from PIL import Image

    p = tmp_path / "mini.png"
    Image.new("L", (60, 60), 255).save(p)
    up = client.post(
        "/documents/upload",
        headers=headers,
        files={"file": ("mini.png", p.read_bytes(), "image/png")},
    )
    doc_id = up.json()["document_id"]
    r = client.post(
        f"/documents/{doc_id}/process",
        headers=headers,
        json={"engine": "tesseract", "anonymize_mode": "pseudo"},
    )
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    assert r.json()["status"] == "processing"
    result = _process_and_wait(
        client, headers, doc_id, engine="tesseract", anonymize_mode="pseudo"
    )
    # visible dans la liste SANS nouvelle session
    vsms = client.get("/vsm", headers=headers).json()
    assert any(v["id"] == result["vsm_id"] for v in vsms)


def test_passage_source_document_id_coherent(client, tmp_path):
    # « Voir le passage source » : source.document_id du VSM doit égaler l'id
    # d'upload (clé de stockage OCR) — sinon le visualiseur renvoie 404.
    headers = _login(client)
    synth = (
        Path(__file__).resolve().parents[2] / "data" / "synthetic" / "cas_001_clean.png"
    )
    if not synth.exists():
        pytest.skip("dataset non généré")
    up = client.post(
        "/documents/upload",
        headers=headers,
        files={"file": ("cas.png", synth.read_bytes(), "image/png")},
    )
    doc_id = up.json()["document_id"]
    result = _process_and_wait(
        client, headers, doc_id, engine="tesseract", anonymize_mode="pseudo"
    )
    vsm_id = result["vsm_id"]
    vsm = client.get(f"/vsm/{vsm_id}", headers=headers).json()

    # 1) source.document_id == id uploadé (et non un id interne généré)
    sources = [
        it["source"]["document_id"]
        for items in vsm["sections"].values()
        for it in items
        if it.get("source", {}).get("document_id")
    ]
    assert sources, "aucune source documentée"
    assert all(d == doc_id for d in sources), f"ids incohérents : {set(sources)}"

    # 2) le passage est bien retrouvable via GET /documents/{doc_id}/ocr
    ocr = client.get(f"/documents/{doc_id}/ocr", headers=headers)
    assert ocr.status_code == 200
    text = ocr.json()["text"]
    passages = [
        it["source"]["passage"]
        for items in vsm["sections"].values()
        for it in items
        if it.get("source", {}).get("passage")
    ]
    assert any(p in text for p in passages)


def test_export_pdf(client, tmp_path):
    # Export PDF : réponse application/pdf téléchargeable (ReportLab local).
    headers = _login(client)
    synth = (
        Path(__file__).resolve().parents[2] / "data" / "synthetic" / "cas_001_clean.png"
    )
    if not synth.exists():
        pytest.skip("dataset non généré")
    up = client.post(
        "/documents/upload",
        headers=headers,
        files={"file": ("cas.png", synth.read_bytes(), "image/png")},
    )
    doc_id = up.json()["document_id"]
    body = _process_and_wait(
        client,
        headers,
        doc_id,
        engine="tesseract",
        anonymize_mode="pseudo",
    )
    vsm_id = body["vsm_id"]
    r = client.get(f"/vsm/{vsm_id}/export?fmt=pdf", headers=headers)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/pdf")
    assert "attachment" in r.headers["content-disposition"]
    assert r.content[:4] == b"%PDF"  # en-tête de fichier PDF
    assert len(r.content) > 1000
    # Format inconnu → 400 (sur un VSM existant)
    r_bad = client.get(f"/vsm/{vsm_id}/export?fmt=docx", headers=headers)
    assert r_bad.status_code == 400


def test_health_exposes_upload_limit(client):
    assert client.get("/health").json()["max_upload_mb"] == 50


def test_process_invalid_nlp_engine_rejected(client):
    _login(client)
    # nlp_engine hors (rules|llm) → 422 (validation Pydantic) — pas de cloud.
    r = client.post(
        "/documents/x/process",
        headers=_login(client),
        json={"engine": "tesseract", "anonymize_mode": "pseudo", "nlp_engine": "cloud"},
    )
    assert r.status_code == 422


def test_process_unlimited_engine_rejected_without_gpu(client, tmp_path):
    # Sans carte NVIDIA, le moteur « unlimited » n'existe pas → 400 explicite
    # (et non 500). Il n'apparaît pas non plus dans /health.
    assert "unlimited" not in client.get("/health").json()["available_engines"]
    headers = _login(client)
    from PIL import Image

    p = tmp_path / "mini.png"
    Image.new("L", (40, 40), 255).save(p)
    up = client.post(
        "/documents/upload",
        headers=headers,
        files={"file": ("mini.png", p.read_bytes(), "image/png")},
    )
    doc_id = up.json()["document_id"]
    r = client.post(
        f"/documents/{doc_id}/process",
        headers=headers,
        json={"engine": "unlimited", "anonymize_mode": "pseudo"},
    )
    assert r.status_code == 400
    assert "unlimited" in r.json()["detail"]


def test_health_lists_available_engines(client):
    engines = client.get("/health").json()["available_engines"]
    assert "tesseract" in engines  # moteur CPU de référence toujours présent


def _seed_vsm(store, vsm_id, statut, codes, extra=None):
    """Crée un VSM de test dans le store (sans OCR — rapide)."""
    sections = {}
    for section, entries in codes.items():
        sections[section] = [
            {
                "valeur": f"item-{i}",
                "confiance": 0.8,
                "source": {"passage": "x", "offset_debut": 0, "offset_fin": 1},
                "code_normalise": {
                    "systeme": sys_,
                    "code": code,
                    "libelle_officiel": f"lib-{code}",
                },
            }
            for i, (sys_, code) in enumerate(entries)
        ]
    vsm = {
        "schema_version": "1.1.0",
        "document_id": vsm_id,
        "date_generation": "2026-08-01T10:00:00+00:00",
        "statut": statut,
        "patient": {},
        "medecin_traitant": {},
        "sections": sections,
    }
    if extra:
        vsm.update(extra)
    store.store_vsm(vsm_id, vsm_id, vsm)


def test_stats_aggregates_and_masking(client):
    # 6 VSM « E11 » + 1 VSM « J45 » : E11 (6, affiché) ; J45 (1, masqué).
    headers = _login(client)
    # Accès au store de la session du TestClient (pas d'OCR — VSM injectés).
    import src.ui_backend.main as m

    sess = m._sessions[client.cookies.get("vsm_session")]
    st = sess["store"]
    for i in range(6):
        _seed_vsm(
            st, f"vsm_e{i}", "signe", {"pathologies_actives": [("CIM-10", "E11")]}
        )
    _seed_vsm(st, "vsm_j", "a_valider", {"pathologies_actives": [("CIM-10", "J45")]})

    r = client.get("/stats", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 7
    assert body["par_statut"] == {"signe": 6, "a_valider": 1}
    # E11 (6 ≥ 5) → count réel ; J45 (1 < 5) → masqué
    by_code = {p["code"]: p for p in body["pathologies"]}
    assert by_code["E11"]["count"] == 6 and by_code["E11"]["masque"] is False
    assert by_code["J45"]["count"] is None and by_code["J45"]["masque"] is True
    assert body["seuil"] == 5
    assert "non représentatif" in body["avertissement"]


def test_stats_no_patient_detail(client):
    headers = _login(client)
    import src.ui_backend.main as m

    sess = m._sessions[client.cookies.get("vsm_session")]
    _seed_vsm(
        sess["store"],
        "vsm_x",
        "valide",
        {
            "traitements_long_cours": [("ATC", "A10BA02"), ("ATC", "J01CA04")],
        },
        extra={
            "patient": {
                "identite": {"valeur": "[PATIENT_001]", "confiance": 1.0, "source": {}}
            },
        },
    )
    r = client.get("/stats", headers=headers)
    body = r.json()
    # Aucun détail patient / token dans la réponse
    assert "PATIENT" not in str(body)
    assert "identite" not in str(body)
    codes = {t["code"] for t in body["traitements"]}
    assert codes == {"A10BA02", "J01CA04"}  # les codes normalisés restent visibles
    assert body["traitements"][0]["masque"] is True  # 1 occurrence < 5


def test_stats_reflect_right_to_be_forgotten(client):
    # Recalcul à la demande : supprimer un dossier le retire des stats.
    headers = _login(client)
    import src.ui_backend.main as m

    sess = m._sessions[client.cookies.get("vsm_session")]
    st = sess["store"]
    _seed_vsm(st, "vsm_a", "signe", {"pathologies_actives": [("CIM-10", "E11")]})
    _seed_vsm(st, "vsm_b", "signe", {"pathologies_actives": [("CIM-10", "E11")]})
    assert client.get("/stats", headers=headers).json()["total"] == 2
    st.delete_dossier("vsm_a")
    body = client.get("/stats", headers=headers).json()
    assert body["total"] == 1
    # E11 passe sous le seuil après oubli → masqué
    assert body["pathologies"][0]["masque"] is True


def test_upload_limit_configurable(client_small_limit):
    # VSM_MAX_UPLOAD_MB=1 : un fichier de 2 Mo est rejeté (413), 1 Ko passe.
    headers = _login(client_small_limit)
    big = b"x" * (2 * 1024 * 1024)
    r = client_small_limit.post(
        "/documents/upload",
        headers=headers,
        files={"file": ("big.png", big, "image/png")},
    )
    assert r.status_code == 413
    assert "VSM_MAX_UPLOAD_MB" in r.json()["detail"]
    small = b"y" * 1024
    r2 = client_small_limit.post(
        "/documents/upload",
        headers=headers,
        files={"file": ("small.png", small, "image/png")},
    )
    assert r2.status_code == 200
