from pathlib import Path

import pytest

from src.ingestion_ocr.ocr_engines import ENGINES
from src.ingestion_ocr.pipeline import run_pipeline

SYNTH = Path(__file__).resolve().parents[2] / "data" / "synthetic"
needs_tesseract = pytest.mark.skipif(
    "tesseract" not in ENGINES, reason="tesseract absent"
)


@needs_tesseract
def test_pipeline_contract_and_anonymization():
    img = SYNTH / "cas_001_clean.png"
    if not img.exists():
        pytest.skip("dataset non généré")
    out = run_pipeline(img, anonymize_mode="pseudo")
    for key in (
        "document_id",
        "source_file",
        "sha256",
        "ocr_engine",
        "text",
        "pages",
        "anonymization_applied",
        "pii_detected_count",
        "processing_report",
    ):
        assert key in out
    assert out["anonymization_applied"] is True
    assert out["pii_detected_count"] >= 3
    assert "DUPONT" not in out["text"]
    assert "[PATIENT_001]" in out["text"]
    assert len(out["sha256"]) == 64
    assert out["processing_report"]["pages_ok"] == 1


@needs_tesseract
def test_pipeline_strict_has_no_mapping():
    img = SYNTH / "cas_001_clean.png"
    if not img.exists():
        pytest.skip("dataset non généré")
    out = run_pipeline(img, anonymize_mode="strict")
    assert "_pii_mapping" not in out
    assert "[REDACTED:" in out["text"]


def test_invalid_mode_rejected(tmp_path):
    from PIL import Image

    p = tmp_path / "x.png"
    Image.new("L", (50, 50), 255).save(p)
    with pytest.raises(ValueError):
        run_pipeline(p, anonymize_mode="none")
