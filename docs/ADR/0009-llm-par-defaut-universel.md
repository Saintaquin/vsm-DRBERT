# ADR-0009 — LLM local PAR DÉFAUT sur toutes les machines (modèle universel)

- Statut : accepté · Date : 2026-08-22 · Modifie ADR-0004 (le LLM n'est plus
  optionnel) et ADR-0001.

## Contexte

Sur la version de base, l'extraction par règles rend la phase de validation
longue et génère des erreurs récurrentes (documents non rubriqués). Décision :
**un LLM local par défaut sur TOUTES les versions**, y compris machines
< 8 Go RAM et **sans GPU**. Le moteur règles reste en **repli automatique**
(garantie de fonctionnement, jamais supprimé).

## Audit des petits LLM (machines < 8 Go, sans GPU)

Critères : licence (annexe 1), taille GGUF Q4, RAM, qualité français, fiabilité
JSON, CPU. Licences vérifiées (fiches HF / éditeurs) :

| Modèle | Taille Q4 | RAM min. | Licence | Français | Verdict |
|---|---|---|---|---|---|
| **Qwen 2.5 3B Instruct** | ~2,0 Go | 4 Go | **Apache 2.0** ✅ | Bon (multilingue, FR correct) | ⭐ **Défaut universel** |
| **Qwen 2.5 1.5B Instruct** | ~1,0 Go | 3 Go | **Apache 2.0** ✅ | Correct | Repli < 4 Go |
| SmolLM2 1.7B | ~1,0 Go | 3 Go | Apache 2.0 ✅ | Plus faible (FR) | — |
| Llama 3.2 3B | ~2,0 Go | 4 Go | Llama Community ⚠️ | Correct | — |
| Gemma 2 2B | ~1,6 Go | 4 Go | Gemma ⚠️ | Bon | — |
| Phi-3.5-mini 3.8B | ~2,3 Go | 5 Go | MIT ✅ | Correct | — |

→ **Qwen 2.5 3B Q4_K_M (Apache 2.0, ~2 Go, CPU)** : tient sur 4-8 Go sans GPU,
licence propre pour l'annexe 1, JSON fiable (llama.cpp `json_object`).

## Système de prompt efficace (implémenté)

`build_llm_messages(text)` dans `src/extraction_nlp/entity_extractor.py` :
- **Rôle** : assistant médical français remplissant un VSM ;
- **Schéma JSON strict** : 7 rubriques, éléments `{valeur, passage}` ;
- **Anti-hallucination** : « N'INVENTE RIEN » — rubrique absente → `[]` ;
- **Normalisation** : orthographe corrigée dans « valeur », dosage conservé,
  « passage » reproduit à l'identique ;
- **Négations** : « aucune allergie » → `[]` ;
- **Pseudonymes** : jamais dans « valeur » (RGPD) ;
- **Few-shot** : un exemple complet de réponse ancré dans le prompt ;
- **Troncature** : texte limité au contexte (6 000 caractères).

## Intégration « non optionnelle »

- `ProcessIn.nlp_engine` : **défaut « llm »** (API) ; l'UI n'affiche plus le
  choix Règles/LLM — **LLM par défaut**, règles = repli invisible ;
- `/health` expose `llm_available` ; l'UI avertit si le modèle n'est pas
  téléchargé (`python -m src.extraction_nlp.llm` → Qwen 2.5 3B) ;
- Téléchargement à l'installation (jamais pendant le traitement — art. 9) ;
- Repli automatique `llm → rules` si modèle absent ou échec (déjà en place,
  testé) ; provenance XAI trace le moteur réellement utilisé.

## Conformité au règlement

- **Art. 9** : 100 % local (llama.cpp), modèle téléchargé à l'installation,
  extraction sur texte déjà pseudonymisé, aucun appel réseau.
- **Annexe 1** : **Apache 2.0** (Qwen 2.5) — aucune contrainte de droits de
  tiers pour la licence aux Organisateurs.
- **Art. 7** : performance d'extraction améliorée (LLM sur documents non
  rubriqués) ; XAI conservée (confiance 0,65 → champs « À valider », source et
  moteur tracés).
- **Risque maîtrisé** : si le modèle est absent, l'app fonctionne quand même
  (repli règles) — aucune régression.

## Conséquences

- + LLM par défaut sur toutes machines (4 Go+), licence Apache 2.0, CPU.
- + Prompt structuré (anti-hallucination) = extraction plus fiable et relecture
  plus rapide (objectif initial).
- − Téléchargement ~2 Go à l'installation (une fois) ; inférence CPU plus lente
  que les règles (compensée par le traitement asynchrone).
- − Les règles restent le socle si l'utilisateur ne télécharge pas le modèle
  (alerte dans l'UI).
