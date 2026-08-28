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
| treatment   | titre antécédents / « antécédent », « chirurgical », « opéré », « en 2003 » | antecedents |
| treatment   | durée courte explicite (« cure de 7 jours »)    | points_vigilance      |
| treatment   | sinon                                           | traitements_long_cours|
| problem     | « allergi », « intoléran » (mention d'allergie) | allergies             |
| problem     | titre antécédents / « antécédent », « chirurgical », « opéré », « en 2003 » | antecedents |
| problem     | consommation, poids, activité physique          | facteurs_risque       |
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

# Fenêtre de contexte AVANT l'entité (caractères) — décision de l'étape 2.
CONTEXTE_AVANT = 120

# Titres de rubriques (même esprit que SECTION_HEADERS d'entity_extractor ;
# maintenu séparé pour garder ce module sans dépendance circulaire).
_TITRES_RX = re.compile(
    r"^\s*("
    r"(?P<antecedents>ANT[ÉE]C[ÉE]DENTS?|ATCD)"
    r"|(?P<allergies>ALLERGIES?)"
    r"|(?P<traitements_long_cours>TRAITEMENTS?(?:\s+(?:EN\s+COURS|LONG\s+COURS"
    r"|DE\s+SORTIE|DE\s+FOND))?|ORDONNANCE)"
    r"|(?P<vaccinations>VACCINATIONS?|VACCINS?)"
    r"|(?P<pathologies_actives>PATHOLOGIES?\s+ACTIVES?|MOTIF|DIAGNOSTICS?)"
    r"|(?P<facteurs_risque>FACTEURS?\s+DE\s+RISQUE)"
    r"|(?P<points_vigilance>POINTS?\s+DE\s+VIGILANCE|PR[ÉE]CAUTIONS?)"
    r")\s*:?",
    re.IGNORECASE | re.MULTILINE,
)

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
_RX_FACTEUR_RISQUE = re.compile(
    r"tabac|tabag|alcool|consommation|poids|ob[ée]sit|surpoids|\bimc\b"
    r"|activit[ée]\s+physique|s[ée]dentai|sport|cigarette",
    re.IGNORECASE,
)


def rubrique_de(label: str, contexte: str = "", titre: str | None = None) -> str:
    """Étiquette CASM2 + contexte → rubrique VSM (fonction pure, testable).

    ``contexte`` : texte précédant l'entité (≈120 caractères, borné au titre
    de rubrique courant) + l'entité elle-même, casse quelconque.
    ``titre`` : dernière rubrique titrée rencontrée au-dessus, ou None.
    Ordre de priorité (sécurité d'abord) : allergies > vaccinations >
    antécédents > durée courte pour les traitements ; allergies > antécédents
    > facteurs de risque pour les problèmes.
    """
    bas = contexte or ""
    if label == "treatment":
        if _RX_ALLERGIE.search(bas):
            return "allergies"
        if _RX_VACCIN.search(bas):
            return "vaccinations"
        if titre == "antecedents" or _RX_ANTECEDENT.search(bas):
            # Acte chirurgical ou traitement cité dans l'historique : c'est un
            # antécédent, pas un traitement en cours (CASM2 étiquette les actes
            # « treatment » — le contexte décide).
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
        if titre == "facteurs_risque" or _RX_FACTEUR_RISQUE.search(bas):
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
        sorties.append((ent, rubrique_de(ent.label, contexte, titre)))
    return sorties
