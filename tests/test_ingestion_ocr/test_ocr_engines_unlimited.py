"""Tests du moteur OCR optionnel « unlimited » (baidu/Unlimited-OCR, GPU NVIDIA).

L'inférence réelle exige une carte NVIDIA : non exécutée ici. On teste la
fonction pure de nettoyage des marqueurs, le gating GPU (fonctionnalité
INEIXISTANTE sans carte NVIDIA) et le comportement de l'API."""

from src.ingestion_ocr import ocr_engines as oe


def test_strip_det_markers_removes_structure_tokens():
    raw = (
        "<|det|>text [1,2,3,4]<|/det|>Ligne de texte 1\n"
        "<|det|>text [5,6,7,8]<|/det|>Ligne de texte 2\n"
        "<|det|>table [9,0,1,2]<|/det|>cellule de tableau\n"
        "\n"
    )
    out = oe.strip_det_markers(raw)
    assert "Ligne de texte 1" in out
    assert "Ligne de texte 2" in out
    assert "<|det|>" not in out and "<|/det|>" not in out
    assert "[1,2,3,4]" not in out


def test_unlimited_not_available_without_nvidia_gpu():
    # Sans carte NVIDIA (ou torch absent), le moteur n'EXISTE pas.
    assert oe.UnlimitedOCREngine.name == "unlimited"
    assert oe.UnlimitedOCREngine.is_available() is False
    assert "unlimited" not in oe.ENGINES


def test_get_engine_unlimited_rejected_without_gpu():
    import pytest

    with pytest.raises(ValueError):
        oe.get_engine("unlimited")
