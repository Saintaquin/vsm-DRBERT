"""Affectation des entités DrBERT aux 7 rubriques du VSM (étape 2).

Les trois étiquettes de CASM2 (problem / treatment / test) ne couvrent pas les
7 rubriques du VSM : des règles de contexte tranchent. Elles sont fiables ici
parce qu'elles s'appliquent à une entité DÉJÀ reconnue comme cliniquement
pertinente par l'encodeur (et filtrée : bords de mots, seuil 0,70) — jamais à
du texte brut. C'est la différence avec le moteur de règles historique, qui
capture « docteur » parce qu'il travaille à l'aveugle.

Le contexte d'une entité = jusqu'à 120 caractères qui la précèdent (borne :
le dernier TITRE DE RUBRIQUE rencontré au-dessus — le contexte ne déborde
JAMAIS sur la rubrique précédente), PLUS l'entité elle-même (une entité
« Tabagisme actif » est son propre contexte de facteur de risque ; « vaccin
antitétanique » son propre contexte de vaccination).

Table (décision de conception, étape 2 du plan, AJUSTÉE au comportement
RÉEL du modèle observé sur le premier test de fumée : CASM2 étiquette les
mention d'allergie comme « problem » et les actes chirurgicaux comme
« treatment ») :

| Entité      | Contexte                                        | Rubrique              |
|-------------|-------------------------------------------------|-----------------------|
| treatment   | « allergi », « intoléran »                      | allergies             |
| treatment   | « vaccin »                                      | vaccinations          |
| treatment   | ACTE chirurgical (liste/suffixes, P3 — l'entité)| antecedents           |
| treatment   | titre antécédents / « antécédent », « chirurgical », « opéré », « en 2003 » | antecedents |
| treatment   | durée courte explicite (« cure de 7 jours »)    | points_vigilance      |
| treatment   | sinon                                           | traitements_long_cours|
| problem     | « allergi », « intoléran » (mention d'allergie) | allergies             |
| problem     | titre antécédents / « antécédent », « chirurgical », « opéré », « en 2003 » | antecedents |
| problem     | LISTE FERMÉE d'expositions sur l'ENTITÉ (P5 : tabac, alcool, obésité, ménopause…) ou titre « Facteurs de risque » | facteurs_risque |
| problem     | sinon                                           | pathologies_actives   |
| test        | (toujours)                                      | points_vigilance      |

L'allergie est vérifiée AVANT l'antécédent dans les deux branches : c'est une
donnée de sécurité critique du VSM (« antécédent d'allergie à la pénicilline »
doit finir dans allergies, pas noyé dans les antécédents).

Évolutivité : le passage à 9 étiquettes (fine-tune sur les rubriques) ne
demandera qu'une table plus courte — les fonctions restent identiques.
"""

from __future__ import annotations

import re

from .drbert_extractor import Entite
from .filtres_vsm import (
    TITRES_RX,
    est_facteur_risque,
    est_un_acte,
    est_un_support_therapeutique,
)

# Fenêtre de contexte AVANT l'entité (caractères) — décision de l'étape 2.
CONTEXTE_AVANT = 120

# Titres de rubriques : maintenus dans filtres_vsm (la zone d'en-tête P6
# s'y arrête aussi) — réimportés ici pour lisibilité.
_TITRES_RX = TITRES_RX

# Contextes (recherchés sans casse dans « 120 caractères avant + entité »).
_RX_ALLERGIE = re.compile(r"allergi|intol[ée]ran", re.IGNORECASE)
_RX_VACCIN = re.compile(r"vaccin", re.IGNORECASE)
# Durée courte explicite — mêmes motif que le validateur aval (cohérence des
# deux étages, duplication assumée pour éviter une dépendance circulaire).
_RX_DUREE_COURTE = re.compile(
    r"\b(cure|pendant|pour)(?:\s+de)?\s+\d+\s*(jours?|semaines?)|"
    r"\b\d+\s*(jours|semaines)\s+de\s+traitement",
    re.IGNORECASE,
)
_RX_ANTECEDENT = re.compile(
    r"ant[ée]c[ée]dent|chirurgical|op[ée]r[ée]|notion\s+de"
    r"|\ben\s+(?:19|20)\d{2}\b",
    re.IGNORECASE,
)
# P5 : les facteurs de risque sont une LISTE FERMÉE sur le TEXTE DE
# L'ENTITÉ (filtres_vsm.est_facteur_risque) — l'ancienne règle CONTEXTUELLE
# (consommation, poids, sport…) routait des diagnostics entiers vers la
# rubrique (sténose urétrale, Trichomonas…) : une seule entrée juste sur
# neuf dans les dossiers réels. Un facteur de risque est une exposition, le
# vocabulaire en est très restreint.

