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
