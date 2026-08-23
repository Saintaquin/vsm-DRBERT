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


def test_pdf_processed_in_page_batches(tmp_path, monkeypatch):
    # Correction « OCR vide sur gros PDF » : les pages sont converties et
    # traitées par LOTS (mémoire bornée), tous les numéros de page présents.
    import pypdf
    from src.ingestion_ocr import pipeline as pl

    pdf = tmp_path / "doc5.pdf"
    writer = pypdf.PdfWriter()
    for _ in range(5):
        writer.add_blank_page(width=300, height=400)
    with open(pdf, "wb") as f:
        writer.write(f)

    monkeypatch.setattr(pl, "_OCR_PDF_BATCH", 2)  # lots de 2 pages
    out = run_pipeline(pdf, engine="tesseract", anonymize_mode="strict")
    rep = out["processing_report"]
    assert rep["pages_total"] == 5
    assert rep["pages_ok"] == 5
    assert [p["page"] for p in out["pages"]] == [1, 2, 3, 4, 5]
    assert out["anonymization_applied"] is True
