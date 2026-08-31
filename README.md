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

> 📘 **Manuel d'installation téléchargeable (PDF)** :
> [`docs/MANUAL_INSTALLATION.pdf`](docs/MANUAL_INSTALLATION.pdf) — même
> contenu pas à pas, généré par `py -3.12 tools/make_manual_pdf.py` à partir
> de [`docs/MANUAL_INSTALLATION.md`](docs/MANUAL_INSTALLATION.md).

### Prérequis système

| Composant | Windows 10/11 | Debian/Ubuntu | Pourquoi |
|---|---|---|---|
| **Python 3.12** (64 bits) | python.org/downloads, cocher « Add to PATH » | `sudo apt install python3.12 python3.12-venv` | torch n'est disponible qu'en 3.12 dans cet environnement |
| **Tesseract OCR + français** | Installeur UB Mannheim (`tesseract-ocr-w64-setup…exe`), cocher le pack langue **fra** | `sudo apt install tesseract-ocr tesseract-ocr-fra` | OCR des scans |
| **Poppler** (pdf2image) | Binaires poppler (`poppler-xx.zip`) extraits dans `C:\Program Files\poppler`, `bin\` ajouté au `PATH` | `sudo apt install poppler-utils` | Conversion PDF → images |
| **Node.js 18+** (frontend uniquement) | nodejs.org | `sudo apt install nodejs npm` | Build de l'interface web |

Vérifier après installation des binaires, dans un nouveau terminal :

```powershell
py -3.12 --version
tesseract --version          # doit afficher … with tesseract 5.x
pdftoppm -v                  # doit afficher poppler version …
node --version
```

### Installation pas à pas

**Étape 1 — Récupérer le code source.**

```powershell
git clone https://github.com/Saintaquin/vsm-DRBERT.git vsm-ocr
cd vsm-ocr
```

**Étape 2 — Environnement virtuel Python 3.12.**

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\activate        # Windows (Bash/Linux : source .venv/bin/activate)
py -m pip install --upgrade pip
```

**Étape 3 — Dépendances Python** (le `--extra-index-url` fournit la roue CPU
de llama-cpp-python, aucun appel cloud).

```powershell
py -m pip install -r requirements.txt
```

**Étape 4 — PyTorch CPU + transformers** (requis pour le moteur d'extraction
par défaut DrBERT-CASM2 ; roue CPU uniquement — pas de CUDA nécessaire).

```powershell
py -m pip install torch --index-url https://download.pytorch.org/whl/cpu
py -m pip install "transformers>=4.53,<5" huggingface_hub
```

> ⚠️ L'interpréteur doit être **`py -3.12`**, jamais `python` : le `python`
> par défaut de Windows (3.14+) n'a pas torch — l'application fonctionnerait
> quand même, mais en repli « moteur de règles » (bannière DrBERT indisponible).

**Étape 5 — Télécharger le modèle DrBERT-CASM2** (≈ 440 Mo, une seule fois,
réseau requis UNIQUEMENT à cette étape ; ensuite tout est local).

```powershell
py -3.12 packaging/fetch_models.py
```

Le script télécharge le dépôt HuggingFace `medkit/DrBERT-CASM2` (licence MIT ;
base Dr-BERT Apache 2.0) vers `models/drbert/` et vérifie la complétude et la
taille (`model.safetensors` ≥ 400 Mo) — un modèle tronqué est refusé.

Vérification de bout en bout avec le vrai modèle :

```powershell
py -3.12 tools/e2e_drbert.py
```

**Étape 6 — Construire le frontend** (interface web servie par le backend).

```powershell
cd frontend
npm ci
npm run build
cd ..
```

**Étape 7 — Configurer le terminal** : la phrase secrète du coffre-fort de
pseudonymisation (clé maître HORS application — à gérer avec votre DSI) et la
taille d'upload.

```powershell
$env:VSM_VAULT_PASSPHRASE = "votre-phrase-secrete-longue"
$env:VSM_MAX_UPLOAD_MB = "500"
```

