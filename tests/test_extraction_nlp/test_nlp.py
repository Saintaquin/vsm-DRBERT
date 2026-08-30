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


# ---------------------------------------------------------------------------
# M1/MANGUE v9 — critère de mot DISCRIMINANT (CIM-10)
# ---------------------------------------------------------------------------


def test_m1_insuffisance_renale_refuse_i50():
    """« insuffisance rénale » → AUCUN code : le score global (0,78) est
    exactement à la frontière du seuil, mais le discriminant « rénale » est
    absent de « Insuffisance cardiaque » — la tête commune ne prouve rien."""
    assert normalize_diagnosis("insuffisance rénale")["code_cim10"] is None


def test_m1_zygarthrose_refuse_m15():
    """Mot-valise : « zygarthrose » vs « polyarthrose » = 0,783 — les
    préfixes diffèrent (zyg- ≠ poly-), la racine porte le sens."""
    assert normalize_diagnosis("zygarthrose")["code_cim10"] is None


def test_m1_avc_nu_refuse_i63():
    """« AVC » seul ne documente PAS l'ischémie : I63 l'affirme (« AVC
    ischémique »), I64 serait le code honnête (absent du référentiel →
    aucun code). Mesuré avant correctif : I63 attribué à 1,00."""
    assert normalize_diagnosis("AVC")["code_cim10"] is None


def test_m1_avc_ischemique_garde_i63():
    """La forme documentée garde son code : le discriminant « ischémique »
    est dans le libellé."""
    assert normalize_diagnosis("AVC ischémique")["code_cim10"] == "I63"


def test_m1_non_regressions_discriminants():
    """Les cas où le code est JUSTE ne bougent pas — c'est la vérification
    que M1 répare sans casser (checklist MANGUE : insuffisance cardiaque
    DROITE est le cas où I50 est correct)."""
    assert normalize_diagnosis("insuffisance cardiaque droite")["code_cim10"] == "I50"
    assert normalize_diagnosis("insuffisance cardiaque")["code_cim10"] == "I50"
    # « rénale » présent dans le libellé N18 → code conservé.
    assert (
        normalize_diagnosis("insuffisance rénale chronique")["code_cim10"] == "N18"
    )
    assert normalize_diagnosis("Maladie Rénale chronique")["code_cim10"] == "N18"
    assert normalize_diagnosis("pacemaker")["code_cim10"] == "Z95.0"
    # I48 est un libellé de REGROUPEMENT (« Fibrillation et flutter ») :
    # un seul discriminant suffit — sinon régression.
    assert normalize_diagnosis("fibrillation auriculaire")["code_cim10"] == "I48"
    # Terme purement générique vs libellé sans précision : passe.
    assert normalize_diagnosis("allergie")["code_cim10"] == "T78.4"
    assert normalize_diagnosis("diabète de type 2")["code_cim10"] == "E11"
    assert normalize_diagnosis("infarctus du myocarde")["code_cim10"] == "I21"
    assert normalize_diagnosis("BPCO")["code_cim10"] == "J44"


def test_m1_refus_specificite_inchanges():
    """Les refus C2 (« diabète » nu trop générique pour E11) ne régressent
    pas : M1 et C2 sont des filets indépendants."""
    for terme in ("diabète", "tumeur", "anémie", "cirrhose", "lombalgie"):
        assert normalize_diagnosis(terme)["code_cim10"] is None


def test_m1_alias_atc_posologie():
    """Les ALIAS parenthésés sont des clés du référentiel ATC : « Kardegic
    75mg » ne peut pas être un sur-ensemble du libellé long (0,76 < 0,95)
    mais l'est de l'alias « Kardégic » (1,00). Régression C1 constatée sur
    le VSM MANGUE, non-régression explicite de la checklist."""
    assert normalize_medication("Kardegic 75mg")["code_atc"] == "B01AC06"
    assert normalize_medication("Aspirine 75 mg")["code_atc"] == "B01AC06"


def test_m1_non_regressions_atc():
    """Les codes ATC du VSM MANGUE ne bougent pas."""
    assert normalize_medication("Pantoprazole 20mg : 1/j")["code_atc"] == "A02BC02"
    assert normalize_medication("Amlodipine 10mg : 1/j")["code_atc"] == "C08CA01"
    assert (
        normalize_medication("PARACETAMOL 500 mg 2 gélules")["code_atc"] == "N02BE01"
    )
    # Refus C1 inchangés : hors référentiel, pas de code inventé.
    assert normalize_medication("LODOZ 5 mg")["code_atc"] is None
    assert normalize_medication("TRAMADOL 50 mg")["code_atc"] is None
    assert normalize_medication("SPIRAMYCINE")["code_atc"] is None


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
