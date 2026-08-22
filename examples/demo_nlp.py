"""Démo NLP : texte clinique exemple -> JSON structuré conforme au schéma.
Usage : python -m examples.demo_nlp"""

import json

from src.extraction_nlp.pipeline import run_pipeline

OCR_JSON = {
    "document_id": "demo_001",
    "source_file": "demo.txt",
    "ocr_engine": "demo",
    "text": (
        "ANTECEDENTS : Diabete de type 2 depuis 2010. Hypertension arterielle.\n"
        "ALLERGIES : Penicilline (eruption cutanee).\n"
        "TRAITEMENTS EN COURS : Metformine 1000 mg matin et soir. Ramipril 5 mg le matin.\n"
        "VACCINATIONS : Grippe 10/2023. DTP a jour.\n"
    ),
    "metadata": {"pages": 1},
    "pii_detected_count": 0,
    "anonymization_applied": False,
    "sha256": "0" * 64,
}

if __name__ == "__main__":
    print(json.dumps(run_pipeline(OCR_JSON), ensure_ascii=False, indent=2))