# M4/MANGUE v9 : une CLASSE médicamenteuse que le modèle étiquette
# « problem » (DrBERT voyait « giucoconticoids » — glucocorticoïdes — comme
# une pathologie) est un TRAITEMENT, pas un diagnostic. Liste fermée et
# courte, tolérante aux fautes d'OCR (« conticoid » dans « giucoconticoids ») ;
# l'allergie passe AVANT (une « allergie aux corticoïdes » reste une
# allergie, donnée de sécurité du VSM).
_RX_CLASSE_MEDICAMENT = re.compile(
    r"cortico[ïi]d|contico[ïi]d|corticoth[ée]rap|cortisone|prednisone|"
    r"prednisolone|m[ée]thylprednisolone",
    re.IGNORECASE,
)


def rubrique_de(
    label: str, contexte: str = "", titre: str | None = None, entite: str = ""
) -> str:
    """Étiquette CASM2 + contexte + entité → rubrique VSM (pure, testable).

    ``contexte`` : texte précédant l'entité (≈120 caractères, borné au titre
    de rubrique courant) + l'entité elle-même, casse quelconque.
    ``titre`` : dernière rubrique titrée rencontrée au-dessus, ou None.
    ``entite`` : le texte de l'entité elle-même — les règles P3 (acte
    chirurgical) et P5 (facteur de risque, liste fermée) portent sur
    l'ENTITÉ, pas sur son contexte.
    Ordre de priorité (sécurité d'abord) : allergies > vaccinations > acte
    chirurgical > antécédents > durée courte pour les traitements ;
    allergies > antécédents > facteurs de risque pour les problèmes.
    """
    bas = contexte or ""
    if label == "treatment":
        if _RX_ALLERGIE.search(bas):
            return "allergies"
        if _RX_VACCIN.search(bas):
            return "vaccinations"
        # P3/C3 (DRAGON v7) : les SUPPORTS thérapeutiques (transfusion,
        # oxygénothérapie, support inotrope, dialyse, épuration) ne sont
        # ni des médicaments au long cours ni des antécédents chirurgicaux
        # — points de vigilance.
        if entite and est_un_support_therapeutique(entite):
            return "points_vigilance"
        # P3 : CASM2 étiquette les actes chirurgicaux « treatment » (exact
        # de son point de vue) — c'est ici qu'on les envoie aux antécédents :
        # « excision du pertuis cutané » n'est pas un traitement en cours.
        if entite and est_un_acte(entite):
            return "antecedents"
        if titre == "antecedents" or _RX_ANTECEDENT.search(bas):
            # Acte chirurgical ou traitement cité dans l'historique : c'est un
            # antécédent, pas un traitement en cours.
            return "antecedents"
        if _RX_DUREE_COURTE.search(bas):
            return "points_vigilance"
        return "traitements_long_cours"
    if label == "problem":
        if _RX_ALLERGIE.search(bas):
            # CASM2 étiquette les mentions d'allergie « problem » : la sécurité
            # du VSM exige qu'elles rejoignent la rubrique allergies.
            return "allergies"
        if titre == "antecedents" or _RX_ANTECEDENT.search(bas):
            return "antecedents"
        # M4/MANGUE : classe médicamenteuse étiquetée « problem » →
        # traitement (l'allergie a déjà été traitée ci-dessus).
        if entite and _RX_CLASSE_MEDICAMENT.search(entite):
            return "traitements_long_cours"
        # P5 : liste fermée sur l'entité (exposition/comportement), ou titre
        # de rubrique explicite « FACTEURS DE RISQUE ». Tout le reste —
        # même suggéré par le contexte — est un diagnostic → pathologies.
        if titre == "facteurs_risque" or (entite and est_facteur_risque(entite)):
            return "facteurs_risque"
        return "pathologies_actives"
    if label == "test":
        return "points_vigilance"
    # Étiquette inconnue (futur checkpoint à 9 étiquettes non encore branché) :
    # défaut prudent, documenté — le validateur aval garde son veto.
    return "pathologies_actives"


def affecter_rubriques(entites: list[Entite], texte: str) -> list[tuple[Entite, str]]:
    """Affecte chaque entité à sa rubrique VSM selon son contexte dans le texte.

    Retourne [(entite, rubrique)] dans l'ordre du document. Le titre courant
    est le dernier TITRE DE RUBRIQUE rencontré STRICTEMENT AU-DESSUS de
    l'entité ; le contexte ne remonte JAMAIS au-delà de ce titre — sinon
    « Allergies : … » déteindrait sur le traitement de la rubrique suivante.
    """
    titres = [(m.start(), m.lastgroup) for m in _TITRES_RX.finditer(texte)]
    sorties: list[tuple[Entite, str]] = []
    for ent in sorted(entites, key=lambda e: (e.debut, e.fin)):
        titre: str | None = None
        naissance = 0
        for position, nom in titres:
            if position <= ent.debut:
                titre = nom
                naissance = position  # borne le contexte au titre courant
            else:
                break
        contexte = texte[max(naissance, ent.debut - CONTEXTE_AVANT) : ent.fin]
        sorties.append((ent, rubrique_de(ent.label, contexte, titre, ent.texte)))
    return sorties
