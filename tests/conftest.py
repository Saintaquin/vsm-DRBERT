import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest


def pytest_configure(config):
    """Enregistre le marqueur « slow » (tests exigeant un vrai modèle local)."""
    config.addinivalue_line(
        "markers", "slow: tests lents (vrai modèle DrBERT, vrai LLM…)"
    )


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


@pytest.fixture(autouse=True)
def _no_real_drbert_inference(monkeypatch, tmp_path):
    """Jamais d'inférence DrBERT réelle pendant les tests (même logique que
    le LLM) : VSM_DRBERT_PATH pointe vers un dossier vide → DrBERTIndisponible
    → repli règles tracé. Les tests DrBERT injectent un moteur FACTICE par-
    dessus (le monkeypatch du test l'emporte) ; les tests « slow » re-pointent
    VSM_DRBERT_PATH vers le vrai modèle."""
    import src.extraction_nlp.drbert_extractor as dtx

    monkeypatch.setenv("VSM_DRBERT_PATH", str(tmp_path / "drbert_absent"))
    monkeypatch.setattr(dtx, "_MOTEUR", None)


@pytest.fixture(autouse=True)
def _no_real_drbert_augment(monkeypatch):
    """Pas d'inférence de l'ANCIEN DrBERT (complément du moteur de règles,
    drbert.py) pendant les tests : sur un poste où le modèle MedicalNER-FR
    est caché, l'augmentation faisait de vraies inférences et rendait les
    résultats dépendants de la machine (test_free_text_does_not_duplicate_
    headers échouait localement mais pas en CI). Même logique que pour le
    LLM ; les tests visés re-patchent ``is_available`` par-dessus."""
    import src.extraction_nlp.drbert as drbert_ancien

    monkeypatch.setattr(drbert_ancien, "is_available", lambda: False)
