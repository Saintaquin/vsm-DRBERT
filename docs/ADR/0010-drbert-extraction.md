# ADR-0010 — Intégration DrBERT-MedicalNER-FR (extraction d'entités, CPU léger)

- Statut : accepté · Date : 2026-08-22 · Modifie ses ADR-0001 (moteurs NLP) et
  complète ADR-0009 (LLM par défaut). Prolonge la recommandation de
  `outputs/AUDIT_DRBERT.md`.

## Contexte

Le LLM (Qwen 2.5 3B, ~2 Go) est le moteur par défaut, mais il reste lourd pour
les postes les plus faibles (4-8 Go, sans GPU) et généraliste (pas spécialisé
médical). Sur les documents non rubriqués, le rappel doit être amélioré sans
alourdir la machine. DrBERT est un **encodeur biomédical français** (CamemBERT)
décliné en **NER de token-classification** spécialisé pour le domaine.

Décision : **ajouter DrBERT-MedicalNER-FR** comme moteur d'extraction
d'entités, **complémentaire** au LLM (machines puissantes) et aux règles
(repli), et **très léger** → l'unique brique NLP capable de tourner sur les
postes les plus lents.

## Choix du modèle

| Modèle | Base | Checkpoint NER | Taille | RAM | GPU | Français médical |
|---|---|---|---|---|---|---|
| **DrBERT-MedicalNER-FR** | DrBERT-7GB | fine-tuné NER | ~500 Mo fp32 / ~150 Mo quantizé | ≤ 1 Go | non requis | **Spécialisé** ✅ |

- **Base DrBERT-7GB** : **Apache 2.0** → licence propre pour l'annexe 1.
- **Checkpoint DrBERT-MedicalNER-FR** : licence « style OpenRAIL »
  **personnalisée** (fichier `LICENSE` du repo), **plus permissive que la
  licence OpenRAIL-M officielle** — **usage commercial explicitement permis**,
  et **pas de clause interdisant l'usage médical** (la vraie OpenRAIL-M
  l'interdirait via *Attachment A* `(l)` ; absente ici). L'usage médical n'est
  traité que par un **disclaimer** (§4) : à respecter en documentant un VSM
  « brouillon à valider par un médecin ». Détaillé dans
  `outputs/AUDIT_DRBERT.md` (§2a). Repli si le jury exige de l'Apache pur :
  re-paramétrer/remplacer le checkpoint — la **base** (Apache 2.0) reste
  utilisable, la dépendance est isolée dans `src/extraction_nlp/drbert.py`.
- **Modèle** : `spideystreet/DrBERT-MedicalNER-FR` (configurable
  `VSM_DRBERT_MODEL`). Téléchargé **à l'installation** (CLI
  `python -m src.extraction_nlp.drbert`), jamais pendant le traitement
  (art. 9). Cache Hugging Face local.

## Mise en œuvre

- `src/extraction_nlp/drbert.py` (nouveau) :
  - `extract_entities_drbert(text)` → liste `{valeur, section, confiance,
    passage, offset_debut, offset_fin}` ; **confiance = probabilité softmax**
    réelle du label (sous `DRBERT_CONFIDENCE` = 0,7 → « À valider » en aval) ;
  - **Regroupement au niveau du MOT** (pas du sous-mot) : le NER est entraîné
    par mot et répète « B- » sur les sous-mots → `_aggregate_subwords` (via
    `word_ids` + `offset_mapping`) puis `group_bio` (basé sur l'*étiquette*,
    B-/I- ignorés). Correction : une entité = un mot/une suite de mots, plus de
    fragmentation (« Metformine » au lieu de « Met » + « formine ») ;
  - **Offsets caractères précis** (`offset_mapping`) → le surlignage « Voir le
    passage source » du document fonctionne ; troncature à 512 tokens
    (CamemBERT), `return_offsets_mapping=True`.
- `src/extraction_nlp/entity_extractor.py` :
  - `_augment_with_drbert(base, text)` — **ajoute** les entités manquantes
    (non destructif, dédupliqué par `(valeur, section)`), à la suite du moteur
    règles **ou** du LLM (appliqué à tous les chemins) ;
  - `_drbert_entities_to_extracted` → `ExtractedEntity` avec
    `moteur_nlp="drbert-nlp-v1"` (provenance XAI tracée) ;
  - Repli silencieux si `torch`/`transformers` absents ou échec d'inférence
    (la sortie de base est conservée).
- **Dépendances** : `torch` (CPU) + `transformers >=4.53,<5` (épinglé — la
  5.x casse le tokenizer CamemBERT). Présentes uniquement dans l'environnement
  Python 3.12 (celui qui exécute le pipeline d'extraction).

## Conformité au règlement

- **Art. 9** : 100 % local (torch/transformers CPU). Modèle téléchargé à
  l'installation, jamais au traitement. Inférence sur texte déjà pseudonymisé,
  aucun appel réseau.
- **Annexe 1** : **base Apache 2.0** (propre). Checkpoint : licence « style
  OpenRAIL » personnalisée → **compatible** (usage commercial permis, pas de
  clause interdisant l'usage médical ; disclaimer à respecter). À formuler :
  le modèle est fourni **sous sa licence amont** (attribution §5), pas
  re-licencié comme notre œuvre ; l'app (code) reste sous notre licence.
  Détaillé dans `outputs/AUDIT_DRBERT.md` (§2a).
- **Art. 7** : rappel d'extraction amélioré sur les documents non rubriqués
  (NER spécialisé) ; **XAI conservée** (confiance réelle, seuil 0,7, source et
  moteur tracés).
- **Ergonomie** : aucun impact perceptible — DrBERT est léger (CPU ≤ 1 Go),
  complément silencieux, ne bloque pas l'utilisateur.

## Conséquences

- + NER médical français spécialisé, **très léger** (CPU, ≤ 1 Go) — répond à
  « un LLM/NLP même sur les PC les plus lents » ;
- + Complémentaire (LLM sur machines puissantes, règles en repli) : les trois
  moteurs cohabitent, hiérarchie claire, tracée via `moteur_nlp` ;
- + Rappel amélioré (le NER détecte des entités que les règles/le LLM
  manquent) ;
- − Téléchargement du modèle à l'installation (une fois) ;
- − Licence « style OpenRAIL » personnalisée et révisable → documenter
  (attribution, disclaimer médical, transmission sous licence amont) ; plage de
  repli Apache 2.0 si le jury exige moins de conditions (brique isolée).
