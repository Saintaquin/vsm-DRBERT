"""Pipeline d'ingestion : document (PDF ou image) → JSON OCR contractuel.

- PDF multi-pages via pdf2image (Poppler), images PNG/JPG directes
- preprocess_image() puis moteur OCR sélectionné
- Anonymisation niveau texte intégrée : "off" | "pseudo" | "strict"
- Tolérance aux pannes : une page corrompue est consignée, le pipeline continue
- Sortie : document_id, source_file, sha256, ocr_engine, pages[], text,
  metadata, anonymization_applied, pii_detected_count, processing_report
"""

from __future__ import annotations

import hashlib
import os
import time
import uuid
from pathlib import Path

from PIL import Image

from src.anonymization.anonymizer import anonymize
from src.anonymization.audit import build_audit_entries
from src.anonymization.pseudonymizer import Pseudonymizer

from .ocr_engines import get_engine
from .preprocessing import preprocess_image

PIPELINE_VERSION = "1.0.0"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# Taille des LOTS de pages PDF traités en mémoire (correction « OCR vide » sur
# les gros PDF) : convert_from_path(sans bornes) charge TOUTES les pages en
# RAM → OOM/swap sur les documents volumineux → pages vides. Le découpage par
# lots (défaut 20 pages, configurable VSM_OCR_PDF_BATCH) borne la mémoire.
_OCR_PDF_BATCH = int(os.environ.get("VSM_OCR_PDF_BATCH", "20"))


def _load_pages(input_path: Path):
    """Générateur (numéro de page réel, image). Pour les PDF : par LOTS de
    pages — la conversion complète d'un gros PDF en une fois provoque une
    saturation mémoire (résultat OCR vide). Pour une image : une seule page."""
    suffix = input_path.suffix.lower()
    if suffix == ".pdf":
        from pdf2image import convert_from_path
        from pypdf import PdfReader

        try:
            total = len(PdfReader(str(input_path)).pages)
        except Exception as exc:  # document entier illisible
            raise RuntimeError(f"PDF illisible : {exc}") from exc

        def _gen():
            for start in range(0, total, _OCR_PDF_BATCH):
                end = min(start + _OCR_PDF_BATCH, total)
                pages = convert_from_path(
                    str(input_path),
                    dpi=200,
                    first_page=start + 1,
                    last_page=end,
                )
                yield from enumerate(pages, start=start + 1)

        return _gen()

    def _one():
        yield 1, Image.open(input_path)

    return _one()


def run_pipeline(
    input_path: str | Path,
    engine: str = "tesseract",
    lang: str = "fra",
    anonymize_mode: str = "pseudo",
    preprocess: bool = True,
    dossier_id: str | None = None,
    document_id: str | None = None,
) -> dict:
    """Exécute le pipeline complet sur un document.

    ``document_id`` (optionnel) : identifiant EXTERNE à préserver (ex. id du
    document uploadé) — sans lui, un identifiant interne est généré. IMPORTANT
    pour la cohérence « passage source » : le VSM référence ce document_id
    (source.document_id) ; il doit correspondre à la clé sous laquelle l'OCR
    est stocké (bug « Voir le passage source » corrigé).

    anonymize_mode : "off" (déconseillé), "pseudo" (réversible via coffre),
    "strict" (irréversible). Le mapping de pseudonymisation est retourné dans
    la clé privée "_pii_mapping" — à stocker UNIQUEMENT dans le MappingVault,
    jamais sur disque en clair.
    """
    t0 = time.perf_counter()
    input_path = Path(input_path)
    if not input_path.exists():
        raise FileNotFoundError(input_path)
    if anonymize_mode not in ("off", "pseudo", "strict"):
        raise ValueError(f"anonymize_mode invalide : {anonymize_mode}")

    document_id = document_id or f"doc_{uuid.uuid4().hex[:12]}"
    sha256 = _sha256_file(input_path)
    ocr = get_engine(engine)

    pages_out: list[dict] = []
    anomalies: list[dict] = []
    raw_pages: list[str] = []

    try:
        pages = _load_pages(input_path)
    except Exception as exc:  # document entier illisible
        raise RuntimeError(f"Document illisible : {exc}") from exc

    for idx, page_img in pages:
        try:
            if preprocess:
                pre = preprocess_image(page_img)
                img, pre_meta = (
                    pre["processed"],
                    {
                        "steps_applied": pre["steps_applied"],
                        "angle_corrected": pre["angle_corrected"],
                        "preprocessing_time_sec": pre["processing_time_sec"],
                    },
                )
            else:
                img, pre_meta = page_img, {"steps_applied": []}
            result = ocr.recognize(img, lang=lang)
            raw_pages.append(result.text)
            pages_out.append(
                {
                    "page": idx,
                    "text": result.text,
                    "confidence": result.confidence,
                    "words": result.words,
                    **pre_meta,
                }
            )
        except Exception as exc:
            anomalies.append({"page": idx, "error": str(exc)})
            pages_out.append(
                {
                    "page": idx,
                    "text": "",
                    "confidence": 0.0,
                    "words": [],
                    "error": str(exc),
                }
            )

    full_text = "\n\n".join(raw_pages)

    # ---------------- Anonymisation niveau texte ----------------
    pii_count, mapping, audit_entries = 0, {}, []
    if anonymize_mode == "pseudo":
        pseudo = Pseudonymizer()
        res = pseudo.pseudonymize(full_text)
        full_text, mapping, pii_count = res.text, res.mapping, res.pii_count
        for p in pages_out:
            p["text"] = pseudo.pseudonymize(p["text"]).text if p["text"] else p["text"]
            p.pop(
                "words", None
            )  # les mots bruts contiennent des PII → retirés en mode anonymisé
        audit_entries = build_audit_entries(document_id, res.matches, "pseudonymize")
    elif anonymize_mode == "strict":
        res = anonymize(full_text)
        full_text, pii_count = res.text, res.pii_count
        for p in pages_out:
            p["text"] = anonymize(p["text"]).text if p["text"] else p["text"]
            p.pop("words", None)
        audit_entries = build_audit_entries(document_id, res.matches, "anonymize")

    out = {
        "document_id": document_id,
        "source_file": input_path.name,
        "sha256": sha256,
        "ocr_engine": engine,
        "lang": lang,
        "pipeline_version": PIPELINE_VERSION,
        "text": full_text,
        "pages": pages_out,
        "anonymization_applied": anonymize_mode != "off",
        "anonymization_mode": anonymize_mode,
        "pii_detected_count": pii_count,
        "dossier_id": dossier_id or document_id,
        "processing_report": {
            "pages_total": len(pages_out),
            "pages_ok": len(pages_out) - len(anomalies),
            "anomalies": anomalies,
            "duration_sec": round(time.perf_counter() - t0, 3),
            "engine": engine,
        },
        "audit_entries": audit_entries,
    }
    if anonymize_mode == "pseudo":
        out["_pii_mapping"] = mapping  # → MappingVault uniquement
    return out
