# Manuel d'installation — VSM-OCR

Application médicale **100 % locale** (aucun appel cloud). Ce manuel décrit
l'installation complète sur **Windows 10/11** pas à pas, depuis les prérequis
système jusqu'au lancement de l'application. La version PDF de ce document
est générée par `py -3.12 tools/make_manual_pdf.py`.

> ! **Avertissement** — le VSM généré est un brouillon assisté par machine. Il doit toujours
> être relu, corrigé et validé par un médecin avant tout usage clinique.

## 1. Prérequis système

Installer, dans cet ordre :

| Composant | Installation (Windows) | Pourquoi |
|---|---|---|
| Python 3.12 (64 bits) | https://www.python.org/downloads/ — cocher **« Add python.exe to PATH »** | torch n'est disponible que pour Python 3.12 |
| Tesseract OCR 5.x | Installeur **UB Mannheim** (`tesseract-ocr-w64-setup-5.x.exe`), cocher le pack langue **français (fra)** pendant l'installation | OCR des documents scannés |
| Poppler | Extraire `poppler-x.x.x.zip` dans `C:\Program Files\poppler`, puis ajouter `C:\Program Files\poppler\bin` au `PATH` (Paramètres -> Variables d'environnement) | Conversion PDF -> images |
| Node.js 18+ | https://nodejs.org/ (LTS) | Build de l'interface web |

**Vérification** — ouvrir un nouveau terminal et exécuter :

```powershell
py -3.12 --version
tesseract --version
pdftoppm -v
node --version
```

Chaque commande doit répondre sans erreur. Si `tesseract` ou `pdftoppm` est
introuvable : fermer et rouvrir le terminal (le `PATH` est relu au démarrage),
puis vérifier que le dossier d'installation figure bien dans les variables
d'environnement.

## 2. Récupérer le code source

```powershell
git clone https://github.com/Saintaquin/vsm-DRBERT.git vsm-ocr
cd vsm-ocr
```

> Sans `git` : télécharger le dépôt en ZIP (bouton vert « Code » -> Download
> ZIP) et extraire l'archive, puis `cd` dans le dossier extrait.

## 3. Créer l'environnement Python 3.12

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\activate
```

L'invite doit maintenant commencer par `(.venv)`. Mettre à jour pip :

```powershell
py -m pip install --upgrade pip
```

## 4. Installer les dépendances Python

```powershell
py -m pip install -r requirements.txt
```

Ce fichier installe : FastAPI + uvicorn (serveur local), pydantic,
python-multipart, cryptography (AES-256-GCM), argon2-cffi (dérivation de
clé), rapidfuzz (normalisation CIM-10/ATC), jsonschema, pytesseract,
pdf2image, pillow, numpy, reportlab (rendu PDF du VSM), jiwer, psutil et
llama-cpp-python (roue CPU précompilée — le `--extra-index-url` du fichier
fournit le binaire, aucune compilation, aucun appel cloud).

## 5. Installer PyTorch CPU et transformers

Le moteur d'extraction par défaut (DrBERT-CASM2) tourne sur **torch CPU**
(pas de carte graphique nécessaire) :

```powershell
py -m pip install torch --index-url https://download.pytorch.org/whl/cpu
py -m pip install "transformers>=4.53,<5" huggingface_hub
```

> ! **Attention** : toujours lancer avec `py -3.12`, jamais `python` : l'interpréteur
> `python` par défaut (3.14) n'a pas torch. L'application fonctionnerait
> quand même, mais en repli « moteur de règles » (bannière « DrBERT
> indisponible » sur la page d'accueil).

## 6. Télécharger le modèle DrBERT-CASM2

Le modèle (≈ 440 Mo, licence MIT) est téléchargé **une seule fois** vers le
dossier local `models/drbert/` :

```powershell
py -3.12 packaging/fetch_models.py
```

Le script vérifie la complétude (config, tokenizer, `model.safetensors`) et la
taille (≥ 400 Mo) : un téléchargement tronqué est refusé. Une fois
téléchargé, l'application ne fait plus AUCUN accès réseau.

Vérification de bout en bout avec le vrai modèle :

```powershell
py -3.12 tools/e2e_drbert.py
```

## 7. Construire l'interface web (frontend)

```powershell
cd frontend
npm ci
npm run build
cd ..
```

Le résultat est produit dans `frontend/dist/`, servi automatiquement par le
backend sur http://127.0.0.1:8741.

## 8. Configurer le terminal (variables d'environnement)

Deux variables sont à définir **avant chaque lancement** :

| Variable | Valeur | Rôle |
|---|---|---|
| `VSM_VAULT_PASSPHRASE` | votre phrase secrète longue | Clé maître du coffre de pseudonymisation (mappings PII / pseudonyme) |
| `VSM_MAX_UPLOAD_MB` | 500 | Taille maximale des documents téléversés (Mo) |

Dans PowerShell :

```powershell
$env:VSM_VAULT_PASSPHRASE = "votre-phrase-secrete-longue"
$env:VSM_MAX_UPLOAD_MB = "500"
```

> ! **Attention** : la phrase du coffre doit rester TOUJOURS la même entre deux
> lancements : elle chiffre les correspondances pseudonyme / identité.
> Changer de phrase rend illisibles les mappings déjà stockés (c'est le
> chiffrement, pas un bug). À gérer comme un secret par votre DSI.

**Variables optionnelles** :

| Variable | Défaut | Rôle |
|---|---|---|
| `VSM_DRBERT_PATH` | `models/drbert/` | Dossier du modèle local |
| `VSM_NLP_ENGINE` | `drbert` | Moteur d'extraction : `drbert` \| `llm` \| `regles` |
| `VSM_DATA_DIR` | `~/.vsm-ocr` | Dossier des données chiffrées |
| `VSM_DRBERT_MIN_SCORE` | `0.70` | Seuil de confiance des entités DrBERT |
| `VSM_LOG_LEVEL` | `INFO` | Niveau de journalisation |

## 9. Lancer l'application

```powershell
py -3.12 -m src.ui_backend.main
```

Puis ouvrir **http://127.0.0.1:8741** dans un navigateur. Au démarrage, le
modèle DrBERT est préchauffé (~5 s). Le serveur est lié à `127.0.0.1`
exclusivement.

**Alternative « double-clic »** : le fichier `lancer_vsm.bat` (fourni sur la
machine locale, non versionné) exécute les étapes 8-9 automatiquement —
personnaliser la ligne `set VSM_VAULT_PASSPHRASE=…` avec VOTRE phrase.

## 10. Première utilisation

1. Cliquer **« Première utilisation ? Créer le premier compte »**.
2. Choisir un mot de passe d'**au moins 12 caractères** : il dérive la clé de
   chiffrement de la base. **Il n'est récupérable nulle part** — en cas
   d'oubli, les données chiffrées deviennent illisibles (pas de porte
   dérobée, c'est une garantie de sécurité).
3. Se connecter (session : 15 minutes d'inactivité maximum).
4. **Tableau de bord -> Nouveau document** : choisir le mode d'anonymisation,
   téléverser un scan (PDF/PNG/JPG/TIFF), attendre la fin du pipeline, puis
   **relire et valider** chaque champ du VSM.
5. Le VSM n'a de valeur légale qu'**après relecture et signature** par un
   médecin (empreinte SHA-256, événement journalisé).

## 11. Dépannage

| Symptôme | Cause probable | Correctif |
|---|---|---|
| Bannière « DrBERT indisponible » | Modèle absent/incomplet, ou lancé avec `python` | `py -3.12 packaging/fetch_models.py`, puis relancer avec `py -3.12 -m src.ui_backend.main` — la cause exacte est affichée |
| « Le port 8741 est déjà utilisé » | Une instance tourne déjà | Fermer l'ancien terminal ; `Get-NetTCPConnection -LocalPort 8741` pour identifier le processus |
| Upload rejeté (413) | Fichier > `VSM_MAX_UPLOAD_MB` | Augmenter la limite (étape 8) puis relancer |
| Mappings pseudonyme illisibles | Phrase du coffre changée | Remettre la phrase ORIGINALE |
| `tesseract` introuvable | `PATH` non mis à jour | Fermer/rouvrir le terminal ; vérifier les variables d'environnement |

## 12. Pour aller plus loin

- `docs/USER_MANUAL.md` — manuel utilisateur (secrétaire / médecin / admin)
- `docs/SECURITY.md` — modèle de menace, sauvegardes (`~/.vsm-ocr/`), incident
- `docs/ANONYMIZATION.md` — stratégies d'anonymisation, droit à l'oubli
- `docs/LICENCES_TIERS.md` — inventaire des licences (conformité annexe 1)
- `pytest tests/ -q` — suite de tests complète (312 tests)
