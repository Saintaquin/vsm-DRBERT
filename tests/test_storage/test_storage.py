import sqlite3

import pytest

from src.storage.auth import AuthManager
from src.storage.encrypted_store import EncryptedStore
from src.storage.key_manager import SessionKey, derive_master_key


def _store(tmp_path, passphrase="une-passphrase-tres-forte"):
    key = SessionKey(derive_master_key(passphrase, b"0123456789abcdef"))
    return EncryptedStore(tmp_path / "vsm.db", key), key


def test_data_unreadable_without_key(tmp_path):
    store, _ = _store(tmp_path)
    store.store_ocr_result("doc1", {"text": "Patient Jean DUPONT secret"})
    raw = (tmp_path / "vsm.db").read_bytes()
    assert b"Jean DUPONT" not in raw and b"secret" not in raw


def test_wrong_key_fails(tmp_path):
    store, key = _store(tmp_path)
    store.store_ocr_result("doc1", {"text": "x"})
    store.close()
    key.close()
    store2, _ = _store(tmp_path, passphrase="autre-passphrase-differente")
    with pytest.raises(Exception):
        store2.load_ocr_result("doc1")


def test_roundtrip_and_delete_dossier(tmp_path):
    store, _ = _store(tmp_path)
    store.store_document("d1", "dossierA", "scan.png", "ab" * 32, b"binary")
    store.store_ocr_result("d1", {"text": "t"})
    store.store_vsm("v1", "dossierA", {"statut": "brouillon", "sections": {}})
    assert store.load_document("d1") == b"binary"
    assert store.delete_dossier("dossierA") == 1
    with pytest.raises(KeyError):
        store.load_ocr_result("d1")
    assert store.list_vsm() == []


def test_audit_chain_tamper_detection(tmp_path):
    store, _ = _store(tmp_path)
    store.append_audit("alice", "login", {})
    store.append_audit("alice", "upload", {"document_id": "d1"})
    assert store.verify_audit_chain() is True
    store.conn.execute("UPDATE audit_log SET payload='{\"forged\":1}' WHERE id=1")
    store.conn.commit()
    assert store.verify_audit_chain() is False


def test_session_key_zeroized(tmp_path):
    key = SessionKey(b"k" * 32, timeout_sec=600)
    key.close()
    with pytest.raises(RuntimeError):
        key.get()


def test_auth_roles_and_lockout(tmp_path):
    conn = sqlite3.connect(tmp_path / "users.db")
    auth = AuthManager(conn, max_attempts=2)
    auth.create_user("dr.house", "mot-de-passe-fort!", "medecin")
    with pytest.raises(ValueError):
        auth.create_user("x", "court", "medecin")
    with pytest.raises(ValueError):
        auth.create_user("y", "mot-de-passe-fort!", "hacker")
    assert auth.verify("dr.house", "mot-de-passe-fort!")["role"] == "medecin"
    assert auth.verify("dr.house", "faux") is None
    assert auth.verify("dr.house", "faux") is None
    with pytest.raises(PermissionError):
        auth.verify("dr.house", "mot-de-passe-fort!")
