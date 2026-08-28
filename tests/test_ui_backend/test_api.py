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


def test_session_cookie_is_sliding(client):
    # Session GLISSANTE : chaque requête authentifiée re-émet le cookie avec un
    # max_age rafraîchi — sinon un traitement > 15 min (gros document + LLM)
    # déconnecterait l'utilisateur à son retour (le cookie expirait 15 min
    # après la connexion, indépendamment de l'activité).
    _login(client)
    r = client.get("/documents")
    assert r.status_code == 200
    assert "vsm_session=" in r.headers.get("set-cookie", "")


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
    # NB : on POLLE le job lancé (un second POST sur le même document est
    # désormais refusé : 409 anti double-traitement).
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
    import time

    deadline = time.time() + 120
    result = None
    while time.time() < deadline:
        job = client.get(f"/documents/process/{job_id}", headers=headers).json()
        if job["status"] == "done":
            result = job["result"]
            break
        if job["status"] == "error":
            raise AssertionError(f"traitement en échec : {job['error']}")
        time.sleep(0.5)
    assert result is not None, "délai dépassé pour le traitement asynchrone"
    # visible dans la liste SANS nouvelle session
    vsms = client.get("/vsm", headers=headers).json()
    assert any(v["id"] == result["vsm_id"] for v in vsms)


def test_process_duplicate_job_rejected(client, tmp_path):
    # Anti double-traitement : un second POST /process sur le même document
    # pendant qu'un job tourne → 409 (deux jobs concurrents se disputent le
    # modèle et gâchent des heures de génération).
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
    r1 = client.post(
        f"/documents/{doc_id}/process",
        headers=headers,
        json={"engine": "tesseract", "anonymize_mode": "pseudo"},
    )
    assert r1.status_code == 200
    r2 = client.post(
        f"/documents/{doc_id}/process",
        headers=headers,
        json={"engine": "tesseract", "anonymize_mode": "pseudo"},
    )
    assert r2.status_code == 409
    assert "déjà en cours" in r2.json()["detail"]


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
    # nlp_engine hors (drbert|llm|rules|regles) → 422 (validation Pydantic) —
    # pas de cloud.
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


def test_health_reports_llm_availability(client, monkeypatch):
    # LLM par défaut : /health expose « available » dès que le modèle est
    # présent (tenté sur toutes les machines), + avertissement RAM non bloquant.
    import src.extraction_nlp.llm as llm_mod

    monkeypatch.setattr(llm_mod, "llm_attemptable", lambda: False)
    monkeypatch.setattr(llm_mod, "llm_ram_warning", lambda: "")
    h = client.get("/health").json()
    assert h["llm_available"] is False
    monkeypatch.setattr(llm_mod, "llm_attemptable", lambda: True)
    monkeypatch.setattr(llm_mod, "llm_ram_warning", lambda: "RAM juste, lent")
    h = client.get("/health").json()
    assert h["llm_available"] is True
    assert h["llm_reason"] == "RAM juste, lent"


def test_process_default_nlp_engine_is_drbert_with_fallback(client, tmp_path):
    # Sans nlp_engine explicite, le POST /process utilise « drbert » par
    # défaut (VSM_NLP_ENGINE) ; le conftest force un modèle ABSENT : le repli
    # règles produit quand même le VSM, et le rapport trace « modele_absent ».
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
    r = client.post(
        f"/documents/{doc_id}/process",
        headers=headers,
        json={"engine": "tesseract", "anonymize_mode": "pseudo"},
    )
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    import time

    deadline = time.time() + 120
    result = None
    while time.time() < deadline:
        job = client.get(f"/documents/process/{job_id}", headers=headers).json()
        if job["status"] == "done":
            result = job["result"]
            break
        if job["status"] == "error":
            raise AssertionError(f"traitement en échec : {job['error']}")
        time.sleep(0.5)
    assert result is not None
    assert result["vsm_id"]
    vsm = client.get(f"/vsm/{result['vsm_id']}", headers=headers).json()
    assert vsm["provenance"]["moteur_nlp"] == "rules-fr-v1"  # repli automatique
    assert result["nlp_report"]["statut"] == "modele_absent"  # repli TRACÉ


