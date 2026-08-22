"""Stockage local chiffré : SQLite + chiffrement champ par champ AES-256-GCM
(cryptography). Toutes les données patient (documents, JSON OCR/NLP, VSM)
sont chiffrées avec la clé de session ; sans clé, la base est illisible.

L'audit log est en clair (pas de PII, par construction — cf. anonymization/
audit.py) et chaîné par hash : chaque entrée inclut le SHA-256 de la
précédente, rendant toute modification rétroactive détectable.

Droit à l'oubli : delete_dossier() supprime documents, résultats et VSM du
dossier en une transaction (le mapping PII est supprimé séparément via
MappingVault.forget())."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .key_manager import SessionKey


class _LockedConn:
    """Proxy fin autour de sqlite3.Connection : sérialise tous les appels
    derrière un RLock (l'app est mono-processus ; TestClient et uvicorn
    peuvent appeler depuis plusieurs threads)."""

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock):
        self._conn = conn
        self._lock = lock

    def execute(self, *a, **kw):
        with self._lock:
            return self._conn.execute(*a, **kw)

    def executescript(self, *a, **kw):
        with self._lock:
            return self._conn.executescript(*a, **kw)

    def commit(self):
        with self._lock:
            return self._conn.commit()

    def close(self):
        with self._lock:
            return self._conn.close()

    def __getattr__(self, item):
        return getattr(self._conn, item)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents(
    id TEXT PRIMARY KEY, dossier_id TEXT NOT NULL, filename TEXT NOT NULL,
    sha256 TEXT NOT NULL, blob_nonce BLOB, blob_ct BLOB, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS ocr_results(
    document_id TEXT PRIMARY KEY, nonce BLOB, ciphertext BLOB, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS nlp_results(
    document_id TEXT PRIMARY KEY, nonce BLOB, ciphertext BLOB, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS vsm_documents(
    id TEXT PRIMARY KEY, dossier_id TEXT NOT NULL, statut TEXT NOT NULL,
    nonce BLOB, ciphertext BLOB, created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS audit_log(
    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL,
    actor TEXT NOT NULL, event TEXT NOT NULL, payload TEXT NOT NULL,
    prev_hash TEXT NOT NULL, entry_hash TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_doc_dossier ON documents(dossier_id);
CREATE INDEX IF NOT EXISTS idx_vsm_dossier ON vsm_documents(dossier_id);
"""


