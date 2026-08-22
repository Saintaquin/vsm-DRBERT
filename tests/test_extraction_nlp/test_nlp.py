from src.extraction_nlp.entity_extractor import extract_entities
from src.extraction_nlp.normalizer import normalize_diagnosis, normalize_medication
from src.extraction_nlp.pipeline import run_pipeline

TEXT = (
    "ANTECEDENTS : Diabete de type 2 depuis 2010. Hypertension arterielle.\n"
    "ALLERGIES : Penicilline (eruption cutanee).\n"
    "TRAITEMENTS EN COURS : Metformine 1000 mg matin et soir. Ramipril 5 mg le matin.\n"
    "VACCINATIONS : Grippe 10/2023."
)


def test_sections_extracted():
    ents = extract_entities(TEXT)
    sections = {e.section for e in ents}
    assert {
        "antecedents",
        "allergies",
        "traitements_long_cours",
        "vaccinations",
    } <= sections


def test_conclusion_does_not_pollute_sections():
    # « CONCLUSION : … » clôt la dernière rubrique au lieu de s'y rattacher
    # (régression audit : qualité de la synthèse, gabarit VSM).
    text = (
        "VACCINATIONS : Grippe 10/2023. DTP a jour.\n"
        "CONCLUSION : Equilibre glycemique satisfaisant. Poursuite du traitement."
    )
    ents = extract_entities(text)
    valeurs = [e.valeur for e in ents if e.section == "vaccinations"]
    assert any("Grippe" in v for v in valeurs)
    assert not any("CONCLUSION" in v or "Poursuite" in v for v in valeurs)
    # le contenu de la conclusion alimente Points de vigilance (repli texte libre)
    vigil = [e.valeur for e in ents if e.section == "points_vigilance"]
    assert any("Poursuite" in v for v in vigil)


def test_free_text_fallback_on_unstructured_document():
    # CR de laboratoire / anapath sans en-têtes de rubriques : le repli
    # « texte libre » remplit les sections (correction P0-4 résiduelle).
    text = (
        "CABINET D'ANATOMIE ET DE CYTOLOGIE PATHOLOGIQUES\n"
        "Examen concernant Mme [PATIENT_001]\n"
        "Dans ses antécédents on note :\n"
        "Sur le plan chirurgical, Cholécystectomie\n"
        "Sur le plan médical, Pneumothorax\n"
        "Allergie à la pénicilline\n"
        "Elle a été traitée par Rivotril pendant plusieurs mois\n"
        "Tabagisme sevré depuis 2019\n"
        "CONCLUSION : Frottis satisfaisant. Absence de cellule suspecte."
    )
    ents = extract_entities(text)
    by = {}
    for e in ents:
        by.setdefault(e.section, []).append(e.valeur)
    assert any("Cholécystectomie" in v for v in by.get("antecedents", []))
    assert any("Pneumothorax" in v for v in by.get("antecedents", []))
    assert any("pénicilline" in v for v in by.get("allergies", []))
    assert any("Rivotril" in v for v in by.get("traitements_long_cours", []))
    assert any("Tabagisme" in v for v in by.get("facteurs_risque", []))
    assert any(
        "Absence de cellule suspecte" in v for v in by.get("points_vigilance", [])
    )


def test_free_text_negative_allergy_not_captured():
    text = "Aucune allergie connue. Pas d'allergie médicamenteuse."
    ents = extract_entities(text)
    assert not [e for e in ents if e.section == "allergies"]


def test_free_text_bounded_conclusion():
    # Le repli CONCLUSION est borné : il ne capture pas la fin entière d'un
    # document multi-pages (bruit, signatures, pagination).
    text = (
        "CONCLUSION : Frottis satisfaisant. Frottis non inflammatoire. "
        "Absence de cellule suspecte.\n"
        "le 10/10/2014\nDocteur\nCP/BP Page 1/1\n"
        + "\n".join(f"Ligne de bruit {i} du document suivant" for i in range(20))
    )
    ents = [e for e in extract_entities(text) if e.section == "points_vigilance"]
    assert any("Frottis satisfaisant" in e.valeur for e in ents)
    assert not any("Ligne de bruit" in e.valeur for e in ents)


