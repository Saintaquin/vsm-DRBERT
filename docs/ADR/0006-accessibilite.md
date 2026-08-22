# ADR-0006 — Fonctionnalités d'accessibilité (page « Paramètres »)

- Statut : accepté · Date : 2026-08-22 · Fonctionnalités validées par l'équipe
  (package complet A1–A5, B1–B2, C1, D1–D3 — cf. `outputs/AUDIT_ACCESSIBILITE.md`).

## Contexte

Audit accessibilité du frontend : fondations WCAG AA déjà présentes (skip-link,
`lang="fr"`, focus-visible, labels, `prefers-reduced-motion`, ARIA), mais
aucun réglage utilisateur (malvoyants, aveugles), focus trap manquant sur la
Palette, pas de page de préférences. Le critère jury « Facilité d'usage et
ergonomie » (10 %) est directement concerné.

## Décision

Fonctionnalités d'accessibilité dans la page **Paramètres** (section
« Accessibilité ») :

| Id | Fonctionnalité | Mise en œuvre |
|---|---|---|
| A1 | Taille de texte (100/112,5/125 %) | `html[data-text-scale]` → `font-size` racine (WCAG 1.4.4) |
| A2 | Contraste renforcé | `html[data-contrast="high"]` : palette AAA, focus 3 px (WCAG 1.4.6) |
| A3 | Thème sombre | `html[data-theme="dark"]` : surcharges ciblées de la palette « blouse » |
| A4 | Police Atkinson Hyperlegible (basse vision) | @font-face **embarquée** (SIL OFL), `html[data-font]` — aucun CDN |
| A5 | Réduire les animations (forcé) | `html[data-motion="reduce"]` (WCAG 2.3.3) |
| B1 | Mode lecteur d'écran | Zone `aria-live` globale + `announce()` conditionnel (résultats, erreurs) |
| B2 | Focus trap + restauration | Hook `useFocusTrap` sur Palette et aide (WCAG 2.4.3) |
| C1 | Raccourcis + aide accessible | `?` (aide dialog), `Ctrl+,` (Paramètres), bouton « Raccourcis (?) » |
| D1 | Persistance locale + réinitialisation | `localStorage` (`vsm-prefs`), détection préférences système, bouton réinitialiser |

Fichiers : `frontend/src/accessibility.ts`, `frontend/src/pages/Accessibility.tsx`,
`frontend/src/App.tsx`, `frontend/src/index.css`, `frontend/src/pages/AuditSettings.tsx`,
`Dashboard.tsx`, `VSMEditor.tsx`, polices dans `frontend/public/fonts/`.

## Conformité au règlement du concours

- **Art. 7 — ergonomie (10 %)** : levier direct ; maturité produit démontrée.
- **Art. 9 — RGPD** : préférences **locales** uniquement (localStorage), aucune
  donnée patient, aucun envoi réseau ; polices embarquées (pas de CDN).
- **100 % local** : aucun appel externe ajouté.
- Backend et pipeline (OCR/anonymisation/VSM) **inchangés** — risque minimal.

## Conséquences

- + Conformité WCAG AA renforcée (focus trap corrigé), AAA en option
  (contraste), dossier technique argumenté.
- + Aucune régression attendue sur le pipeline (frontend pur).
- − Vérifications visuelles (thèmes sombre/contraste, tailles) à réaliser en
  recette utilisateur (documentées dans `outputs/AUDIT_ACCESSIBILITE.md` §5).
