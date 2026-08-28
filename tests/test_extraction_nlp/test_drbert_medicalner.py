"""Tests de l'adaptateur DrBERT (NER médical français).

L'inférence réelle (torch + modèle) n'est pas exécutée en CI : on teste les
fonctions pures (regroupement BIO, mapping étiquettes→VSM) et l'augmentation
dédupliquée de l'extraction."""

from src.extraction_nlp import drbert as db
from src.extraction_nlp import entity_extractor as ee
from src.extraction_nlp.entity_extractor import ExtractedEntity


def test_group_bio():
    tokens = [
        "Le",
        "patient",
        "a",
        "un",
        "diabète",
        "sévère",
        "et",
        "prend",
        "metformine",
    ]
    labels = [
        "O",
        "O",
        "O",
        "O",
        "B-Disease",
        "I-Disease",
        "O",
        "O",
        "B-Medication/Vaccine",
    ]
    ents = db.group_bio(tokens, labels)
    # une entité Disease (2 tokens) + une Medication/Vaccine (1 token)
    diso = [e for e in ents if e["label"] == "Disease"]
    meds = [e for e in ents if e["label"] == "Medication/Vaccine"]
    assert len(diso) == 1 and diso[0]["text"] == "diabète sévère"
    assert len(meds) == 1 and meds[0]["text"] == "metformine"


def test_group_bio_handles_B_and_offset():
    tokens = ["x", "métastase", "hépatique"]
    labels = ["O", "B-Disease", "I-Disease"]
    ents = db.group_bio(tokens, labels)
    assert ents[0]["text"] == "métastase hépatique"
    assert ents[0]["start"] == 1 and ents[0]["end"] == 3


def test_label_mapping():
    assert db.map_label("B-Disease") == "pathologies_actives"
    assert db.map_label("B-Medication/Vaccine") == "traitements_long_cours"
    assert db.map_label("B-MedicalProcedure") == "antecedents"
    assert db.map_label("B-Symptom") == "points_vigilance"
    assert db.map_label("B-PER") is None  # identité déjà pseudonymisée
    assert db.map_label("B-LOC") is None  # métadonnée


def test_label_to_section_context():
    # « Disease » en contexte antécédent → antecedents
    assert (
        db.label_to_section("B-Disease", "antécédents : cardiopathie") == "antecedents"
    )
    # sans contexte → pathologies_actives
    assert (
        db.label_to_section("B-Disease", "le patient présente") == "pathologies_actives"
    )


def test_is_available_without_torch():
    import importlib

    # torch/transformers peuvent ne pas être installés → is_available False
    db_mod = importlib.import_module("src.extraction_nlp.drbert")
    assert isinstance(db_mod.is_available(), bool)


def test_augment_with_drbert_dedup(monkeypatch):
    # L'augmentation ajoute les entités DrBERT manquantes, sans doublon,
    # avec le moteur tracé.
    base = [
        ExtractedEntity("Diabète de type 2", "antecedents", 0.8, "x", 0, 18),
    ]
    monkeypatch.setattr(db, "is_available", lambda: True)
    monkeypatch.setattr(
        db,
        "extract_entities_drbert",
        lambda text: [
            {
                "valeur": "Diabète de type 2",
                "section": "antecedents",
                "confiance": 0.7,
                "passage": "Diabète de type 2",
                "offset_debut": 0,
                "offset_fin": 18,
            },  # doublon
            {
                "valeur": "Insuffisance cardiaque",
                "section": "pathologies_actives",
                "confiance": 0.7,
                "passage": "Insuffisance cardiaque",
                "offset_debut": 30,
                "offset_fin": 51,
            },
        ],
    )
    out = ee._augment_with_drbert(base, "texte")
    vals = [(e.valeur, e.section) for e in out]
    assert ("Diabète de type 2", "antecedents") in vals
    assert ("Insuffisance cardiaque", "pathologies_actives") in vals
    assert vals.count(("Diabète de type 2", "antecedents")) == 1  # pas de doublon
    added = [e for e in out if e.moteur_nlp == "drbert-nlp-v1"]
    assert len(added) == 1
    assert added[0].confiance == 0.7  # sous le seuil → « À valider »
