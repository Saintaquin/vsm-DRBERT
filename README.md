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
├── extraction_nlp/   extraction par rubriques (moteur règles offline ; adaptateur LLM
│                     local llama-cpp optionnel), normalisation CIM-10/ATC (rapidfuzz)
├── vsm_generation/   assemblage VSM (ordre HAS, complétude, validation jsonschema),
│                     rendu markdown / HTML / PDF
├── storage/          SQLite chiffré champ par champ (AES-256-GCM), clés de session
│                     Argon2id avec timeout, auth multi-rôles, audit chaîné par hash
└── ui_backend/       FastAPI 127.0.0.1:8741 (cookies httpOnly + CSRF), sert frontend/dist
frontend/             React + TypeScript + Vite + Tailwind (style shadcn), WCAG AA
src-tauri/            wrapper desktop (bundles .msi / .AppImage / .deb)
schema/vsm_schema.json  contrat d'interface entre les modules (v1.1.0)
```

Le moteur NLP par défaut est **à base de règles** (aucun téléchargement, aucune
dépendance lourde). Un adaptateur **LLM local** (llama-cpp-python, modèle dans
`~/.cache/vsm-ocr/`, jamais committé) peut être activé — voir `docs/ADR/`.

## Tests, qualité, benchmark

```bash
pytest tests/ -v                          # 44 tests (anonymisation, OCR, NLP, VSM, storage, API)
python -m src.ingestion_ocr.benchmark     # CER/WER → outputs/benchmark.csv + BENCHMARK_REPORT.md
python -m examples.demo_nlp               # démo extraction NLP sur texte exemple
```

## Documentation

- `docs/USER_MANUAL.md` — manuel utilisateur (médecin / secrétaire / admin)
- `docs/ANONYMIZATION.md` — stratégies, limites connues, procédure droit à l'oubli
- `docs/COMPLIANCE.md` — matrice RGPD / HDS / XAI
- `docs/SECURITY.md` — modèle de menace, mesures, procédure d'incident
- `docs/ADR/` — décisions d'architecture (format MADR)
- `outputs/BENCHMARK_REPORT.md` — benchmark OCR reproductible

## Licence et données

Le dataset `data/synthetic/` est **100 % fictif** (identités générées,
seed 42). Aucune donnée patient réelle ne doit jamais être committée — la CI
bloque les patterns de NIR hors dataset synthétique.
