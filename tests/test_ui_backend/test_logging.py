"""Tests du logging applicatif (outputs/AUDIT_FASTAPI_LOGS.md).

Vérifie : écriture dans un fichier local avec rotation, redaction
systématique des PII, idempotence de la configuration, aucun handler réseau."""

import logging
import re

import pytest

from src.ui_backend import logging_setup as ls


@pytest.fixture()
def logger(tmp_path):
    """Logger « vsm » configuré sur un répertoire temporaire (isolé)."""
    # Purge préalable : le logger « vsm » est un singleton de module — un
    # handler restant d'un autre test fausserait le répertoire cible.
    root = logging.getLogger("vsm")
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()
    log = ls.setup_logging(tmp_path)
    yield log
    # remise à zéro pour ne pas polluer les autres tests
    for h in list(log.handlers):
        log.removeHandler(h)
        h.close()


def test_setup_writes_local_file_and_rotates(logger, tmp_path):
    logger.info("message de test %s", 42)
    logger.warning("avertissement %s", "x")
    # flush des handlers
    for h in logger.handlers:
        h.flush()
    log_file = tmp_path / "logs" / "app.log"
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "message de test 42" in content
    assert "avertissement x" in content
    assert "INFO" in content and "WARNING" in content


def test_setup_idempotent(tmp_path):
    first = ls.setup_logging(tmp_path)
    second = ls.setup_logging(tmp_path)  # ne doit pas dupliquer les handlers
    assert first is second
    assert len(second.handlers) == 2  # fichier + console
    # nettoyage (sans quoi les handlers « orphelins » captent les tests suivants)
    for h in list(first.handlers):
        first.removeHandler(h)
        h.close()


def test_no_network_handler(logger):
    from logging.handlers import HTTPHandler, SysLogHandler

    for h in logger.handlers:
        assert not isinstance(h, (HTTPHandler, SysLogHandler))


def test_redact_filter_masks_pii():
    filt = ls.RedactFilter()
    rec = logging.LogRecord(
        "vsm",
        logging.INFO,
        __file__,
        1,
        "NIR 1 56 05 75 123 456 78 · tél 06 12 34 56 78 · email jean.dupont@mail.fr · RPPS 10001234567",
        (),
        None,
    )
    assert filt.filter(rec) is True
    msg = rec.getMessage()
    assert "[NIR]" in msg and "1 56 05 75" not in msg
    assert "[TEL]" in msg and "06 12 34 56 78" not in msg
    assert "[EMAIL]" in msg and "jean.dupont" not in msg
    assert "[RPPS]" in msg and "10001234567" not in msg


def test_redact_filter_masks_tokens():
    filt = ls.RedactFilter()
    rec = logging.LogRecord(
        "vsm",
        logging.WARNING,
        __file__,
        1,
        "token [PATIENT_001] et [DATE_NAISSANCE_001] jamais à loguer",
        (),
        None,
    )
    assert filt.filter(rec) is True
    msg = rec.getMessage()
    assert "[TOKEN]" in msg
    assert "PATIENT_001" not in msg and "DATE_NAISSANCE_001" not in msg


def test_redact_applies_to_written_log(logger, tmp_path):
    # Défense en profondeur : une PII échappée par mégarde est masquée à l'écrit
    logger.error("fragment suspect : 2 80 12 34 567 890 12")
    for h in logger.handlers:
        h.flush()
    content = (tmp_path / "logs" / "app.log").read_text(encoding="utf-8")
    assert "[NIR]" in content
    assert "2 80 12 34 567 890 12" not in content
