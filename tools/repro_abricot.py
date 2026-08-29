"""Reproduction ABRICOT avec journal des rejets — chaîne causale complète.

Objectif : expliquer pourquoi « maladie rénale chronique » et « MRC » sont
absents du VSM. Pour chaque occurrence des termes dans le texte OCR (anonymisé) :
1. la page où elle se trouve ;
2. ce que le modèle DrBERT a détecté autour (entités BRUTES, avant filtres) ;
3. le rejet journalisé responsable (règle + détail), s'il y en a un ;
4. la présence éventuelle dans le VSM final.

Usage :
    py -3.12 tools/repro_abricot.py            # depuis outputs/repro_abricot
"""

from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))

from src.extraction_nlp import drbert_extractor as dtx
from src.extraction_nlp.filtres_vsm import carte_pages, ligne_rejet, page_de
from src.extraction_nlp.pipeline import run_pipeline

CIBLES = ["maladie rénale chronique", "MRC"]


def _norm(s: str) -> str:
    sans = "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"\s+", " ", sans).lower()


def _rx_cible(cible: str) -> re.Pattern:
    """Regex tolérante accents/espaces multiples — offsets BRUTS exacts."""
    equiv = {
        "e": "[eèéêë]", "a": "[aàâä]", "i": "[iìîï]", "o": "[oôö]",
        "u": "[uùûü]", "c": "[cç]", "y": "[yÿ]",
    }
    motif = "".join(equiv.get(ch, re.escape(ch)) for ch in cible.lower())
    motif = re.sub(r"\\\s+", r"\\s+", motif)  # espaces → \s+
    return re.compile(rf"\b{motif}\b", re.IGNORECASE)


def main() -> int:
    dossier = RACINE / "outputs" / "repro_abricot"
    pages_txt = sorted(
        (p for p in dossier.glob("*_p*.txt")),
        key=lambda p: int(re.search(r"_p(\d+)", p.stem).group(1)),
    )
    pages = []
    for i, f in enumerate(pages_txt, start=1):
        pages.append({"page": i, "text": f.read_text(encoding="utf-8")})
    texte = "\n\n".join(p["text"] for p in pages)
    ocr_json = {
        "document_id": "repro_abricot",
        "ocr_engine": "tesseract",
        "source_file": "ABRICOT_Anthony.pdf",
        "text": texte,
        "pages": pages,
    }
    carte = carte_pages(texte, pages)
    print(f"{len(pages)} pages · {len(texte)} caractères · carte "
          f"{'valide' if carte else 'INVALIDE'}")

    # --- 1. Occurrences des cibles dans le texte (offsets bruts) ----------
    positions: list[tuple[int, str, int]] = []  # (offset, cible, page)
    for cible in CIBLES:
        for m in _rx_cible(cible).finditer(texte):
            positions.append((m.start(), cible, page_de(carte, m.start())))
    print("\n=== Occurrences (insensible casse/accents) ===")
    for offset, cible, page in positions:
        print(f"  page {page} · offset {offset} · « {cible} »")

    # --- 2. Entités BRUTES du modèle autour des occurrences ----------------
    moteur = dtx._get_moteur()
    brutes = moteur.annoter(texte)
    print(f"\n=== DrBERT brut : {len(brutes)} entités (avant filtres) ===")
    for offset, cible, page in positions:
        zone = [
            e for e in brutes
            if abs(e.debut - offset) <= 40 or (e.debut <= offset <= e.fin)
        ]
        if zone:
            for e in zone:
                print(f"  page {page} · autour de « {cible} » : "
                      f"[{e.label}] « {e.texte} » score={e.score:.3f} "
                      f"offsets={e.debut}-{e.fin}")
        else:
            print(f"  page {page} · autour de « {cible} » : RIEN détecté")

    # --- 3. Pipeline complet avec journal des rejets -----------------------
    vsm = run_pipeline(ocr_json, nlp_engine="drbert")
    rejets = vsm["provenance"]["nlp"].get("rejets") or []
    print(f"\n=== Journal des rejets : {len(rejets)} entrées ===")
    for r in rejets:
        ligne = ligne_rejet(r)
        if any(_norm(c) in _norm(r.get("valeur", "")) or _norm(r.get("valeur", "")) in _norm(c) for c in CIBLES):
            print(f"  *** {ligne}")
    print("\n--- Rejets liés aux cibles (ci-dessus ***) ---")
    print("\n=== Par règle ===")
    compte: dict[str, int] = {}
    for r in rejets:
        compte[r["regle"]] = compte.get(r["regle"], 0) + 1
    for regle, n in sorted(compte.items(), key=lambda kv: -kv[1]):
        print(f"  {n:4} × {regle}")

    # --- 4. Présence dans le VSM final ------------------------------------
    print("\n=== VSM final : les cibles y figurent-elles ? ===")
    for cible in CIBLES:
        bas_c = _norm(cible)
        trouve = []
        for rub, items in vsm["sections"].items():
            for c in items:
                if bas_c in _norm(c["valeur"]) or _norm(c["valeur"]) in bas_c:
                    trouve.append((rub, c["valeur"]))
        print(f"  « {cible} » : {trouve if trouve else 'ABSENT'}")

    # --- 5. Rejets P6 complets ------------------------------------------------
    print("\n=== Rejets P6 (en-tête répété) — liste complète ===")
    for r in rejets:
        if r["regle"] == "P6_entete_repété":
            print(f"  {ligne_rejet(r)}")

    # --- 6. SIMULATION : zone d'en-tête stricte (500 car. sans extension
    #        jusqu'au premier titre) — donnée pour la décision de recalage,
    #        AUCUN changement de comportement ici.
    print("\n=== Simulation : zone d'en-tête stricte (500 car.) ===")
    formes_p6 = sorted({
        r["valeur"] for r in rejets if r["regle"] == "P6_entete_repété"
    })
    strictes = [t[:500] for t in (p["text"] for p in pages)]
    zones_strictes = [_norm(z) for z in strictes]
    for forme in formes_p6:
        bas = _norm(forme)
        n_strict = sum(1 for z in zones_strictes if bas in z)
        print(f"  « {forme} » : encore {n_strict} pages en zone stricte "
              f"{'→ toujours rejeté' if n_strict > 3 else '→ SURVIVRAIT'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
