# ADR-0005 — Moteur OCR optionnel « Unlimited-OCR » (baidu, GPU NVIDIA)

- Statut : accepté · Date : 2026-08-22 · Optionnel, conditionné à une carte NVIDIA.

## Contexte

Demande : étudier https://github.com/baidu/Unlimited-OCR et intégrer les
fonctionnalités utiles, **exclusivement pour les postes équipés d'une carte
graphique NVIDIA** (fonctionnalité inexistante sinon), dans le respect du
règlement du concours.

## Ce qu'est Unlimited-OCR (étude du dépôt)

- OCR **documentaire panoptique en une passe** (« one-shot long-horizon
  parsing ») : détection + reconnaissance + compréhension de la mise en page
  (blocs texte/tableau/image via marqueurs `<|det|>`), héritier de
  DeepSeek-OCR / PaddleOCR.
- Modèle 3B, **licence MIT** (code et modèle — vérifié : fichier `LICENSE` du
  dépôt = MIT, modèle annoncé « MIT-Licensed 3B »).
- **Exige une carte NVIDIA** : `torch.cuda`/bf16, Python 3.12, CUDA 12.x.
- Inférence : transformers direct (`model.infer`) ou serveurs vLLM/SGLang
  (sur-dimensionnés ici).
- Langues principales : chinois/anglais/japonais/coréen — **le français n'est
  pas garanti** (à évaluer par benchmark avant adoption pour des CR français).

## Décision

Intégrer un **adaptateur OCREngine « unlimited »** optionnel dans
`src/ingestion_ocr/ocr_engines.py` (même motif que DocTR/Paddle) :
- **Disponibilité** = carte NVIDIA (`torch.cuda.is_available()`) ET
  torch/transformers installés. Sans GPU NVIDIA, le moteur **n'existe pas**
  (absent de `ENGINES`, rejeté par l'API en 400 explicite, absent de l'UI) —
  exigence « fonctionnalité réservée aux postes NVIDIA ».
- Inférence : transformers direct, modèle en cache Hugging Face (téléchargé à
  l'installation, jamais pendant le traitement), sortie nettoyée des
  marqueurs `<|det|>` (`strip_det_markers`, fonction pure testée).
- `GET /health` expose `available_engines` ; l'UI n'affiche le sélecteur
  « Unlimited-OCR » que si présent.
- Benchmark possible via `python -m src.ingestion_ocr.benchmark --engines
  tesseract unlimited` (harnais existant).

## Conformité au règlement du concours

| Règle | Application |
|---|---|
| **Art. 9 (RGPD/offline)** | ✅ Inférence 100 % locale (CUDA) ; modèle téléchargé à l'installation ; l'anonymisation précède toujours l'extraction |
| **Annexe 1 art. 4 (droits de tiers)** | ✅ **Licence MIT** — aucune contrainte de redistribution ni d'usage |
| **Art. 7 (performance extraction 25 %)** | ⚠️ Gain potentiel sur documents complexes (mise en page, tableaux) — **à démontrer par benchmark** ; le français étant non garanti, **Tesseract+fra reste le moteur par défaut** |
| **Art. 7 (ergonomie 10 %)** | ✅ Optionnel et masqué sans GPU — aucune confusion pour l'utilisateur |

## Conséquences

- + Licence MIT, local, haute qualité potentielle sur documents complexes.
- + Gating GPU strict : les postes sans NVIDIA ne voient jamais la
  fonctionnalité (exigence produit respectée).
- − Dépendances lourdes (torch/transformers) et modèle ~2-3 Go ; langue
  française non garantie ; confiance mot à mot non fournie (valeur neutre 0,9,
  la relecture médecin reste obligatoire via les champs « À valider »).
- − L'adaptateur est fourni « prêt à valider sur matériel NVIDIA » : la
  procédure de recette (benchmark CER/WER sur le dataset français, validation
  du format de sortie) est documentée dans `outputs/AUDIT_OCR_UNLIMITED.md`.
