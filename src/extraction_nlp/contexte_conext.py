"""N1 — Algorithme ConText (négation, expérienceur, modalité).

Correctif N1/MANGUE v9+ : le VSM ne conservait que le terme extrait et son
passage, jamais ce qui le QUALIFIAIT dans le document. Toute entité était
donc traitée comme un fait affirmé, concernant le patient, au présent —
d'où « mort subite » en antécédent personnel (alors que le document dit
« antécédents familiaux… chez un frère ») et « signe de malignité » en
pathologie active (alors que le compte rendu dit « Absence de signe de
malignité »).

Trois axes (ConText, extension de NegEx) :

===== HISTORIQUE DE LA MESURE (avant implémentation, doctrine du projet) =====
L'évaluation de ``medkit.text.context`` a d'abord été menée : medkit n'est
PAS une dépendance du projet (le DrBERT-CASM2 est chargé via transformers ;
« medkit/DrBERT-CASM2 » est le nom du modèle HuggingFace) et l'axe
EXPÉRIENCEUR y est absent de toute façon — c'est celui qui répond au cas
« mort subite ». Implémentation minimale (règles du correctif), corrigée
par la mesure sur les QUATRE dossiers :

- 118 entités sur 758 (16 %) portent un marqueur dans leur contexte
  gauche — la quasi-totalité est NIÉE dans le document mais AFFIRMÉE dans
  le VSM (le correctif parlait d'un « risque avéré » : il est mesuré).
- Piège 1 — DOUBLE NÉGATION : « IL n'est pas exclu qu'il se soit agi d'un
  abcès » AFFIRME l'abcès ; le marqueur « exclu » ne doit donc jamais
  suffire au rejet (les marqueurs du correctif type « éliminé/écarté/
  infirmé/exclu » ne sont que des qualifications, jamais des rejets).
- Piège 2 — PORTÉE : « sans anomalie décelée, et qui lui prescrit du
  CLAMOXYL » — la négation porte sur l'anomalie, pas sur CLAMOXYL : une
  virgule ou une conjonction (et/ou) ENTRE le marqueur et l'entité rompt
  la portée (c'est l'erreur classique des implémentations naïves de
  NegEx, signalée par le correctif).
- Piège 3 — ANAPHORE : « l'examen ne retrouve pas d'éléments pour
  expliquer cette symptomatologie » — la négation porte sur les éléments
  explicatifs ; le démonstratif/possessif immédiat (« cette », « son »…)
  signale une reprise d'un concept AFFIRMÉ plus tôt → ne pas rejeter.
- Piège 4 — NÉGATION SUR LA TECHNIQUE : « [la chirurgie] n'a pas été
  validée dans les cas d'obésité extrême » nie la validation, pas
  l'obésité : seules les négations FRANCHES (« pas de », « absence de »,
  « aucun », « sans ») rejettent ; « ne…pas » sans article est une
  simple nuance (précaution du correctif : en cas de doute, conserver et
  marquer plutôt que rejeter).

Arbitrage (mesuré) : négation franche > familial > hypothétique > nuance.
Une information niée ne doit jamais partir en facteurs de risque.
"""

import re

# --- Expérienceur : l'entité concerne un tiers, pas le patient -------------
# (règles du correctif N1, inchangées)
_RX_FAMILIAL = re.compile(
    r"\b(ant[ée]c[ée]dents?\s+familiaux|familiaux?|dans\s+la\s+famille|"
    r"h[ée]r[ée]ditaires?|son\s+p[èe]re|sa\s+m[èe]re|ses\s+parents|"
    r"son\s+fr[èe]re|sa\s+s[œoe]ur|c[ôo]t[ée]\s+paternel|c[ôo]t[ée]\s+maternel|"
    r"chez\s+le\s+p[èe]re|chez\s+la\s+m[èe]re)\b",
    re.IGNORECASE,
)

# --- Modalité : envisagé, redouté, prévenu — mais non constaté -------------
_RX_HYPOTHETIQUE = re.compile(
    r"\b(risques?\s+de|pr[ée]vention\s+de|[àa]\s+[ée]liminer|[àa]\s+rechercher|"
    r"en\s+cas\s+de|suspicion\s+de|[ée]ventuelle?|possible|probable|"
    r"[àa]\s+confirmer|d[ée]pistage\s+de|surveillance\s+de|"
    r"pr[ée]disposition|indication\s+de)\b",
    re.IGNORECASE,
)

# --- Négation FRANCHE : la seule qui justifie un rejet ----------------------
# « pas de/d' », « pas üe » (bruit d'OCR mesuré sur BANANE), « absence de/d' »,
# « aucun(e) », « sans ». Les apostrophes TYPOGRAPHIQUES de l'OCR (' U+2019,
# ' U+2018) sont couvertes comme la droite — « Il n'y a pas d'anomalie »
# (MANGUE, mesuré) doit matcher. Les négations longues du correctif (ne…pas,
# éliminé, écarté, infirmé, exclu, non retrouvé) sont délibérément ABSENTES
# de cette liste : la mesure montre qu'elles nient trop souvent autre chose
# que l'entité (pièges 1 et 4) — elles qualifient sans supprimer.
_APOS = "[''’‘]"
_RX_NEGATION_FRANCHE = re.compile(
    r"\b(?:pas\s+d" + _APOS + r"|pas\s+de|pas\s+[üu]e|absence\s+d" + _APOS +
    r"|absence\s+de|aucune?\s|sans\s)",
    re.IGNORECASE,
)

