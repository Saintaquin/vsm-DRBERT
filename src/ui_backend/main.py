"""Backend local de l'application VSM-OCR (FastAPI).

- Écoute UNIQUEMENT sur 127.0.0.1 (jamais 0.0.0.0)
- Auth : session cookie httpOnly + SameSite=Strict + token CSRF (double submit)
- Tous les endpoints (sauf /auth/*, /health) exigent une session valide
- Validation Pydantic systématique
- Aucune PII en clair dans les logs

Lancement : python -m src.ui_backend.main  (ou via le wrapper Tauri)
"""

from __future__ import annotations

import os
import secrets
import tempfile
import time
import uuid
from pathlib import Path

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.anonymization.mapping_vault import MappingVault
from src.extraction_nlp.pipeline import run_pipeline as nlp_pipeline
from src.ingestion_ocr.ocr_engines import ENGINES
from src.ingestion_ocr.pipeline import run_pipeline as ocr_pipeline
from src.storage.auth import AuthManager
from src.storage.encrypted_store import EncryptedStore
from src.storage.key_manager import SessionKey, new_salt

APP_DIR = Path(os.environ.get("VSM_DATA_DIR", Path.home() / ".vsm-ocr"))
APP_DIR.mkdir(parents=True, exist_ok=True)
SALT_FILE = APP_DIR / "kdf.salt"
# Limite de taille d'upload, configurable par l'opérateur (Mo) :
# VSM_MAX_UPLOAD_MB — défaut 50 Mo (voir docs/SECURITY.md, menace T6).
MAX_UPLOAD_MB = int(os.environ.get("VSM_MAX_UPLOAD_MB", "50"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

app = FastAPI(title="VSM-OCR", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:1420",
        "http://localhost:1420",
        "tauri://localhost",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------------------------------------------- état
_sessions: dict[str, dict] = {}  # sid -> {user, csrf, key: SessionKey, store}
SESSION_TTL = 15 * 60


def _get_salt() -> bytes:
    if SALT_FILE.exists():
        return SALT_FILE.read_bytes()
    salt = new_salt()
    SALT_FILE.write_bytes(salt)
    return salt


# ----------------------------------------------------------------- modèles
class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class BootstrapIn(LoginIn):
    role: str = Field(pattern="^(medecin|secretaire|admin)$")


class ProcessIn(BaseModel):
    engine: str = Field(default="tesseract", pattern="^[a-z0-9_]+$")
    anonymize_mode: str = Field(default="pseudo", pattern="^(pseudo|strict)$")
    # Moteur NLP : « rules » (défaut, offline) ou « llm » (LLM local,
    # téléchargé par l'admin — voir docs/ADR/0004). Strictement local.
    nlp_engine: str = Field(default="rules", pattern="^(rules|llm)$")


class ValidateIn(BaseModel):
    sections: dict | None = None
    statut: str = Field(default="valide", pattern="^(a_valider|valide|signe)$")
    signe_par: str | None = Field(default=None, max_length=128)


# ----------------------------------------------------------------- deps
def current_session(
    request: Request,
    vsm_session: str | None = Cookie(default=None),
    x_csrf_token: str | None = Header(default=None),
) -> dict:
    sess = _sessions.get(vsm_session or "")
    if not sess or time.monotonic() - sess["last"] > SESSION_TTL:
        if sess:
            sess["key"].close()
            _sessions.pop(vsm_session, None)
        raise HTTPException(401, "Session expirée ou absente")
    # Les lectures (GET/HEAD) ne modifient aucun état : le cookie
    # SameSite=Strict bloque déjà les requêtes cross-site, un token CSRF
    # n'y est pas requis — nécessaire pour l'export HTML ouvert dans un
    # nouvel onglet (navigation simple, sans en-tête X-CSRF-Token).
    if request.method not in ("GET", "HEAD") and x_csrf_token != sess["csrf"]:
        raise HTTPException(403, "Token CSRF invalide")
    sess["last"] = time.monotonic()
    sess["key"].touch()
    return sess


# ----------------------------------------------------------------- auth
@app.get("/health")
def health():
    return {
        "status": "ok",
        "max_upload_mb": MAX_UPLOAD_MB,
        # Moteurs OCR réellement disponibles sur ce poste — « unlimited »
        # n'apparaît QUE si une carte NVIDIA est détectée (docs/ADR/0005).
        "available_engines": sorted(ENGINES),
    }


@app.post("/auth/bootstrap")
def bootstrap(body: BootstrapIn):
    """Création du premier compte admin (uniquement si aucun utilisateur)."""
    import sqlite3

    conn = sqlite3.connect(str(APP_DIR / "users.db"))
    auth = AuthManager(conn)
    count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if count:
        conn.close()
        raise HTTPException(409, "Des comptes existent déjà")
    auth.create_user(body.username, body.password, body.role)
    conn.close()
    return {"created": body.username}


@app.post("/auth/login")
def login(body: LoginIn):
    import sqlite3

    conn = sqlite3.connect(str(APP_DIR / "users.db"))
    auth = AuthManager(conn)
    try:
        user = auth.verify(body.username, body.password)
    except PermissionError as exc:
        conn.close()
        raise HTTPException(423, str(exc)) from exc
    conn.close()
    if user is None:
        raise HTTPException(401, "Identifiants invalides")

    sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    key = SessionKey.from_passphrase(
        body.password, _get_salt(), timeout_sec=SESSION_TTL
    )
    store = EncryptedStore(APP_DIR / "vsm.db", key)
    _sessions[sid] = {
        "user": user,
        "csrf": csrf,
        "key": key,
        "store": store,
        "last": time.monotonic(),
    }
    store.append_audit(user["username"], "login", {"role": user["role"]})

    resp = JSONResponse(
        {"username": user["username"], "role": user["role"], "csrf": csrf}
    )
    resp.set_cookie(
        "vsm_session", sid, httponly=True, samesite="strict", max_age=SESSION_TTL
    )
    return resp


@app.post("/auth/logout")
def logout(
    sess: dict = Depends(current_session),
    vsm_session: str | None = Cookie(default=None),
):
    sess["store"].append_audit(sess["user"]["username"], "logout", {})
    sess["key"].close()
    _sessions.pop(vsm_session, None)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("vsm_session")
    return resp


# ----------------------------------------------------------------- documents
_CHUNK = 1 << 20  # 1 Mo — lecture par blocs de l'upload


@app.post("/documents/upload")
async def upload(file: UploadFile, sess: dict = Depends(current_session)):
    suffix = Path(file.filename or "doc").suffix.lower()
    if suffix not in (".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"):
        raise HTTPException(415, f"Format non supporté : {suffix}")
    # Lecture par blocs : un fichier trop volumineux est rejeté en cours de
    # flux, sans jamais être chargé intégralement en mémoire.
    chunks: list[bytes] = []
    size = 0
    while chunk := await file.read(_CHUNK):
        size += len(chunk)
        if size > MAX_UPLOAD_BYTES:
            raise HTTPException(
                413,
                f"Fichier trop volumineux (limite {MAX_UPLOAD_MB} Mo — "
                f"configurable via VSM_MAX_UPLOAD_MB)",
            )
        chunks.append(chunk)
    content = b"".join(chunks)
    document_id = f"doc_{uuid.uuid4().hex[:12]}"
    import hashlib

    sha = hashlib.sha256(content).hexdigest()
    store: EncryptedStore = sess["store"]
    store.store_document(
        document_id, document_id, file.filename or document_id, sha, content
    )
    store.append_audit(
        sess["user"]["username"],
        "document_uploaded",
        {"document_id": document_id, "sha256": sha, "size": size},
    )
    return {"document_id": document_id, "sha256": sha}


@app.post("/documents/{document_id}/process")
def process(document_id: str, body: ProcessIn, sess: dict = Depends(current_session)):
    store: EncryptedStore = sess["store"]
    try:
        content = store.load_document(document_id)
        meta = next(d for d in store.list_documents() if d["id"] == document_id)
    except (KeyError, StopIteration):
        raise HTTPException(404, "Document inconnu") from None

    suffix = Path(meta["filename"]).suffix or ".png"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        ocr_json = ocr_pipeline(
            tmp_path,
            engine=body.engine,
            anonymize_mode=body.anonymize_mode,
            dossier_id=document_id,
        )
    except ValueError as exc:
        # Moteur inconnu ou indisponible sur ce poste (ex. « unlimited »
        # sans carte NVIDIA) → 400 explicite, jamais 500.
        raise HTTPException(400, str(exc)) from None
    finally:
        os.unlink(tmp_path)

    mapping = ocr_json.pop("_pii_mapping", None)
    if mapping:
        passphrase = os.environ.get("VSM_VAULT_PASSPHRASE")
        if passphrase:
            MappingVault(APP_DIR / "vault.bin", passphrase).store_mapping(
                document_id, mapping
            )
        # sans passphrase de coffre, le mapping est volontairement perdu (≈ strict)

    nlp_json = nlp_pipeline(ocr_json, nlp_engine=body.nlp_engine)
    from src.vsm_generation.vsm_builder import build_vsm

    vsm = build_vsm(nlp_json)
    vsm_id = f"vsm_{uuid.uuid4().hex[:12]}"
    vsm["document_id"] = document_id
    store.store_ocr_result(document_id, ocr_json)
    store.store_nlp_result(document_id, nlp_json)
    store.store_vsm(vsm_id, document_id, vsm)
    for entry in ocr_json.get("audit_entries", []):
        store.append_audit(sess["user"]["username"], "pii_detected", entry)
    store.append_audit(
        sess["user"]["username"],
        "document_processed",
        {
            "document_id": document_id,
            "vsm_id": vsm_id,
            "engine": body.engine,
            "nlp_engine": body.nlp_engine,
            "pii_count": ocr_json["pii_detected_count"],
        },
    )
    return {
        "vsm_id": vsm_id,
        "vsm": vsm,
        "processing_report": ocr_json["processing_report"],
        "pii_detected_count": ocr_json["pii_detected_count"],
    }


@app.get("/documents")
def list_documents(sess: dict = Depends(current_session)):
    return sess["store"].list_documents()


@app.get("/documents/{document_id}/ocr")
def get_ocr(document_id: str, sess: dict = Depends(current_session)):
    try:
        return sess["store"].load_ocr_result(document_id)
    except KeyError:
        raise HTTPException(404, "Résultat OCR introuvable") from None


@app.delete("/documents/{dossier_id}")
def forget(dossier_id: str, sess: dict = Depends(current_session)):
    """Droit à l'oubli : suppression complète du dossier en une action."""
    store: EncryptedStore = sess["store"]
    n = store.delete_dossier(dossier_id)
    passphrase = os.environ.get("VSM_VAULT_PASSPHRASE")
    vault_forgotten = False
    if passphrase and (APP_DIR / "vault.bin").exists():
        vault_forgotten = MappingVault(APP_DIR / "vault.bin", passphrase).forget(
            dossier_id
        )
    store.append_audit(
        sess["user"]["username"],
        "dossier_deleted",
        {
            "dossier_id": dossier_id,
            "documents_removed": n,
            "pii_mapping_removed": vault_forgotten,
        },
    )
    return {"deleted_documents": n, "pii_mapping_removed": vault_forgotten}


# ----------------------------------------------------------------- VSM
@app.get("/vsm")
def list_vsm(sess: dict = Depends(current_session)):
    return sess["store"].list_vsm()


@app.get("/vsm/{vsm_id}")
def get_vsm(vsm_id: str, sess: dict = Depends(current_session)):
    try:
        return sess["store"].load_vsm(vsm_id)
    except KeyError:
        raise HTTPException(404, "VSM introuvable") from None


@app.post("/vsm/{vsm_id}/validate")
def validate_vsm(vsm_id: str, body: ValidateIn, sess: dict = Depends(current_session)):
    if body.statut == "signe" and sess["user"]["role"] != "medecin":
        raise HTTPException(403, "Seul un médecin peut signer un VSM")
    store: EncryptedStore = sess["store"]
    try:
        vsm = store.load_vsm(vsm_id)
    except KeyError:
        raise HTTPException(404, "VSM introuvable") from None
    if body.sections:
        vsm["sections"].update(body.sections)
    vsm["statut"] = body.statut
    if body.statut == "signe":
        import hashlib
        import json as _json
        from datetime import datetime, timezone

        vsm["signature"] = {
            "signe_par": body.signe_par or sess["user"]["username"],
            "date_signature": datetime.now(timezone.utc).isoformat(),
            "empreinte_vsm": hashlib.sha256(
                _json.dumps(vsm["sections"], sort_keys=True).encode()
            ).hexdigest(),
        }
    from src.vsm_generation.vsm_builder import validate_vsm as _check

    _check(vsm)
    store.store_vsm(vsm_id, vsm.get("document_id", vsm_id), vsm)
    store.append_audit(
        sess["user"]["username"],
        "vsm_status_changed",
        {"vsm_id": vsm_id, "statut": body.statut},
    )
    return vsm


@app.get("/vsm/{vsm_id}/export")
def export_vsm(vsm_id: str, fmt: str = "html", sess: dict = Depends(current_session)):
    from src.vsm_generation.renderer import render_vsm

    try:
        vsm = sess["store"].load_vsm(vsm_id)
    except KeyError:
        raise HTTPException(404, "VSM introuvable") from None
    if fmt == "html":
        return HTMLResponse(render_vsm(vsm, "html"))
    if fmt == "markdown":
        return JSONResponse({"markdown": render_vsm(vsm, "markdown")})
    if fmt == "pdf":
        # Génération PDF 100 % locale (ReportLab) puis réponse téléchargeable.
        from src.vsm_generation.renderer import render_pdf

        fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        try:
            render_pdf(vsm, tmp_path)
            data = Path(tmp_path).read_bytes()
        finally:
            os.unlink(tmp_path)
        return Response(
            content=data,
            media_type="application/pdf",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="vsm_{vsm_id.replace("/", "_")}.pdf"'
                )
            },
        )
    raise HTTPException(400, "Format : html | markdown | pdf")


# ----------------------------------------------------------------- audit
@app.get("/audit")
def audit(limit: int = 200, sess: dict = Depends(current_session)):
    if sess["user"]["role"] not in ("admin", "medecin"):
        raise HTTPException(403, "Accès réservé")
    store: EncryptedStore = sess["store"]
    return {
        "chain_valid": store.verify_audit_chain(),
        "entries": store.read_audit(limit),
    }


# ----------------------------------------------------------------- frontend statique
_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _DIST.exists():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="frontend")


def main():
    import uvicorn

    # 127.0.0.1 STRICTEMENT — jamais 0.0.0.0 (cf. garde-fous projet)
    uvicorn.run(app, host="127.0.0.1", port=8741, log_level="warning")


if __name__ == "__main__":
    main()
