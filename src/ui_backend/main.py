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
import threading
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
from src.ui_backend.logging_setup import get_logger, setup_logging

_log = get_logger()

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
# Traitements asynchrones (longs) : job_id -> état (correction « temps infini »
# + expiration de session pendant le traitement — voir process/process_status).
_jobs: dict[str, dict] = {}
_JOB_TTL = 60 * 60  # rétention des tâches terminées (purge après 1 h)


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
    _log.info("document uploadé id=%s taille=%d", document_id, size)
    return {"document_id": document_id, "sha256": sha}


@app.post("/documents/{document_id}/process")
def process(document_id: str, body: ProcessIn, sess: dict = Depends(current_session)):
    """Lance le traitement EN ARRIÈRE-PLAN et répond immédiatement.

    Le traitement (OCR puis extraction) peut être long sur des PDF volumineux :
    il s'exécute dans un thread ; l'UI interroge GET /documents/process/{job_id}
    (chaque requête « touche » la session → pas d'expiration pendant le job)."""
    store: EncryptedStore = sess["store"]
    try:
        store.load_document(document_id)
        meta = next(d for d in store.list_documents() if d["id"] == document_id)
    except (KeyError, StopIteration):
        raise HTTPException(404, "Document inconnu") from None
    if body.engine not in ENGINES:
        # Moteur indisponible sur ce poste (ex. « unlimited » sans NVIDIA) → 400
        raise HTTPException(
            400,
            f"Moteur OCR '{body.engine}' indisponible. Installés : {sorted(ENGINES)}",
        )

    job_id = f"job_{uuid.uuid4().hex[:12]}"
    _prune_jobs()
    _jobs[job_id] = {
        "job_id": job_id,
        "document_id": document_id,
        "filename": meta["filename"],
        "status": "processing",
        "step": "démarrage",
        "vsm_id": None,
        "result": None,
        "error": None,
        "created": time.time(),
        "updated": time.time(),
    }
    thread = threading.Thread(
        target=_run_process_job,
        args=(job_id, document_id, body, sess),
        daemon=True,
    )
    thread.start()
    _log.info("traitement lancé job=%s document=%s", job_id, document_id)
    return {"job_id": job_id, "status": "processing"}


@app.get("/documents/process/{job_id}")
def process_status(job_id: str, sess: dict = Depends(current_session)):
    """État d'un traitement : progression puis résultat (ou erreur)."""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Tâche inconnue")
    out = {k: v for k, v in job.items() if k != "updated"}
    if job["status"] == "done":
        # résultat final fusionné (vsm_id, rapport, nb PII) — l'UI rafraîchit
        out["result"] = job["result"]
    return out


def _prune_jobs() -> None:
    """Élimine les tâches terminées de plus d'une heure (mémoire bornée)."""
    now = time.time()
    for jid in [j for j, jb in _jobs.items() if jb["status"] in ("done", "error")]:
        if now - _jobs[jid]["updated"] > _JOB_TTL:
            _jobs.pop(jid, None)


def _run_process_job(
    job_id: str, document_id: str, body: ProcessIn, sess: dict
) -> None:
    """Exécution du pipeline complète, dans un thread dédié.

    La clé de session est « touchée » avant chaque opération chiffrée pour
    éviter son expiration (timeout d'inactivité) pendant les traitements
    longs ; le résultat est stocké normalement (visible immédiatement)."""
    store: EncryptedStore = sess["store"]
    job = _jobs[job_id]

    def step(name: str) -> None:
        job["step"] = name
        job["updated"] = time.time()

    try:
        content = store.load_document(document_id)
        meta = next(d for d in store.list_documents() if d["id"] == document_id)
        suffix = Path(meta["filename"]).suffix or ".png"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        try:
            step("OCR (lecture du document)")
            ocr_json = ocr_pipeline(
                tmp_path,
                engine=body.engine,
                anonymize_mode=body.anonymize_mode,
                dossier_id=document_id,
                # Cohérence « passage source » : le VSM référence CE document_id
                # (source.document_id) ; il doit égaler la clé de stockage OCR.
                document_id=document_id,
            )
        finally:
            os.unlink(tmp_path)

        mapping = ocr_json.pop("_pii_mapping", None)
        passphrase = os.environ.get("VSM_VAULT_PASSPHRASE")
        if mapping and passphrase:
            MappingVault(APP_DIR / "vault.bin", passphrase).store_mapping(
                document_id, mapping
            )

        step("extraction et assemblage du VSM")
        nlp_json = nlp_pipeline(ocr_json, nlp_engine=body.nlp_engine)
        from src.vsm_generation.vsm_builder import build_vsm

        vsm = build_vsm(nlp_json)
        vsm_id = f"vsm_{uuid.uuid4().hex[:12]}"
        vsm["document_id"] = document_id

        # rafraîchit la clé avant les écritures chiffrées (jobs > 15 min)
        sess["key"].touch()
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

        job["vsm_id"] = vsm_id
        job["result"] = {
            "vsm_id": vsm_id,
            "vsm": vsm,
            "processing_report": ocr_json["processing_report"],
            "pii_detected_count": ocr_json["pii_detected_count"],
        }
        job["status"] = "done"
        step("terminé")
        _log.info(
            "document traité id=%s vsm=%s ocr=%s nlp=%s pii=%d",
            document_id,
            vsm_id,
            body.engine,
            body.nlp_engine,
            ocr_json["pii_detected_count"],
        )
    except Exception as exc:  # noqa: BLE001 - l'erreur est rapportée à l'UI
        job["status"] = "error"
        job["error"] = str(exc)
        job["updated"] = time.time()
        _log.warning(
            "traitement en échec job=%s document=%s : %s", job_id, document_id, exc
        )


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
    _log.info("vsm %s → statut %s", vsm_id, body.statut)
    return vsm


