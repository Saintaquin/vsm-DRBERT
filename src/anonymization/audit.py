"""Audit du module d'anonymisation.

Règle absolue : AUCUNE PII en clair dans les entrées d'audit. On consigne
le type, la position, la méthode, la confiance, et un hash SHA-256 tronqué
de la valeur (preuve de cohérence sans divulgation)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .pii_detector import PIIMatch


def _hash_value(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def build_audit_entries(
    document_id: str, matches: list[PIIMatch], action: str
) -> list[dict]:
    """action ∈ {'pseudonymize', 'anonymize', 'detect_only'}"""
    ts = datetime.now(timezone.utc).isoformat()
    return [
        {
            "timestamp": ts,
            "document_id": document_id,
            "event": "pii_detected",
            "pii_type": m.pii_type,
            "span": [m.start, m.end],
            "confidence": round(m.confidence, 3),
            "method": m.method,
            "value_hash": _hash_value(m.value),
            "action": action,
        }
        for m in matches
    ]


def append_audit_log(entries: list[dict], path: str | Path) -> None:
    """Journal append-only en JSONL (le stockage chiffré offre en plus
    une chaîne de hash anti-falsification, cf. src/storage)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
