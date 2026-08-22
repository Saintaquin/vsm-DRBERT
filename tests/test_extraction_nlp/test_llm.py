"""Tests de l'intégration LLM local (docs/ADR/0004).

L'inférence réelle (llama-cpp + GGUF) n'est pas exécutée en CI : les tests
vérifient la configuration, le repli sur les règles et le câblage API."""

from pathlib import Path

import pytest

from src.extraction_nlp import llm as llm_mod
from src.extraction_nlp.entity_extractor import (
    LLM_CONFIDENCE,
    NLP_ENGINE_LLM,
    extract_entities,
    extract_entities_llm,
    _extract_json_llm,
)
from src.extraction_nlp.pipeline import run_pipeline


def test_default_model_path_uses_cache():
    p = llm_mod.default_model_path()
    assert p.name == "model.gguf"
    assert ".cache" in str(p)


def test_model_path_env_override(monkeypatch):
    monkeypatch.setenv("VSM_LLM_MODEL_PATH", "C:/models/mon-modele.gguf")
    assert llm_mod.default_model_path() == Path("C:/models/mon-modele.gguf")


def test_model_available_false_without_model(monkeypatch):
    monkeypatch.setenv("VSM_LLM_MODEL_PATH", "C:/chemin/inexistant/model.gguf")
    assert llm_mod.model_available() is False


def test_recommended_models_metadata():
    # Métadonnées cohérentes : taille>0, RAM>0, licence non vide, note 1..5
    assert llm_mod.RECOMMENDED_MODELS
    for m in llm_mod.RECOMMENDED_MODELS:
        assert m["taille_gb"] > 0
        assert m["ram_min_gb"] >= 4
        assert m["licence"]
        assert 1 <= m["note"] <= 5
        assert m["url"].startswith("https://huggingface.co/")


def test_unknown_model_rejected():
    with pytest.raises(ValueError):
        llm_mod.download_model(key="inexistant", dest="x.gguf")


def test_extract_json_llm_tolerant():
    # JSON nu, entouré de fences markdown ou de texte parasite
    assert _extract_json_llm('{"a": []}') == {"a": []}
    assert _extract_json_llm('```json\n{"a": []}\n```') == {"a": []}
    assert _extract_json_llm('Voici : {"a": [1]} fin.') == {"a": [1]}


def test_extract_entities_llm_not_available_falls_back_to_rules():
    # llama_cpp n'est pas installé : engine="llm" retombe sur les règles
    ents = extract_entities(
        "ANTECEDENTS : Diabete de type 2.\nALLERGIES : Penicilline.", engine="llm"
    )
    assert {e.section for e in ents} >= {"antecedents", "allergies"}


def test_pipeline_llm_engine_provenance():
    # Le moteur « llm » est tracé dans la provenance du VSM (XAI)
    ocr_json = {
        "document_id": "doc_x",
        "source_file": "f.png",
        "sha256": "0" * 64,
        "ocr_engine": "tesseract",
        "text": "ANTECEDENTS : Diabete de type 2.",
        "anonymization_applied": True,
        "pii_detected_count": 0,
        "pipeline_version": "1.0.0",
    }
    out = run_pipeline(ocr_json, nlp_engine="llm")
    # sans modèle : repli règles → provenance trace le moteur réel
    assert out["provenance"]["moteur_nlp"] == "rules-fr-v1"


def test_llm_confidence_is_conservative():
    # La confiance LLM est sous le seuil 0,7 → champs « À valider » (XAI)
    assert LLM_CONFIDENCE < 0.7
    assert NLP_ENGINE_LLM == "llama-3.1-8b-q4-local"


def test_suggest_model_by_ram(monkeypatch):
    # Guidance matérielle : la recommandation suit la RAM détectée
    monkeypatch.setattr(llm_mod, "detect_ram_gb", lambda: 16.0)
    assert llm_mod.suggest_model() == "mistral-nemo-12b"
    monkeypatch.setattr(llm_mod, "detect_ram_gb", lambda: 10.0)
    assert llm_mod.suggest_model() == "qwen2.5-7b"
    monkeypatch.setattr(llm_mod, "detect_ram_gb", lambda: 8.0)
    assert llm_mod.suggest_model() == "qwen2.5-3b"  # prudent sur 8 Go
    monkeypatch.setattr(llm_mod, "detect_ram_gb", lambda: 4.0)
    assert llm_mod.suggest_model() == "qwen2.5-3b"


def test_light_model_for_small_machines():
    # Machine < 16 Go : une option Apache 2.0 ≤ 2 Go doit exister
    light = [
        m
        for m in llm_mod.RECOMMENDED_MODELS
        if m["licence"] == "Apache 2.0" and m["taille_gb"] <= 2.0
    ]
    assert light and light[0]["key"] == "qwen2.5-3b"
