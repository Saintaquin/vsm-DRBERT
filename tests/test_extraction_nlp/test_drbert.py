"""Tests de l'extraction DrBERT-CASM2 (étapes 1 à 3) — sans téléchargement.

Le moteur neural est SIMULÉ (FakeMoteur injecté dans le singleton) : on teste
la mécanique autour — décodage BIO et fusion inter-fenêtres (fonctions
pures), filtres de l'étape 0 (bords de mots, seuil 0,70, étiquette « test »,
jetons d'anonymisation), affectation aux 7 rubriques, validateur aval
branché, ancrage des offsets et sélection du moteur. Les tests marqués
``slow`` utilisent le VRAI modèle local s'il est présent (ignorés sinon).

NB : l'ancien moteur complémentaire DrBERT-MedicalNER-FR (drbert.py) garde
ses propres tests dans test_drbert_medicalner.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.extraction_nlp import drbert_extractor as dtx
from src.extraction_nlp.drbert_extractor import (
    DrBERTIndisponible,
    Entite,
    decoder_fenetre,
    extraire_entites,
    fusionner,
)
from src.extraction_nlp.entity_extractor import (
    NLP_ENGINE_DRBERT_CASM2,
    NLP_ENGINE_RULES,
    extract_entities_drbert,
    extract_entities_with_report,
    moteur_nlp_par_defaut,
)
from src.extraction_nlp.rubriques import affecter_rubriques, rubrique_de

RACINE = Path(__file__).resolve().parents[2]
VRAI_DOSSIER = RACINE / "models" / "drbert"

TEXTE = (
    "Antécédents : appendicectomie en 1998.\n"
    "Allergies : pénicilline.\n"
    "Traitement en cours : oméprazole 20 mg.\n"
    "Cure de 7 jours : amoxicilline.\n"
    "Vaccination antigrippale à jour.\n"
    "Le patient présente une œsophagite chronique.\n"
    "Tabagisme actif, 20 cigarettes par jour.\n"
    "Bilan sanguin : CRP à 12 mg/L.\n"
)

RUBRIQUES_VSM = {
    "pathologies_actives",
    "antecedents",
    "allergies",
    "traitements_long_cours",
    "facteurs_risque",
    "vaccinations",
    "points_vigilance",
}


class FakeMoteur:
    """Moteur factice : renvoie des entités prédéfinies (offsets exacts)."""

    def __init__(self, entites: list[Entite]) -> None:
        self._entites = entites

    def annoter(self, texte: str) -> list[Entite]:
        return list(self._entites)


def _ent(label: str, mot: str, texte: str, score: float = 0.9) -> Entite:
    """Entité positionnée aux offsets RÉELS de ``mot`` dans ``texte``."""
    debut = texte.index(mot)
    return Entite(label, mot, debut, debut + len(mot), score)


def _injecter(monkeypatch, entites: list[Entite]) -> None:
    """Remplace le singleton DrBERT par un moteur factice (aucun modèle)."""
    monkeypatch.setattr(dtx, "_MOTEUR", FakeMoteur(entites))


# ---------------------------------------------------------------------------
# Étape 1 — décodage, fusion, filtres
# ---------------------------------------------------------------------------


def test_decoder_offsets_designent_le_texte():
    """Les offsets renvoyés désignent bien le texte attendu dans la source."""
    texte = "Œsophagite chronique traitée par oméprazole."
    mots = ("Œsophagite", "chronique", "traitée", "par", "oméprazole")
    offsets = []
    for mot in mots:
        debut = texte.index(mot)
        offsets.append((debut, debut + len(mot)))
    offsets.append((len(texte) - 1, len(texte)))  # le point final
    types = ["problem", "problem", None, None, "treatment", None]
    scores = [0.9, 0.8, 0.5, 0.5, 0.95, 0.5]
    debuts_b = [True, False, False, False, True, False]

    entites = decoder_fenetre(texte, offsets, types, scores, debuts_b)

    assert len(entites) == 2
    premiere, deuxieme = entites
    assert premiere.label == "problem"
    assert premiere.texte == "Œsophagite chronique"
    assert texte[premiere.debut : premiere.fin] == premiere.texte  # ancrage exact
    assert premiere.score == pytest.approx(0.85)  # moyenne des tokens
    assert deuxieme.label == "treatment"
    assert deuxieme.texte == "oméprazole"
    assert texte[deuxieme.debut : deuxieme.fin] == deuxieme.texte


def test_fusion_entite_a_cheval_deux_fenetres():
    """Une entité coupée au bord d'une fenêtre est fusionnée UNE seule fois."""
    texte = "il présente une œsophagite chronique peptique sévère au reflux"
    complet = "œsophagite chronique peptique"
    debut = texte.index(complet)
    # Fenêtre A : coupée après « chronique » ; fenêtre B : entité complète
    fragment_a = Entite("problem", complet[:20], debut, debut + 20, 0.80)
    fragment_b = Entite("problem", complet, debut, debut + len(complet), 0.90)

    fusion = fusionner([fragment_a, fragment_b], texte)
    problemes = [e for e in fusion if e.label == "problem"]
    assert len(problemes) == 1  # ni doublon, ni perte
    assert problemes[0].debut == debut
    assert problemes[0].fin == debut + len(complet)
    assert problemes[0].texte == complet
    assert texte[problemes[0].debut : problemes[0].fin] == problemes[0].texte


