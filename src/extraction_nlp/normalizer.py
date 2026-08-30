"""Normalisation des entités vers les référentiels CIM-10 et ATC (extraits
locaux dans data/referentials/, TSV). Match exact insensible aux accents,
puis repli fuzzy (rapidfuzz). Aucun appel réseau."""

from __future__ import annotations

import csv
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from rapidfuzz import fuzz, process

REFERENTIALS = Path(__file__).resolve().parents[2] / "data" / "referentials"
_DOSE_RX = re.compile(r"(\d+[.,]?\d*)\s*(mg|g|µg|ug|ml|ui|%)", re.IGNORECASE)
_FREQ_RX = re.compile(
    r"(matin et soir|matin|soir|midi|par jour|/j|x\s?\d|fois par jour|par semaine|a jeun|à jeun)",
    re.IGNORECASE,
)


def _norm(s: str) -> str:
    s = "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"\s+", " ", s.lower()).strip()


@lru_cache(maxsize=1)
def _load(name: str) -> list[dict]:
    with (REFERENTIALS / name).open(encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def _decouper(s: str) -> str:
    """Processeur rapidfuzz : la ponctuation NE fait PAS partie des jetons.

    ``token_set_ratio`` découpe sur les espaces seuls — sans ce processeur,
    « allergie » ne matche JAMAIS « Allergie, sans précision » (jeton
    « allergie, »), « BPCO » jamais « (BPCO) », « AVC ischémique » rate
    I63 « Infarctus cérébral (AVC ischémique) » et retombe sur I25 à 0,83
    (constat mesuré, audit C2/DRAGON v7). Les chaînes comparées sont déjà
    normalisées (minuscules, sans accents) par _norm.
    """
    return re.sub(r"[^a-z0-9]+", " ", s)


def _best_match(query: str, choices: dict[str, dict], threshold: float = 72.0):
    if not query:
        return None, 0.0
    res = process.extractOne(
        _norm(query), list(choices), scorer=fuzz.token_set_ratio,
        processor=_decouper,
    )
    if res and res[1] >= threshold:
        return choices[res[0]], res[1] / 100.0
    return None, 0.0


# Audit du normalisateur (2026-08-24, outputs/AUDIT_NORMALISATEUR.md) : avec
# le seuil historique de 72, « maladie coronaire » recevait N18 « maladie
# rénale chronique » (0,73) et « maladie de Basedow » G20 « maladie de
# Parkinson » (0,74) — des libellés partageant des mots vides fusionnent à
# ~73. Mesuré sur l'échantillon : TOUS les codes faux ≤ 0,74, TOUS les codes
# corrects ≥ 0,80 — frontière franche. Le seuil 78 ne garde que ce qui est
# au-dessus de la zone grise ; l'absence de code vaut mieux qu'un code faux.
SEUIL_CIM10 = 78.0

# Seuil ATC (constat DRAGON v7, 2026-08-30) : un nom de molécule est un
# IDENTIFIANT, pas une expression. Aux anciens seuils (70/78), trois
# antibiotiques recevaient des codes de classes thérapeutiques étrangères
# par pure ressemblance graphique — « SPIRAMYCINE » → B01AC06 (aspirine),
# « GENTALLINE » → N06AB06 (sertraline), « ofloxacine » ≈ fluoxétine →
# N06AB03. Risque clinique direct : lire B01AC06 sur une spiramycine,
# c'est conclure « patient sous aspirine » (anticoagulation, contre-
# indication chirurgicale). Décision : en dessous de 0,95, AUCUN code —
# les appariements légitimes (exact ou posologie accolée :
# « Paracétamol 1 g », « Metformine 1000 mg matin ») sont des
# sur-ensembles à 1,00 ; tout le reste est du bruit graphique.
SEUIL_ATC = 95.0

# Déclenchement de la règle de spécificité (C2, DRAGON v7) : abaissé de
# 1,00 à 0,90 — le piège du sous-ensemble ne produit pas toujours un score
# parfait (« tumeur » vs « Tumeur maligne du sein » rend 94).
SEUIL_SPECIFICITE = 0.90


def normalize_diagnosis(text: str) -> dict:
    rows = _load("cim10_fr.tsv")
    choices = {_norm(r["libelle"]): r for r in rows}
    row, score = _best_match(text, choices, threshold=SEUIL_CIM10)
    # Règle de spécificité (C2, DRAGON v7) : « diabète » ne doit pas porter
    # E11 « de type 2 », « tumeur » pas C50 « du sein ». Ne déclenche QUE
    # au-dessus de SEUIL_SPECIFICITE : en dessous, le filet « à vérifier »
    # du seuil 78 suffit (I48 pour « fibrillation auriculaire » à 0,80 est
    # le code correct d'un regroupement, le refuser serait une régression).
    if row is not None and score >= SEUIL_SPECIFICITE:
        manquants = _qualificatifs_absents(text, row["libelle"], cim10=True)
        if manquants and not _nomme_par_alias(text, row["libelle"]):
            # Refuser la feuille : remonter au parent CIM-10 (E78.0 → E78)
            # s'il est au référentiel, sinon ne rien afficher.
            parent = _remonter_au_parent(row["code"], rows, text, cim10=True)
            if parent is None:
                row = None
            else:
                row = parent
    if row is None:
        return {"code_cim10": None, "label_official": None, "confidence": 0.0}
    return {
        "code_cim10": row["code"],
        "label_official": row["libelle"],
        "confidence": round(score, 3),
    }


# ---------------------------------------------------------------------------
# Règle de spécificité ATC (audit 2026-08-24, recommandation appliquée)
# ---------------------------------------------------------------------------
# Un libellé officiel portant des QUALIFICATIFS absents du terme extrait
# affirme PLUS que le texte : « insuline » → A10AE04 « Insuline glargine »
# (aucun analogue long n'est documenté), « vitamine D » → A12AX « Calcium +
# vitamine D » (aucun calcium documenté). Le piège est STRUCTUREL :
# token_set_ratio rend 100 dès que les jetons du terme sont un sous-ensemble
# de ceux du libellé — un qualificatif ne coûte rien au score.
#
# Règle : refuser le code feuille, remonter au code parent si le référentiel
# en contient un sans qualificatif absent, sinon ne rien afficher.
#
# Périmètre (C2, DRAGON v7) : les DEUX systèmes. La distinction n'est pas
# « ATC contre CIM-10 » mais la NATURE du qualificatif du libellé officiel :
#   - coordination / imprécision (et, ou, autres, sans précision…) → le
#     code REGROUPE, l'accepter (« Fibrillation ET flutter » I48 est le
#     code correct d'une fibrillation isolée) ;
#   - localisation, étiologie, sévérité, type (du sein, par carence en
#     fer, de type 2, ischémique chronique, pure) → le code AFFIRME plus
#     que le texte : refuser (« diabète » ≠ E11 « de type 2 »,
#     « anémie » ≠ D50 « par carence en fer », « tumeur » ≠ C50 « du
#     sein »).
# Cas limite assumé (décision curatée, testée) : les mots qui COMPLÈTENT le
# nom canonique de l'entité NUE (« sucré » — diabète sucré = LE diabète ;
# « aigu » — infarctus aigu = L'infarctus du myocarde par convention ;
# « sodique » — lévothyroxine sodique) ne sont pas des qualificatifs : le
# terme nu se lit déjà comme l'entité complète. À l'inverse « de type 2 »
# DIFFÉRENCIE E11 de E10/E14 — c'est une affirmation sur le patient.
#
# Convention des libellés du référentiel : les PARENTHÈSES portent des
# ALIAS de la même substance (« Acide acétylsalicylique (Aspirine/
# Kardégic) ») — un terme qui nomme la substance par son alias (« Aspirine
# ») ne subit pas la règle : il nomme le produit, pas son genre.
_MOTS_VIDES = {
    "a", "au", "aux", "d", "de", "des", "du", "et", "en", "l", "la", "le",
    "les", "ou", "par", "pour", "avec", "sans",
}
# Mots qui COMPLÈTENT le nom canonique sans le préciser (le terme nu se lit
# déjà comme le composé complet) — décision curatée, testée :
_MOTS_BENINS = {"sodique"}  # « Lévothyroxine sodique » = la lévothyroxine

# CIM-10 (C2) : complétions canoniques + mots d'imprécision/coordination —
# le code REGROUPE, il n'affirme pas :
#   - « sucré » (diabète sucré), « aigu » (infarctus aigu) : le nom OFFICIEL
#     de l'entité non qualifiée ;
#   - « autre(s) » (J44, F41) : entrée résiduelle de la CIM-10 ;
#   - « syndrome », « maladie », « présence », « antécédents personnels » :
#     enveloppes administratives (SAS, maladie d'Alzheimer, Z95.1, Z88.0).
# Les négations finales (« sans précision », « sans complication ») sont
# retirées du noyau par _qualificatifs_absents : NE PAS préciser n'affirme
# rien (M81 « Ostéoporose sans fracture pathologique » reste le code de
# l'ostéoporose nu). Jetons au SINGULIER NORMALISÉ (sans accent, pluriel
# retiré par _tokens) : « antecedents personnels » → antecedent, personnel.
_MOTS_BENINS_CIM10 = {
    "sucre", "aigu", "autre", "syndrome", "maladie", "presence",
    "antecedent", "personnel",
}


def _tokens(terme: str) -> set[str]:
    """Jetons normalisés (accents, pluriel) pour la règle de spécificité."""
    sortie: set[str] = set()
    for brut in re.split(r"[^a-z0-9]+", _norm(terme)):
        if len(brut) >= 4 and brut.endswith("s"):
            brut = brut[:-1]  # pluriel français approximatif
        if brut:
            sortie.add(brut)
    return sortie


def _noyau(libelle: str) -> str:
    """Libellé sans ses alias parenthésés (marques, synonymes)."""
    return re.sub(r"\([^)]*\)", " ", libelle)


def _alias(libelle: str) -> list[str]:
    """Alias parenthésés du libellé, découpés (« Aspirine/Kardégic »)."""
    sortie: list[str] = []
    for m in re.finditer(r"\(([^)]*)\)", libelle):
        sortie.extend(a.strip() for a in m.group(1).split("/") if a.strip())
    return sortie


def _qualificatifs_absents(terme: str, libelle: str, cim10: bool = False) -> set[str]:
    """Jetons du NOYAU du libellé absents du terme — le code affirme plus.

    Retour vide : le terme couvre le nom canonique (éventuellement avec sa
    posologie : « Metformine 1000 mg » couvre « Metformine »). Retour non
    vide : le libellé porte un qualificatif que le texte ne documente pas.

    Les négations finales du libellé (« sans complication », « sans
    précision ») sont retirées : ne pas préciser n'affirme rien — M81
    « Ostéoporose sans fracture pathologique » reste le code de
    l'ostéoporose nue. ``cim10=True`` active les exceptions de
    coordination/imprécision (le code REGROUPE).
    """
    noyau = _noyau(libelle)
    noyau = re.sub(r"\b(?:sans|non)\s+[^,;()]*$", "", noyau).strip()
    benins = _MOTS_BENINS | (_MOTS_BENINS_CIM10 if cim10 else set())
    return _tokens(noyau) - _tokens(terme) - _MOTS_VIDES - benins


def _nomme_par_alias(terme: str, libelle: str) -> bool:
    """Le terme nomme la substance par un de ses alias parenthésés."""
    jetons = _tokens(terme)
    return any(jetons & _tokens(a) for a in _alias(libelle))


def _prefixes_parents_atc(code: str) -> list[str]:
    """Préfixes parents valides d'un code ATC (A10AE04 → A10AE, A10A, A10)."""
    # Longueurs des niveaux ATC : L1=1, L2=3, L3=4, L4=5, L5=7 caractères.
    return [code[:n] for n in (5, 4, 3, 1) if n < len(code)]


def _prefixes_parents_cim10(code: str) -> list[str]:
    """Préfixe parent valide d'un code CIM-10 à point (E78.0 → E78)."""
    return [code[:3]] if "." in code else []


def _remonter_au_parent(
    code: str, rows: list[dict], terme: str, cim10: bool = False
) -> dict | None:
    """Premier ancêtre du référentiel sans qualificatif absent du terme."""
    prefixes = _prefixes_parents_cim10(code) if cim10 else _prefixes_parents_atc(code)
    for pref in prefixes:
        candidat = next((r for r in rows if r.get("code") == pref), None)
        if candidat is None:
            continue
        if not _qualificatifs_absents(terme, candidat["libelle"], cim10=cim10):
            return candidat
    return None


def normalize_medication(text: str) -> dict:
    rows = _load("atc.tsv")
    choices = {}
    for r in rows:
        choices[_norm(r["dci"])] = r
        choices[_norm(r["libelle"])] = r
    # le nom du médicament est en général le 1er mot significatif
    first_words = " ".join(text.split()[:3])
    # C1 (DRAGON v7) : seuil unique 0,95 sur les DEUX passes. Les
    # appariements légitimes sont des sur-ensembles à 1,00 (posologie
    # accolée) ou des exacts ; 0,70-0,94 sur une DCI, c'est de la
    # ressemblance graphique (« ofloxacine » ≈ « fluoxétine »), pas une
    # identification. L'absence de code est un état correct.
    row, score = _best_match(first_words, choices, threshold=SEUIL_ATC)
    if row is None:
        row, score = _best_match(text, choices, threshold=SEUIL_ATC)
    # Règle de spécificité : dès que le score atteint SEUIL_SPECIFICITE
    # (0,90 — donc tout match ATC depuis C1), le libellé doit être couvert
    # par le terme : « insuline » ≠ A10AE04 « Insuline glargine »,
    # « salmétérol » ≠ R03AK06 « Salmétérol ET fluticasone ».
    if row is not None and score >= SEUIL_SPECIFICITE:
        manquants = _qualificatifs_absents(text, row["libelle"])
        if manquants and not _nomme_par_alias(text, row["libelle"]):
            # Refuser la feuille : remonter au parent, sinon rien afficher.
            parent = _remonter_au_parent(row["code"], rows, text)
            if parent is None:
                row = None
            else:
                row = parent
    dose = _DOSE_RX.search(text)
    freq = _FREQ_RX.search(text)
    dosage = " ".join(
        filter(None, [dose.group(0) if dose else None, freq.group(0) if freq else None])
    )
    if row is None:
        return {
            "code_atc": None,
            "label_official": None,
            "dosage_parsed": dosage or None,
            "confidence": 0.0,
        }
    return {
        "code_atc": row["code"],
        "label_official": row["libelle"],
        "dosage_parsed": dosage or None,
        "confidence": round(score, 3),
    }
