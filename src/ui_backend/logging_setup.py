"""Logging applicatif structuré — 100 % local, sans PII (art. 9 du concours).

Audit de faisabilité : outputs/AUDIT_FASTAPI_LOGS.md.

- Fichiers : ``<VSM_DATA_DIR>/logs/app.log`` (rotation 1 Mo × 5).
- Niveau : ``VSM_LOG_LEVEL`` (INFO par défaut) ; console stderr en plus.
- **Redaction** : un filtre masque les NIR, téléphones, emails, RPPS/ADELI et
  tokens de pseudonymisation dans TOUTE entrée de log (défense en profondeur).
  Par conception, on ne journalise jamais de corps de requête ni de valeurs
  de champs (IDs et métadonnées uniquement).
- Jamais d'envoi externe : aucun handler réseau (aucun cloud — art. 9).
"""

from __future__ import annotations

import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Motifs à masquer dans les logs (redaction systématique)
_REDACT_RULES: list[tuple[re.Pattern, str]] = [
    # NIR / sécurité sociale (15 chiffres, avec ou sans espaces)
    (re.compile(r"\b[12]\s?\d{2}\s?\d{2}\s?\d{2}\s?\d{3}\s?\d{3}\s?\d{2}\b"), "[NIR]"),
    # Téléphones français
    (re.compile(r"\b0[1-9](?:[\s.\-]?\d{2}){4}\b"), "[TEL]"),
    # Emails
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), "[EMAIL]"),
    # RPPS (11 chiffres commençant par 10), ADELI/FINESS (9 chiffres)
    (re.compile(r"\b10\d{9}\b"), "[RPPS]"),
    (re.compile(r"\b\d{9}\b"), "[NUM9]"),
    # Tokens de pseudonymisation (jamais à loguer, mais masqués par sûreté)
    (
        re.compile(
            r"\[(?:PATIENT|NIR|INS|RPPS|ADELI|TEL|EMAIL|ADRESSE|DATE_NAISSANCE|DATE_DECES|DOSSIER|SEJOUR)_\d{3}\]"
        ),
        "[TOKEN]",
    ),
]

_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


class RedactFilter(logging.Filter):
    """Masque les PII de chaque entrée de log avant écriture (défense en
    profondeur : la redaction complète la règle « pas de corps de requête »)."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for rx, repl in _REDACT_RULES:
            msg = rx.sub(repl, msg)
        record.msg = msg
        record.args = ()
        return True


def setup_logging(
    log_dir: str | Path | None = None, level: str | None = None
) -> logging.Logger:
    """Configure le logger « vsm » (idempotent). À appeler au démarrage."""
    logger = logging.getLogger("vsm")
    if logger.handlers:  # déjà configuré (tests, rechargements) — ne pas dupliquer
        return logger

    log_dir = (
        Path(log_dir)
        if log_dir
        else Path(os.environ.get("VSM_DATA_DIR", Path.home() / ".vsm-ocr"))
    )
    log_dir = log_dir / "logs"  # <base>/logs/app.log
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.setLevel((level or os.environ.get("VSM_LOG_LEVEL", "INFO")).upper())

    formatter = logging.Formatter(_FORMAT)
    file_handler = RotatingFileHandler(
        log_dir / "app.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(RedactFilter())
    logger.addHandler(file_handler)

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    console.addFilter(RedactFilter())
    logger.addHandler(console)

    logger.propagate = False
    logger.info("Journal applicatif initialisé (%s)", log_dir / "app.log")
    return logger


def get_logger(name: str = "vsm") -> logging.Logger:
    """Logger applicatif (sans handler si non initialisé → no-op)."""
    return logging.getLogger(name)