def test_fusion_recouvrement_partiel_et_separation():
    """Recouvrement partiel → union ; entités distinctes → jamais fusionnées."""
    texte = "gastrite chronique puis colite ulcéreuse ensuite"
    dg = texte.index("gastrite")
    dc = texte.index("colite")
    a = Entite("problem", "gastrite chronique", dg, dg + 18, 0.8)
    b = Entite("problem", "chronique puis colite", dg + 9, dc + 6, 0.7)
    # « puis » (5 caractères) sépare : pas de collage <= 2 espacements
    sep1 = Entite("problem", "gastrite", dg, dg + 8, 0.9)
    sep2 = Entite("problem", "colite", dc, dc + 6, 0.9)

    fusionnes = fusionner([a, b], texte)
    assert len(fusionnes) == 1
    assert fusionnes[0].texte == texte[dg : dc + 6]  # union des deux fragments

    separes = fusionner([sep1, sep2], texte)
    assert len(separes) == 2  # trop éloignées : deux entités distinctes


def test_filtre_bords_de_mots_obligatoire(monkeypatch):
    """Un fragment de sous-mot (« fra » dans « fraude ») est rejeté."""
    texte = "Quiconque se rend coupable de fraude ou de fausse déclaration."
    fragment = _ent("problem", "fra", texte, score=0.95)
    mot_complet = _ent("problem", "fraude", texte, score=0.95)
    _injecter(monkeypatch, [fragment, mot_complet])

    gardees = extraire_entites(texte)
    valeurs = [e.texte for e in gardees]
    assert "fra" not in valeurs
    assert "fraude" in valeurs


def test_seuil_score_070(monkeypatch):
    """Score < 0,70 rejeté ; >= 0,70 gardé ; seuil ajustable par variable."""
    texte = "Œsophagite chronique et gastrite aiguë associées."
    faible = _ent("problem", "Œsophagite chronique", texte, score=0.699)
    fort = _ent("problem", "gastrite aiguë", texte, score=0.70)
    _injecter(monkeypatch, [faible, fort])
    assert [e.texte for e in extraire_entites(texte)] == ["gastrite aiguë"]

    monkeypatch.setenv("VSM_DRBERT_MIN_SCORE", "0.5")
    _injecter(monkeypatch, [faible, fort])
    assert len(extraire_entites(texte)) == 2


def test_label_test_ecartee_par_defaut(monkeypatch):
    """L'étiquette « test » est écartée sauf VSM_DRBERT_KEEP_TESTS."""
    texte = "Bilan sanguin : CRP à 12 mg/L, NFS normale."
    examen = _ent("test", "Bilan sanguin", texte, score=0.95)
    _injecter(monkeypatch, [examen])
    assert extraire_entites(texte) == []

    monkeypatch.setenv("VSM_DRBERT_KEEP_TESTS", "1")
    _injecter(monkeypatch, [examen])
    gardees = extraire_entites(texte)
    assert [e.label for e in gardees] == ["test"]


def test_jetons_anonymisation_exclus(monkeypatch):
    """Les jetons [PATIENT_001] ne se retrouvent JAMAIS dans une valeur."""
    texte = "Patient [PATIENT_001] vu en consultation pour œsophagite."
    debut = texte.index("[PATIENT_001]")
    chevauchante = Entite("problem", texte[debut : debut + 12], debut, debut + 12, 0.99)
    complete = Entite("problem", texte[debut : debut + 13], debut, debut + 13, 0.99)
    bonne = _ent("problem", "œsophagite", texte, score=0.9)
    _injecter(monkeypatch, [chevauchante, complete, bonne])

    gardees = extraire_entites(texte)
    assert [e.texte for e in gardees] == ["œsophagite"]


