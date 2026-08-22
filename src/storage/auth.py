"""Comptes utilisateurs locaux : rôles (medecin, secretaire, admin),
mots de passe hachés Argon2id, verrouillage après N tentatives."""

from __future__ import annotations

import sqlite3
import time

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

ROLES = ("medecin", "secretaire", "admin")
_ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)


class AuthManager:
    def __init__(
        self, conn: sqlite3.Connection, max_attempts: int = 5, lock_minutes: int = 15
    ):
        self.conn = conn
        self.max_attempts = max_attempts
        self.lock_seconds = lock_minutes * 60
        conn.execute("""CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL, role TEXT NOT NULL,
            failed_attempts INTEGER DEFAULT 0, locked_until REAL DEFAULT 0,
            created_at REAL NOT NULL)""")
        conn.commit()

    def create_user(self, username: str, password: str, role: str) -> None:
        if role not in ROLES:
            raise ValueError(f"Rôle inconnu : {role}")
        if len(password) < 12:
            raise ValueError("Mot de passe trop court (12 caractères minimum)")
        self.conn.execute(
            "INSERT INTO users(username, password_hash, role, created_at) VALUES(?,?,?,?)",
            (username, _ph.hash(password), role, time.time()),
        )
        self.conn.commit()

    def verify(self, username: str, password: str) -> dict | None:
        row = self.conn.execute(
            "SELECT id, password_hash, role, failed_attempts, locked_until FROM users WHERE username=?",
            (username,),
        ).fetchone()
        if row is None:
            return None
        uid, pw_hash, role, attempts, locked_until = row
        if locked_until > time.time():
            raise PermissionError(
                "Compte verrouillé : trop de tentatives. Réessayez plus tard."
            )
        try:
            _ph.verify(pw_hash, password)
        except VerifyMismatchError:
            attempts += 1
            locked = (
                time.time() + self.lock_seconds if attempts >= self.max_attempts else 0
            )
            self.conn.execute(
                "UPDATE users SET failed_attempts=?, locked_until=? WHERE id=?",
                (attempts, locked, uid),
            )
            self.conn.commit()
            return None
        self.conn.execute(
            "UPDATE users SET failed_attempts=0, locked_until=0 WHERE id=?", (uid,)
        )
        self.conn.commit()
        return {"id": uid, "username": username, "role": role}
