# VSM-OCR — Volet de Synthèse Médicale local

Application médicale **100 % locale** destinée au personnel de santé français.
Elle transforme des documents médicaux scannés (PDF, PNG, JPG, TIFF) en
**Volet de Synthèse Médicale (VSM)** structuré conforme aux rubriques HAS,
avec anonymisation systématique, chiffrement au repos et traçabilité complète.

> ⚠️ **Le VSM généré est un brouillon assisté par machine. Il doit toujours
> être relu, corrigé et validé par un médecin avant tout usage clinique.**

## Garanties

| Exigence | Mise en œuvre |
|---|---|
| Aucun appel cloud | Tout tourne sur la machine ; backend lié à `127.0.0.1` exclusivement |
| Anonymisation | PII détectées et masquées **avant** toute extraction ; non désactivable |
| Chiffrement au repos | AES-256-GCM champ par champ, clé dérivée Argon2id du mot de passe |
| Droit à l'oubli | Suppression d'un dossier (documents + résultats + VSM + mapping PII) en un clic |
| Traçabilité | Audit log chaîné par hash (toute falsification rétroactive est détectée) |
| XAI | Chaque champ : source, score de confiance, moteurs ; < 0,7 → badge « À valider » |

## Installation (utilisateur final)

### Prérequis système

- **Windows 10/11** ou **Linux Debian/Ubuntu LTS** (macOS : non testé mais supporté)
- Python 3.12
- Tesseract OCR avec le pack langue français, et Poppler (pour les PDF) :
  - Debian/Ubuntu : `sudo apt install tesseract-ocr tesseract-ocr-fra poppler-utils`
  - Windows : installeurs Tesseract (UB Mannheim) + Poppler, ajoutés au `PATH`

### Installation

```bash
git clone <repo> vsm-ocr && cd vsm-ocr
python3 -m venv .venv && source .venv/bin/activate   # Windows : .venv\Scripts\activate
pip install -r requirements.txt
python -m src.ingestion_ocr.generate_dataset          # dataset de démonstration (optionnel)
```

### Lancement

```bash
# Clé maître du coffre-fort de pseudonymisation (HORS application — gestion par
# l'administrateur ; sans elle, les mappings PII↔token ne sont pas conservés) :
export VSM_VAULT_PASSPHRASE="<phrase secrète longue gérée par votre DSI>"

python -m src.ui_backend.main
# puis ouvrir http://127.0.0.1:8741 dans un navigateur
```

À la première utilisation, cliquer « Première utilisation ? Créer le premier
compte » (mot de passe : 12 caractères minimum). Ce mot de passe sert aussi à
dériver la clé de chiffrement de la base — **il n'est récupérable nulle part**.

### Application desktop (Tauri, optionnel)

```bash
cd frontend && npm ci && npm run build && cd ..
cargo install tauri-cli --version '^2'
cd src-tauri && cargo tauri build      # produit .msi (Windows) / .AppImage, .deb (Linux)
```

Le wrapper Tauri lance le backend Python en sous-process au démarrage et le
tue à la fermeture. Pas de télémétrie, pas de mise à jour automatique.

## Utilisation en bref

1. **Téléverser** un document scanné (choix pseudonymisation/anonymisation stricte).
   Taille max par défaut : **50 Mo** — configurable : `VSM_MAX_UPLOAD_MB=500` (Mo).
2. Le pipeline tourne : préprocessing → OCR (Tesseract+fra) → **anonymisation**
   → extraction NLP → normalisation CIM-10/ATC → assemblage VSM.
3. **Relire** le VSM : les champs sous le seuil de confiance (0,7) sont sur fond
   ambre « À valider » ; cliquer « Voir le passage source » surligne le texte d'origine.
4. **Corriger / confirmer** les champs (↵ pour confirmer, Ctrl+↵ pour enregistrer).
5. **Signer et finaliser** (rôle médecin uniquement) : le VSM est scellé par
   empreinte SHA-256 et l'événement est journalisé.
6. **Exporter** en HTML/Markdown/PDF.

Raccourcis : `Ctrl+K` recherche globale · `Tab`/`Maj+Tab` navigation · `↵`
confirmer un champ · `Ctrl+↵` enregistrer.

## Architecture

```
src/
├── anonymization/    détection PII (regex+dictionnaires+heuristiques, adaptateur spaCy
│                     optionnel), pseudonymisation réversible, coffre-fort chiffré, audit
├── ingestion_ocr/    preprocessing, moteurs OCR (Tesseract + adaptateurs DocTR/Paddle),
│                     pipeline document→JSON, benchmark CER/WER, dataset synthétique
├── extraction_nlp/   extraction DrBERT-CASM2 (ENCODEUR local, moteur par
│                     défaut), moteur règles (repli), LLM local llama-cpp
│                     (optionnel), NER DrBERT-MedicalNER-FR (complément du
│                     moteur règles), normalisation CIM-10/ATC (rapidfuzz)
├── vsm_generation/   assemblage VSM (ordre HAS, complétude, validation jsonschema),
│                     rendu markdown / HTML / PDF
├── storage/          SQLite chiffré champ par champ (AES-256-GCM), clés de session
│                     Argon2id avec timeout, auth multi-rôles, audit chaîné par hash
└── ui_backend/       FastAPI 127.0.0.1:8741 (cookies httpOnly + CSRF), sert frontend/dist
frontend/             React + TypeScript + Vite + Tailwind (style shadcn), WCAG AA
src-tauri/            wrapper desktop (bundles .msi / .AppImage / .deb)
schema/vsm_schema.json  contrat d'interface entre les modules (v1.1.0)
```