# ---------------------------------------------------------------------------
# Étape 2 — affectation aux rubriques (cas par cas du tableau)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label, contexte, titre, entite, attendu",
    [
        ("treatment", "allergie à la", None, "", "allergies"),
        ("treatment", "intolérance au", None, "", "allergies"),
        ("treatment", "vaccination antigrippale", None, "", "vaccinations"),
        ("treatment", "antécédent de", None, "", "antecedents"),
        ("treatment", "opéré en 2003", None, "", "antecedents"),
        ("treatment", "", "antecedents", "", "antecedents"),
        ("treatment", "cure de 7 jours", None, "", "points_vigilance"),
        ("treatment", "pendant 2 semaines", None, "", "points_vigilance"),
        ("treatment", "traitement en cours", None, "", "traitements_long_cours"),
        # CASM2 étiquette les mentions d'allergie « problem » (test de fumée)
        ("problem", "allergie à la", None, "", "allergies"),
        ("problem", "intolérance au", None, "", "allergies"),
        ("problem", "antécédent de", None, "", "antecedents"),
        ("problem", "chirurgical", None, "", "antecedents"),
        ("problem", "opéré en 2003", None, "", "antecedents"),
        ("problem", "en 2003", None, "", "antecedents"),
        ("problem", "", "antecedents", "", "antecedents"),
        # P5 : facteurs de risque par LISTE FERMÉE sur l'ENTITÉ — plus sur
        # le contexte (sténose urétrale dans un contexte « consommation »
        # restait une sténose urétrale).
        ("problem", "Tabagisme, consommation d'alcool", None,
         "Tabagisme", "facteurs_risque"),
        ("problem", "poids 92 kg", None, "poids 92 kg", "pathologies_actives"),
        ("problem", "activité physique réduite", None,
         "activité physique réduite", "facteurs_risque"),
        ("problem", "sevrage tabagique progressif", None,
         "sténose urétrale", "pathologies_actives"),
        ("problem", "", "facteurs_risque", "IMC 32", "facteurs_risque"),
        ("problem", "douleur", None, "", "pathologies_actives"),
        ("test", "", None, "", "points_vigilance"),
        # P3 : acte chirurgical étiqueté « treatment » → antécédents.
        ("treatment", "excision du pertuis cutané", None,
         "excision du pertuis cutané", "antecedents"),
        ("treatment", "", None, "cholécystectomie rétrograde", "antecedents"),
        ("treatment", "", None, "Metformine", "traitements_long_cours"),
    ],
)
def test_rubrique_de_cas_par_cas(label, contexte, titre, entite, attendu):
    assert rubrique_de(label, contexte, titre, entite) == attendu


def test_affecter_rubriques_titre_au_dessus():
    """Le dernier titre rencontré au-dessus oriente l'entité."""
    texte = (
        "ANTÉCÉDENTS\n"
        "Appendicectomie sous coelioscopie.\n\n"
        "PATHOLOGIES ACTIVES\n"
        "Œsophagite peptique.\n"
    )
    ancien = _ent("problem", "Appendicectomie", texte, score=0.9)
    actif = _ent("problem", "Œsophagite", texte, score=0.9)

    affectations = affecter_rubriques([ancien, actif], texte)
    assert affectations == [
        (ancien, "antecedents"),
        (actif, "pathologies_actives"),
    ]


def test_contexte_borne_au_titre_de_rubrique():
    """« Allergies : … » ne doit PAS déteindre sur le traitement suivant."""
    texte = (
        "ALLERGIES : pénicilline.\n"
        "TRAITEMENT EN COURS : oméprazole 20 mg le matin.\n"
    )
    allergique = _ent("treatment", "pénicilline", texte, score=0.9)
    ordinaire = _ent("treatment", "oméprazole", texte, score=0.9)

    affectations = affecter_rubriques([allergique, ordinaire], texte)
    assert affectations == [
        (allergique, "allergies"),
        (ordinaire, "traitements_long_cours"),
    ]


# ---------------------------------------------------------------------------
# Étape 3 — structure tracée, validateur branché, sélection du moteur
# ---------------------------------------------------------------------------


