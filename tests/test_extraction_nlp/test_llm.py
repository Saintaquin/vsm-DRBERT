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
        assert m["ram_min_gb"] >= 3
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
    assert llm_mod.suggest_model() == "qwen2.5-3b"  # universel, CPU, 4-8 Go
    monkeypatch.setattr(llm_mod, "detect_ram_gb", lambda: 4.0)
    assert llm_mod.suggest_model() == "qwen2.5-3b"
    monkeypatch.setattr(llm_mod, "detect_ram_gb", lambda: 3.0)
    assert llm_mod.suggest_model() == "qwen2.5-1.5b"  # ultra-léger < 4 Go


def test_light_model_for_small_machines():
    # Toutes machines : le modèle UNIVERSEL par défaut (1er du catalogue)
    # est Apache 2.0, ≤ 2 Go, utilisable sans GPU.
    default = llm_mod.RECOMMENDED_MODELS[0]
    assert default["key"] == "qwen2.5-3b"
    assert default["licence"] == "Apache 2.0"
    assert default["taille_gb"] <= 2.0
    # et une option ultra-légère Apache 2.0 existe (< 4 Go)
    assert any(m["key"] == "qwen2.5-1.5b" for m in llm_mod.RECOMMENDED_MODELS)


def test_prompt_system_is_structured():
    # Système de prompt efficace : schéma JSON, anti-hallucination,
    # normalisation, négations, few-shot, pseudonymes exclus.
    from src.extraction_nlp.entity_extractor import build_llm_messages

    msgs = build_llm_messages("ANTECEDENTS : Diabete de type 2.", max_chars=2000)
    assert msgs[0]["role"] == "system" and msgs[1]["role"] == "user"
    system = msgs[0]["content"]
    user = msgs[1]["content"]
    # schéma JSON strict présent
    assert '"pathologies_actives"' in system
    assert '"points_vigilance"' in system
    # anti-hallucination + normalisation + négations + pseudonymes
    assert "N'INVENTE RIEN" in system
    assert "Diabète de type 2" in system  # exemple de normalisation
    assert "aucune allergie" in system
    assert "PATIENT_001" in system  # pseudonymes interdits dans valeur
    # few-shot : un exemple de réponse est intégré
    assert '"valeur": "Diabète de type 2 depuis 2010"' in system
    # le texte utilisateur est tronqué (borne max_chars)
    assert len(msgs[1]["content"]) < 2200


def test_prompt_truncation():
    from src.extraction_nlp.entity_extractor import build_llm_messages

    long = "x" * 10_000
    msgs = build_llm_messages(long, max_chars=500)
    assert "x" * 500 in msgs[1]["content"]
    assert "x" * 501 not in msgs[1]["content"]


def test_llm_feasible_ram_guard(monkeypatch, tmp_path):
    # Prévention du « traitement infini » : le LLM n'est PAS lancé si la RAM
    # disponible est insuffisante pour le modèle (marge incluse).
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF" + b"\x00" * (2 * 1024 * 1024))  # ~2 Mo
    monkeypatch.setattr(llm_mod, "default_model_path", lambda: model)
    monkeypatch.setattr(llm_mod, "available_ram_gb", lambda: 0.5)  # 0,5 Go libre
    ok, reason = llm_mod.llm_feasible()
    assert ok is False
    assert "RAM" in reason and "0.5" in reason
    monkeypatch.setattr(llm_mod, "available_ram_gb", lambda: 10.0)
    ok, _ = llm_mod.llm_feasible()
    assert ok is True


def test_extract_llm_skipped_when_infeasible(monkeypatch):
    # engine="llm" mais infaisable (RAM) → on ne tente JAMAIS llama.cpp,
    # on retombe directement sur les règles.
    from src.extraction_nlp import entity_extractor as ee

    monkeypatch.setattr(llm_mod, "llm_feasible", lambda: (False, "RAM insuffisante"))
    called = {"llm": False}

    def fake_llm(text):  # pragma: no cover - ne doit jamais être appelée
        called["llm"] = True
        return []

    monkeypatch.setattr(ee, "extract_entities_llm", fake_llm)
    ents = ee.extract_entities("ANTECEDENTS : Diabete de type 2.", engine="llm")
    assert called["llm"] is False
    assert any(e.section == "antecedents" for e in ents)