# --- Négation FAIBLE : mentionnée sans rejet --------------------------------
_RX_NEGATION_FAIBLE = re.compile(
    r"\b(?:n[ie]\s|n" + _APOS + r"\s*y?\s*a\s+pas|ne\s+pr[ée]sente\s+pas|"
    r"non\s+retrouv[ée]|[ée]limin[ée]|[ée]cart[ée]|infirm[ée]|exclu)",
    re.IGNORECASE,
)

# --- Portée et anaphore (pièges 2 et 3, mesurés) ---------------------------
# Une virgule ou une conjonction ET/OU entre le marqueur de négation et
# l'entité rompt la portée (« sans anomalie décelée, et … prescrit du
# CLAMOXYL »). « ni » ne rompt PAS : « pas de signe d'IVG ni de phlébite »
# nie bien les deux. « jusqu'à » (y compris l'apostrophe OCR ”) rompt aussi :
# « essayé SANS SUCCÈS jusqu'à ce qu'elle prenne du Rivotril » AFFIRME le
# Rivotril (BANANE, mesuré) — la négation est bornée dans le temps.
_RX_PORTEE_ROMPUE = re.compile(
    r"[,;]|\b(?:et|ou)\b|\bjusqu[''’”]?\s*[àa]\b",
    re.IGNORECASE,
)
# Démonstratif/possessif immédiat avant l'entité : anaphore — le concept a
# été affirmé plus tôt dans le document (« expliquer cette symptomatologie »,
# « réparer sa hernie ») → ne pas rejeter.
_RX_ANAPHORE = re.compile(
    r"\b(?:ce|cet|cette|son|sa|ses|leur|leurs)\s*$", re.IGNORECASE
)

# Séparateurs forts : la fenêtre ne franchit jamais une frontière de phrase.
_SEPARATEURS = re.compile(r"[.;:\n\r]|\s{4,}")


def contexte_gauche(texte: str, debut: int, largeur: int = 100) -> str:
    """Fenêtre précédant l'entité, bornée au dernier séparateur fort.

    Sans ce bornage, la négation d'une phrase précédente contaminerait
    l'entité suivante — « Pas de fièvre. Douleur thoracique. » ferait
    passer la douleur pour niée. C'est le faux positif classique de NegEx
    (précaution explicite du correctif N1).
    """
    if debut <= 0:
        return ""
    fenetre = texte[max(0, debut - largeur) : debut]
    coupes = list(_SEPARATEURS.finditer(fenetre))
    return fenetre[coupes[-1].end() :] if coupes else fenetre


def qualifier(texte: str, debut: int, largeur: int = 100) -> dict:
    """Renvoie les qualificatifs ConText de l'entité débutant à ``debut``.

    Drapeaux : ``nie_franche`` (rejet si portée directe et sans anaphore),
    ``nie_faible``, ``familial``, ``hypothetique`` ; ``contexte`` conserve
    une trace courte pour l'éditeur et l'audit.
    """
    ctx = contexte_gauche(texte, max(0, debut), largeur)
    nie_franche = False
    francs = list(_RX_NEGATION_FRANCHE.finditer(ctx))
    if francs:
        # Dernier marqueur : celui qui commande l'entité. La portée est
        # rompue si une virgule/conjonction s'intercale, ou si l'entité
        # est une anaphore (reprise d'un concept affirmé).
        entre = ctx[francs[-1].end() :]
        if not _RX_PORTEE_ROMPUE.search(entre) and not _RX_ANAPHORE.search(ctx):
            nie_franche = True
    return {
        "nie_franche": nie_franche,
        "nie_faible": bool(_RX_NEGATION_FAIBLE.search(ctx)),
        "familial": bool(_RX_FAMILIAL.search(ctx)),
        "hypothetique": bool(_RX_HYPOTHETIQUE.search(ctx)),
        "contexte": ctx.strip()[-60:],
    }


def arbitrer(qualif: dict) -> tuple[str, str]:
    """Verdict ConText : (verdict, mention).

    Verdicts : ``niee`` (rejet tracé), ``familial`` (→ facteurs de risque,
    mention « antécédent familial »), ``hypothetique`` (→ points de
    vigilance, mention « à confirmer »), ``nuance`` (mention seule,
    rubrique inchangée — précaution « conserver et marquer »), ``aucun``
    (comportement inchangé).

    Ordre mesuré : négation franche > familial > hypothétique > nuance.
    Une information niée ne part jamais en facteurs de risque ; une
    information familiale n'est jamais noyée dans une modalité.
    """
    ctx = (qualif.get("contexte") or "").strip()
    if qualif.get("nie_franche"):
        return "niee", f"nié dans le document : « {ctx} »"
    if qualif.get("familial"):
        return (
            "familial",
            f"antécédent familial — contexte : « {ctx} »",
        )
    if qualif.get("hypothetique"):
        return (
            "hypothetique",
            f"à confirmer (mention hypothétique) — contexte : « {ctx} »",
        )
    if qualif.get("nie_faible"):
        return "nuance", f"mention nuancée dans le document : « {ctx} »"
    return "aucun", ""
