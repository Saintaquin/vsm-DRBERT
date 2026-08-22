# ADR-0003 — Wrapper desktop : Tauri (vs Electron / navigateur seul)

- Statut : accepté · Date : 2026-06-12

## Décision
Tauri 2 : fenêtre native sur le frontend bundlé, backend Python lancé en
sous-process au démarrage et tué à la fermeture (= effacement des clés de
session). Bundles `.msi`, `.AppImage`, `.deb`. Ni télémétrie ni auto-update.

## Justification
- Surface d'attaque et empreinte très inférieures à Electron (pas de Chromium
  embarqué) ; CSP stricte configurée (`connect-src` limité à 127.0.0.1:8741).
- Le navigateur seul reste possible (FastAPI sert `frontend/dist`), mais le
  wrapper garantit le cycle de vie du backend et une expérience « application »
  attendue en cabinet.
- Coût : toolchain Rust au build (CI uniquement, sur tag) — aucun impact à
  l'exécution ni pour les développeurs Python/JS au quotidien.