def test_extract_entities_drbert_structure_et_ancrage(monkeypatch):
    """Structure des 7 rubriques, champs tracés, garantie anti-hallucination :
    AUCUNE valeur produite n'est absente du texte source (vérifié par découpe
    aux offsets, même si la garantie est structurelle)."""
    entites = [
        _ent("problem", "appendicectomie", TEXTE, score=0.85),
        _ent("treatment", "pénicilline", TEXTE, score=0.92),
        _ent("treatment", "oméprazole", TEXTE, score=0.88),
        _ent("problem", "œsophagite chronique", TEXTE, score=0.91),
    ]
    _injecter(monkeypatch, entites)

    sections = extract_entities_drbert(TEXTE, document_id="doc_test", page=3)

    assert set(sections) == RUBRIQUES_VSM
    valeurs = {rub: [c["valeur"] for c in items] for rub, items in sections.items()}
    assert "appendicectomie" in valeurs["antecedents"]
    assert "pénicilline" in valeurs["allergies"]
    # P7 : la posologie « 20 mg » qui suit dans le texte source est
    # absorbée — l'empan s'étend DANS le texte source, jamais inventé.
    assert "oméprazole 20 mg" in valeurs["traitements_long_cours"]
    assert "œsophagite chronique" in valeurs["pathologies_actives"]

    for items in sections.values():
        for champ in items:
            assert champ["source"]["document_id"] == "doc_test"
            assert champ["source"]["page"] == 3
            assert champ["moteur_nlp"] == NLP_ENGINE_DRBERT_CASM2
            assert champ["origine"] == "drbert"
            assert champ["correction_ocr"] is False
            assert champ["confiance"] >= 0.70  # score réel, pas une constante
            # Le passage est un extrait PAR DÉCOUPE AUX OFFSETS :
            debut = champ["source"]["offset_debut"]
            fin = champ["source"]["offset_fin"]
            assert TEXTE[debut:fin] == champ["valeur"] == champ["source"]["passage"]


def test_validateur_branche_sur_sortie_drbert(monkeypatch):
    """Le validateur aval rejette le bruit résiduel, même pour l'encodeur."""
    texte = (
        "Laboratoire de biologie.\n"
        "Traitement par antibiotique.\n"
        "Œsophagite chronique."
    )
    entites = [
        _ent("problem", "Laboratoire", texte, score=0.95),  # blocklist
        _ent("treatment", "antibiotique", texte, score=0.95),  # classe seule
        _ent("problem", "Œsophagite chronique", texte, score=0.9),
    ]
    _injecter(monkeypatch, entites)

    sections = extract_entities_drbert(texte)
    toutes = [c["valeur"] for items in sections.values() for c in items]
    assert "Laboratoire" not in toutes
    assert "antibiotique" not in toutes
    assert "Œsophagite chronique" in toutes


def test_en_tete_seule_rejetee(monkeypatch):
    """Un en-tête de rubrique étiqueté comme entité (« ALLERGIES ») est du
    bruit : le validateur le rejette (observé sur le vrai modèle)."""
    texte = (
        "ALLERGIES\n"
        "Allergie à la pénicilline.\n"
        "TRAITEMENT EN COURS\n"
        "Oméprazole 20 mg.\n"
    )
    entites = [
        _ent("problem", "ALLERGIES", texte, score=0.80),  # en-tête seul
        _ent("problem", "TRAITEMENT EN COURS", texte, score=0.80),  # idem
        _ent("problem", "Allergie à la pénicilline", texte, score=0.96),
        _ent("treatment", "Oméprazole", texte, score=0.85),
    ]
    _injecter(monkeypatch, entites)

    sections = extract_entities_drbert(texte)
    toutes = [c["valeur"] for items in sections.values() for c in items]
    assert "ALLERGIES" not in toutes
    assert "TRAITEMENT EN COURS" not in toutes
    # … et les vraies entités restent, dans les bonnes rubriques :
    assert "Allergie à la pénicilline" in [
        c["valeur"] for c in sections["allergies"]
    ]
    # P7 : « 20 mg » suit « Oméprazole » dans le texte source → absorbé.
    assert "Oméprazole 20 mg" in [
        c["valeur"] for c in sections["traitements_long_cours"]
    ]


def test_acte_chirurgical_sous_antecedents(monkeypatch):
    """Acte chirurgical étiqueté « treatment » par CASM2 mais cité sous un
    titre d'antécédents → antecedents (test de fumée sur le vrai modèle)."""
    texte = "ANTÉCÉDENTS\nAppendicectomie en 1998.\n"
    _injecter(monkeypatch, [_ent("treatment", "Appendicectomie", texte, 0.88)])

    sections = extract_entities_drbert(texte)
    toutes = [(rub, c["valeur"]) for rub, items in sections.items() for c in items]
    assert ("antecedents", "Appendicectomie") in toutes
    assert not any(rub == "traitements_long_cours" for rub, _ in toutes)


