"""Tests de l'intégration LLM local (docs/ADR/0004).

L'inférence réelle (llama-cpp + GGUF) n'est pas exécutée en CI : les tests
vérifient la configuration, les prompts (correction OCR + extraction), le
rapport d'exécution XAI (provenance.nlp) et le repli sur les règles."""

from pathlib import Path

import pytest

from src.extraction_nlp import entity_extractor as ee
from src.extraction_nlp import llm as llm_mod
from src.extraction_nlp.entity_extractor import (
    ExtractedEntity,
    LLM_CONFIDENCE_ANCHORED,
    LLM_CONFIDENCE_UNANCHORED,
    NLP_ENGINE_LLM,
    build_correction_messages,
    build_llm_messages,
    extract_entities,
    extract_entities_with_report,
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


def test_extract_entities_llm_not_available_falls_back_to_rules(monkeypatch):
    # llama_cpp n'est pas installé : engine="llm" retombe sur les règles
    monkeypatch.setattr(llm_mod, "llm_attemptable", lambda: False)
    ents = extract_entities(
        "ANTECEDENTS : Diabete de type 2.\nALLERGIES : Penicilline.", engine="llm"
    )
    assert {e.section for e in ents} >= {"antecedents", "allergies"}


def test_pipeline_llm_engine_provenance(monkeypatch):
    # Le moteur « llm » est tracé dans la provenance du VSM (XAI)
    monkeypatch.setattr(llm_mod, "llm_attemptable", lambda: False)
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
    # sans modèle : repli règles → provenance trace le moteur réel + le rapport
    assert out["provenance"]["moteur_nlp"] == "rules-fr-v1"
    assert out["provenance"]["nlp"]["statut"] == "modele_absent"
    assert "nlp" in out["provenance"]


def test_llm_confidence_anchored_policy():
    # La confiance LLM suit l'ANCRAGE dans le texte source (et non plus une
    # constante pessimiste) : passage reproduit à l'identique → fiable (pas de
    # « À valider » automatique) ; valeur introuvable → douteuse (< 0,7).
    assert LLM_CONFIDENCE_ANCHORED >= 0.7
    assert LLM_CONFIDENCE_UNANCHORED < 0.7
    assert NLP_ENGINE_LLM == "llm-local-q4"


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
    # Système de prompt efficace : schéma JSON, interdits en premier, refus par
    # défaut, few-shot avec un exemple de refus, aucun nom clinique dans les
    # descriptions de rubriques (anti-hallucination).
    msgs = build_llm_messages("ANTECEDENTS : Diabete de type 2.", max_chars=2000)
    assert msgs[0]["role"] == "system" and msgs[1]["role"] == "user"
    system = msgs[0]["content"]
    # schéma JSON strict présent
    assert '"pathologies_actives"' in system
    assert '"points_vigilance"' in system
    # les interdits passent en premier (refus = comportement par défaut)
    assert "INTERDITS" in system
    assert "mot pour mot" in system
    assert "crochets" in system
    assert "en-tête" in system
    assert "Une liste vide est une bonne réponse" in system
    # few-shot : un exemple de REFUS (réponse vide) est intégré
    assert '"pathologies_actives": [], "antecedents": []' in system
    # aucune valeur clinique concrète dans les descriptions de rubriques
    assert "tabac, alcool" not in system  # l'ancien piège à hallucination
    # le texte utilisateur est tronqué (borne max_chars)
    assert len(msgs[1]["content"]) < 2200


def test_prompt_truncation():
    long = "x" * 10_000
    msgs = build_llm_messages(long, max_chars=500)
    assert "x" * 500 in msgs[1]["content"]
    assert "x" * 501 not in msgs[1]["content"]


def test_correction_prompt_structured():
    # Phase 1 dédiée : system prompt de correction OCR (erreurs typiques,
    # doses/pseudonymes intouchables, sortie JSON stricte, interdiction de
    # deviner).
    msgs = build_correction_messages("ANTECEDENTS : Diabete de type 2.", max_chars=2000)
    assert msgs[0]["role"] == "system" and msgs[1]["role"] == "user"
    system = msgs[0]["content"]
    assert '"texte_corrige"' in system
    assert "diabete" in system and "diabète" in system
    assert "PATIENT_001" in system  # pseudonymes conservés à l'identique
    assert "1000 mg" in system  # doses conservées à l'identique
    assert "devines" in system  # interdiction d'inventer
    assert "RECOPIE À L'IDENTIQUE" in system  # refus de nettoyer à tout prix


def test_extraction_prompt_with_raw_and_corrected():
    # Phase 2 : l'extraction reçoit le texte BRUT (pour « passage ») et le
    # texte CORRIGÉ (référence de lecture pour « valeur »).
    msgs = build_llm_messages(
        "ANTÉCÉDENTS : Diabète de type 2.",
        texte_brut="ANTECEDENTS : Diabete de type 2.",
        max_chars=2000,
    )
    user = msgs[1]["content"]
    assert "BRUT" in user and "CORRIGÉ" in user
    assert "passage" in user and "valeur" in user


def test_count_ocr_corrections():
    assert ee._count_ocr_corrections("Diabete", "Diabète") >= 1
    assert ee._count_ocr_corrections("Metformine 1000 mg", "Metformine 1000 mg") == 0


def test_anchor_passage_verbatim_coherent():
    # Passage trouvé à l'identique et contenant la valeur → niveau 2.
    idx, length, niveau, eff = ee._anchor(
        "ANTECEDENTS : Diabete de type 2.", "Diabete de type 2", "Diabète de type 2"
    )
    assert niveau == 2 and eff == "Diabete de type 2"


def test_anchor_recycled_passage_downgraded():
    # Le LLM a recyclé un passage qui ne contient pas la valeur → niveau 0
    # (pas de fausse confiance 0,9).
    idx, length, niveau, eff = ee._anchor(
        "ANTECEDENTS : Diabete de type 2.",
        "Diabete de type 2",
        "Hypertension artérielle",
    )
    assert niveau == 0


def test_anchor_fuzzy_finds_raw_line_for_corrected_passage():
    # Le LLM cite le texte CORRIGÉ : on retrouve la ligne BRUTE (surlignable
    # dans le visualiseur) malgré les différences d'accents.
    text = "ALLERGIES : Penicilline (eruption cutanee)."
    idx, length, niveau, eff = ee._anchor(
        text, "ALLERGIES : Pénicilline (éruption cutanée)", "Pénicilline"
    )
    assert niveau == 1
    assert eff in text  # segment brut réel, pas la forme corrigée
    assert "Pénicilline" not in eff  # accent du texte corrigé absent du brut


def test_anchor_fuzzy_tolerates_punctuation():
    # « satisfaisant. » (avec point) doit matcher « satisfaisant » — la
    # ponctuation est neutralisée avant la comparaison floue.
    text = "CONCLUSION : Equilibre glycemique satisfaisant."
    idx, length, niveau, eff = ee._anchor(
        text,
        "CONCLUSION : Équilibre glycémique satisfaisant.",
        "Équilibre glycémique satisfaisant.",
    )
    assert niveau == 1
    assert eff == text


def test_anchor_unfound_hallucination():
    idx, length, niveau, eff = ee._anchor("Bilan normal.", "Rien", "Tout va bien")
    assert niveau == 0 and eff == ""


# ---------------------------------------------------------------------------
# Garde-fou aval : valider_sortie / valider_element
# ---------------------------------------------------------------------------


def test_valider_conserve_element_ancre():
    brut = {
        "antecedents": [
            {"valeur": "Appendicectomie en 1998", "passage": "appendicectomie en 1998"}
        ]
    }
    propre = ee.valider_sortie(brut, "ANTECEDENTS : appendicectomie en 1998.")
    assert propre["antecedents"] == [
        {"valeur": "Appendicectomie en 1998", "passage": "appendicectomie en 1998"}
    ]


def test_valider_rejette_passage_absent():
    # Fuite de few-shot : le « passage » n'est pas dans le document → rejeté.
    brut = {
        "antecedents": [{"valeur": "Diabète de type 2", "passage": "Diabete de type 2"}]
    }
    propre = ee.valider_sortie(brut, "ANTECEDENTS : appendicectomie en 1998.")
    assert propre["antecedents"] == []


def test_valider_rejette_passage_vide():
    brut = {"antecedents": [{"valeur": "Hypertension", "passage": ""}]}
    propre = ee.valider_sortie(brut, "ANTECEDENTS : Hypertension.")
    assert propre["antecedents"] == []


def test_valider_rejette_pseudonyme():
    brut = {
        "antecedents": [
            {
                "valeur": "[TEL_004] - Fax : [TEL_005]",
                "passage": "[TEL_004] - Fax : [TEL_005]",
            }
        ]
    }
    propre = ee.valider_sortie(brut, "[TEL_004] - Fax : [TEL_005]")
    assert propre["antecedents"] == []


def test_valider_rejette_entete():
    brut = {
        "antecedents": [
            {
                "valeur": "Centre Hospitalier",
                "passage": "CENTRE HOSPITALIER — Sce de Gastro-entérologie",
            }
        ]
    }
    texte = "CENTRE HOSPITALIER — Sce de Gastro-entérologie\nANTECEDENTS : appendicectomie en 1998."
    propre = ee.valider_sortie(brut, texte)
    assert propre["antecedents"] == []


def test_valider_rejette_fragment_vide():
    # « (s) » issu de « Allergie(s) : » sans contenu → rejeté.
    brut = {"allergies": [{"valeur": "(s)", "passage": "Allergie(s) :"}]}
    propre = ee.valider_sortie(brut, "Allergie(s) :")
    assert propre["allergies"] == []


def test_valider_reclasse_cure_courte():
    # Cure d'éradication de 7 jours : vraie info, mauvaise rubrique → points de
    # vigilance au lieu de traitements_long_cours.
    brut = {
        "traitements_long_cours": [
            {
                "valeur": "Cure de 7 jours : CLAMOXYL 500",
                "passage": "Cure de 7 jours : CLAMOXYL 500, 2 gélules matin et soir",
            }
        ]
    }
    propre = ee.valider_sortie(
        brut, "Cure de 7 jours : CLAMOXYL 500, 2 gélules matin et soir."
    )
    assert propre["traitements_long_cours"] == []
    assert any("CLAMOXYL" in e["valeur"] for e in propre["points_vigilance"])


def test_valider_rejette_valeur_biologique():
    brut = {
        "points_vigilance": [
            {
                "valeur": "Hémoglobine 15,9 g/100mL",
                "passage": "Hémoglobine 15,9 g/100mL",
            }
        ]
    }
    propre = ee.valider_sortie(brut, "Hémoglobine 15,9 g/100mL")
    assert propre["points_vigilance"] == []


def test_valider_rejette_classe_sans_produit():
    # « antibiotique » sans nom de produit → rejeté de traitements_long_cours.
    brut = {
        "traitements_long_cours": [
            {"valeur": "antibiotique", "passage": "antibiotique"}
        ]
    }
    propre = ee.valider_sortie(brut, "antibiotique")
    assert propre["traitements_long_cours"] == []


def test_valider_rejette_fusion_deux_medicaments():
    # « MAALOX et RANIPLEX » : deux médicaments fusionnés → rejeté.
    brut = {
        "traitements_long_cours": [
            {"valeur": "MAALOX et RANIPLEX", "passage": "MAALOX et RANIPLEX"}
        ]
    }
    propre = ee.valider_sortie(brut, "MAALOX et RANIPLEX")
    assert propre["traitements_long_cours"] == []


def test_run_with_timeout_times_out():
    import time

    def slow():  # pragma: no cover - doit dépasser le délai
        time.sleep(1.0)
        return "x"

    result, timed_out = ee._run_with_timeout(slow, 0.05)
    assert timed_out is True and result is None


def test_correction_phase_unpacks_result(monkeypatch):
    # Régression : _run_with_timeout renvoie (résultat, a_temporisé) — la phase
    # de correction doit déballer le résultat en (texte_corrige, nb_corrections).
    monkeypatch.setattr(
        ee,
        "correct_ocr_llm",
        lambda text, llm=None, model_path=None: ("texte corrigé", 7),
    )
    corrige, n_corr, duree = ee._correction_phase("brut", llm="dummy")
    assert corrige == "texte corrigé" and n_corr == 7
    assert duree is not None and duree >= 0.0


def test_llm_attemptable_when_model_present(monkeypatch, tmp_path):
    # Exigence « LLM sur toutes machines » : le LLM est TENTÉ dès que le modèle
    # est présent ET que llama-cpp-python est importable — la RAM ne bloque
    # plus (juste un avertissement).
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF" + b"\x00" * (2 * 1024 * 1024))  # ~2 Mo
    monkeypatch.setattr(llm_mod, "default_model_path", lambda: model)
    monkeypatch.setattr(llm_mod, "_llama_cpp_available", lambda: True)
    assert llm_mod.llm_attemptable() is True
    # avertissement (non bloquant) si RAM juste
    monkeypatch.setattr(llm_mod, "available_ram_gb", lambda: 0.5)
    assert "lent" in llm_mod.llm_ram_warning()
    monkeypatch.setattr(llm_mod, "available_ram_gb", lambda: 20.0)
    assert llm_mod.llm_ram_warning() == ""
    # sans modèle → non tenté
    monkeypatch.setattr(llm_mod, "default_model_path", lambda: tmp_path / "absent.gguf")
    assert llm_mod.llm_attemptable() is False


def test_llm_unavailable_reason_distinguishes_causes(monkeypatch, tmp_path):
    # Le modèle présent SANS la bibliothèque llama-cpp-python a une raison
    # distincte (pip install) — bug « LLM annoncé actif mais jamais exécuté ».
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF" + b"\x00" * (2 * 1024 * 1024))
    monkeypatch.setattr(llm_mod, "default_model_path", lambda: model)
    monkeypatch.setattr(llm_mod, "_llama_cpp_available", lambda: False)
    assert "llama-cpp-python" in llm_mod.llm_unavailability_reason()
    # tout est prêt → aucune raison (LLM utilisable)
    monkeypatch.setattr(llm_mod, "_llama_cpp_available", lambda: True)
    assert llm_mod.llm_unavailability_reason() == ""
    # modèle absent → indication de téléchargement
    monkeypatch.setattr(llm_mod, "default_model_path", lambda: tmp_path / "absent.gguf")
    assert "python -m" in llm_mod.llm_unavailability_reason()


# ---------------------------------------------------------------------------
# Orchestration de la phase LLM par document (rapport XAI inclus)
# ---------------------------------------------------------------------------


def _patch_llm(monkeypatch, ents=None, error=None):
    """Simule une phase LLM (chargement + correction + extraction) sans GPU."""
    monkeypatch.setattr(llm_mod, "llm_attemptable", lambda: True)
    monkeypatch.setattr(ee, "_charger_modele", lambda report: "dummy-model")

    def correction(text, llm):  # pragma: no cover - test
        return "texte corrigé", 3, 1.5

    def extraction(text, llm, corrige, segment=None):  # pragma: no cover - test
        if error is not None:
            raise error
        return ents or [], 2.5

    monkeypatch.setattr(ee, "_correction_phase", correction)
    monkeypatch.setattr(ee, "_extraction_phase", extraction)


def test_llm_phase_runs_and_reports(monkeypatch):
    # Document traité : phase LLM obligatoire (correction OCR + extraction),
    # le rapport atteste les deux étapes, les durées et les corrections.
    ents = [
        ExtractedEntity(
            "Diabète de type 2",
            "antecedents",
            0.9,
            "Diabete de type 2",
            0,
            18,
            correction_ocr=True,
        )
    ]
    _patch_llm(monkeypatch, ents)
    out, report = extract_entities_with_report(
        "ANTECEDENTS : Diabete de type 2.", engine="llm"
    )
    assert [e.valeur for e in out] == ["Diabète de type 2"]
    assert report["statut"] == "llm_complet"
    assert report["moteur"] == NLP_ENGINE_LLM
    assert report["phase_correction_ocr"] is True
    assert report["nb_corrections_ocr"] == 3
    assert report["duree_correction_sec"] == 1.5
    assert report["duree_extraction_sec"] == 2.5


def test_model_absent_reported(monkeypatch):
    # Modèle absent → règles directes, jamais d'appel llama.cpp, rapport clair.
    monkeypatch.setattr(llm_mod, "llm_attemptable", lambda: False)
    monkeypatch.setattr(llm_mod, "llm_unavailability_reason", lambda: "")
    called = {"llm": False}

    def fake_llm(text):  # pragma: no cover - ne doit jamais être appelée
        called["llm"] = True
        return []

    monkeypatch.setattr(ee, "extract_entities_llm", fake_llm)
    ents, report = extract_entities_with_report(
        "ANTECEDENTS : Diabete de type 2.", engine="llm"
    )
    assert called["llm"] is False
    assert any(e.section == "antecedents" for e in ents)
    assert report["statut"] == "modele_absent"
    assert "python -m" in report["raison"]


def test_llm_empty_result_falls_back_to_rules_and_reports(monkeypatch):
    # Hybride : si le LLM renvoie une sortie VIDE, les règles prennent le
    # relais et le rapport l'explique (jamais silencieux).
    _patch_llm(monkeypatch, ents=[])
    ents, report = extract_entities_with_report(
        "ANTECEDENTS : Diabete de type 2.\nALLERGIES : Penicilline.", engine="llm"
    )
    assert {e.section for e in ents} >= {"antecedents", "allergies"}
    assert report["statut"] == "repli_regles"
    assert report["moteur"] == "rules-fr-v1"
    assert report["raison"] == "Sortie LLM vide"


def test_llm_error_falls_back_to_rules_and_reports(monkeypatch):
    # Erreur d'inférence → règles, avec la raison dans le rapport.
    _patch_llm(monkeypatch, error=RuntimeError("mémoire insuffisante"))
    ents, report = extract_entities_with_report(
        "ANTECEDENTS : Diabete de type 2.", engine="llm"
    )
    assert any(e.section == "antecedents" for e in ents)
    assert report["statut"] == "repli_regles"
    assert "mémoire insuffisante" in report["raison"]


def test_correction_failure_keeps_extraction(monkeypatch):
    # Une correction OCR qui échoue (erreur NON-temporelle) ne fait pas tomber
    # la phase LLM entière : l'extraction continue sur le texte brut.
    monkeypatch.setattr(llm_mod, "llm_attemptable", lambda: True)
    monkeypatch.setattr(ee, "_charger_modele", lambda report: "dummy-model")

    def correction_fails(text, llm):  # pragma: no cover - test
        raise RuntimeError("correction OCR indisponible")

    def extraction(text, llm, corrige, segment=None):  # pragma: no cover - test
        assert corrige is None  # extraction sur texte brut
        return [ExtractedEntity("HTA", "antecedents", 0.9, "HTA", 0, 3)], 1.0

    monkeypatch.setattr(ee, "_correction_phase", correction_fails)
    monkeypatch.setattr(ee, "_extraction_phase", extraction)
    ents, report = extract_entities_with_report("ANTECEDENTS : HTA.", engine="llm")
    assert [e.valeur for e in ents] == ["HTA"]
    assert report["statut"] == "llm_extraction_seule"
    assert report["phase_correction_ocr"] is False
    assert report["raison"] == "correction OCR non appliquée"


def test_timeout_disables_llm_for_rest_of_document(monkeypatch):
    # Sur une machine trop lente, un premier dépassement de délai bascule le
    # RESTE du document sur les règles (pas d'empilement d'inférences qui
    # expireraient en cascade) — le traitement aboutit quand même.
    monkeypatch.setattr(llm_mod, "llm_attemptable", lambda: True)
    monkeypatch.setattr(ee, "_charger_modele", lambda report: "dummy-model")
    monkeypatch.setattr(llm_mod, "LLM_CHUNK_CHARS", 40)  # force plusieurs segments

    def correction_timeout(text, llm):  # pragma: no cover - test
        raise TimeoutError("délai de correction OCR dépassé (300 s)")

    monkeypatch.setattr(ee, "_correction_phase", correction_timeout)
    monkeypatch.setattr(
        ee,
        "_extraction_phase",
        lambda text, llm, corrige, segment=None: ([], 0.0),
    )
    texte = "ANTECEDENTS : Diabete de type 2. " * 10
    ents, report = extract_entities_with_report(texte, engine="llm")
    assert report["nb_chunks"] > 1
    assert report["statut"] == "repli_regles"
    assert any(e.section == "antecedents" for e in ents)
    assert "LLM désactivé" in report["raison"]


def test_timeout_does_not_disable_llm_permanently(monkeypatch):
    # Un dépassement de délai ne désactive PLUS le LLM pour la session :
    # le document suivant retente la phase LLM (le modèle reste chargé).
    monkeypatch.setattr(llm_mod, "llm_attemptable", lambda: True)
    monkeypatch.setattr(ee, "_charger_modele", lambda report: "dummy-model")
    monkeypatch.setattr(ee, "_correction_phase", lambda text, llm: ("corrigé", 0, 1.0))
    state = {"calls": 0}

    def slow_then_ok(text, llm, corrige, segment=None):  # pragma: no cover - test
        state["calls"] += 1
        if state["calls"] == 1:
            raise TimeoutError("délai d'extraction LLM dépassé (300 s)")
        return [ExtractedEntity("HTA", "antecedents", 0.9, "HTA", 0, 3)], 1.0

    monkeypatch.setattr(ee, "_extraction_phase", slow_then_ok)
    _, report1 = extract_entities_with_report("ANTECEDENTS : HTA.", engine="llm")
    assert report1["statut"] == "repli_regles"
    # deuxième document de la session : le LLM est RETENTÉ et réussit
    ents2, report2 = extract_entities_with_report("ANTECEDENTS : HTA.", engine="llm")
    assert [e.valeur for e in ents2] == ["HTA"]
    assert report2["statut"] == "llm_complet"


def test_llm_non_empty_result_is_kept(monkeypatch):
    # Si le LLM trouve du contenu, il est conservé tel quel (pas de règles).
    ents = [ExtractedEntity("Malaise", "points_vigilance", 0.9, "Malaise", 0, 6)]
    _patch_llm(monkeypatch, ents)
    out, report = extract_entities_with_report("Malaise au réveil.", engine="llm")
    assert len(out) == 1 and out[0].section == "points_vigilance"
    assert report["moteur"] == NLP_ENGINE_LLM


def test_rules_engine_report(monkeypatch):
    # Moteur règles explicite : rapport « regles », pas d'appel LLM.
    monkeypatch.setattr(llm_mod, "llm_attemptable", lambda: False)
    ents, report = extract_entities_with_report(
        "ANTECEDENTS : Diabete de type 2.", engine="rules"
    )
    assert report["statut"] == "regles"
    assert report["moteur"] == "rules-fr-v1"
    assert any(e.section == "antecedents" for e in ents)
