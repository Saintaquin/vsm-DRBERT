"""PDF/image → textes OCR anonymisés pour le banc d'essai DrBERT (étape 0).

Prépare les entrées de tools/eval_drbert.py : lance la chaîne d'ingestion du
PROJET (src/ingestion_ocr/pipeline.py — prétraitement + Tesseract fra +
pseudonymisation), puis écrit UN fichier .txt par page (texte anonymisé) dans
le dossier de sortie. Aucune logique OCR propre : ce qui est mesuré par le
banc est exactement ce que produira l'application.

- Anonymisation par défaut « pseudo » : jetons typés [PATIENT_001], [NIR_001]…
  Le mapping jeton→identité renvoyé par le pipeline (_pii_mapping) est
  VOLONTAIREMENT JETÉ ici : outil de développement, pas de coffre-fort — il ne
  doit jamais finir sur disque en clair (règle projet).
- Un `_resume.json` sans PII (document_id, sha256, compteurs) accompagne les
  textes pour la traçabilité.
- Sortie par défaut dans outputs/eval_drbert/ (ignoré par git) — les textes
  OCR, même anonymisés, ne doivent pas être commités.

Usage (PowerShell — ATTENTION : pas de chevrons < > autour des chemins,
ce sont des marqueurs de substitution dans les exemples, pas de la syntaxe) :

    py -3.12 tools/pdf_vers_texte.py --input "C:\\chemin\\document.pdf" --max-pages 15
    py -3.12 tools/eval_drbert.py --input outputs/eval_drbert --max-length 128
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _configurer_console() -> None:
    """Console en UTF-8 (PowerShell sous Windows casse les accents sinon)."""
    for flux in (sys.stdout, sys.stderr):
        try:
            flux.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def _importer_pipeline():
    """Importe run_pipeline du projet (src/ingestion_ocr/pipeline.py)."""
    racine = str(PROJECT_ROOT)
    if racine not in sys.path:
        sys.path.insert(0, racine)
    from src.ingestion_ocr.pipeline import run_pipeline

    return run_pipeline


def _stem_sur(nom: str) -> str:
    """Nom de fichier sûr : ABRICOT_Anthony.pdf → ABRICOT_Anthony."""
    stem = Path(nom).stem
    return re.sub(r"[^A-Za-z0-9_-]+", "_", stem) or "document"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parseur = argparse.ArgumentParser(
        prog="pdf_vers_texte",
        description=(
            "PDF ou image → un fichier .txt par page (texte OCR anonymisé), "
            "prêts pour tools/eval_drbert.py. Réutilise la chaîne d'ingestion "
            "du projet (Tesseract + pseudonymisation)."
        ),
    )
    parseur.add_argument(
        "--input",
        required=True,
        type=Path,
        help="Chemin du PDF ou de l'image source (guillemets si espaces)",
    )
    parseur.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/eval_drbert"),
        help="Dossier de sortie (défaut : outputs/eval_drbert — ignoré par git)",
    )
    parseur.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="Ne traiter que les N premières pages (défaut : 0 = tout)",
    )
    parseur.add_argument(
        "--mode",
        choices=("pseudo", "strict", "off"),
        default="pseudo",
        help="Anonymisation : pseudo (jetons [PATIENT_001], défaut), strict, off",
    )
    parseur.add_argument("--lang", default="fra", help="Langue OCR (défaut : fra)")
    parseur.add_argument(
        "--engine", default="tesseract", help="Moteur OCR (défaut : tesseract)"
    )
    parseur.add_argument(
        "--no-preprocess",
        action="store_true",
        help="Désactiver le prétraitement d'image (redressement, netteté…)",
    )
    return parseur.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    _configurer_console()
    args = _parse_args(argv)

    source = args.input.resolve()
    if not source.is_file():
        print(f"Introuvable : {source}")
        return 2

    sortie = args.output.resolve()
    sortie.mkdir(parents=True, exist_ok=True)

    run_pipeline = _importer_pipeline()

    def _progression(num_page: int) -> None:
        print(f"  page {num_page} : OCR en cours…")

    print(f"Source     : {source.name} ({source.stat().st_size / 1e6:.1f} Mo)")
    print(f"Sortie     : {sortie}")
    print(f"Anonymisation : {args.mode} — moteur {args.engine} ({args.lang})")
    if args.max_pages > 0:
        print(f"Limite    : {args.max_pages} premières pages")

    try:
        resultat = run_pipeline(
            source,
            engine=args.engine,
            lang=args.lang,
            anonymize_mode=args.mode,
            preprocess=not args.no_preprocess,
            on_page=_progression,
            max_pages=args.max_pages or None,
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"Échec d'ingestion : {exc}")
        return 2

    # _pii_mapping ne doit JAMAIS atteindre le disque depuis cet outil.
    mapping = resultat.pop("_pii_mapping", None)

    stem = _stem_sur(resultat["source_file"])
    ecrits: list[Path] = []
    vides = 0
    total_caracteres = 0
    for page in resultat["pages"]:
        texte = (page.get("text") or "").strip()
        if not texte:
            vides += 1
            continue
        chemin = sortie / f"{stem}_p{page['page']:03d}.txt"
        chemin.write_text(texte + "\n", encoding="utf-8")
        ecrits.append(chemin)
        total_caracteres += len(texte)

    resume = {
        "document_id": resultat["document_id"],
        "source_file": resultat["source_file"],
        "sha256": resultat["sha256"],
        "anonymization_mode": args.mode,
        "pii_detected_count": resultat.get("pii_detected_count", 0),
        "pages_traitees": len(resultat["pages"]),
        "pages_ecrites": len(ecrits),
        "pages_vides": vides,
        "total_caracteres": total_caracteres,
        "duree_ocr_s": resultat["processing_report"]["duration_sec"],
    }
    (sortie / "_resume.json").write_text(
        json.dumps(resume, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(
        f"\nTerminé : {len(ecrits)} page(s) écrite(s) "
        f"({vides} vide(s) ignorée(s)), {total_caracteres} caractères, "
        f"OCR {resultat['processing_report']['duration_sec']:.1f} s, "
        f"{resultat.get('pii_detected_count', 0)} PII anonymisée(s)."
    )
    if mapping is not None and args.mode == "pseudo":
        print(
            "Mapping de pseudonymisation jeté (outil de développement, "
            "pas de coffre-fort) — les textes ne sont PAS réversibles ici."
        )
    if not ecrits:
        print("Aucun texte extrait : vérifier le PDF (scan lisible ? poppler ?).")
        return 1

    print("\nBanc d'essai ensuite (copier-coller tel quel, SANS chevrons) :")
    print(f'  py -3.12 tools/eval_drbert.py --input "{sortie}" '
          "--max-length 128 --output eval_f128.csv")
    print(f'  py -3.12 tools/eval_drbert.py --input "{sortie}" '
          "--max-length 512 --output eval_f512.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
