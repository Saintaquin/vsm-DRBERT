"""Tests N1 — algorithme ConText (négation, expérienceur, modalité).

Cas issus de la MESURE sur les quatre dossiers réels (doctrine du projet :
aucune règle sans exemple mesuré) :
- « mort subite » MANGUE p107 : « antécédents familiaux… chez un frère » ;
- « Absence de signe de malignité » MANGUE p100, DRAGON p096/p115, BANANE ;
- « sans déficit neuro-vasculaire » BANANE p030 ;
- « IL n'est pas exclu qu'il se soit agi d'un abcès » BANANE (double
  négation = affirmation) ;
- « sans anomalie décelée, et qui lui prescrit du CLAMOXYL » ABRICOT
  (portée rompue par la virgule + conjonction) ;
- « expliquer cette symptomatologie » DRAGON p004 (anaphore) ;
- « n'a pas été validée dans les cas d'obésité extrême » MANGUE (négation
  sur la technique, pas sur la pathologie).
"""

from src.extraction_nlp.contexte_conext import (
    arbitrer,
    contexte_gauche,
    qualifier,
)
from src.extraction_nlp.entity_extractor import (
    ExtractedEntity,
    _valider_raison,
)


def _item(valeur: str, offset: int, score: float = 0.95) -> dict:
    return {
        "valeur": valeur,
        "passage": valeur,
        "offset_debut": offset,
        "offset_fin": offset + len(valeur),
        "score": score,
    }


# ---------------------------------------------------------------------------
# contexte_gauche : bornage au séparateur fort (le faux positif classique
# de NegEx — « Pas de fièvre. Douleur thoracique. » doit garder la douleur
# affirmée).
# ---------------------------------------------------------------------------


def test_contexte_borne_au_separateur():
    texte = "Pas de fièvre. Douleur thoracique."
    # « Douleur » débute après le point : sa fenêtre gauche est VIDE.
    assert contexte_gauche(texte, 14) == ""


def test_contexte_fenetre_100():
    texte = "x" * 250 + " ; antécédents familiaux de mort subite"
    ctx = contexte_gauche(texte, len(texte))
    assert ctx == " antécédents familiaux de mort subite"
    assert len(contexte_gauche(texte[:0] + texte, 100)) <= 100


# ---------------------------------------------------------------------------
# qualifier / arbitrer — les sept situations mesurées.
# ---------------------------------------------------------------------------


def test_negation_franche():
    q = qualifier("Absence de signe de malignité sur les prélèvements.", 22)
    assert q["nie_franche"] is True
    assert arbitrer(q)[0] == "niee"


def test_negation_apostrophe_typographique():
    """MANGUE (mesuré) : « Il n'y a pas d'anomalie de la morphologie » avec
    apostrophes TYPOGRAPHIQUES (U+2019) — l'OCR français les utilise
    massivement. Sans couverture, la négation est invisible."""
    for texte in (
        "Il n’y a pas d’anomalie de la morphologie.",
        "Il n'y a pas d'anomalie de la morphologie.",
    ):
        q = qualifier(texte, texte.index("anomalie"))
        assert q["nie_franche"] is True, texte
        assert arbitrer(q)[0] == "niee"


def test_negation_sans():
    q = qualifier("fracture fermée, sans déficit neuro-vasculaire associé.", 27)
    assert arbitrer(q)[0] == "niee"


def test_negation_ocr_pas_ue():
    """BANANE p100 : « Pas üe malignité » — bruit d'OCR de « pas de »."""
    q = qualifier("Pas üe malignité.", 7)
    assert arbitrer(q)[0] == "niee"


def test_experienceur_familial():
    texte = "Il a des antécédents familiaux avec notion d'un mort subite"
    q = qualifier(texte, texte.index("mort subite"))
    assert q["familial"] is True
    verdict, mention = arbitrer(q)
    assert verdict == "familial"
    assert "antécédent familial" in mention


def test_modalite_hypothetique():
    texte = "En cas de suspicion de carence martiale, compléter le bilan."
    q = qualifier(texte, texte.index("carence martiale"))
    verdict, mention = arbitrer(q)
    assert verdict == "hypothetique"
    assert "à confirmer" in mention


def test_double_negation_est_une_affirmation():
    """« IL n'est pas exclu qu'il se soit agi d'un abcès » AFFIRME l'abcès :
    jamais de rejet — la mention « exclu » n'est qu'une nuance."""
    texte = "IL n'est pas exclu qu'il se soit agi d'un abcès sacro-coccygien."
    q = qualifier(texte, texte.index("abcès"))
    verdict, _ = arbitrer(q)
    assert verdict in ("nuance", "aucun")
    assert verdict != "niee"


def test_portee_rompue_par_virgule():
    """« sans anomalie décelée, et qui lui prescrit du CLAMOXYL » : la
    négation porte sur l'anomalie — CLAMOXYL reste affirmé."""
    texte = "sans anomalie décelée, et qui lui prescrit du CLAMOXYL."
    q = qualifier(texte, texte.index("CLAMOXYL"))
    assert q["nie_franche"] is False


def test_portee_rompue_par_jusqua():
    """BANANE (mesuré) : « essayé sans succès jusqu'à ce qu'elle prenne du
    Rivotril » — la négation est BORNÉE par « jusqu'à » : le Rivotril est
    bien pris (20 gouttes). Variante OCR « jusqu” à qu'elle » incluse."""
    for texte in (
        "essayé sans succès jusqu'à ce qu'elle prenne du Rivotril",
        "essayé sans succès jusqu” à qu’elle prenne du Rivotril",
    ):
        q = qualifier(texte, texte.index("Rivotril"))
        assert q["nie_franche"] is False, texte


