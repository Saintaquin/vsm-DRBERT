"""Gestion du modèle LLM local (100 % offline — aucune donnée ne quitte la
machine, exigence RGPD art. 9 du concours).

- Chemin du modèle : variable d'environnement ``VSM_LLM_MODEL_PATH``, sinon
  ``~/.cache/vsm-ocr/model.gguf`` (défaut).
- Téléchargement : ``python -m src.extraction_nlp.llm`` (ou la fonction
  ``download_model``) récupère un GGUF depuis Hugging Face — à l'installation,
  par l'administrateur ; jamais pendant le traitement de documents.
- Le choix du modèle par défaut est documenté dans docs/ADR/0004 (audit des
  modèles, licences, contraintes matérielles).
"""

from __future__ import annotations

import os
import sys
import urllib.request
from pathlib import Path

# Référence : ~/.cache/vsm-ocr/ (jamais committé, cf. .gitignore)
CACHE_DIR = Path(os.environ.get("VSM_LLM_CACHE", Path.home() / ".cache" / "vsm-ocr"))

# Modèles candidats recommandés (audit ADR-0004) : licence, taille GGUF Q4_K_M
# approximative, RAM nécessaire, qualité français, note globale /5.
# Le premier élément est le modèle par défaut recommandé.
RECOMMENDED_MODELS = [
    {
        "key": "mistral-nemo-12b",
        "nom": "Mistral NeMo 12B Instruct",
        "hf_repo": "TheBloke/Mistral-Nemo-Instruct-12B-GGUF",
        "hf_file": "mistral-nemo-instruct-12b.Q4_K_M.gguf",
        "url": "https://huggingface.co/TheBloke/Mistral-Nemo-Instruct-12B-GGUF/resolve/main/mistral-nemo-instruct-12b.Q4_K_M.gguf",
        "taille_gb": 7.2,
        "ram_min_gb": 16,
        "licence": "Apache 2.0",
        "francais": "Excellent (Mistral AI, française)",
        "note": 5,
    },
    {
        "key": "qwen2.5-7b",
        "nom": "Qwen 2.5 7B Instruct",
        "hf_repo": "Qwen/Qwen2.5-7B-Instruct-GGUF",
        "hf_file": "qwen2.5-7b-instruct-q4_k_m.gguf",
        "url": "https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/main/qwen2.5-7b-instruct-q4_k_m.gguf",
        "taille_gb": 4.7,
        "ram_min_gb": 8,
        "licence": "Apache 2.0",
        "francais": "Bon (multilingue fort)",
        "note": 4,
    },
    {
        "key": "mistral-7b-v0.3",
        "nom": "Mistral 7B Instruct v0.3",
        "hf_repo": "TheBloke/Mistral-7B-Instruct-v0.3-GGUF",
        "hf_file": "mistral-7b-instruct-v0.3.Q4_K_M.gguf",
        "url": "https://huggingface.co/TheBloke/Mistral-7B-Instruct-v0.3-GGUF/resolve/main/mistral-7b-instruct-v0.3.Q4_K_M.gguf",
        "taille_gb": 4.1,
        "ram_min_gb": 8,
        "licence": "Apache 2.0",
        "francais": "Très bon (française)",
        "note": 4,
    },
    {
        "key": "llama3.1-8b",
        "nom": "Llama 3.1 8B Instruct (choix ADR-0001)",
        "hf_repo": "TheBloke/Llama-3.1-8B-Instruct-GGUF",
        "hf_file": "llama-3.1-8b-instruct.Q4_K_M.gguf",
        "url": "https://huggingface.co/TheBloke/Llama-3.1-8B-Instruct-GGUF/resolve/main/llama-3.1-8b-instruct.Q4_K_M.gguf",
        "taille_gb": 4.9,
        "ram_min_gb": 12,
        "licence": "Llama Community License (non exclusive, <700M MAU)",
        "francais": "Bon",
        "note": 3,
    },
    {
        "key": "llama3.2-3b",
        "nom": "Llama 3.2 3B Instruct (ultra-léger)",
        "hf_repo": "TheBloke/Llama-3.2-3B-Instruct-GGUF",
        "hf_file": "llama-3.2-3b-instruct.Q4_K_M.gguf",
        "url": "https://huggingface.co/TheBloke/Llama-3.2-3B-Instruct-GGUF/resolve/main/llama-3.2-3b-instruct.Q4_K_M.gguf",
        "taille_gb": 2.0,
        "ram_min_gb": 4,
        "licence": "Llama Community License",
        "francais": "Correct (moins précis)",
        "note": 2,
    },
]


