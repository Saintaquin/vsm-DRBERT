# ADR-0001 — Moteur NLP : règles par défaut, LLM local en adaptateur

- Statut : accepté · Date : 2026-06-12

## Contexte
Le prompt projet propose llama-cpp-python (Llama 3.1 8B Q4, ~5 Go). Contraintes :
offline-first, poste praticien 16 Go RAM sans GPU, installation simple, aucun
modèle lourd dans le repo.

## Décision
Moteur par défaut = **extraction par règles** (segmentation par rubriques
regex + normalisation fuzzy CIM-10/ATC). **Adaptateur LLM local optionnel**
(`extract_entities_llm`, llama-cpp, modèle téléchargé au premier usage dans
`~/.cache/vsm-ocr/`).

## Justification
Les documents cibles (CR, ordonnances, lettres de sortie) sont fortement
structurés par rubriques : les règles y sont précises, déterministes,
auditables (XAI trivial : le passage source EST la règle déclenchée), et
fonctionnent sans téléchargement — installation garantie en environnement
hospitalier verrouillé. Le LLM apporte un gain sur texte libre non rubriqué,
au prix de 5 Go de RAM et d'une explicabilité moindre : pertinent en option,
pas en socle.

## Conséquences
+ Installation minimale, comportement reproductible, tests stables.
− Rappel plus faible sur prose libre → roadmap : brancher CamemBERT-bio/Dr-BERT
  ou le LLM local via le même contrat `extract_entities()`.
