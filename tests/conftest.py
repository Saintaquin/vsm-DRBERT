import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest


@pytest.fixture(autouse=True)
def _no_real_llm_inference(monkeypatch):
    """Jamais d'inférence LLM réelle pendant les tests : sur un poste de dev
    où le GGUF et llama-cpp-python sont installés, les tests d'API
    déclencheraient de vraies inférences (minutes + RAM). On force le repli
    « modèle indisponible » ; les tests qui le veulent re-patch
    ``llm_attemptable``/``_llama_cpp_available`` par-dessus (le monkeypatch
    du test l'emporte)."""
    import src.extraction_nlp.llm as llm_mod

    monkeypatch.setattr(llm_mod, "_llama_cpp_available", lambda: False)
