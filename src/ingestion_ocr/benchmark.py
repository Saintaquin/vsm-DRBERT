"""Benchmark des moteurs OCR sur le dataset synthétique.

Pour chaque (image, moteur) : CER + WER (jiwer) + temps, avec et sans
preprocessing. Exporte outputs/benchmark.csv et outputs/BENCHMARK_REPORT.md
avec recommandation argumentée.

Usage : python -m src.ingestion_ocr.benchmark [--engines tesseract doctr]"""

from __future__ import annotations

import argparse
import csv
import time
import unicodedata
from pathlib import Path
from statistics import mean

from jiwer import cer, wer
from PIL import Image

from .ocr_engines import ENGINES, get_engine
from .preprocessing import preprocess_image

ROOT = Path(__file__).resolve().parents[2]
SYNTH = ROOT / "data" / "synthetic"
OUT = ROOT / "outputs"


def _norm(s: str) -> str:
    s = "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )
    return " ".join(s.lower().split())


def run_benchmark(engines: list[str] | None = None) -> list[dict]:
    engines = engines or sorted(ENGINES)
    images = sorted(SYNTH.glob("cas_*_*.png"))
    if not images:
        raise FileNotFoundError(
            "Dataset absent — lancer python -m src.ingestion_ocr.generate_dataset"
        )
    rows = []
    for img_path in images:
        case_id = "_".join(img_path.stem.split("_")[:2])
        variant = img_path.stem.split("_")[-1]
        gt = _norm((SYNTH / f"{case_id}_ground_truth.txt").read_text(encoding="utf-8"))
        img = Image.open(img_path)
        for engine_name in engines:
            engine = get_engine(engine_name)
            for prep in (True, False):
                t0 = time.perf_counter()
                target = preprocess_image(img)["processed"] if prep else img
                try:
                    hyp = _norm(engine.recognize(target).text)
                except Exception as exc:
                    rows.append(
                        {
                            "image": img_path.name,
                            "case": case_id,
                            "variant": variant,
                            "engine": engine_name,
                            "preprocessing": prep,
                            "cer": 1.0,
                            "wer": 1.0,
                            "time_sec": 0.0,
                            "error": str(exc),
                        }
                    )
                    continue
                rows.append(
                    {
                        "image": img_path.name,
                        "case": case_id,
                        "variant": variant,
                        "engine": engine_name,
                        "preprocessing": prep,
                        "cer": round(cer(gt, hyp), 4) if hyp else 1.0,
                        "wer": round(wer(gt, hyp), 4) if hyp else 1.0,
                        "time_sec": round(time.perf_counter() - t0, 2),
                        "error": "",
                    }
                )
    return rows


def write_outputs(rows: list[dict]) -> None:
    OUT.mkdir(exist_ok=True)
    with (OUT / "benchmark.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    lines = [
        "# Rapport de benchmark OCR — vsm-ocr",
        "",
        f"Images : {len({r['image'] for r in rows})} · Moteurs : {sorted({r['engine'] for r in rows})}",
        "",
        "| Moteur | Preprocessing | CER moyen | WER moyen | Temps moyen (s) |",
        "|---|---|---|---|---|",
    ]
    summary = {}
    for engine in sorted({r["engine"] for r in rows}):
        for prep in (True, False):
            sub = [
                r
                for r in rows
                if r["engine"] == engine
                and r["preprocessing"] == prep
                and not r["error"]
            ]
            if not sub:
                continue
            c, w_, t = (
                mean(r["cer"] for r in sub),
                mean(r["wer"] for r in sub),
                mean(r["time_sec"] for r in sub),
            )
            summary[(engine, prep)] = c
            lines.append(
                f"| {engine} | {'oui' if prep else 'non'} | {c:.4f} | {w_:.4f} | {t:.2f} |"
            )

    lines += [
        "",
        "## Détail par variante de dégradation",
        "",
        "| Moteur | Variante | CER moyen (avec preprocessing) |",
        "|---|---|---|",
    ]
    for engine in sorted({r["engine"] for r in rows}):
        for variant in ("clean", "skewed", "blurred", "noisy"):
            sub = [
                r
                for r in rows
                if r["engine"] == engine
                and r["variant"] == variant
                and r["preprocessing"]
                and not r["error"]
            ]
            if sub:
                lines.append(
                    f"| {engine} | {variant} | {mean(r['cer'] for r in sub):.4f} |"
                )

    best = min(summary, key=summary.get) if summary else None
    if best:
        engine_name, best_prep = best
        other = summary.get((engine_name, not best_prep))
        if other is not None and other > 0:
            delta = (1 - summary[best] / other) * 100
            prep_label = "avec" if best_prep else "sans"
            if abs(delta) < 2:
                gain = (
                    " Avec et sans preprocessing, les CER sont équivalents (écart < 2 %) "
                    "sur ce dataset synthétique propre ; le preprocessing garde son intérêt "
                    "sur de vrais scans (inclinaison, bruit de numérisation)."
                )
            else:
                gain = (
                    f" Le mode « {prep_label} preprocessing » améliore le CER de {delta:.0f} % "
                    f"par rapport à l'autre mode sur ce jeu de test."
                )
        else:
            gain, prep_label = "", "avec" if best_prep else "sans"
        lines += [
            "",
            "## Recommandation",
            "",
            f"Sur ce jeu de test, le meilleur compromis est **{engine_name}** "
            f"(CER {summary[best]:.4f} {prep_label} preprocessing).{gain} "
            "Pour du médical français on-premises sans GPU, Tesseract+fra "
            "reste la référence en sobriété ; DocTR/Paddle, s'ils sont installés, peuvent gagner "
            "en précision sur les scans dégradés au prix d'une empreinte mémoire bien supérieure. "
            "À rejouer sur de vrais scans hospitaliers (anonymisés) avant décision finale.",
        ]
    (OUT / "BENCHMARK_REPORT.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--engines", nargs="*", default=None)
    args = parser.parse_args()
    rows = run_benchmark(args.engines)
    write_outputs(rows)
    print(f"{len(rows)} mesures → outputs/benchmark.csv + outputs/BENCHMARK_REPORT.md")
