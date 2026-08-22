from pathlib import Path

import pytest

from src.extraction_nlp.pipeline import run_pipeline as nlp
from src.vsm_generation.renderer import render_vsm
from src.vsm_generation.vsm_builder import build_vsm

SYNTH = Path(__file__).resolve().parents[2] / "data" / "synthetic"


def _vsm_from_gt(case_id):
    text = (SYNTH / f"{case_id}_ground_truth.txt").read_text(encoding="utf-8")
    ocr_json = {
        "document_id": case_id,
        "source_file": f"{case_id}.png",
        "sha256": "0" * 64,
        "ocr_engine": "tesseract",
        "text": text,
        "anonymization_applied": True,
        "pii_detected_count": 1,
        "pipeline_version": "1.0.0",
    }
    return build_vsm(nlp(ocr_json))


@pytest.mark.parametrize("case_id", [f"cas_{i:03d}" for i in range(1, 8)])
def test_vsm_for_each_synthetic_case(case_id, tmp_path):
    if not (SYNTH / f"{case_id}_ground_truth.txt").exists():
        pytest.skip("dataset non généré")
    vsm = _vsm_from_gt(case_id)  # build_vsm valide déjà contre le schéma
    assert vsm["statut"] == "a_valider"
    assert "PAS été vérifié médicalement" in vsm["avertissement"]
    md = render_vsm(vsm, "markdown")
    html = render_vsm(vsm, "html")
    assert "Volet de Synthèse Médicale" in md and "<html" in html
    pdf = render_vsm(vsm, "pdf", out_path=tmp_path / "v.pdf")
    assert pdf.stat().st_size > 1000


def test_render_follows_has_gabarit(tmp_path):
    """Le document généré ressemble à un vrai VSM : rubriques numérotées HAS,
    blocs d'identité, avertissement, zone de signature (régression audit —
    gabarit docs/gabarit_vsm.md + contexte/)."""
    vsm = _vsm_from_gt("cas_001")
    md = render_vsm(vsm, "markdown")
    html = render_vsm(vsm, "html")
    pdf = render_vsm(vsm, "pdf", out_path=tmp_path / "vsm_gabarit.pdf")

    # Rubriques 1 et 2 : identification patient et médecin traitant
    assert "1. Identification du patient" in md
    assert "2. Médecin traitant" in md
    # Rubriques cliniques numérotées 3..9 dans l'ordre HAS
    for i, titre in enumerate(
        (
            "Pathologies actives",
            "Antécédents médicaux et chirurgicaux",
            "Allergies et intolérances",
            "Traitements au long cours",
            "Facteurs de risque",
            "Vaccinations",
            "Points de vigilance",
        ),
        start=3,
    ):
        assert f"{i}. {titre}" in md, f"rubrique {i}. {titre} absente"
    # Avertissement + signature + date de génération
    assert "validé par un médecin" in md or "valider par un médecin" in md
    assert "Signature du médecin" in md
    assert "Généré le" in md
    # HTML : mêmes blocs
    assert "1. Identification du patient" in html
    assert "2. Médecin traitant" in html
    assert "Signature du médecin" in html
    # PDF généré
    assert pdf.stat().st_size > 1000


def test_render_identity_blocks(tmp_path):
    """Identité patient + médecin rendues avec leurs champs (pseudo mode)."""
    ocr_json = {
        "document_id": "doc_id",
        "source_file": "f.png",
        "sha256": "0" * 64,
        "ocr_engine": "tesseract",
        "text": (
            "Monsieur [PATIENT_001] — [DATE_NAISSANCE_001]\n"
            "Sexe : Masculin\n"
            "Prescrit par [RPPS_001] [ADELI_001]\n"
            "ANTECEDENTS : Diabete de type 2."
        ),
        "anonymization_applied": True,
        "pii_detected_count": 4,
        "pipeline_version": "1.0.0",
    }
    vsm = build_vsm(nlp(ocr_json))
    md = render_vsm(vsm, "markdown")
    assert "**Identité :** [PATIENT_001]" in md
    assert "**Date de naissance :** [DATE_NAISSANCE_001]" in md
    assert "**Sexe :** H" in md
    assert "**RPPS :** [RPPS_001]" in md
    assert "**ADELI :** [ADELI_001]" in md


def test_render_no_cleartext_pii(tmp_path):
    """Aucune PII en clair ne doit apparaître dans les rendus (art. 9)."""
    vsm = _vsm_from_gt("cas_001")
    for fmt in ("markdown", "html"):
        out = render_vsm(vsm, fmt)
        assert "DUPONT" not in out and "10001234567" not in out


def test_low_confidence_flagged():
    vsm = (
        _vsm_from_gt("cas_001")
        if (SYNTH / "cas_001_ground_truth.txt").exists()
        else pytest.skip("dataset")
    )
    vsm2 = build_vsm(vsm, confidence_threshold=0.99)
    items = [i for s in vsm2["sections"].values() for i in s]
    assert items and all(i["a_valider"] for i in items)