> ⚠️ La phrase du coffre doit rester **TOUJOURS identique** entre deux
> lancements : elle chiffre les correspondances pseudonyme ↔ identité
> (Argon2id → AES-256-GCM). Changer de phrase rend illisibles les mappings
> déjà stockés (c'est le chiffrement, pas un bug).

**Étape 8 — Lancer l'application.**

```powershell
py -3.12 -m src.ui_backend.main
```

Puis ouvrir **http://127.0.0.1:8741** dans un navigateur. Le serveur est lié à
`127.0.0.1` exclusivement (aucun accès réseau, aucun cloud). Au démarrage, le
modèle DrBERT est préchauffé (~5 s) ; la page d'accueil affiche l'état réel du
moteur (`/health`).

**Alternative « double-clic »** : un lanceur `lancer_vsm.bat` (non versionné,
à personnaliser avec VOTRE phrase) exécute les étapes 7-8 automatiquement.

### Première utilisation

1. Cliquer **« Première utilisation ? Créer le premier compte »**.
2. Choisir un mot de passe d'**au moins 12 caractères** — il dérive la clé de
   chiffrement de la base : **il n'est récupérable nulle part** (oubli =
   données illisibles, pas de porte dérobée).
3. Se connecter : la session expire après 15 minutes d'inactivité (clé de
   chiffrement effacée de la mémoire).
4. Tableau de bord → **Nouveau document** → téléverser un scan (PDF/PNG/JPG/
   TIFF, 500 Mo max avec la configuration ci-dessus) → le pipeline tourne
   (OCR → anonymisation → extraction DrBERT → normalisation CIM-10/ATC →
   assemblage VSM) → **relire et valider** chaque champ.

### Variables d'environnement

| Variable | Défaut | Rôle |
|---|---|---|
| `VSM_VAULT_PASSPHRASE` | — (obligatoire) | Clé maître du coffre de pseudonymisation (Argon2id) |
| `VSM_MAX_UPLOAD_MB` | `50` | Taille maximale des documents téléversés (Mo) |
| `VSM_DRBERT_PATH` | `models/drbert/` | Dossier du modèle local (installeur : `C:\Program Files\VSM-OCR\models\drbert`) |
| `VSM_NLP_ENGINE` | `drbert` | Moteur d'extraction : `drbert` \| `llm` \| `regles` |
| `VSM_DATA_DIR` | `~/.vsm-ocr` | Dossier de données chiffrées (base, coffre, journaux) |
| `VSM_DRBERT_MIN_SCORE` | `0.70` | Seuil de confiance des entités DrBERT |
| `VSM_DRBERT_KEEP_TESTS` | non défini | Garder les entités « test » (examens), sinon écartées |
| `VSM_LOG_LEVEL` | `INFO` | Niveau de journalisation (`<VSM_DATA_DIR>/logs/app.log`) |

### Dépannage

| Symptôme | Cause probable | Correctif |
|---|---|---|
| Bannière « ⚠ DrBERT indisponible » | Modèle absent de `models/drbert/` (ou `VSM_DRBERT_PATH`), ou lancé avec `python` au lieu de `py -3.12` | `py -3.12 packaging/fetch_models.py` puis relancer avec `py -3.12 -m src.ui_backend.main` ; la cause exacte est affichée au démarrage et sur la page d'accueil |
| « Le port 8741 est déjà utilisé » | Une instance tourne déjà | Fermer l'ancien terminal, ou `Get-NetTCPConnection -LocalPort 8741` pour identifier le processus |
| Upload rejeté (413) | Fichier > `VSM_MAX_UPLOAD_MB` | Relever la limite dans la config du terminal avant de lancer |
| Mappings PII illisibles | Phrase du coffre changée entre deux lancements | Remettre la phrase ORIGINALE (voir Étape 7) |

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

Le moteur NLP par défaut est l'**encodeur DrBERT-CASM2** (modèle HuggingFace
`medkit/DrBERT-CASM2`, licence MIT — « medkit » est l'ORGANISATEUR du dépôt
Hugging Face, pas une bibliothèque : le modèle est chargé via `transformers`,
`local_files_only=True`, zéro accès réseau). Décision du banc d'essai
`tools/eval_drbert.py`, étape 0 : il **étiquette** des tokens au lieu de
générer du texte, donc **aucune hallucination possible** (toute valeur est un
extrait exact du document, avec ses offsets — l'ancrage XAI est structurel),
~440 Mo, CPU seul, 100 % offline (RGPD, art. 9). Le modèle est vendorisé à la
fabrication de l'installeur (`packaging/fetch_models.py`) et lu localement
(`VSM_DRBERT_PATH`). Le moteur **règles reste le repli automatique** si le
modèle est absent ou échoue (l'application fonctionne toujours, repli tracé
dans `provenance.nlp`).

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
