"""Test de BOUT EN BOUT de l'application avec le VRAI modèle DrBERT (étape 5).

Vérifie la chaîne complète — upload → OCR Tesseract → anonymisation →
extraction DrBERT-CASM2 → assemblage du VSM → stockage chiffré — via l'API
réelle du backend (authentification, CSRF, job asynchrone), dans un
répertoire de données TEMPORAIRE (le ~/.vsm-ocr de la machine n'est jamais
touché). Document synthétique obligatoire : aucun texte réel ne transite.

Ce que le test vérifie (décisions des étapes 0 à 3) :
- /health : DrBERT disponible, et le LLM génératif (GGUF retiré du paquet)
  correctement signalé absent ;
- le traitement n'exécute AUCUNE phase « LLM » — DrBERT est le moteur ;
- moteur tracé « drbert-casm2-v1 », statut « drbert » dans le rapport NLP
  ET dans la provenance du VSM ;
- le VSM contient des entités dont le passage est l'EXTRAIT EXACT de la
  valeur (ancrage XAI structurel de l'encodeur) ;
- sans champ nlp_engine, le moteur par défaut reste DrBERT (VSM_NLP_ENGINE).

Usage :
    py -3.12 tools/e2e_drbert.py                # cas_001_clean.png
    py -3.12 tools/e2e_drbert.py --input img.png
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))


def _check(echecs: list[str], nom: str, cond: bool, detail: str = "") -> None:
    marque = "✓" if cond else "✗ ÉCHEC"
    print(f"  [{marque}] {nom}" + (f" — {detail}" if detail else ""))
    if not cond:
        echecs.append(nom)


def main(argv: list[str] | None = None) -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument(
        "--input",
        type=Path,
        default=RACINE / "data" / "synthetic" / "cas_001_clean.png",
        help="image/PDF synthétique à traiter (défaut : cas_001_clean.png)",
    )
    args = parseur.parse_args(argv)

    if not args.input.exists():
        print(f"[ÉCHEC] document introuvable : {args.input}")
        return 1

    # Répertoire de données temporaire — à faire AVANT l'import du backend
    # (les chemins du store sont résolus à l'import).
    os.environ["VSM_DATA_DIR"] = tempfile.mkdtemp(prefix="vsm_e2e_")
    from fastapi.testclient import TestClient

    import src.ui_backend.main as m

    client = TestClient(m.app)
    echecs: list[str] = []

    # --- 1. /health : DrBERT présent, LLM génératif retiré -------------------
    print("=== 1. /health ===")
    h = client.get("/health").json()
    _check(echecs, "DrBERT disponible", h.get("drbert_available") is True,
           str(h.get("drbert_path", "")))
    _check(echecs, "LLM retiré (GGUF absent)", h.get("llm_available") is False,
           (h.get("llm_reason") or "")[:80])

    # --- 2. Authentification --------------------------------------------------
    print("=== 2. Authentification ===")
    client.post("/auth/bootstrap",
                json={"username": "doc", "password": "mot-de-passe-fort!",
                      "role": "medecin"})
    r = client.post("/auth/login",
                    json={"username": "doc", "password": "mot-de-passe-fort!"})
    _check(echecs, "login médecin", r.status_code == 200)
    headers = {"X-CSRF-Token": r.json()["csrf"]}

    # --- 3. Upload + traitement (charge UTILE du frontend) -------------------
    print("=== 3. Upload + traitement (payload du frontend : nlp_engine=drbert) ===")
    up = client.post("/documents/upload", headers=headers,
                     files={"file": (args.input.name, args.input.read_bytes(),
                                     "application/octet-stream")})
    _check(echecs, "upload", up.status_code == 200)
    doc_id = up.json()["document_id"]

    def _traiter(corps: dict) -> tuple[dict | None, list[str], float]:
        """Lance un traitement et retourne (résultat, étapes, durée)."""
        t0 = time.time()
        r = client.post(f"/documents/{doc_id}/process", headers=headers,
                        json=corps)
        if r.status_code != 200:
            _check(echecs, "process accepté", False, r.text[:120])
            return None, [], 0.0
        job_id = r.json()["job_id"]
        etapes: list[str] = []
        while time.time() - t0 < 300:
            job = client.get(f"/documents/process/{job_id}",
                             headers=headers).json()
            if job.get("step") and (not etapes or etapes[-1] != job["step"]):
                etapes.append(job["step"])
            if job["status"] == "done":
                return job["result"], etapes, time.time() - t0
            if job["status"] == "error":
                _check(echecs, "traitement terminé", False, str(job["error"]))
                return None, etapes, time.time() - t0
            time.sleep(0.3)
        _check(echecs, "traitement terminé", False, "délai dépassé")
        return None, etapes, time.time() - t0

    result, etapes, duree = _traiter(
        {"engine": "tesseract", "anonymize_mode": "pseudo", "nlp_engine": "drbert"}
    )
    _check(echecs, "traitement terminé", result is not None, f"{duree:.1f} s")
    print(f"  étapes observées : {etapes}")

    # --- 4. AUCUNE phase LLM nulle part ---------------------------------------
    print("=== 4. Absence de phase LLM ===")
    _check(echecs, "aucune étape « LLM »", not any("LLM" in e for e in etapes))
    _check(echecs, "étape DrBERT présente", any("DrBERT" in e for e in etapes))
    nlp_report = (result or {}).get("nlp_report", {})
    _check(echecs, "moteur = drbert-casm2-v1",
           nlp_report.get("moteur") == "drbert-casm2-v1",
           str(nlp_report.get("moteur")))
    _check(echecs, "statut = drbert", nlp_report.get("statut") == "drbert",
           f"{nlp_report.get('statut')} / {nlp_report.get('raison')}")

    # --- 5. Le VSM -------------------------------------------------------------
    print("=== 5. VSM produit ===")
    vsm = client.get(f"/vsm/{result['vsm_id']}", headers=headers).json()
    _check(echecs, "provenance.moteur_nlp = drbert-casm2-v1",
           vsm.get("provenance", {}).get("moteur_nlp") == "drbert-casm2-v1",
           str(vsm.get("provenance", {}).get("moteur_nlp")))
    sections = vsm.get("sections", {})
    nb = sum(
        1
        for items in sections.values()
        for c in items
        if isinstance(c, dict) and "valeur" in c
    )
    ancres = all(
        c.get("source", {}).get("passage") in (None, c["valeur"])
        for items in sections.values() for c in items
        if isinstance(c, dict) and "valeur" in c
    )
    _check(echecs, "champs extraits présents", nb > 0, f"{nb} entités")
    _check(echecs, "passage = valeur (ancrage exact DrBERT)", ancres)
    print("  aperçu des entités :")
    for rubrique, items in sections.items():
        for c in items[:4]:
            if isinstance(c, dict) and "valeur" in c:
                print(f"    [{rubrique}] {c['valeur']!r} "
                      f"(confiance {c.get('confiance')})")

    # --- 6. Traitement SANS nlp_engine : le défaut doit rester DrBERT ---------
    print("=== 6. Défaut sans champ nlp_engine ===")
    up2 = client.post("/documents/upload", headers=headers,
                      files={"file": (args.input.name, args.input.read_bytes(),
                                      "application/octet-stream")})
    doc_id = up2.json()["document_id"]
    res2, _, _ = _traiter({"engine": "tesseract", "anonymize_mode": "pseudo"})
    rep2 = (res2 or {}).get("nlp_report", {})
    _check(echecs, "défaut = DrBERT (sans champ)",
           rep2.get("moteur") == "drbert-casm2-v1",
           f"{rep2.get('moteur')} / {rep2.get('statut')}")

    # --- Bilan -----------------------------------------------------------------
    print("=" * 60)
    if echecs:
        print(f"ÉCHECS ({len(echecs)}) : {echecs}")
        return 1
    print(f"BOUT EN BOUT OK — DrBERT seul moteur, aucune phase LLM, "
          f"{nb} entités, {duree:.1f} s de traitement.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
