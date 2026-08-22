"""Gestion des clés : dérivation Argon2id du mot de passe maître, clé de
session en mémoire uniquement, effacement explicite à la fermeture /
inactivité (timeout configurable, défaut 15 min). Jamais persistée en clair."""

from __future__ import annotations

import ctypes
import secrets
import time

from argon2.low_level import Type, hash_secret_raw

KDF = {"time_cost": 3, "memory_cost": 65536, "parallelism": 2, "hash_len": 32}
DEFAULT_TIMEOUT_SEC = 15 * 60


def derive_master_key(passphrase: str, salt: bytes) -> bytes:
    return hash_secret_raw(
        secret=passphrase.encode("utf-8"), salt=salt, type=Type.ID, **KDF
    )


def _zero_bytes(buf: bytearray) -> None:
    """Écrasement mémoire best-effort (CPython ne garantit pas l'absence de
    copies intermédiaires — limite documentée dans docs/SECURITY.md)."""
    addr = (ctypes.c_char * len(buf)).from_buffer(buf)
    ctypes.memset(ctypes.addressof(addr), 0, len(buf))


class SessionKey:
    """Clé maître en mémoire pour la durée d'une session utilisateur."""

    def __init__(self, key: bytes, timeout_sec: int = DEFAULT_TIMEOUT_SEC):
        self._key = bytearray(key)
        self._timeout = timeout_sec
        self._last_used = time.monotonic()
        self._closed = False

    @classmethod
    def from_passphrase(
        cls, passphrase: str, salt: bytes, timeout_sec: int = DEFAULT_TIMEOUT_SEC
    ):
        return cls(derive_master_key(passphrase, salt), timeout_sec)

    def get(self) -> bytes:
        if self._closed:
            raise RuntimeError("Session fermée : clé effacée")
        if time.monotonic() - self._last_used > self._timeout:
            self.close()
            raise TimeoutError("Session expirée par inactivité : clé effacée")
        self._last_used = time.monotonic()
        return bytes(self._key)

    def touch(self) -> None:
        self._last_used = time.monotonic()

    def close(self) -> None:
        if not self._closed:
            _zero_bytes(self._key)
            self._closed = True

    def __del__(self):  # filet de sécurité
        try:
            self.close()
        except Exception:
            pass


def new_salt() -> bytes:
    return secrets.token_bytes(16)
