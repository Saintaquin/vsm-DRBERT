"""Vendorisation du modèle DrBERT-CASM2 pour la fabrication de l'installeur (étape 4).

RÉSEAU ICI, JAMAIS CHEZ L'UTILISATEUR : ce script s'exécute UNIQUEMENT à la
fabrication du paquet (poste du développeur, avec réseau). Le dossier
models/drbert/ produit est embarqué dans l'installeur — l'application
installée ne fait AUCUN accès réseau (contrainte cabinet sans Internet,
art. 9 : le cabinet PC n'a pas de réseau).

Usage (fabrication) :
    py -3.12 packaging/fetch_models.py              # télécharge si absent
    py -3.12 packaging/fetch_models.py --force      # re-télécharge toujours

Vérifications après téléchargement :
- fichiers attendus présents (config, safetensors, tokenizer) ;
- taille cohérente (~440 Mo) : un modèle tronqué est un modèle qui plante
  chez l'utilisateur, sans réseau pour le réparer.

Développement : tools/eval_drbert.py --download fait la même chose pour le
banc d'essai ; les deux chemins restent volontairement identiques.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))

from src.extraction_nlp.drbert_extractor import (
    FICHIERS_MODELE,
    modele_disponible,
)

# Dépôt Hugging Face (licence MIT ; base Dr-BERT Apache 2.0).
MODEL_REPO = "medkit/DrBERT-CASM2"
# Cible par défaut de la FABRICATION : toujours models/drbert/ du dépôt, sans
# lire VSM_DRBERT_PATH (variable d'EXÉCUTION de l'application installée —
# ici on fabrique le paquet, les deux contextes ne se mélangent pas).
DOSSIER_DEFAUT = RACINE / "models" / "drbert"
# Taille attendue de model.safetensors (Mo) — garde-fou contre un téléchargement
# tronqué : en dessous, le modèle corrompu partirait chez l'utilisateur.
TAILLE_MIN_MO = 400
ALLOW_PATTERNS = ["*.json", "*.txt", "*.md", "*.yml", "model.safetensors"]


def telecharger(dossier: Path) -> None:
    """Télécharge le modèle vers ``dossier`` (réseau requis)."""
    from huggingface_hub import snapshot_download

    print(f"Téléchargement de {MODEL_REPO} vers {dossier} …")
    dossier.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=MODEL_REPO,
        local_dir=str(dossier),
        allow_patterns=ALLOW_PATTERNS,
    )
    print("Téléchargement terminé (model.safetensors ; le .bin redondant est ignoré).")


def verifier(dossier: Path) -> int:
    """Vérifie complétude et taille ; renvoie 0 si tout est bon, 1 sinon."""
    manquants = [f for f in FICHIERS_MODELE if not (dossier / f).is_file()]
    if manquants:
        print(f"[ÉCHEC] fichiers manquants dans {dossier} : {manquants}")
        return 1
    taille_mo = (dossier / "model.safetensors").stat().st_size / (1024 * 1024)
    print(f"model.safetensors : {taille_mo:.0f} Mo — fichiers présents : {list(FICHIERS_MODELE)}")
    if taille_mo < TAILLE_MIN_MO:
        print(
            f"[ÉCHEC] taille inattendue (< {TAILLE_MIN_MO} Mo) : téléchargement "
            "probablement tronqué — relancer avec --force"
        )
        return 1
    print("[OK] modèle DrBERT-CASM2 prêt à être vendorisé dans l'installeur.")
    return 0


def main(argv: list[str] | None = None) -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument(
        "--force", action="store_true", help="re-télécharger même si déjà présent"
    )
    parseur.add_argument(
        "--dossier",
        type=Path,
        default=DOSSIER_DEFAUT,
        help="dossier cible (défaut : models/drbert/ du dépôt)",
    )
    args = parseur.parse_args(argv)

    dossier = args.dossier
    if not args.force and modele_disponible(dossier):
        print(f"Modèle déjà présent dans {dossier} — rien à faire (--force pour re-télécharger).")
        return verifier(dossier)

    telecharger(dossier)
    return verifier(dossier)


if __name__ == "__main__":
    raise SystemExit(main())
