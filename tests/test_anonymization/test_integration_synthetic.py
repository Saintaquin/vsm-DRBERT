"""Intégration : les identifiants connus des 5 cas synthétiques sont détectés."""

from pathlib import Path

import pytest

from src.anonymization.pii_detector import detect_pii

SYNTH = Path(__file__).resolve().parents[2] / "data" / "synthetic"
EXPECTED = {
    "cas_001": ["DUPONT", "10001234567", "14/05/1956"],
    "cas_002": ["MARTIN", "06 12 34 56 78", "rue des Lilas"],
    "cas_003": ["BERNARD", "10009876543", "CARD-2024-0892"],
    "cas_004": ["BENALI", "fatima.exemple@mail.fr"],
    "cas_005": ["PETIT", "10005551234"],
    # formats réels de laboratoire / anapath (régression audit 2026-08-20)
    "cas_006": ["DURAND", "Pascal", "15/03/1968", "LAB-2024-0117"],
    "cas_007": ["LEFEBVRE", "Claire"],
}


@pytest.mark.parametrize("case_id,needles", EXPECTED.items())
def test_known_identifiers_detected(case_id, needles):
    gt = SYNTH / f"{case_id}_ground_truth.txt"
    if not gt.exists():
        pytest.skip("dataset non généré")
    text = gt.read_text(encoding="utf-8")
    covered = set()
    for m in detect_pii(text):
        for n in needles:
            if n.lower() in m.value.lower():
                covered.add(n)
    assert covered == set(needles), f"non détectés : {set(needles) - covered}"