def test_cure_courte_reclassee_points_vigilance(monkeypatch):
    """Un traitement de durée courte va en points de vigilance (règle de
    contexte ET reclassement du validateur — les deux étages sont d'accord)."""
    entites = [_ent("treatment", "amoxicilline", TEXTE, score=0.9)]
    _injecter(monkeypatch, entites)

    sections = extract_entities_drbert(TEXTE)
    toutes = [(rub, c["valeur"]) for rub, items in sections.items() for c in items]
    assert ("points_vigilance", "amoxicilline") in toutes


def test_dispatch_moteur_drbert(monkeypatch):
    """engine="drbert" → entités tracées DrBERT, rapport « drbert »."""
    _injecter(monkeypatch, [_ent("problem", "œsophagite chronique", TEXTE, 0.9)])
    entites, rapport = extract_entities_with_report(TEXTE, engine="drbert")

    assert rapport["moteur"] == NLP_ENGINE_DRBERT_CASM2
    assert rapport["statut"] == "drbert"
    assert entites
    assert all(e.moteur_nlp == NLP_ENGINE_DRBERT_CASM2 for e in entites)
    assert all(e.origine == "drbert" for e in entites)


def test_dispatch_alias_regles():
    """« regles » est un alias accepté du moteur de règles."""
    _, rapport = extract_entities_with_report(
        "ANTÉCÉDENTS : appendicectomie.", engine="regles"
    )
    assert rapport["statut"] == "regles"


def test_modele_absent_exception_explicite():
    """Modèle absent → exception claire (jamais de plantage silencieux)."""
    with pytest.raises(DrBERTIndisponible, match="DrBERT"):
        extraire_entites("Œsophagite chronique.")


def test_modele_absent_repli_trace_sans_plantage():
    """Le traitement ENTIER continue : repli règles, statut « modele_absent »."""
    entites, rapport = extract_entities_with_report(
        "ANTÉCÉDENTS : appendicectomie.\nALLERGIES : pénicilline.",
        engine="drbert",
    )
    assert rapport["statut"] == "modele_absent"
    assert rapport["moteur"] == NLP_ENGINE_RULES
    assert rapport["raison"]
    sections = {e.section for e in entites}
    assert "antecedents" in sections
    assert "allergies" in sections


def test_sortie_drbert_vide_complement_regles(monkeypatch):
    """Sortie DrBERT vide → complément par les règles, tracé dans le rapport."""
    _injecter(monkeypatch, [])
    entites, rapport = extract_entities_with_report(
        "ANTÉCÉDENTS : appendicectomie.", engine="drbert"
    )
    assert rapport["statut"] == "repli_regles"
    assert "Sortie DrBERT vide" in rapport["raison"]
    assert any(e.section == "antecedents" for e in entites)


@pytest.mark.parametrize(
    "env, attendu",
    [
        (None, "drbert"),
        ("drbert", "drbert"),
        ("llm", "llm"),
        ("regles", "rules"),
        ("rules", "rules"),
        ("REGLES", "rules"),
        ("nimporte", "drbert"),  # valeur invalide → défaut, sans plantage
    ],
)
def test_moteur_nlp_par_defaut(monkeypatch, env, attendu):
    monkeypatch.delenv("VSM_NLP_ENGINE", raising=False)
    if env is not None:
        monkeypatch.setenv("VSM_NLP_ENGINE", env)
    assert moteur_nlp_par_defaut() == attendu


# ---------------------------------------------------------------------------
# Vrai modèle local (lent) — ignoré si models/drbert/ est absent
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.skipif(
    not all((VRAI_DOSSIER / f).is_file() for f in dtx.FICHIERS_MODELE),
    reason="modèle DrBERT local absent (models/drbert/)",
)
def test_modele_reel_ancrage_et_filtres(monkeypatch):
    """Le VRAI modèle : offsets exacts, seuil, bords de mots, étiquettes."""
    monkeypatch.setenv("VSM_DRBERT_PATH", str(VRAI_DOSSIER))
    dtx.reinitialiser()
    try:
        texte = (
            "Le patient présente une œsophagite chronique traitée par "
            "oméprazole 20 mg."
        )
        entites = extraire_entites(texte)
        for ent in entites:
            assert texte[ent.debut : ent.fin] == ent.texte  # anti-hallucination
            assert ent.score >= 0.70
            assert dtx.bords_alignes(texte, ent.debut, ent.fin)
            assert ent.label in ("problem", "treatment", "test")
    finally:
        dtx.reinitialiser()