@app.get("/vsm/{vsm_id}/export")
def export_vsm(vsm_id: str, fmt: str = "html", sess: dict = Depends(current_session)):
    from src.vsm_generation.renderer import render_vsm

    try:
        vsm = sess["store"].load_vsm(vsm_id)
    except KeyError:
        raise HTTPException(404, "VSM introuvable") from None
    _log.info("export %s vsm=%s", fmt, vsm_id)
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


# ----------------------------------------------------------------- stats
# Statistiques anonymes (audit de faisabilité : outputs/AUDIT_STATISTIQUES.md ;
# ADR-0007). Garde-fous conformité (art. 9 du règlement / RGPD) :
#  - agrégats UNIQUEMENT (comptages) — aucun détail patient, aucun token ;
#  - le coffre-fort de mapping n'est JAMAIS lu (aucun lien vers l'identité) ;
#  - petits effectifs masqués (secret statistique CNIL : n < SEUIL → « < seuil ») ;
#  - calcul à la demande (recalculé à chaque requête) → le droit à l'oubli
#    (suppression d'un dossier) se répercute automatiquement ;
#  - aucun croisement avec des sources externes ; 100 % local.
_STATS_MIN_COUNT = int(os.environ.get("VSM_STATS_MIN_COUNT", "5"))
_STATS_AVERTISSEMENT = (
    "Statistiques descriptives locales, calculées sur les VSM de cette "
    "machine. Échantillon potentiellement non représentatif ; aucune "
    "interprétation médicale. Effectifs inférieurs au seuil de secret "
    f"statistique (n < {_STATS_MIN_COUNT}) affichés « < {_STATS_MIN_COUNT} »."
)


@app.get("/stats")
def stats(sess: dict = Depends(current_session)):
    from collections import Counter
    from datetime import datetime

    store: EncryptedStore = sess["store"]
    vsm_meta = store.list_vsm()

    par_statut: Counter[str] = Counter()
    par_mois: Counter[str] = Counter()
    pathologies: Counter[tuple[str, str]] = Counter()  # (code, libellé)
    traitements: Counter[tuple[str, str]] = Counter()
    completude: dict[str, list[float]] = {}

    for meta in vsm_meta:
        par_statut[meta["statut"]] += 1
        try:
            vsm = store.load_vsm(meta["id"])
        except KeyError:
            continue
        gen = vsm.get("date_generation", "")
        if gen:
            try:
                par_mois[datetime.fromisoformat(gen).strftime("%Y-%m")] += 1
            except ValueError:
                par_mois[gen[:7]] += 1
        for section, items in vsm.get("sections", {}).items():
            if not isinstance(items, list):
                continue
            vals = [i.get("confiance", 0) for i in items if isinstance(i, dict)]
            completude.setdefault(section, []).extend(vals)
            for it in items:
                if not isinstance(it, dict):
                    continue
                code = it.get("code_normalise")
                if not isinstance(code, dict) or not code.get("code"):
                    continue
                key = (str(code["code"]), str(code.get("libelle_officiel", "")))
                if code.get("systeme") == "CIM-10":
                    pathologies[key] += 1
                elif code.get("systeme") == "ATC":
                    traitements[key] += 1

    def top(counter: Counter, limit: int = 10) -> list[dict]:
        out = []
        for (code, libelle), count in counter.most_common(limit):
            masque = count < _STATS_MIN_COUNT
            out.append(
                {
                    "code": code,
                    "libelle": libelle,
                    "count": None if masque else count,
                    "masque": masque,
                }
            )
        return out

    # Événement d'audit sans PII (seulement le volume)
    store.append_audit(
        sess["user"]["username"], "stats_viewed", {"vsms": len(vsm_meta)}
    )

    return {
        "total": len(vsm_meta),
        "par_statut": dict(par_statut),
        "par_mois": dict(sorted(par_mois.items())),
        "pathologies": top(pathologies),
        "traitements": top(traitements),
        "completude": {
            k: round(sum(v) / len(v), 3) if v else 0.0 for k, v in completude.items()
        },
        "avertissement": _STATS_AVERTISSEMENT,
        "seuil": _STATS_MIN_COUNT,
    }


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

    # Journal applicatif structuré, local, sans PII (outputs/AUDIT_FASTAPI_LOGS.md)
    setup_logging(APP_DIR)
    _log.info("VSM-OCR démarré sur 127.0.0.1:8741 (100%% local)")

    # 127.0.0.1 STRICTEMENT — jamais 0.0.0.0 (cf. garde-fous projet)
    uvicorn.run(app, host="127.0.0.1", port=8741, log_level="warning")


if __name__ == "__main__":
    main()