def test_free_text_does_not_duplicate_headers():
    # Document structuré : l'extraction par rubriques prime, pas de doublon
    # issu du repli texte libre.
    ents = extract_entities(TEXT)
    meds = [e.valeur for e in ents if e.section == "traitements_long_cours"]
    assert len(meds) == len({m.strip().lower() for m in meds})  # pas de doublon
    assert sum("Metformine" in m for m in meds) == 1


def test_normalize_diagnosis_cim10():
    n = normalize_diagnosis("Diabete de type 2")
    assert n["code_cim10"] == "E11"
    assert n["confidence"] > 0.7


def test_normalize_medication_atc_and_dosage():
    n = normalize_medication("Metformine 1000 mg matin et soir")
    assert n["code_atc"] == "A10BA02"
    assert "1000 mg" in n["dosage_parsed"]


def test_unknown_medication_graceful():
    n = normalize_medication("Zorglubine 3 mg")
    assert n["code_atc"] is None
    assert n["dosage_parsed"] is not None


def test_pipeline_schema_shape():
    ocr_json = {
        "document_id": "doc_x",
        "source_file": "f.png",
        "sha256": "0" * 64,
        "ocr_engine": "tesseract",
        "text": TEXT,
        "anonymization_applied": True,
        "pii_detected_count": 0,
        "pipeline_version": "1.0.0",
    }
    out = run_pipeline(ocr_json)
    assert out["sections"]["traitements_long_cours"]
    champ = out["sections"]["traitements_long_cours"][0]
    assert {"valeur", "confiance", "source", "a_valider"} <= set(champ)
    assert champ["source"]["document_id"] == "doc_x"


def test_identity_filled_from_pseudonym_tokens():
    # Les tokens de pseudonymisation (mode « pseudo ») remplissent l'identité
    # du VSM — correction audit 2026-08-20 (P0-4, champs patient/médecin vides).
    text = (
        "Monsieur [PATIENT_001] — [DATE_NAISSANCE_001]\n"
        "Sexe : Masculin\n"
        "Prescrit par [RPPS_001] [ADELI_001]\n"
        "ANTECEDENTS : Diabete de type 2."
    )
    ocr_json = {
        "document_id": "doc_pseudo",
        "source_file": "f.png",
        "sha256": "0" * 64,
        "ocr_engine": "tesseract",
        "text": text,
        "anonymization_applied": True,
        "pii_detected_count": 4,
        "pipeline_version": "1.0.0",
    }
    out = run_pipeline(ocr_json)
    assert out["patient"]["identite"]["valeur"] == "[PATIENT_001]"
    assert out["patient"]["date_naissance"]["valeur"] == "[DATE_NAISSANCE_001]"
    assert out["patient"]["sexe"]["valeur"] == "H"
    assert out["medecin_traitant"]["rpps"]["valeur"] == "[RPPS_001]"
    assert out["medecin_traitant"]["adelI"]["valeur"] == "[ADELI_001]"
    assert out["patient"]["identite"]["confiance"] == 1.0


def test_identity_empty_in_strict_mode():
    # Sans token (mode strict), l'identité reste vide (non réversible).
    ocr_json = {
        "document_id": "doc_strict",
        "source_file": "f.png",
        "sha256": "0" * 64,
        "ocr_engine": "tesseract",
        "text": "Monsieur [REDACTED:NOM_PERSONNE]",
        "anonymization_applied": True,
        "pii_detected_count": 1,
        "pipeline_version": "1.0.0",
    }
    out = run_pipeline(ocr_json)
    assert out["patient"] == {}
    assert out["medecin_traitant"] == {}
