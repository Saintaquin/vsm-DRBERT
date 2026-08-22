"""Coffre-fort des mappings PII↔token, chiffré au repos (AES-256-GCM).

La clé maître est dérivée d'un secret fourni à l'exécution (variable
d'environnement VSM_VAULT_PASSPHRASE ou saisie interactive) via Argon2id.
Le secret n'est jamais persisté. Supprimer une entrée du coffre = perte
définitive d'accès aux PII du dossier concerné (droit à l'oubli).

Format fichier : en-tête JSON clair {salt, kdf params} + blobs chiffrés
par dossier (nonce 96 bits unique par écriture)."""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

from argon2.low_level import Type, hash_secret_raw
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_KDF_PARAMS = {"time_cost": 3, "memory_cost": 65536, "parallelism": 2, "hash_len": 32}


def derive_key(passphrase: str, salt: bytes) -> bytes:
    return hash_secret_raw(
        secret=passphrase.encode("utf-8"),
        salt=salt,
        time_cost=_KDF_PARAMS["time_cost"],
        memory_cost=_KDF_PARAMS["memory_cost"],
        parallelism=_KDF_PARAMS["parallelism"],
        hash_len=_KDF_PARAMS["hash_len"],
        type=Type.ID,
    )


class MappingVault:
    """Un fichier-coffre par installation ; une entrée par dossier patient."""

    def __init__(self, path: str | Path, passphrase: str | None = None):
        self.path = Path(path)
        passphrase = passphrase or os.environ.get("VSM_VAULT_PASSPHRASE")
        if not passphrase:
            raise ValueError(
                "Aucune passphrase fournie (argument ou VSM_VAULT_PASSPHRASE). "
                "La clé maître doit rester hors application."
            )
        self._store = self._load_or_init()
        self._key = derive_key(passphrase, bytes.fromhex(self._store["salt"]))

    # ------------------------------------------------------------------
    def _load_or_init(self) -> dict:
        if self.path.exists():
            return json.loads(self.path.read_text(encoding="utf-8"))
        store = {"version": 1, "salt": secrets.token_bytes(16).hex(), "entries": {}}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(store), encoding="utf-8")
        return store

    def _persist(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._store), encoding="utf-8")
        tmp.replace(self.path)

    # ------------------------------------------------------------------
    def store_mapping(self, dossier_id: str, mapping: dict[str, str]) -> None:
        # Fusion avec un mapping existant (plusieurs documents par dossier)
        existing = {}
        if dossier_id in self._store["entries"]:
            existing = self.load_mapping(dossier_id)
        existing.update(mapping)
        nonce = secrets.token_bytes(12)
        ct = AESGCM(self._key).encrypt(
            nonce,
            json.dumps(existing, ensure_ascii=False).encode("utf-8"),
            dossier_id.encode(),
        )
        self._store["entries"][dossier_id] = {
            "nonce": nonce.hex(),
            "ciphertext": ct.hex(),
        }
        self._persist()

    def load_mapping(self, dossier_id: str) -> dict[str, str]:
        entry = self._store["entries"].get(dossier_id)
        if entry is None:
            raise KeyError(f"Aucun mapping pour le dossier {dossier_id}")
        pt = AESGCM(self._key).decrypt(
            bytes.fromhex(entry["nonce"]),
            bytes.fromhex(entry["ciphertext"]),
            dossier_id.encode(),
        )
        return json.loads(pt.decode("utf-8"))

    def forget(self, dossier_id: str) -> bool:
        """Droit à l'oubli : suppression irréversible du mapping."""
        removed = self._store["entries"].pop(dossier_id, None) is not None
        if removed:
            self._persist()
        return removed

    def list_dossiers(self) -> list[str]:
        return sorted(self._store["entries"])
