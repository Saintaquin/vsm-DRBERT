from src.anonymization.pii_detector import detect_pii

SAMPLE = (
    "Patient : Jean DUPONT\nNé le : 14/05/1956\n"
    "N° sécurité sociale : 1 56 05 75 123 456 78\n"
    "Médecin : Dr Marie LAURENT — RPPS : 10001234567\n"
    "Tel : 06 12 34 56 78 — Email : jean.dupont@mail.fr\n"
    "Adresse : 14 rue des Lilas, 75011 Paris\n"
    "N° dossier : CARD-2024-0892"
)


def _types(text):
    return {m.pii_type for m in detect_pii(text)}


def test_detects_all_major_types():
    t = _types(SAMPLE)
    for expected in (
        "NOM_PERSONNE",
        "NIR",
        "RPPS",
        "DATE_NAISSANCE",
        "TELEPHONE",
        "EMAIL",
        "ADRESSE",
        "NUMERO_DOSSIER",
    ):
        assert expected in t, f"{expected} non détecté"


def test_no_false_positive_on_medical_text():
    text = (
        "ANTECEDENTS : Diabète de type 2. Hypertension artérielle. Metformine 1000 mg."
    )
    matches = detect_pii(text)
    assert not [m for m in matches if m.pii_type == "NOM_PERSONNE"]


def test_known_false_negative_documented():
    # Limite connue : nom seul sans contexte ni prénom du dictionnaire
    text = "Le compte rendu mentionne DURANDET en marge."
    assert "NOM_PERSONNE" not in _types(text)


def test_date_without_context_not_birth():
    matches = detect_pii("Consultation du 12/03/2024 sans autre précision.")
    assert not [m for m in matches if m.pii_type == "DATE_NAISSANCE"]


def test_no_overlapping_matches():
    matches = detect_pii(SAMPLE)
    for i, a in enumerate(matches):
        for b in matches[i + 1 :]:
            assert not a.overlaps(b)


# ---------------------------------------------------------------------------
# Régression audit 2026-08-20 : formats réels de laboratoire / comptes-rendus
# (voir outputs/AUDIT_CONFORMITE_RAPPORT.md — découverte critique n°1)
# ---------------------------------------------------------------------------


def test_surname_in_caps_before_firstname():
    # « Monsieur ABRICOT Anthony » : nom en MAJUSCULES avant le prénom
    # (format standard des CR de laboratoire) — fuyait avant la correction.
    ms = detect_pii("Monsieur ABRICOT Anthony DR")
    names = [m for m in ms if m.pii_type == "NOM_PERSONNE"]
    assert names and "ABRICOT" in names[0].value


def test_mme_caps_surname():
    ms = detect_pii("Mme BANANE Sophie")
    names = [m for m in ms if m.pii_type == "NOM_PERSONNE"]
    assert names and "BANANE" in names[0].value


def test_capitalized_titles_trigger_heuristic():
    # « Patient » capitalisé (titre réel) doit déclencher le heuristique
    # — avant la correction, seul le dictionnaire fonctionnait.
    ms = detect_pii("Patient : Jean DUPONT")
    names = [
        m for m in ms if m.pii_type == "NOM_PERSONNE" and m.method == "heuristique"
    ]
    assert names and names[0].value == "Jean DUPONT"


def test_birth_context_ddn_and_ocr_variants():
    # Contextes « DDN : » / « DON: » (OCR dégradé) → date de naissance.
    for probe in ("DDN : le 15/03/1968", "DON: le 24/07/1963 soit 51 Ans"):
        ms = detect_pii(probe)
        assert any(
            m.pii_type == "DATE_NAISSANCE"
            and m.value == probe.split("le ")[1].split(" ")[0]
            for m in ms
        ), probe


def test_two_digit_year_date_with_birth_context():
    ms = detect_pii("Né(e) le : 04/12/14")
    assert any(m.pii_type == "DATE_NAISSANCE" and m.value == "04/12/14" for m in ms)


def test_short_date_without_context_not_flagged():
    # « Prélèvement du 04/12/14 » : date d'examen, sans contexte de naissance.
    ms = detect_pii("Prélèvement du 04/12/14 à 08H20")
    assert not [m for m in ms if m.pii_type == "DATE_NAISSANCE"]


def test_name_group_does_not_capture_lowercase_words():
    # « Jean DUPONT est suivi » ne doit pas capturer « est suivi ».
    ms = detect_pii("Le patient Jean DUPONT est suivi par Dr Marie LAURENT")
    names = [m.value for m in ms if m.pii_type == "NOM_PERSONNE"]
    assert "Jean DUPONT" in names
    assert not any("est suivi" in n for n in names)


def test_lab_values_not_detected_as_names():
    # Faux positifs historiques sur documents de labo.
    text = (
        "Prescrit par : DR\nSodium 140 mmol/L. Potassium 4,2 mmol/L.\n"
        "Hématies 5,16 Téra/L. Leucocytes 10,12 Giga/L."
    )
    ms = detect_pii(text)
    assert not [m for m in ms if m.pii_type == "NOM_PERSONNE"]


def test_ocr_title_variants_mr_m_beneficiaire():
    # Variantes de titres observées sur les vrais scans (régression audit).
    for probe in (
        "Demande n° 04/12/14- MR ABRICOT Anthony .",
        "M ABRICOT Anthony",
        "Bénéficiaire : ABRICOT Anthony",
        "Nomp - Prénom BANANE Soÿnhie",
    ):
        ms = detect_pii(probe)
        assert any(
            m.pii_type == "NOM_PERSONNE"
            and ("ABRICOT" in m.value or "BANANE" in m.value)
            for m in ms
        ), probe


def test_identity_line_fallback_ocr_variant():
    # « Nom et Rang du bénéficiaire / … ABRICOT Antrony » (erreur OCR du
    # prénom) doit quand même être détecté via le repli ligne d'identité.
    line = "Nom et Rang du bénéficiaire / Date sccident ABRICOT Antrony"
    ms = detect_pii(line)
    assert any(m.pii_type == "NOM_PERSONNE" and "ABRICOT" in m.value for m in ms)


def test_birth_date_repeated_in_table_line():
    # Date de naissance répétée dans une ligne de tableau sans contexte
    # direct : le mot-clé « netssance » (OCR) sur la ligne suffit.
    line = "Date netssance et rang bénef reçus / retenus 24/07/0062 - 1 24/07/1963 -1"
    ms = detect_pii(line)
    assert any(m.pii_type == "DATE_NAISSANCE" and m.value == "24/07/1963" for m in ms)
