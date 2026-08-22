import json

import pytest

from src.anonymization.audit import build_audit_entries
from src.anonymization.mapping_vault import MappingVault
from src.anonymization.pii_detector import detect_pii


def test_vault_roundtrip(tmp_path):
    v = MappingVault(tmp_path / "vault.bin", passphrase="test-passphrase-forte")
    v.store_mapping("dossier1", {"[PATIENT_001]": "Jean DUPONT"})
    assert v.load_mapping("dossier1") == {"[PATIENT_001]": "Jean DUPONT"}


def test_vault_unreadable_without_key(tmp_path):
    path = tmp_path / "vault.bin"
    MappingVault(path, "bonne-passphrase-123").store_mapping(
        "d1", {"[T]": "secret-pii"}
    )
    raw = path.read_text()
    assert "secret-pii" not in raw
    bad = MappingVault(path, "mauvaise-passphrase")
    with pytest.raises(Exception):
        bad.load_mapping("d1")


def test_right_to_be_forgotten(tmp_path):
    v = MappingVault(tmp_path / "vault.bin", passphrase="test-passphrase-forte")
    v.store_mapping("d1", {"[T]": "x"})
    assert v.forget("d1") is True
    with pytest.raises(KeyError):
        v.load_mapping("d1")


def test_audit_contains_no_cleartext_pii():
    matches = detect_pii("Patient : Jean DUPONT, RPPS : 10001234567")
    entries = build_audit_entries("doc1", matches, "pseudonymize")
    blob = json.dumps(entries)
    assert "Jean DUPONT" not in blob and "10001234567" not in blob
    assert all(len(e["value_hash"]) == 16 for e in entries)
