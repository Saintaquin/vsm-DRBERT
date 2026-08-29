"""Filtres VSM post-analyse de deux dossiers réels (correctifs P1-P7).

Analyse de dossiers réels de 75 pages / 20 ans produits par DrBERT : le
moteur extrait correctement, mais le CLASSEMENT et le VOLUME posent problème.
Ce module regroupe les filtres purs (testables, sans dépendance aux autres
modules d'extraction) :

- P1 : pages de laboratoire (antibiogramme) et fiches de référence écartées
  AVANT l'affectation aux rubriques — 40 antibiotiques d'un antibiogramme
  présentés comme « traitements au long cours » sont un risque clinique ;
- P2 : déduplication sémantique (forme → similarité ; la fusion par code
  CIM-10/ATC est désactivée en attendant l'audit du normalisateur) — un
  dossier de 20 ans décrit la même pathologie à chaque compte rendu avec les
  fautes d'OCR propres à chaque page ;
- P3 : actes chirurgicaux (CASM2 les étiquette « treatment ») → antécédents ;
- P4 : termes trop génériques isolés (« douleur » sans qualificatif) ;
- P5 : facteurs de risque par LISTE FERMÉE (une exposition, pas un
  diagnostic — le vocabulaire en est très restreint) ;
- P6 : en-tête de cabinet répété sur toutes les pages (« Consultations
  Maladies du Foie… », papier à lettres) ;
- P7 : posologies accolées aux traitements (« OGAST 1 gél/j » au lieu
  d'« OGAST ») — l'empan s'étend dans le TEXTE SOURCE uniquement, la
  garantie anti-hallucination tient.

Les frontières de pages sont reconstruites par longueurs cumulées du join
« \\n\\n » : l'anonymisation s'applique au texte entier ET par page avec des
numéros de tokens qui peuvent diverger légèrement — l'approximation (quelques
caractères par page) est sans effet sur ces filtres ; en cas d'écart majeur
(texte non reconstruisable), la carte est INVALIDÉE et les filtres par page
se désactivent proprement (dégradation sûre, jamais de plantage).
"""

from __future__ import annotations

import logging
import re
import unicodedata

_log = logging.getLogger("vsm")

# ---------------------------------------------------------------------------
# Normalisation commune (accents, casse, espaces) — comparaison tolérante.
# Dupliquée volontairement : ce module ne doit importer AUCUN module
# d'extraction (rubriques.py nous importe — pas de dépendance circulaire).
# ---------------------------------------------------------------------------


def normaliser(texte: str) -> str:
    """Minuscules sans accents, espaces unique, sans ponctuation parasite."""
    sans_accent = "".join(
        c
        for c in unicodedata.normalize("NFD", texte or "")
        if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"\s+", " ", sans_accent).strip().lower()


# ---------------------------------------------------------------------------
# Titres de rubriques (déplacés depuis rubriques.py : la zone d'en-tête P6
# s'arrête au premier titre reconnu, et rubriques.py importe ce module).
# ---------------------------------------------------------------------------