def test_anaphore_demonstrative():
    """« ne retrouve pas d'éléments pour expliquer cette symptomatologie » :
    la négation porte sur les éléments explicatifs — la symptomatologie
    existe (elle est reprise par « cette »)."""
    texte = (
        "L'examen ne retrouve pas d'élements pour expliquer cette "
        "symptomatologie neurologique."
    )
    q = qualifier(texte, texte.index("symptomatologie"))
    assert q["nie_franche"] is False


def test_negation_sur_la_technique_pas_la_pathologie():
    """« [La chirurgie] n'a pas été validée dans les cas d'obésité
    extrême » : l'obésité extrême est un fait du patient (motif de la
    consultation) — « ne…pas » sans article n'est qu'une nuance."""
    texte = "Elle n'a pas été validée dans les cas d'obésité extrême."
    q = qualifier(texte, texte.index("obésité"))
    verdict, _ = arbitrer(q)
    assert verdict in ("nuance", "hypothetique", "aucun")
    assert verdict != "niee"


def test_arbitrage_negation_avant_familial():
    """« pas d'antécédent familial de cancer » : l'information NIÉE ne doit
    jamais partir en facteurs de risque."""
    q = {
        "nie_franche": True,
        "nie_faible": False,
        "familial": True,
        "hypothetique": False,
        "contexte": "pas d'antécédent familial de",
    }
    assert arbitrer(q)[0] == "niee"


def test_entite_affirmee_sans_marqueur():
    texte = "Le patient présente une insuffisance cardiaque droite."
    q = qualifier(texte, texte.index("insuffisance"))
    assert arbitrer(q)[0] == "aucun"


# ---------------------------------------------------------------------------
# _valider_raison : rejet tracé + reroutage + mention portée.
# ---------------------------------------------------------------------------


def test_validateur_rejete_entite_niee():
    texte = "Absence de signe de malignité sur les prélèvements examinés."
    ok, raison = _valider_raison(
        _item("signe de malignité", texte.index("signe de malignité")),
        texte,
        "pathologies_actives",
    )
    assert ok is None
    assert raison[0] == "N1_entite_niee"
    assert "malignité" in raison[1] or "Absence" in raison[1]


def test_validateur_mort_subite_en_facteurs_de_risque():
    texte = (
        "Il a des antécédents familiaux avec notion d'un mort subite "
        "chez un frère à l'âge de 40 ans."
    )
    ok, _ = _valider_raison(
        _item("mort subite", texte.index("mort subite")),
        texte,
        "antecedents",
    )
    assert ok is not None
    assert ok["_reclasser"] == "facteurs_risque"
    assert "antécédent familial" in ok["mention_contexte"]


def test_validateur_hypothetique_en_points_de_vigilance():
    texte = "En cas de suspicion de carence martiale, compléter le bilan."
    ok, _ = _valider_raison(
        _item("carence martiale", texte.index("carence martiale")),
        texte,
        "pathologies_actives",
    )
    assert ok is not None
    assert ok["_reclasser"] == "points_vigilance"
    assert "à confirmer" in ok["mention_contexte"]


def test_validateur_affirme_sans_qualificatif():
    """Non-régression majeure (checklist N1) : un diagnostic affirmé ne
    porte NI rejet NI reroutage NI mention."""
    texte = "Le patient présente une insuffisance cardiaque droite chronique."
    ok, raison = _valider_raison(
        _item(
            "insuffisance cardiaque droite",
            texte.index("insuffisance cardiaque"),
        ),
        texte,
        "pathologies_actives",
    )
    assert ok is not None
    assert "_reclasser" not in ok
    assert "mention_contexte" not in ok
    assert raison is None


def test_validateur_allergie_niee_rejetee():
    """« pas d'allergie aux pénicillines » → l'entité disparaît : la
    rubrique Allergies reste vide (non-régression checklist N1). La valeur
    nue « allergie » est déjà tuée en amont (validateur_entete_seule) —
    le ConText doit attraper les allergies QUALIFIÉES niées."""
    texte = "Pas d'allergie aux pénicillines connue."
    ok, raison = _valider_raison(
        _item("allergie aux pénicillines", texte.index("allergie aux")),
        texte,
        "allergies",
    )
    assert ok is None
    assert raison[0] == "N1_entite_niee"


# ---------------------------------------------------------------------------
# to_champ : la mention traverse la frontière extraction → VSM.
# ---------------------------------------------------------------------------


def test_to_champ_porte_la_mention():
    ent = ExtractedEntity(
        valeur="mort subite",
        section="facteurs_risque",
        confiance=0.95,
        passage="mort subite",
        offset_debut=48,
        offset_fin=59,
        mention_contexte="antécédent familial — contexte : « … »",
    )
    champ = ent.to_champ()
    assert champ["mention_contexte"].startswith("antécédent familial")


def test_to_champ_sans_mention_inchange():
    ent = ExtractedEntity(
        valeur="pacemaker",
        section="antecedents",
        confiance=0.99,
        passage="pacemaker",
        offset_debut=0,
        offset_fin=9,
    )
    assert "mention_contexte" not in ent.to_champ()
