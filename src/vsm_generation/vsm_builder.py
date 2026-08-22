"""Assemblage du Volet de Synthèse Médicale dans l'ordre canonique HAS,
calcul de complétude par section, validation jsonschema."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema" / "vsm_schema.json"
ORDRE_HAS = (
    "pathologies_actives",
    "antecedents",
    "allergies",
    "traitements_long_cours",
    "facteurs_risque",
    "vaccinations",
    "points_vigilance",
)
TITRES = {
    "pathologies_actives": "Pathologies actives",
    "antecedents": "Antécédents médicaux et chirurgicaux",
    "allergies": "Allergies et intolérances",
    "traitements_long_cours": "Traitements au long cours",
    "facteurs_risque": "Facteurs de risque",
    "vaccinations": "Vaccinations",
    "points_vigilance": "Points de vigilance",
}
AVERTISSEMENT = (
    "Document généré automatiquement à partir de documents scannés. "
    "Le contenu n'a PAS été vérifié médicalement : il doit être relu, "
    "corrigé si nécessaire et validé par un médecin avant tout usage clinique."
)


def _completude(items: list[dict]) -> float:
    if not items:
        return 0.0
    return round(sum(i.get("confiance", 0) for i in items) / len(items), 3)


def build_vsm(nlp_json: dict, confidence_threshold: float = 0.7) -> dict:
    vsm = json.loads(json.dumps(nlp_json))  # copie profonde
    sections = vsm.get("sections", {})
    ordered = {k: sections.get(k, []) for k in ORDRE_HAS}
    for items in ordered.values():
        for champ in items:
            champ["a_valider"] = champ.get("confiance", 0) < confidence_threshold
    vsm["sections"] = ordered
    vsm["completude"] = {k: _completude(v) for k, v in ordered.items()}
    vsm["statut"] = "a_valider"
    vsm["avertissement"] = AVERTISSEMENT
    validate_vsm(vsm)
    return vsm


def validate_vsm(vsm: dict) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(vsm, schema)
