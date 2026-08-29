"""Pipeline NLP : JSON OCR (sortie ingestion_ocr) → JSON conforme à
schema/vsm_schema.json (sections remplies, champs vides si non trouvés)."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from .entity_extractor import NLP_ENGINE_RULES, extract_entities_with_report
from .filtres_vsm import normaliser
from .normalizer import normalize_diagnosis, normalize_medication

_log = logging.getLogger("vsm")

SCHEMA_VERSION = "1.1.0"
SECTIONS = (
    "pathologies_actives",
    "antecedents",
    "allergies",
    "traitements_long_cours",
    "facteurs_risque",
    "vaccinations",
    "points_vigilance",
)
_DIAG_SECTIONS = {"pathologies_actives", "antecedents"}

# Tokens de pseudonymisation produits par ingestion_ocr (mode « pseudo ») :
# [PATIENT_001], [DATE_NAISSANCE_001], [NIR_001], [RPPS_001], …
_TOKEN_RX = re.compile(r"\[(PATIENT|DATE_NAISSANCE|NIR|INS|RPPS|ADELI)_\d{3}\]")
# « Sexe : H » / « Sexe : Masculin » / « Sexe : Féminin » (en-têtes réels)
_SEXE_RX = re.compile(r"\bsexe\s*:?\s*([HMF]|[Mm]asculin|[Ff][ée]minin)", re.IGNORECASE)


def _fill_identity(vsm: dict, text: str, ocr_json: dict) -> None:
    """Remplit l'identité (pseudonymisée) du patient et du médecin traitant à
    partir des tokens présents dans le texte OCR anonymisé.

    En mode « strict » (aucun token, texte [REDACTED:TYPE]), l'identité reste
    vide : elle n'est pas réversible, par conception. Le token est la valeur
    affichée — le mapping token↔identité réelle vit dans le coffre-fort
    (MappingVault), jamais dans le VSM."""
    for m in _TOKEN_RX.finditer(text):
        kind = m.group(1)
        token = m.group(0)
        champ = {
            "valeur": token,
            "confiance": 1.0,
            "source": {
                "document_id": ocr_json.get("document_id", ""),
                "passage": token,
                "offset_debut": m.start(),
                "offset_fin": m.end(),
            },
            "moteur_ocr": ocr_json.get("ocr_engine", ""),
            "moteur_nlp": NLP_ENGINE_RULES,
        }
        patient = vsm.setdefault("patient", {})
        if kind == "PATIENT":
            patient.setdefault("identite", champ)
        elif kind == "DATE_NAISSANCE":
            patient.setdefault("date_naissance", champ)
        elif kind in ("NIR", "INS"):
            patient.setdefault("ins", champ)
        elif kind == "RPPS":
            vsm.setdefault("medecin_traitant", {}).setdefault("rpps", champ)
        elif kind == "ADELI":
            vsm.setdefault("medecin_traitant", {}).setdefault("adelI", champ)

    # Sexe : « Sexe : H » / « Sexe : Masculin » — reste en clair dans le
    # texte anonymisé (non identifiant seul) et alimente la rubrique 1.
    sm = _SEXE_RX.search(text)
    if sm and "sexe" not in vsm.get("patient", {}):
        raw = sm.group(1)
        sexe = raw[0].upper() if raw.isalpha() else raw
        vsm.setdefault("patient", {})["sexe"] = {
            "valeur": "H" if sexe in ("H", "M", "MAS") else "F",
            "confiance": 0.9,
            "source": {
                "document_id": ocr_json.get("document_id", ""),
                "passage": sm.group(0),
                "offset_debut": sm.start(),
                "offset_fin": sm.end(),
            },
            "moteur_ocr": ocr_json.get("ocr_engine", ""),
            "moteur_nlp": NLP_ENGINE_RULES,
        }


def run_pipeline(
    ocr_json: dict,
    nlp_engine: str = "rules",
    confidence_threshold: float = 0.7,
    progress=None,
) -> dict:
    from .filtres_vsm import (
        carte_pages,
        dedupliquer,
        formes_en_tete_repetees,
        page_de,
    )

    text = ocr_json.get("text", "")
    pages_ocr = ocr_json.get("pages") or []
    # Carte des pages : nécessaire aux filtres par page (P6 en-tête de
    # cabinet, page de chaque entité pour P2). Reconstruite par longueurs
    # cumulées du join "\n\n" ; invalide → filtres désactivés (sûr).
    carte = carte_pages(text, pages_ocr)
    entities, nlp_report = extract_entities_with_report(
        text, engine=nlp_engine, progress=progress, pages=pages_ocr
    )
    # XAI : tracer le moteur RÉELLEMENT utilisé (repli « llm » → règles inclus)
    moteur_effectif = nlp_report["moteur"]

    sections: dict[str, list] = {s: [] for s in SECTIONS}
    for ent in entities:
        champ = ent.to_champ()
        champ["source"]["document_id"] = ocr_json.get("document_id", "")
        champ["moteur_ocr"] = ocr_json.get("ocr_engine", "")
        if carte:
            page = page_de(carte, int(champ["source"].get("offset_debut", 0)))
            if page is not None:
                champ["source"]["page"] = page
        if ent.section in _DIAG_SECTIONS:
            n = normalize_diagnosis(ent.valeur)
            if n["code_cim10"]:
                champ["code_normalise"] = {
                    "systeme": "CIM-10",
                    "code": n["code_cim10"],
                    "libelle_officiel": n["label_official"],
                    "confiance_normalisation": n["confidence"],
                }
        elif ent.section == "traitements_long_cours":
            n = normalize_medication(ent.valeur)
            if n["code_atc"]:
                champ["code_normalise"] = {
                    "systeme": "ATC",
                    "code": n["code_atc"],
                    "libelle_officiel": n["label_official"],
                    "confiance_normalisation": n["confidence"],
                }
        champ["a_valider"] = champ["confiance"] < confidence_threshold
        sections[ent.section].append(champ)

    # P6 — en-tête de cabinet : une forme qui apparaît à l'identique dans la
    # zone d'en-tête de PLUS de 3 pages est du papier à lettres (« Maladies
    # du Foie », l'adresse du cabinet…), pas un diagnostic. Un vrai
    # diagnostic répété est dispersé dans le corps du texte.
    if carte:
        formes = [
            c["valeur"] for items in sections.values() for c in items
        ]
        rejetees = formes_en_tete_repetees(formes, carte)
        if rejetees:
            supprimees = 0
            for cle, items in sections.items():
                sections[cle] = [
                    c for c in items if normaliser(c["valeur"]) not in rejetees
                ]
                supprimees += len(items) - len(sections[cle])
            _log.info(
                "P6 en-tête répété : %d entrée(s) supprimée(s) (%s)",
                supprimees,
                ", ".join(sorted(rejetees))[:120],
            )

    # P2 — déduplication sémantique PAR RUBRIQUE (code normalisé → forme →
    # similarité ≥ 88) : un dossier de 20 ans décrit la même pathologie à
    # chaque compte rendu, avec les fautes d'OCR propres à chaque page. Le
    # représentant est la forme la plus fréquente ; chaque entrée fusionnée
    # porte ses occurrences et ses pages (« 15 mentions, pages 8 à 62 »).
    for cle, items in sections.items():
        if isinstance(items, list) and items:
            sections[cle] = dedupliquer(items)

    result = {
        "schema_version": SCHEMA_VERSION,
        "document_id": ocr_json.get("document_id", ""),
        "date_generation": datetime.now(timezone.utc).isoformat(),
        "statut": "brouillon",
        "patient": {},
        "medecin_traitant": {},
        "sections": sections,
        "provenance": {
            "documents_sources": [
                {
                    "document_id": ocr_json.get("document_id", ""),
                    "fichier": ocr_json.get("source_file", ""),
                    "sha256": ocr_json.get("sha256", ""),
                    "moteur_ocr": ocr_json.get("ocr_engine", ""),
                    "anonymization_applied": ocr_json.get(
                        "anonymization_applied", False
                    ),
                    "pii_detected_count": ocr_json.get("pii_detected_count", 0),
                }
            ],
            "moteur_nlp": moteur_effectif,
            # Rapport complet de la phase NLP/LLM (statut, raisons, durées,
            # corrections OCR) — affiché au médecin (XAI).
            "nlp": nlp_report,
            "pipeline_version": ocr_json.get("pipeline_version", ""),
        },
    }
    _fill_identity(result, text, ocr_json)
    return result