def default_model_path() -> Path:
    """Chemin du GGUF : VSM_LLM_MODEL_PATH sinon ~/.cache/vsm-ocr/model.gguf."""
    env = os.environ.get("VSM_LLM_MODEL_PATH")
    if env:
        return Path(env)
    return CACHE_DIR / "model.gguf"


def model_available() -> bool:
    path = default_model_path()
    return path.exists() and path.stat().st_size > 1_000_000  # ≥ ~1 Mo (en-tête)


def _validate_gguf(path: Path) -> bool:
    """L'en-tête d'un GGUF commence par la signature « GGUF »."""
    try:
        with path.open("rb") as f:
            return f.read(4) == b"GGUF"
    except OSError:
        return False


def download_model(
    key: str | None = None, dest: str | Path | None = None, force: bool = False
) -> Path:
    """Télécharge le modèle recommandé (ou celui de la clé donnée) vers dest.

    Strictement hors flux de traitement : à lancer par l'administrateur, au
    préalable (aucune donnée patient n'est impliquée)."""
    if key is None:
        key = os.environ.get("VSM_LLM_MODEL", RECOMMENDED_MODELS[0]["key"])
    try:
        meta = next(m for m in RECOMMENDED_MODELS if m["key"] == key)
    except StopIteration:
        raise ValueError(
            f"Modèle inconnu : {key!r}. Disponibles : "
            + ", ".join(m["key"] for m in RECOMMENDED_MODELS)
        ) from None

    dest = Path(dest) if dest else default_model_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not force:
        if _validate_gguf(dest):
            print(f"Modèle déjà présent : {dest} ({dest.stat().st_size / 1e6:.0f} Mo)")
            return dest
        print(f"Fichier existant invalide ({dest}) — re-téléchargement.")

    print(
        f"Téléchargement de {meta['nom']} ({meta['taille_gb']:.1f} Go, "
        f"licence {meta['licence']}) depuis Hugging Face…"
    )
    print("  " + meta["url"])
    tmp = dest.with_suffix(".part")
    req = urllib.request.Request(meta["url"], headers={"User-Agent": "vsm-ocr/1.0"})
    with urllib.request.urlopen(req) as resp, tmp.open("wb") as f:
        total = int(resp.headers.get("Content-Length", 0))
        done = 0
        while chunk := resp.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            if total:
                pct = done * 100 // total
                print(f"\r  {done / 1e6:.0f} / {total / 1e6:.0f} Mo ({pct} %)", end="")
    print("\n")
    if not _validate_gguf(tmp):
        tmp.unlink(missing_ok=True)
        raise RuntimeError("Fichier téléchargé invalide (signature GGUF absente).")
    tmp.replace(dest)
    print(f"Modèle prêt : {dest} ({dest.stat().st_size / 1e6:.0f} Mo)")
    return dest


def main() -> int:
    """python -m src.extraction_nlp.llm [--model KEY] [--dest CHEMIN] [--force]"""
    import argparse

    ap = argparse.ArgumentParser(description="Télécharge le modèle LLM local VSM-OCR")
    ap.add_argument(
        "--model",
        default=os.environ.get("VSM_LLM_MODEL"),
        help="clé du modèle (défaut : le modèle recommandé)",
    )
    ap.add_argument("--dest", default=None, help="chemin de destination du GGUF")
    ap.add_argument(
        "--force", action="store_true", help="re-télécharger même si présent"
    )
    ap.add_argument(
        "--list", action="store_true", help="lister les modèles disponibles"
    )
    args = ap.parse_args()

    if args.list:
        print("Modèles disponibles (audit ADR-0004) :")
        for m in RECOMMENDED_MODELS:
            star = (
                " *"
                if m["key"]
                == os.environ.get("VSM_LLM_MODEL", RECOMMENDED_MODELS[0]["key"])
                else ""
            )
            print(
                f"  - {m['key']:16s} {m['nom']:32s} {m['taille_gb']:.1f} Go | "
                f"{m['licence']:35s} | RAM ≥ {m['ram_min_gb']} Go | note {m['note']}/5{star}"
            )
        return 0
    download_model(args.model, args.dest, args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