TITRES_RX = re.compile(
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


# ---------------------------------------------------------------------------
# Journal des rejets — chaque entité écartée est TRACÉE avec sa règle
# ---------------------------------------------------------------------------


def tracer_rejet(
    journal: list | None,
    valeur: str,
    score: float,
    regle: str,
    detail: str = "",
    offset_debut: int | None = None,
    page: int | None = None,
) -> dict | None:
    """Journalise un rejet d'entité (observabilité — la demande prioritaire).

    Historique : le validateur non branché pendant trois itérations, le repli
    global sur les règles invisible dans les logs, une pathologie disparue
    sans trace — à chaque fois le problème n'était pas le code mais
    l'ABSENCE D'OBSERVABILITÉ. Chaque entité écartée par un filtre (étape 0,
    P1, validateur, P4, P6) produit une entrée structurée dans le rapport NLP
    (``rejets``) ; ``finaliser_rejets`` résout les pages puis émet UNE ligne
    de journal par rejet, au format fixe :

        rejet | page=41 | «maladie rénale chronique» | score=0.90 | regle=P6 | detail=…

    Les valeurs proviennent du texte ANONYMISÉ (la pseudonymisation précède
    l'extraction) : aucune PII ne transite. ``journal=None`` → no-op (les
    appels sans rapport, ex. règles, ne changent pas de comportement).
    """
    if journal is None:
        return None
    entree = {
        "page": page,
        "valeur": valeur,
        "score": round(float(score or 0.0), 3),
        "regle": regle,
        "detail": detail,
    }
    if offset_debut is not None:
        entree["offset_debut"] = offset_debut
    journal.append(entree)
    return entree


def ligne_rejet(entree: dict) -> str:
    """Ligne de journal au format fixe (une par rejet, greppable)."""
    page = entree.get("page")
    return (
        f"rejet | page={page if page is not None else '?'} "
        f"| «{entree.get('valeur', '')}» "
        f"| score={float(entree.get('score', 0.0)):.2f} "
        f"| regle={entree.get('regle', '?')} "
        f"| detail={entree.get('detail') or '—'}"
    )


def finaliser_rejets(journal: list, carte: list[dict]) -> list:
    """Résout les pages des rejets (offset → page) puis trace chaque ligne.

    Les rejets précoces (filtres d'étape 0, validateur) connaissent leur
    offset mais pas leur page — la carte est construite en amont du pipeline,
    pas de l'extracteur. Appelé UNE fois par run_pipeline, quand la carte est
    disponible : muter les entrées (page) puis journaliser.
    """
    for entree in journal:
        if entree.get("page") is None and "offset_debut" in entree:
            entree["page"] = page_de(carte, entree["offset_debut"])
        _log.info(ligne_rejet(entree))
    return journal


# ---------------------------------------------------------------------------
# Carte des pages : frontières dans le texte joint (join "\n\n")
# ---------------------------------------------------------------------------

# Tolérance de reconstruction : l'anonymisation par page peut diverger
# légèrement du texte global (numéros de tokens), quelques caractères par
# page. Au-delà (texte non reconstruisable : appel direct sans pages, tests),
# la carte est invalidée et les filtres par page se désactivent.
_TOLERANCE_CARTE = 0.02  # 2 % d'écart relatif, plancher 40 caractères


def carte_pages(texte: str, pages_ocr: list[dict] | None) -> list[dict]:
    """Frontières des pages dans le texte joint : [{page, debut, fin, texte}].

    La reconstruction est APPROXIMATIVE (quelques caractères près si les
    tokens de pseudonymisation divergent) : suffisant pour savoir à quelle
    page appartient une entité — jamais pour découper un passage.
    """
    if not pages_ocr or not texte:
        return []
    carte: list[dict] = []
    curseur = 0
    for i, p in enumerate(pages_ocr):
        t = p.get("text", "")
        carte.append(
            {
                "page": p.get("page", i + 1),
                "debut": curseur,
                "fin": curseur + len(t),
                "texte": t,
            }
        )
        curseur += len(t) + 2  # séparateur "\n\n"
    attendu = curseur - 2
    ecart = abs(attendu - len(texte))
    if ecart > max(40, int(_TOLERANCE_CARTE * len(texte))):
        return []  # texte non reconstruisable → filtres par page désactivés
    return carte


def page_de(carte: list[dict], offset: int) -> int | None:
    """Numéro de la page contenant l'offset, ou None (carte vide/hors page)."""
    for p in carte:
        if p["debut"] <= offset < p["fin"]:
            return p["page"]
    return None


# ---------------------------------------------------------------------------
# P1 — Pages non prescriptives (antibiogramme, fiche de référence)
# ---------------------------------------------------------------------------

# Vocabulaire d'antibiogramme : la liste des molécules TESTÉES contre une
# souche (S/I/R, CMI…) n'est pas une prescription. Vocabulaire de fiche de
# référence : notice, valeurs de référence — pas les diagnostics du patient.
_RX_ANTIBIOGRAMME = re.compile(
    r"antibiogramme|\bc\.?m\.?i\.?\b|sensibilit[ée]\s+aux|souche|"
    r"\bS\s*/\s*I\s*/\s*R\b|r[ée]sistant|interm[ée]diaire|sensible\b",
    re.IGNORECASE,
)
_RX_REFERENCE = re.compile(
    r"valeurs?\s+de\s+r[ée]f[ée]rence|not[ie]ce|fiche\s+technique|"
    r"pour\s+information|rappel\s+[ée]pid[ée]miologique",
    re.IGNORECASE,
)

# Une ordonnance réelle dépasse rarement 10 médicaments ; 40 molécules sur
# une page signalent un tableau de laboratoire.
SEUIL_TRAITEMENTS_PAR_PAGE = 10


def page_non_prescriptive(texte_page: str, entites_page: list) -> str | None:
    """Motif d'exclusion de la page, ou None si elle est clinique.

    Deux signaux, indépendants et cumulables :
    - vocabulaire d'antibiogramme ou de fiche de référence ;
    - densité anormale de traitements (entités d'étiquette « treatment »).
    """
    if _RX_ANTIBIOGRAMME.search(texte_page or ""):
        return "antibiogramme"
    if _RX_REFERENCE.search(texte_page or ""):
        return "reference"
    n_traitements = sum(1 for e in entites_page if getattr(e, "label", "") == "treatment")
    if n_traitements > SEUIL_TRAITEMENTS_PAR_PAGE:
        return f"densite ({n_traitements} traitements sur la page)"
    return None


def pages_non_prescriptives(entites: list, carte: list[dict]) -> list[dict]:
    """Pages à écarter AVANT l'affectation aux rubriques (P1).

    Retourne la liste des rejets JOURNALISABLES : un rejet en masse doit
    être auditable — {page, motif, entites_supprimees}.
    """
    if not carte:
        return []
    rejetees: list[dict] = []
    for p in carte:
        dans_page = [
            e for e in entites if p["debut"] <= e.debut < p["fin"]
        ]
        motif = page_non_prescriptive(p.get("texte", ""), dans_page)
        if motif:
            rejetees.append(
                {
                    "page": p.get("page"),
                    "motif": motif,
                    "entites_supprimees": len(dans_page),
                }
            )
    return rejetees


def entites_hors_pages_rejetees(entites: list, carte: list[dict]) -> list:
    """Entités situées HORS des pages non prescriptives (P1, applicatif)."""
    if not carte:
        return list(entites)
    rejetees = {
        p["page"] for p in carte if page_non_prescriptive(
            p.get("texte", ""),
            [e for e in entites if p["debut"] <= e.debut < p["fin"]],
        )
    }
    return [
        e
        for e in entites
        if not any(
            p["debut"] <= e.debut < p["fin"]
            for p in carte
            if p["page"] in rejetees
        )
    ]


# ---------------------------------------------------------------------------
# P3 — Actes chirurgicaux (antécédents, pas traitements)
# ---------------------------------------------------------------------------

# Mots pleins (recherchés comme sous-chaînes de la forme normalisée) et
# suffixes (« -ectomie » attrape cholécystectomie, thyroidectomie…). En
# sous-chaîne (et non endswith) : « cholécystectomie rétrograde » doit être
# reconnue elle aussi.
_ACTES_MOTS = (
    "excision", "exerese", "resection", "curetage", "cure de", "osteosynthese",
    "suture", "drainage", "drain ", "reduction", "immobilisation", "attelle",
    "platre", "reeducation", "kinesitherapie", "infiltration", "anesthesie",
    "intervention", "geste chirurgical", "lavage", "ablation", "biopsie",
    "ponction", "pose de", "pose d'",
)
_ACTES_SUFFIXES = ("ectomie", "otomie", "oplastie", "oscopie", "orraphie")


def est_un_acte(valeur: str) -> bool:
    """Distingue un acte (antécédent chirurgical) d'un médicament (P3)."""
    bas = normaliser(valeur)
    if not bas:
        return False
    if any(suf in bas for suf in _ACTES_SUFFIXES):
        return True
    return any(mot in bas for mot in _ACTES_MOTS)


# ---------------------------------------------------------------------------
# P4 — Termes trop génériques isolés
# ---------------------------------------------------------------------------

# Un mot clinique sans qualificatif n'apporte rien à un VSM : « douleur »
# seule ne dit ni où, ni quand, ni pourquoi. Formes normalisées (sans
# accents) ; les pluriels sont listés. « douleur thoracique » passe (qualifié),
# « lésion osseuse » est rejeté : listée explicitement.
_TROP_GENERIQUE = {
    "lesion", "lesions", "atteinte", "infection", "infections",
    "douleur", "douleurs", "kyste", "kystes", "tumefaction",
    "inflammatoire", "anomalie", "obstacle", "immunite", "cure",
    "plaque", "traitement", "intervention", "geste",
    "lesion osseuse", "anomalie de structure", "traitement medical",
    "traitement chirurgical",
}


def est_trop_generique(valeur: str) -> bool:
    """Valeur trop générique pour le VSM (P4) — rejetée."""
    bas = re.sub(r"[^\w\s]", " ", normaliser(valeur)).strip()
    if not bas:
        return True
    return bas in _TROP_GENERIQUE


# ---------------------------------------------------------------------------
# P5 — Facteurs de risque : LISTE FERMÉE
# ---------------------------------------------------------------------------

# Un facteur de risque n'est pas un diagnostic : c'est une exposition ou un
# comportement, et le vocabulaire en est très restreint. Tout ce qui n'y
# figure pas retourne dans pathologies_actives (sténose urétrale,
# Trichomonas vaginalis… ne sont pas des facteurs de risque).
_FACTEURS_RISQUE_RX = re.compile(
    r"tabac|tabagi|fumeur|\bfume\w*|paquet[s]?[-\s]ann[ée]e|"
    r"alcool|[ée]thyli|ob[ée]s|surpoids|\bimc\b|s[ée]dentar|"
    r"activit[ée]\s+physique|toxicoman|stup[ée]fiant|"
    r"exposition\s+professionnelle|amiante|silice|h[ée]r[ée]di|"
    r"ant[ée]c[ée]dents?\s+familiaux|m[ée]nopause|contraception",
    re.IGNORECASE,
)


def est_facteur_risque(valeur: str) -> bool:
    """L'entité ELLE-MÊME (pas son contexte) est-elle une exposition (P5) ?"""
    return bool(_FACTEURS_RISQUE_RX.search(valeur or ""))


# ---------------------------------------------------------------------------
# P6 — En-tête de cabinet répété
# ---------------------------------------------------------------------------

# Zone d'en-tête : jusqu'au premier titre de rubrique reconnu (borne 1200
# caractères : le papier à lettres du cabinet repousse la mention bien au-
# delà des 300 anciennement filtrés), sinon 500 caractères.
ZONE_ENTETE = 500
ZONE_ENTETE_MAX = 1200


def zone_en_tete(texte_page: str) -> str:
    """Zone d'en-tête de la page : jusqu'au premier titre, sinon 500 car."""
    m = TITRES_RX.search(texte_page or "")
    if m and m.start() <= ZONE_ENTETE_MAX:
        return texte_page[: m.start()]
    return (texte_page or "")[:ZONE_ENTETE]


def formes_en_tete_repetees(formes: list[str], carte: list[dict]) -> dict[str, int]:
    """Formes apparaissant dans l'en-tête de PLUS de 3 pages (P6).

    Un vrai diagnostic répété est dispersé dans le corps du texte ; un
    en-tête est toujours au même endroit, page après page. Retourne
    {forme normalisée: nombre de pages} pour les formes rejetées — le
    nombre alimente le détail du rejet journalisé.
    """
    if not carte or not formes:
        return {}
    zones = [normaliser(zone_en_tete(p.get("texte", ""))) for p in carte]
    rejetees: dict[str, int] = {}
    for forme in formes:
        bas = normaliser(forme)
        if not bas:
            continue
        n_pages = sum(1 for z in zones if bas in z)
        if n_pages > 3:
            rejetees[bas] = n_pages
    return rejetees


# ---------------------------------------------------------------------------
# P7 — Posologies accolées aux traitements
# ---------------------------------------------------------------------------

# Après une entité « treatment », étendre l'empan vers la droite tant que le
# texte est une posologie (dose, fréquence, moment), limite 60 caractères.
# L'extension est une DÉCOUPE du texte source : la garantie anti-hallucination
# tient (jamais de texte inventé).
# Pas de « ^ » : l'ancrage se fait par re.match(texte, pos) — « ^ » ne
# s'ancre qu'au VRAI début de la chaîne, jamais à pos. Unités longues
# AVANT les lettres simples (« g » seul après « gél » : sans cet ordre,
# « 1 gél/j » s'arrête à « 1 g »).
_RX_POSOLOGIE = re.compile(
    r"[\s,:]*("
    r"\d+([.,]\d+)?\s*(mg|g[ée]l(?:ules?)?|gcl|gouttes?|sachets?|"
    r"comprim[ée]s?|bouff[ée]es?|amp|ui|µg|ml|cp|g)"
    r"|x\s*\d+"
    r"|\d+\s*(fois|/)\s*(j|jour|24h)"
    r"|matin|midi|soir|coucher|par jour|/j\b|en permanence"
    r"|\d+\s*/\s*\d+"
    r")",
    re.IGNORECASE,
)
LIMITE_POSOLOGIE = 60


def etendre_posologie(texte: str, fin: int) -> int:
    """Nouvelle fin de l'empan après absorption de la posologie (P7).

    Boucle : tant que le texte à la position courante COMMENCE par un motif
    de posologie, l'empan s'étend. Jamais plus de 60 caractères au-delà de
    ``fin``, jamais au-delà du texte.
    """
    nouvelle_fin = fin
    limite = min(len(texte or ""), fin + LIMITE_POSOLOGIE)
    while nouvelle_fin < limite:
        m = _RX_POSOLOGIE.match(texte, nouvelle_fin)
        if not m or m.end() <= nouvelle_fin:
            break
        nouvelle_fin = min(m.end(), limite)
    return nouvelle_fin


# ---------------------------------------------------------------------------
# P2 — Déduplication sémantique (forme → similarité)
# ---------------------------------------------------------------------------

# Similarité : rapidfuzz token_set_ratio ≥ 88 absorbe les fautes d'OCR
# (« ulcère libéaire » → « ulcère bulbaire linéaire »). Ne fusionner qu'à
# l'intérieur d'une même rubrique (fait par l'appelant : un appel par
# section). La fusion par CODE normalisé est désactivée : voir la
# docstring de dedupliquer (audit du normalisateur, 2026-08-24).
SEUIL_SIMILARITE = 88

# Latéralité : cliniquement distinct malgré la ressemblance lexicale —
# « kyste de l'ovaire droit » ≠ « kyste de l'ovaire gauche ».
_LATERALITE = {
    "droit", "droite", "gauche", "bilateral", "bilaterale",
    "unilateral", "unilaterale",
}


def _lateralite(forme: str) -> set[str]:
    return {t for t in forme.split() if t in _LATERALITE}


def _lateralement_distinct(a: str, b: str) -> bool:
    """Latéralités présentes et différentes : cliniquement distinct."""
    la, lb = _lateralite(a), _lateralite(b)
    return bool(la) and bool(lb) and la != lb


def _fusionnables(a: str, b: str) -> bool:
    """Deux formes normalisées fusionnables par similarité (P2, passe 3) ?"""
    if not a or not b:
        return False
    if _lateralement_distinct(a, b):
        return False  # latéralités différentes : cliniquement distinct
    from rapidfuzz import fuzz

    return fuzz.token_set_ratio(a, b) >= SEUIL_SIMILARITE


def dedupliquer(champs: list[dict]) -> list[dict]:
    """Fusionne les entités d'une MÊME rubrique (P2) — 2 passes TEXTE.

    Passe 1 : même forme normalisée (accents/casse/espaces).
    Passe 2 : similarité ≥ 88 (fautes d'OCR), latéralité différente bloquée.

    La passe par CODE normalisé (CIM-10/ATC) est DÉSACTIVÉE : l'audit du
    normalisateur (``outputs/AUDIT_NORMALISATEUR.md``, 2026-08-24) mesure
    ~14 % de codes faux sur un échantillon — « maladie coronaire » se voit
    attribuer N18 « maladie rénale chronique » (confiance 0,732) : fusionner
    sur le code aurait fusionné les DEUX maladies dans le VSM. Les deux
    passes conservées comparent le TEXTE RÉEL — elles sont sûres. La fusion
    par code ne reviendra qu'après correction du normalisateur (seuil,
    référentiel) et validation de l'audit.

    La passe similarité compare la nouvelle entité aux formes de TOUS les
    clusters et RELIE les clusters ponts : dans la famille ulcère réelle,
    « ulcère libéaire » (71.8 avec « bulbaire linéaire ») fusionne via
    « ulcère linéaire » (93.3) — la chaîne complète s'effondre en une
    entrée. Les synonymes purs (« fuites urinaires » / « incontinence »,
    similarité 80) ne fusionnent donc PLUS : c'est le prix de la sûreté,
    assumé.

    Le représentant conservé est la forme la plus FRÉQUENTE (à départager
    par le score le plus élevé) — jamais la plus longue, souvent la plus
    bruitée. Chaque entité fusionnée porte ``occurrences`` (nombre de
    mentions) et ``pages`` (pages où elle apparaît) : « Ulcère bulbaire
    linéaire — 15 mentions, pages 8 à 62 » restitue la chronicité.
    """
    clusters: list[dict] = []  # {formes: {forme: [champ...]}, membres: [...]}

    def _fusionner(cible: dict, autres: list[dict]) -> None:
        """Absorbe les clusters « ponts » dans la cible."""
        for autre in autres:
            for forme, ms in autre["formes"].items():
                cible["formes"].setdefault(forme, []).extend(ms)
            cible["membres"].extend(autre["membres"])
            clusters.remove(autre)

    for champ in champs:
        forme = normaliser(champ.get("valeur", ""))
        if not forme:
            clusters.append({"formes": {"": [champ]}, "membres": [champ]})
            continue
        # Passe 1 : forme normalisée identique.
        cibles: list[dict] = [c for c in clusters if forme in c["formes"]]
        # Passe 2 : similarité (toutes les formes du cluster : les chaînes
        # de fautes d'OCR s'absorbent progressivement).
        if not cibles:
            cibles = [
                c
                for c in clusters
                if any(_fusionnables(forme, f) for f in c["formes"] if f)
            ]
        if not cibles:
            clusters.append({"formes": {forme: [champ]}, "membres": [champ]})
        else:
            cible = cibles[0]
            cible["formes"].setdefault(forme, []).append(champ)
            cible["membres"].append(champ)
            if len(cibles) > 1:
                _fusionner(cible, cibles[1:])

    sortie: list[dict] = []
    for c in clusters:
        membres = c["membres"]
        if len(membres) == 1:
            rep = membres[0]
        else:
            # Représentant : la FORME la plus fréquente ; à égalité, le
            # membre au score le plus élevé.
            compte_forme = {f: len(ms) for f, ms in c["formes"].items()}
            rang = {id(m): i for i, m in enumerate(membres)}
            rep = min(
                membres,
                key=lambda m: (
                    -compte_forme.get(normaliser(m.get("valeur", "")), 1),
                    -float(m.get("confiance", 0.0)),
                    rang[id(m)],
                ),
            )
        nouveau = dict(rep)
        if len(membres) > 1:
            nouveau["occurrences"] = len(membres)
            pages = sorted(
                {
                    (m.get("source") or {}).get("page")
                    for m in membres
                    if (m.get("source") or {}).get("page") is not None
                }
            )
            if pages:
                nouveau["pages"] = pages
            # Le cluster peut avoir un code quand le représentant n'en a pas
            # (fusion par similarité d'une forme normalisée avec une autre).
            if not nouveau.get("code_normalise"):
                for m in membres:
                    if m.get("code_normalise"):
                        nouveau["code_normalise"] = m["code_normalise"]
                        break
        sortie.append(nouveau)
    return sortie
