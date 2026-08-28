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
import sys
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
from src.extraction_nlp.entity_extractor import moteur_nlp_par_defaut
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
    # Moteur NLP : « drbert » PAR DÉFAUT (encodeur DrBERT-CASM2 local —
    # décision étape 0 : rapide, borné, sans invention, poste 4 cœurs/8 Go).
    # Sélection par VSM_NLP_ENGINE (drbert | llm | regles) et par requête ;
    # « llm » reste disponible sans être imposé, « rules » est le repli
    # automatique (tracé dans provenance.nlp). Strictement local (art. 9).
    nlp_engine: str = Field(
        default=moteur_nlp_par_defaut(), pattern="^(drbert|llm|rules|regles)$"
    )


class ValidateIn(BaseModel):
    sections: dict | None = None
    statut: str = Field(default="valide", pattern="^(a_valider|valide|signe)$")
    signe_par: str | None = Field(default=None, max_length=128)


# ----------------------------------------------------------------- deps
def current_session(
    request: Request,
    response: Response,
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
    # Session GLISSANTE : le cookie est rafraîchi à chaque requête. Sans cela,
    # max_age=15 min est figé à la connexion → le navigateur supprime le cookie
    # après 15 min même en cas d'activité continue (traitements longs > 15 min
    # = déconnexion forcée à l'arrivée).
    response.set_cookie(
        "vsm_session",
        vsm_session or "",
        httponly=True,
        samesite="strict",
        max_age=SESSION_TTL,
    )
    return sess


# ----------------------------------------------------------------- auth
@app.get("/health")
def health():
    from src.extraction_nlp.drbert_extractor import (
        dossier_modele,
        execution_possible,
    )
    from src.extraction_nlp.llm import (
        llm_attemptable,
        llm_ram_warning,
        llm_unavailability_reason,
    )

    drbert_ok, drbert_raison = execution_possible()
    return {
        "status": "ok",
        "max_upload_mb": MAX_UPLOAD_MB,
        # Moteurs OCR réellement disponibles sur ce poste — « unlimited »
        # n'apparaît QUE si une carte NVIDIA est détectée (docs/ADR/0005).
        "available_engines": sorted(ENGINES),
        # DrBERT (moteur NLP PAR DÉFAUT, encodeur local — décision étape 0) :
        # RÉELLEMENT exécutable = fichiers du modèle + torch/transformers
        # importables DANS CET INTERPRÉTEUR. Un modèle présent dans un
        # interpréteur sans torch ne tourne jamais (« python » ≠ « py -3.12 »
        # sous Windows) — la raison l'explique, l'UI l'affiche AVANT tout
        # traitement. Absent → repli règles TRACÉ (provenance.nlp).
        "drbert_available": drbert_ok,
        "drbert_path": str(dossier_modele()),
        "drbert_reason": drbert_raison,
        # LLM génératif : OPTIONNEL désormais (plus dans le flux par défaut) —
        # « available » si le GGUF est présent ET llama-cpp-python importable
        # (repli règles sinon). llm_reason = RAM juste ou indisponibilité.
        "llm_available": llm_attemptable(),
        "llm_reason": llm_ram_warning() or llm_unavailability_reason(),
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
    # Garde-fou anti double-traitement : deux jobs simultanés sur le même
    # document se disputent le modèle (les logs ont montré deux traitements
    # concurrents sur le même fichier 48,9 Mo → 1h30 de génération gâchée).
    for jb in _jobs.values():
        if jb.get("document_id") == document_id and jb.get("status") == "processing":
            raise HTTPException(
                409,
                f"Un traitement est déjà en cours pour ce document ({jb['job_id']}) — "
                "attendez sa fin avant de relancer.",
            )
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
        # Maintient la session et la clé de chiffrement vivantes pendant les
        # traitements longs (OCR + LLM sur gros documents) : sans cela, la clé
        # expire au bout de 15 min et l'écriture finale échoue (job en erreur),
        # et l'utilisateur est déconnecté à son retour.
        try:
            sess["key"].touch()
        except Exception:  # noqa: BLE001 - clé déjà fermée : rien à faire
            pass
        sess["last"] = time.monotonic()

    def step_page(idx: int) -> None:
        step(f"OCR (lecture du document) — page {idx}")

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
                on_page=step_page,
            )
        finally:
            os.unlink(tmp_path)

        mapping = ocr_json.pop("_pii_mapping", None)
        passphrase = os.environ.get("VSM_VAULT_PASSPHRASE")
        if mapping and passphrase:
            MappingVault(APP_DIR / "vault.bin", passphrase).store_mapping(
                document_id, mapping
            )

        # Phase NLP par document : ENCODEUR DrBERT-CASM2 par défaut (décision
        # étape 0 — étiquetage par offsets, pas de génération) ; « llm » reste
        # disponible sur demande explicite. RÈGLE DE DÉRIVATION : si le LLM
        # est demandé mais indisponible (GGUF retiré du paquet, frontend
        # ancien qui l'envoie encore en dur), la demande dérive vers DrBERT —
        # le moteur de l'application — et NON vers les règles : l'encodeur
        # doit tourner quel que soit le bundle servi. Le repli règles
        # éventuel (modèle absent) reste tracé dans provenance.nlp.
        from src.extraction_nlp.drbert_extractor import execution_possible
        from src.extraction_nlp.llm import llm_attemptable

        moteur = body.nlp_engine.strip().lower()
        if moteur in ("regles", "rules"):
            moteur = "rules"
        # Ne dérive vers DrBERT que s'il peut VRAIMENT tourner (fichiers +
        # torch) — sinon on laisse le pipeline tracer le repli règles.
        if moteur == "llm" and not llm_attemptable() and execution_possible()[0]:
            _log.info("LLM demandé mais indisponible — extraction DrBERT utilisée")
            moteur = "drbert"
        use_llm = moteur == "llm" and llm_attemptable()

        def _prog(done: int, total: int) -> None:
            step(f"Phase LLM locale : segment {done}/{total}")

        if use_llm:
            step("Phase LLM locale : correction OCR + extraction")
        elif moteur == "drbert":
            step("Extraction NLP (DrBERT — encodeur local)")
        else:
            step("Extraction NLP (moteur de règles)")
        nlp_json = nlp_pipeline(
            ocr_json, nlp_engine=moteur, progress=_prog if use_llm else None
        )
        from src.vsm_generation.vsm_builder import build_vsm

        step("Assemblage du VSM")
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
                # Moteur EFFECTIVEMENT invoqué (après dérivation llm→drbert) ;
                # le moteur réellement utilisé reste tracé par provenance.nlp.
                "nlp_engine": moteur,
                "nlp_engine_demande": body.nlp_engine,
                "pii_count": ocr_json["pii_detected_count"],
            },
        )

        job["vsm_id"] = vsm_id
        job["result"] = {
            "vsm_id": vsm_id,
            "vsm": vsm,
            "processing_report": ocr_json["processing_report"],
            "pii_detected_count": ocr_json["pii_detected_count"],
            # Rapport de la phase NLP/LLM (moteur réel, statut, raison du
            # repli éventuel, corrections OCR, durées) — affiché à l'UI.
            "nlp_report": nlp_json.get("provenance", {}).get("nlp", {}),
        }
        job["status"] = "done"
        step("terminé")
        # Moteur NLP RÉELLEMENT utilisé (repli possible) + durée totale
        moteur_effectif = vsm.get("provenance", {}).get("moteur_nlp", moteur)
        if moteur_effectif != moteur:
            _log.info(
                "document traité id=%s vsm=%s ocr=%s nlp=%s (repli sur %s) "
                "pii=%d durée=%.1fs",
                document_id,
                vsm_id,
                body.engine,
                moteur,
                moteur_effectif,
                ocr_json["pii_detected_count"],
                time.time() - job["created"],
            )
        else:
            _log.info(
                "document traité id=%s vsm=%s ocr=%s nlp=%s pii=%d durée=%.1fs",
                document_id,
                vsm_id,
                body.engine,
                moteur,
                ocr_json["pii_detected_count"],
                time.time() - job["created"],
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


@app.post("/vsm/{vsm_id}/llm-assist")
def llm_assist(vsm_id: str, sess: dict = Depends(current_session)):
    """Relance la phase LLM locale (correction OCR + extraction) sur le texte
    OCR stocké et améliore les champs encore « À valider ».

    Le médecin garde la main : seuls les champs douteux sont mis à jour, le
    VSM reste au statut « à valider » et doit être relu puis enregistré.
    Retourne le VSM mis à jour + le rapport de la phase LLM."""
    from rapidfuzz import fuzz

    from src.extraction_nlp.entity_extractor import extract_entities_with_report
    from src.extraction_nlp.llm import llm_attemptable

    if not llm_attemptable():
        raise HTTPException(
            409,
            "Modèle LLM local absent — téléchargez-le : "
            "python -m src.extraction_nlp.llm",
        )
    store: EncryptedStore = sess["store"]
    try:
        vsm = store.load_vsm(vsm_id)
        ocr = store.load_ocr_result(vsm.get("document_id", ""))
    except KeyError:
        raise HTTPException(404, "VSM ou texte OCR introuvable") from None

    ents, report = extract_entities_with_report(ocr.get("text", ""), engine="llm")
    if report["statut"] not in ("llm_complet", "llm_extraction_seule"):
        raise HTTPException(
            409,
            "Phase LLM indisponible pour l'instant : "
            f"{report.get('raison') or report['statut']}",
        )

    # N'améliore QUE les champs marqués « À valider » ; chaque candidat LLM
    # n'est utilisé qu'une fois et seulement s'il correspond au champ actuel.
    updated = 0
    used: set[int] = set()
    for section, items in vsm.get("sections", {}).items():
        if not isinstance(items, list):
            continue
        cands = [i for i, e in enumerate(ents) if e.section == section]
        for it in items:
            if not isinstance(it, dict) or not it.get("a_valider"):
                continue
            best_i, best_score = None, 0.0
            for i in cands:
                if i in used:
                    continue
                score = fuzz.token_set_ratio(str(it.get("valeur", "")), ents[i].valeur)
                if score > best_score:
                    best_i, best_score = i, score
            if best_i is None or best_score < 60:
                continue
            e = ents[best_i]
            used.add(best_i)
            it["valeur"] = e.valeur
            it["confiance"] = e.confiance
            it["a_valider"] = e.confiance < 0.7
            it["moteur_nlp"] = e.moteur_nlp
            it["correction_ocr"] = e.correction_ocr
            it["source"] = {
                **it.get("source", {}),
                "passage": e.passage,
                "offset_debut": e.offset_debut,
                "offset_fin": e.offset_fin,
            }
            updated += 1

    prov = vsm.setdefault("provenance", {})
    prov["moteur_nlp"] = report["moteur"]
    prov["nlp"] = {**prov.get("nlp", {}), **report, "assist_llm": True}
    store.store_vsm(vsm_id, vsm.get("document_id", vsm_id), vsm)
    store.append_audit(
        sess["user"]["username"],
        "llm_assist",
        {
            "vsm_id": vsm_id,
            "champs_mis_a_jour": updated,
            "statut_nlp": report["statut"],
        },
    )
    _log.info("vsm %s assisté par le LLM : %d champ(s) mis à jour", vsm_id, updated)
    return {"vsm": vsm, "champs_mis_a_jour": updated, "nlp_report": report}


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

    # DIAGNOSTIC AU DÉMARRAGE : si DrBERT (moteur par défaut) ne peut PAS
    # tourner dans cet interpréteur, il faut le savoir MAINTENANT — pas après
    # 3 minutes d'OCR au moment du repli règles. Cas typique sous Windows :
    # « python » pointe vers un interpréteur sans torch (ex. 3.14) alors que
    # l'environnement ML documenté est py -3.12. Le message donne
    # l'interpréteur fautif ET la commande correcte.
    if moteur_nlp_par_defaut() == "drbert":
        from src.extraction_nlp.drbert_extractor import execution_possible

        _ok, _raison = execution_possible()
        if not _ok:
            _log.warning(
                "DrBERT INUTILISABLE : %s | interpréteur = %s (Python %s) | "
                "Le moteur de règles sera utilisé en repli. Relancer avec : "
                "py -3.12 -m src.ui_backend.main",
                _raison,
                sys.executable,
                sys.version.split()[0],
            )

    # Préchargement du modèle LLM local en arrière-plan : le premier document
    # ne paie pas le coût de chargement du GGUF (minutes sur PC lent).
    # preload_llm() ne lève jamais : il démarre un thread best-effort.
    from src.extraction_nlp.llm import preload_llm

    preload_llm()

    # 127.0.0.1 STRICTEMENT — jamais 0.0.0.0 (cf. garde-fous projet)
    uvicorn.run(app, host="127.0.0.1", port=8741, log_level="warning")


if __name__ == "__main__":
    main()
