# ADR-0004 — Moteur LLM local : audit des modèles et choix par défaut

- Statut : accepté · Date : 2026-08-22 · Remplace la cible de l'ADR-0001
  (Llama 3.1 8B Q4) pour le modèle par défaut.

## Contexte

L'application propose un moteur d'extraction LLM local (adaptateur
`llama-cpp-python`, strictement offline — aucune donnée ne quitte la machine,
exigence RGPD de l'art. 9 du concours). L'ADR-0001 ciblait Llama 3.1 8B Q4.
Cet ADR audite les candidats et fixe le modèle par défaut.

## Critères (règles du concours + contraintes)

| # | Critère | Justification (règlement) |
|---|---|---|
| C1 | **100 % local / offline** | Art. 9 — aucune donnée patient hors machine |
| C2 | **Licence non contraignante** | Annexe 1 art. 4 — le prototype ne doit pas contrefaire de droits tiers ; licence Apache 2.0 préférée |
| C3 | **RAM ≤ 16 Go (poste praticien)** | ADR-0001 ; la machine de développement actuelle n'a que 8 Go |
| C4 | **Français médical** | Documents français (labo, anapath, lettres) |
| C5 | **JSON structuré fiable** | Extraction par sections (`response_format: json_object`) |
| C6 | **GGUF Q4_K_M disponible** | Compatible llama.cpp, taille/RAM maîtrisées |

## Candidats comparés

| Modèle | GGUF Q4_K_M | RAM min. | Licence | Français | JSON | Note /5 |
|---|---|---|---|---|---|---|
| **Mistral NeMo 12B Instruct** | ~7,2 Go | 16 Go | **Apache 2.0** ✅ | Excellent (Mistral AI) | Très bon | **5** |
| **Qwen 2.5 7B Instruct** | ~4,7 Go | 8 Go | **Apache 2.0** ✅ | Bon (multilingue) | Très bon | **4** |
| **Mistral 7B Instruct v0.3** | ~4,1 Go | 8 Go | **Apache 2.0** ✅ | Très bon (française) | Bon | 4 |
| Llama 3.1 8B Instruct (ADR-0001) | ~4,9 Go | 12 Go | Llama Community License ⚠️ | Bon | Bon | 3 |
| Llama 3.2 3B Instruct | ~2,0 Go | 4 Go | Llama Community License ⚠️ | Correct | Moyen | 2 |
| Modèles FR médicaux spécialisés (MedraQ, Quaero, MedGemma-FR) | 4–8 Go | 8–16 Go | Varie, souvent floue ⚠️ | Bon | Variable | 2 |

## Analyse

1. **Licence** : seuls les modèles **Apache 2.0** (Mistral NeMo 12B, Qwen 2.5 7B,
   Mistral 7B v0.3) lèvent toute ambiguïté pour l'annexe 1 du règlement. Les
   modèles Llama (licence communautaire) et les modèles médicaux de recherche
   (licences parfois non précisées) présentent un risque documentaire.
2. **Français** : Mistral (société française) offre le meilleur rendement en
   français ; Qwen 2.5 est très bon multilingue ; Llama 3.1 est correct.
3. **Matériel** : le poste praticien ciblé a **16 Go** (ADR-0001) → Mistral
   NeMo 12B Q4 (~7,2 Go + contexte) tient confortablement. Sur 8 Go,
   Qwen 2.5 7B / Mistral 7B v0.3 (4–5 Go) sont le bon compromis.
4. **Fiabilité** : les GGUF TheBloke/Qwen sont éprouvés avec llama.cpp et le
   mode `json_object` ; les modèles médicaux de recherche sont moins matures
   (quantizations, prompt JSON) et mal maintenus — risque pour une soutenance.

## Décision

- **Modèle par défaut : Mistral NeMo 12B Instruct Q4_K_M** (~7,2 Go, Apache 2.0,
  excellent français) — pour les postes 16 Go, qualité maximale, licence propre.
- **Repli léger documenté** selon la RAM détectée (`--list` conseille
  automatiquement) :
  - **9–14 Go** : Qwen 2.5 7B Instruct Q4_K_M (~4,7 Go, Apache 2.0) ;
  - **< 9 Go** : Qwen 2.5 3B Instruct Q4_K_M (~2,0 Go, Apache 2.0) —
    recommandation prudente : modèle + contexte + OS + application doivent
    tenir en mémoire sans swap.
- Sélection via `python -m src.extraction_nlp.llm --model {mistral-nemo-12b|
  qwen2.5-7b|qwen2.5-3b}` ; choix à l'installation par l'administrateur
  (`VSM_LLM_MODEL` / `VSM_LLM_MODEL_PATH`).

## Note — contrainte de performance du règlement

Le règlement du concours **ne fixe aucune contrainte matérielle ou de temps**
(RAM, CPU, vitesse) : ni l'article 5, ni l'article 7, ni l'annexe 1 ne
l'imposent. Les seuls critères liés sont « *Performance de l'extraction IA*
(25 %) » — la **qualité** (précision/rappel) — et « *Facilité d'usage et
ergonomie* (10 %) ». L'objectif « 16 Go » vient de l'ADR-0001 (hypothèse de
déploiement du projet), pas du règlement. Le moteur **règles reste le défaut
et fonctionne sur toute machine** ; le LLM est optionnel et son choix
s'adapte à la RAM disponible sans impact sur la conformité.

## Conséquences

- + Licence Apache 2.0 : aucun risque sur l'annexe 1 ; français supérieur pour
  les CR réels ; JSON fiable.
- + Téléchargement unique à l'installation (jamais pendant le traitement).
- − Taille 7,2 Go (vs 4,9 Go pour Llama 3.1 8B) : nécessite 16 Go de RAM.
- − Le LLM reste **optionnel** : le moteur par règles demeure le socle
  (déterminisme, XAI triviale, zéro dépendance) ; le LLM apporte le gain sur
  les documents non rubriqués. Confiance LLM conservatrice (0,65 < seuil 0,7 →
  champs « À valider » par le médecin).
