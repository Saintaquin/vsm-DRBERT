# ADR-0007 — Statistiques anonymes (visualisations locales)

- Statut : accepté · Date : 2026-08-22 · POC validé par l'équipe
  (audit de faisabilité : outputs/AUDIT_STATISTIQUES.md).

## Décision

Ajouter une page **« Statistiques »** (locale) : agrégats anonymes sur les VSM
de la machine — nombre de VSM (total/statut/période), **récurrences de
pathologies (CIM-10)** et de **traitements (ATC)** normalisés, complétude par
rubrique.

## Garde-fous de conformité (art. 9 du règlement / RGPD / CNIL)

| Garde-fou | Mise en œuvre |
|---|---|
| Agrégats uniquement | `GET /stats` ne renvoie que des comptages ; aucun détail patient, aucun token |
| Pas de lien identité | Le coffre-fort de mapping **n'est jamais lu** par le module stats (séparé) |
| Secret statistique | Effectifs **n < 5** masqués (« < 5 »), seuil configurable `VSM_STATS_MIN_COUNT` (CNIL MR-001/004/008) |
| Droit à l'oubli | **Recalcul à la demande** (aucun cache) → la suppression d'un dossier met à jour les stats (testé) |
| Aucun croisement | Aucun appel réseau, aucune importation — 100 % local |
| Avertissement | « Statistiques descriptives, non représentatives » affiché dans l'UI |

## Mise en œuvre

- Backend : `GET /stats` (`src/ui_backend/main.py`) — déchiffrement des VSM en
  session, comptage CIM-10/ATC, masquage, événement d'audit `stats_viewed`
  (sans PII).
- Frontend : `pages/Stats.tsx` (cartes + graphiques **SVG maison**, aucun CDN),
  entrée de navigation « Statistiques » (tous rôles — agrégats anonymes).
- Tests : 96 (+3 : agrégats/masquage, absence de détail patient, droit à
  l'oubli → recalcul).

## Conformité / limites

- Art. 7 (RGPD 15 %, innovation 15 %) : atout si les garde-fous sont repris
  dans le dossier éthique.
- Limite : qualité des stats dépend des **référentiels CIM-10/ATC complets**
  (actuellement extraits de démo — recommandation P1 déjà émise).
