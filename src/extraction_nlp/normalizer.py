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


def _best_match(query: str, choices: dict[str, dict], threshold: float = 72.0):
    if not query:
        return None, 0.0
    res = process.extractOne(_norm(query), list(choices), scorer=fuzz.token_set_ratio)
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


def normalize_diagnosis(text: str) -> dict:
    rows = _load("cim10_fr.tsv")
    choices = {_norm(r["libelle"]): r for r in rows}
    row, score = _best_match(text, choices, threshold=SEUIL_CIM10)
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
# Périmètre : ATC SEULEMENT. Les catégories CIM-10 REGROUPENT (I48 =
# « Fibrillation ET flutter auriculaires » est le code correct d'une
# fibrillation seule) ; la règle y refuserait E78.0 « Hypercholestérolémie
# pure » pour « hypercholestérolémie » — coût sans bénéfice (0 faux CIM-10
# depuis le seuil 78). À réévaluer quand le référentiel CIM-10 s'enrichira
# de codes à point (stades N18.x).
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


def _qualificatifs_absents(terme: str, libelle: str) -> set[str]:
    """Jetons du NOYAU du libellé absents du terme — le code affirme plus.

    Retour vide : le terme couvre le nom canonique (éventuellement avec sa
    posologie : « Metformine 1000 mg » couvre « Metformine »). Retour non
    vide : le libellé porte un qualificatif que le texte ne documente pas.
    """
    return (
        _tokens(_noyau(libelle))
        - _tokens(terme)
        - _MOTS_VIDES
        - _MOTS_BENINS
    )


def _nomme_par_alias(terme: str, libelle: str) -> bool:
    """Le terme nomme la substance par un de ses alias parenthésés."""
    jetons = _tokens(terme)
    return any(jetons & _tokens(a) for a in _alias(libelle))


def _prefixes_parents_atc(code: str) -> list[str]:
    """Préfixes parents valides d'un code ATC (A10AE04 → A10AE, A10A, A10)."""
    # Longueurs des niveaux ATC : L1=1, L2=3, L3=4, L4=5, L5=7 caractères.
    return [code[:n] for n in (5, 4, 3, 1) if n < len(code)]


def _remonter_au_parent(code: str, rows: list[dict], terme: str) -> dict | None:
    """Premier ancêtre du référentiel sans qualificatif absent du terme."""
    for pref in _prefixes_parents_atc(code):
        candidat = next((r for r in rows if r.get("code") == pref), None)
        if candidat is None:
            continue
        if not _qualificatifs_absents(terme, candidat["libelle"]):
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
    row, score = _best_match(first_words, choices, threshold=70.0)
    if row is None:
        row, score = _best_match(text, choices, threshold=78.0)
    # Règle de spécificité (audit) : ne déclenche QUE sur le piège du
    # sous-ensemble exact (token_set_ratio = 100 — un qualificatif ne coûte
    # rien au score). Les matches flous < 1,00 (fautes d'OCR) gardent le
    # filet existant : marquage « à vérifier » sous 0,85.
    if row is not None and score >= 0.999:
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