Le moteur NLP par défaut est l'**encodeur DrBERT-CASM2** (medkit, licence
MIT — décision du banc d'essai `tools/eval_drbert.py`, étape 0) : il
**étiquette** des tokens au lieu de générer du texte, donc **aucune
hallucination possible** (toute valeur est un extrait exact du document, avec
ses offsets — l'ancrage XAI est structurel), ~440 Mo, CPU seul, 100 % offline
(RGPD, art. 9). Le modèle est vendorisé à la fabrication de l'installeur
(`packaging/fetch_models.py`) et lu localement (`VSM_DRBERT_PATH`). Le moteur
**règles reste le repli automatique** si le modèle est absent ou échoue
(l'application fonctionne toujours, repli tracé dans `provenance.nlp`).

```bash
py -3.12 packaging/fetch_models.py             # fabrication : vendorise models/drbert/
py -3.12 tools/e2e_drbert.py                   # test de bout en bout (vrai modèle)
# VSM_NLP_ENGINE=drbert|llm|regles             # moteur par défaut (défaut : drbert)
```

### LLM local (optionnel)

Le moteur LLM génératif (llama-cpp-python, Qwen 2.5) est **conservé mais
optionnel** : il n'est plus dans le flux de traitement par défaut. Le bouton
« Relire par le LLM local » du VSM l'utilise pour une seconde passe de
correction OCR sur les champs « À valider » — s'il faut l'activer :

```bash
pip install llama-cpp-python                  # moteur d'inférence
python -m src.extraction_nlp.llm              # télécharge Qwen 2.5 3B (~2 Go)
# --list : voir les options (qwen2.5-1.5b < 4 Go, qwen2.5-7b 9-14 Go…)
```

### NER complémentaire — DrBERT-MedicalNER-FR (moteur règles)

Un second NER médical français (DrBERT-MedicalNER-FR, CamemBERT biomédical)
complète automatiquement l'extraction du **moteur de règles** avec les
entités manquantes.

```bash
py -3.12 -m pip install torch transformers\>=4.53,\<5   # environnement Python 3.12
python -m src.extraction_nlp.drbert                     # télécharge le modèle (~500 Mo) — install, jamais au traitement
```

- **Complémentaire, non bloquant** : aucun choix à faire dans l'UI ; la
  provenance est tracée (`moteur_nlp="drbert-nlp-v1"`).
- **Confiance réelle** (probabilité du label) : sous 0,7 → champ « À valider ».
- **Licence** : base Apache 2.0 (propre) ; checkpoint en licence « style
  OpenRAIL » personnalisée (usage commercial permis, pas de clause interdisant
  l'usage médical — à documenter au dossier). Voir
  `docs/ADR/0010-drbert-extraction.md` et `outputs/AUDIT_DRBERT.md` (§2a).

## Tests, qualité, benchmark

```bash
pytest tests/ -v                          # 205 tests (anonymisation, OCR, NLP, DrBERT, LLM, VSM, storage, API)
python -m src.ingestion_ocr.benchmark     # CER/WER → outputs/benchmark.csv + BENCHMARK_REPORT.md
python -m examples.demo_nlp               # démo extraction NLP sur texte exemple
```

## Documentation

- `docs/USER_MANUAL.md` — manuel utilisateur (médecin / secrétaire / admin)
- `docs/ANONYMIZATION.md` — stratégies, limites connues, procédure droit à l'oubli
- `docs/COMPLIANCE.md` — matrice RGPD / HDS / XAI
- `docs/LICENCES_TIERS.md` — inventaire des licences tiers (conformité annexe 1)
- `docs/SECURITY.md` — modèle de menace, mesures, procédure d'incident
- `docs/ADR/` — décisions d'architecture (format MADR), dont
  `0010-drbert-extraction.md` (NER médical) et `0009-llm-par-defaut-universel.md`
- `outputs/BENCHMARK_REPORT.md` — benchmark OCR reproductible

## Licence et données

Le dataset `data/synthetic/` est **100 % fictif** (identités générées,
seed 42). Aucune donnée patient réelle ne doit jamais être committée — la CI
bloque les patterns de NIR hors dataset synthétique.

La **conformité des licences tiers** (modèles d'IA, bibliothèques, binaires) est
documentée dans [`docs/LICENCES_TIERS.md`](docs/LICENCES_TIERS.md) — compatible
avec l'Annexe 1 (DrBERT-CASM2 MIT, usage commercial permis pour
DrBERT-MedicalNER, Apache-2.0 pour les LLM).