class EncryptedStore:
    def __init__(self, db_path: str | Path, session_key: SessionKey):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False : l'application est mono-processus, les accès
        # concurrents (TestClient / uvicorn threadé) sont sérialisés par le
        # verrou du proxy _LockedConn.
        raw = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn = _LockedConn(raw, threading.RLock())
        self.conn.executescript(_SCHEMA)
        self._key = session_key

    # ----------------------------------------------------------- crypto
    def _encrypt(self, data: bytes, aad: str) -> tuple[bytes, bytes]:
        nonce = secrets.token_bytes(12)
        return nonce, AESGCM(self._key.get()).encrypt(nonce, data, aad.encode())

    def _decrypt(self, nonce: bytes, ct: bytes, aad: str) -> bytes:
        return AESGCM(self._key.get()).decrypt(nonce, ct, aad.encode())

    # -------------------------------------------------------- documents
    def store_document(
        self,
        document_id: str,
        dossier_id: str,
        filename: str,
        sha256: str,
        content: bytes | None = None,
    ) -> None:
        nonce = ct = None
        if content is not None:
            nonce, ct = self._encrypt(content, f"doc:{document_id}")
        self.conn.execute(
            "INSERT OR REPLACE INTO documents VALUES(?,?,?,?,?,?,?)",
            (document_id, dossier_id, filename, sha256, nonce, ct, time.time()),
        )
        self.conn.commit()

    def load_document(self, document_id: str) -> bytes:
        row = self.conn.execute(
            "SELECT blob_nonce, blob_ct FROM documents WHERE id=?", (document_id,)
        ).fetchone()
        if row is None or row[0] is None:
            raise KeyError(document_id)
        return self._decrypt(row[0], row[1], f"doc:{document_id}")

    # ------------------------------------------------------ JSON chiffrés
    def _store_json(
        self,
        table: str,
        key_col: str,
        key: str,
        payload: dict,
        extra: dict | None = None,
    ):
        data = json.dumps(payload, ensure_ascii=False).encode()
        nonce, ct = self._encrypt(data, f"{table}:{key}")
        if table == "vsm_documents":
            self.conn.execute(
                "INSERT OR REPLACE INTO vsm_documents VALUES(?,?,?,?,?,?,?)",
                (
                    key,
                    extra["dossier_id"],
                    extra.get("statut", "brouillon"),
                    nonce,
                    ct,
                    time.time(),
                    time.time(),
                ),
            )
        else:
            self.conn.execute(
                f"INSERT OR REPLACE INTO {table} VALUES(?,?,?,?)",
                (key, nonce, ct, time.time()),
            )
        self.conn.commit()

    def _load_json(self, table: str, key_col: str, key: str) -> dict:
        row = self.conn.execute(
            f"SELECT nonce, ciphertext FROM {table} WHERE {key_col}=?", (key,)
        ).fetchone()
        if row is None:
            raise KeyError(key)
        return json.loads(self._decrypt(row[0], row[1], f"{table}:{key}"))

    def store_ocr_result(self, document_id: str, payload: dict):
        self._store_json("ocr_results", "document_id", document_id, payload)

    def load_ocr_result(self, document_id: str) -> dict:
        return self._load_json("ocr_results", "document_id", document_id)

    def store_nlp_result(self, document_id: str, payload: dict):
        self._store_json("nlp_results", "document_id", document_id, payload)

    def load_nlp_result(self, document_id: str) -> dict:
        return self._load_json("nlp_results", "document_id", document_id)

    def store_vsm(self, vsm_id: str, dossier_id: str, vsm: dict):
        self._store_json(
            "vsm_documents",
            "id",
            vsm_id,
            vsm,
            {"dossier_id": dossier_id, "statut": vsm.get("statut", "brouillon")},
        )

    def load_vsm(self, vsm_id: str) -> dict:
        return self._load_json("vsm_documents", "id", vsm_id)

    def list_documents(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, dossier_id, filename, sha256, created_at FROM documents ORDER BY created_at DESC"
        )
        return [
            dict(zip(("id", "dossier_id", "filename", "sha256", "created_at"), r))
            for r in rows
        ]

    def list_vsm(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, dossier_id, statut, created_at, updated_at FROM vsm_documents ORDER BY updated_at DESC"
        )
        return [
            dict(zip(("id", "dossier_id", "statut", "created_at", "updated_at"), r))
            for r in rows
        ]

    # ------------------------------------------------------ droit à l'oubli
    def delete_dossier(self, dossier_id: str) -> int:
        doc_ids = [
            r[0]
            for r in self.conn.execute(
                "SELECT id FROM documents WHERE dossier_id=?", (dossier_id,)
            )
        ]
        cur = self.conn.cursor()
        for did in doc_ids:
            cur.execute("DELETE FROM ocr_results WHERE document_id=?", (did,))
            cur.execute("DELETE FROM nlp_results WHERE document_id=?", (did,))
        cur.execute("DELETE FROM documents WHERE dossier_id=?", (dossier_id,))
        cur.execute("DELETE FROM vsm_documents WHERE dossier_id=?", (dossier_id,))
        self.conn.commit()
        return len(doc_ids)

    # ------------------------------------------------------------- audit
    def append_audit(self, actor: str, event: str, payload: dict) -> str:
        prev = self.conn.execute(
            "SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        prev_hash = prev[0] if prev else "GENESIS"
        ts = time.time()
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        entry_hash = hashlib.sha256(
            f"{prev_hash}|{ts}|{actor}|{event}|{body}".encode()
        ).hexdigest()
        self.conn.execute(
            "INSERT INTO audit_log(timestamp, actor, event, payload, prev_hash, entry_hash) VALUES(?,?,?,?,?,?)",
            (ts, actor, event, body, prev_hash, entry_hash),
        )
        self.conn.commit()
        return entry_hash

    def verify_audit_chain(self) -> bool:
        prev_hash = "GENESIS"
        for ts, actor, event, body, stored_prev, stored_hash in self.conn.execute(
            "SELECT timestamp, actor, event, payload, prev_hash, entry_hash FROM audit_log ORDER BY id"
        ):
            if stored_prev != prev_hash:
                return False
            expected = hashlib.sha256(
                f"{prev_hash}|{ts}|{actor}|{event}|{body}".encode()
            ).hexdigest()
            if expected != stored_hash:
                return False
            prev_hash = stored_hash
        return True

    def read_audit(self, limit: int = 200) -> list[dict]:
        rows = self.conn.execute(
            "SELECT timestamp, actor, event, payload, entry_hash FROM audit_log ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [
            {
                "timestamp": r[0],
                "actor": r[1],
                "event": r[2],
                "payload": json.loads(r[3]),
                "entry_hash": r[4],
            }
            for r in rows
        ]

    def close(self):
        self.conn.close()
