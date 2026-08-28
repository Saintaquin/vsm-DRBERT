# Fabrication du paquet (empaquetage)

Ce document couvre la fabrication de l'installeur VSM-OCR, côté développeur.
Le poste cible (cabinet médical, PC lent sans GPU ni réseau) ne télécharge
JAMAIS rien : tout ce qui est lourd est vendorisé à la fabrication.

## Modèle DrBERT-CASM2 (moteur NLP par défaut)

L'extraction repose sur l'encodeur **medkit/DrBERT-CASM2** (licence MIT ;
base Dr-BERT Apache 2.0 — voir `docs/LICENCES_TIERS.md`). Il remplace le LLM
génératif comme moteur principal (décision de l'étape 0, banc d'essai
`tools/eval_drbert.py`) :

- ~440 Mo de poids (fp32, `model.safetensors`), ~250 Mo de RAM en service ;
- inférence CPU (torch), fenêtres de 512 tokens, threads = cœurs physiques ;
- AUCUN réseau à l'exécution : le dossier du modèle est lu en local.

### Fabrication

```powershell
py -3.12 packaging/fetch_models.py            # télécharge si absent
py -3.12 packaging/fetch_models.py --force    # re-télécharge toujours
```

Le script télécharge le modèle dans `models/drbert/` (dépôt, gitignoré),
vérifie la présence des fichiers attendus (`config.json`,
`model.safetensors`, `tokenizer_config.json`) et la taille cohérente
(≥ 400 Mo — un modèle tronqué planterait chez l'utilisateur sans réseau
pour le réparer).

L'installeur embarque ensuite `models/drbert/` tel quel (≈ 440 Mo ajoutés au
paquet). À l'installation, le modèle est posé à côté de l'application et
l'exécutable est configuré avec :

```
VSM_DRBERT_PATH=C:\Program Files\VSM-OCR\models\drbert
```

À défaut de cette variable, l'application cherche `models/drbert/` relatif à
la racine du dépôt — suffisant en développement.

### Vérification post-installation

Sur le poste cible, sans réseau :

```powershell
py -3.12 -c "from src.extraction_nlp.drbert_extractor import modele_disponible, dossier_modele; print(modele_disponible(), dossier_modele())"
```

doit afficher `True`. Si le modèle est absent, l'application ne plante pas :
le moteur de règles prend le relais et le remplacement est tracé dans le
rapport de provenance (`statut: "modele_absent"`).

## Sélection du moteur NLP

- **Par défaut** : `drbert` (encodeur local).
- **Variable d'environnement** : `VSM_DRBERT_PATH` (dossier du modèle).
- **Sélection par requête** : champ `nlp_engine` de l'API
  (`drbert` | `llm` | `regles`) ; variable `VSM_NLP_ENGINE` pour la valeur
  par défaut du backend.
- **Repli automatique** : modèle absent, erreur d'inférence ou sortie vide →
  moteur de règles, tracé dans `provenance.nlp` du VSM.

## Options d'exécution DrBERT

| Variable | Défaut | Rôle |
|---|---|---|
| `VSM_DRBERT_PATH` | `models/drbert/` | dossier du modèle local |
| `VSM_DRBERT_MIN_SCORE` | `0.70` | seuil de confiance des entités |
| `VSM_DRBERT_KEEP_TESTS` | non défini | garder les entités « test » (examens), sinon écartées |

## GGUF du LLM (optionnel)

Le moteur `llm` reste disponible (llama-cpp-python). Le GGUF est téléchargé
par l'administrateur au premier lancement (`python -m src.extraction_nlp.llm`)
et caché dans `~/.cache/vsm-ocr/` — il n'est PAS vendorisé dans l'installeur
(≈ 5 Go). Voir `docs/USER_MANUAL.md`.