def test_process_llm_indisponible_derive_vers_drbert(client, tmp_path, monkeypatch):
    # Frontend ANCIEN : il envoie nlp_engine="llm" alors que le GGUF a été
    # retiré du paquet. Le backend doit dériver vers DrBERT (moteur de
    # l'application) — PAS vers les règles. Moteur DrBERT FACTICE : aucun
    # modèle réel n'est chargé (le conftest force VSM_DRBERT_PATH vide, on
    # patche modele_disponible et le singleton).
    import types

    import src.extraction_nlp.drbert_extractor as dtx
    from src.extraction_nlp.drbert_extractor import Entite

    def _annoter(texte: str):
        if "Hypertension" not in texte:
            return []
        debut = texte.index("Hypertension")
        return [
            Entite(
                "problem",
                "Hypertension arterielle",
                debut,
                debut + len("Hypertension arterielle"),
                0.95,
            )
        ]

    monkeypatch.setattr(dtx, "modele_disponible", lambda dossier=None: True)
    monkeypatch.setattr(dtx, "_MOTEUR", types.SimpleNamespace(annoter=_annoter))

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
        client,
        headers,
        doc_id,
        engine="tesseract",
        anonymize_mode="pseudo",
        nlp_engine="llm",  # demande d'un frontend périmé
    )
    assert result["nlp_report"]["moteur"] == "drbert-casm2-v1"
    assert result["nlp_report"]["statut"] == "drbert"
    vsm = client.get(f"/vsm/{result['vsm_id']}", headers=headers).json()
    assert vsm["provenance"]["moteur_nlp"] == "drbert-casm2-v1"


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


def _seed_assist_vsm(store, vsm_id="vsm_assist", doc_id="doc_assist"):
    """VSM avec un champ « À valider » + texte OCR stocké (sans OCR réel)."""
    store.store_ocr_result(
        doc_id, {"document_id": doc_id, "text": "ANTECEDENTS : Diabete de type 2."}
    )
    vsm = {
        "schema_version": "1.1.0",
        "document_id": doc_id,
        "date_generation": "2026-08-01T10:00:00+00:00",
        "statut": "a_valider",
        "patient": {},
        "medecin_traitant": {},
        "sections": {
            "pathologies_actives": [],
            "antecedents": [
                {
                    "valeur": "Diabete de type 2",
                    "confiance": 0.6,
                    "a_valider": True,
                    "source": {"passage": "Diabete de type 2"},
                },
                {
                    "valeur": "Hypertension",
                    "confiance": 0.95,
                    "a_valider": False,
                    "source": {"passage": "Hypertension"},
                },
            ],
            "allergies": [],
            "traitements_long_cours": [],
            "facteurs_risque": [],
            "vaccinations": [],
            "points_vigilance": [],
        },
        "provenance": {"moteur_nlp": "rules-fr-v1"},
    }
    store.store_vsm(vsm_id, doc_id, vsm)
    return vsm_id


def test_llm_assist_updates_fields_to_validate(client, monkeypatch):
    # « Relire par le LLM local » : améliore UNIQUEMENT les champs « À valider »
    # (phase LLM simulée via monkeypatch — pas de GPU en CI).
    headers = _login(client)
    import src.extraction_nlp.entity_extractor as ee
    import src.extraction_nlp.llm as llm_mod
    import src.ui_backend.main as m
    from src.extraction_nlp.entity_extractor import ExtractedEntity

    sess = m._sessions[client.cookies.get("vsm_session")]
    vsm_id = _seed_assist_vsm(sess["store"])
    monkeypatch.setattr(llm_mod, "llm_attemptable", lambda: True)

    def fake_extract(text, engine="rules"):
        return (
            [
                ExtractedEntity(
                    "Diabète de type 2",
                    "antecedents",
                    0.9,
                    "Diabete de type 2",
                    0,
                    18,
                    correction_ocr=True,
                )
            ],
            {
                "moteur": "llm-local-q4",
                "statut": "llm_complet",
                "raison": None,
                "phase_correction_ocr": True,
                "nb_corrections_ocr": 1,
                "duree_correction_sec": 1.0,
                "duree_extraction_sec": 1.0,
                "modele": "test.gguf",
            },
        )

    monkeypatch.setattr(ee, "extract_entities_with_report", fake_extract)
    r = client.post(f"/vsm/{vsm_id}/llm-assist", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["champs_mis_a_jour"] == 1
    champs = body["vsm"]["sections"]["antecedents"]
    assert champs[0]["valeur"] == "Diabète de type 2"
    assert champs[0]["confiance"] == 0.9
    assert champs[0]["a_valider"] is False
    assert champs[0]["correction_ocr"] is True
    # le champ déjà fiable n'est PAS touché
    assert champs[1]["valeur"] == "Hypertension" and champs[1]["confiance"] == 0.95
    assert body["vsm"]["provenance"]["nlp"]["statut"] == "llm_complet"
    # événement d'audit tracé
    audit = sess["store"].read_audit(10)
    assert any(e["event"] == "llm_assist" for e in audit)


def test_llm_assist_requires_model(client, monkeypatch):
    # Sans modèle LLM local → 409 explicite (jamais d'appel cloud).
    headers = _login(client)
    import src.extraction_nlp.llm as llm_mod
    import src.ui_backend.main as m

    sess = m._sessions[client.cookies.get("vsm_session")]
    vsm_id = _seed_assist_vsm(sess["store"])
    monkeypatch.setattr(llm_mod, "llm_attemptable", lambda: False)
    r = client.post(f"/vsm/{vsm_id}/llm-assist", headers=headers)
    assert r.status_code == 409
    assert "python -m" in r.json()["detail"]
