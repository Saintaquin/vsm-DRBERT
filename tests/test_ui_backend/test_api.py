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
    pr = client.post(
        f"/documents/{doc_id}/process",
        headers=headers,
        json={"engine": "tesseract", "anonymize_mode": "pseudo"},
    )
    assert pr.status_code == 200
    body = pr.json()
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
    pr = client.post(
        f"/documents/{doc_id}/process",
        headers=headers,
        json={"engine": "tesseract", "anonymize_mode": "pseudo"},
    )
    vsm_id = pr.json()["vsm_id"]
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
