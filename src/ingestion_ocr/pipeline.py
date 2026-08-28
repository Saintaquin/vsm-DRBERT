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


def _load_pages(input_path: Path, max_pages: int | None = None):
    """Générateur (numéro de page réel, image). Pour les PDF : par LOTS de
    pages — la conversion complète d'un gros PDF en une fois provoque une
    saturation mémoire (résultat OCR vide). Pour une image : une seule page.

    ``max_pages`` (optionnel) : borne le nombre de pages converties — les lots
    sont ajustés pour ne jamais convertir plus que demandé (utile pour les
    documents volumineux traités partiellement, ex. banc d'essai).

    Pas de dépendance pypdf : la fin du document est détectée quand un lot est
    vide (pdf2image retourne [] au-delà de la dernière page)."""
    suffix = input_path.suffix.lower()
    if suffix == ".pdf":
        from pdf2image import convert_from_path

        def _gen():
            start = 0
            while max_pages is None or start < max_pages:
                lot = _OCR_PDF_BATCH
                if max_pages is not None:
                    lot = min(_OCR_PDF_BATCH, max_pages - start)
                pages = convert_from_path(
                    str(input_path),
                    dpi=200,
                    first_page=start + 1,
                    last_page=start + lot,
                )
                if not pages:  # fin du document
                    break
                yield from enumerate(pages, start=start + 1)
                start += lot

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
    on_page=None,
    max_pages: int | None = None,
) -> dict:
    """Exécute le pipeline complet sur un document.

    ``document_id`` (optionnel) : identifiant EXTERNE à préserver (ex. id du
    document uploadé) — sans lui, un identifiant interne est généré. IMPORTANT
    pour la cohérence « passage source » : le VSM référence ce document_id
    (source.document_id) ; il doit correspondre à la clé sous laquelle l'OCR
    est stocké (bug « Voir le passage source » corrigé).

    ``on_page`` (optionnel) : callback appelé à chaque page lue (progression
    dans l'interface sur les gros PDF multi-pages).

    ``max_pages`` (optionnel) : ne traiter que les N premières pages (None =
    tout le document) — utilisé par les outils de développement (banc
    d'essai) pour borner le temps OCR sur les documents volumineux.

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
    if max_pages is not None and max_pages < 1:
        raise ValueError(f"max_pages invalide : {max_pages}")

    document_id = document_id or f"doc_{uuid.uuid4().hex[:12]}"
    sha256 = _sha256_file(input_path)
    ocr = get_engine(engine)

    pages_out: list[dict] = []
    anomalies: list[dict] = []
    raw_pages: list[str] = []

    try:
        pages = _load_pages(input_path, max_pages=max_pages)
    except Exception as exc:  # document entier illisible (création du générateur)
        raise RuntimeError(f"Document illisible : {exc}") from exc

    try:
        for idx, page_img in pages:
            if on_page:
                on_page(idx)
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
            except Exception as exc:  # page individuelle en erreur
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
    except Exception as exc:  # erreur de conversion d'un lot PDF (poppler)
        raise RuntimeError(f"Document illisible : {exc}") from exc

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
