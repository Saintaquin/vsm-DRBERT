# ADR-0002 — Stockage : SQLite + AES-256-GCM champ par champ (vs SQLCipher)

- Statut : accepté · Date : 2026-06-12

## Décision
SQLite standard + chiffrement applicatif **champ par champ** via
`cryptography` (AES-256-GCM, nonce aléatoire, AAD = table/clé), plutôt que
SQLCipher ou Fernet.

## Justification
- SQLCipher exige une compilation/distribution binaire spécifique par OS —
  friction forte pour un déploiement poste praticien Windows/Linux.
- AES-GCM (AEAD) > Fernet : authentification intégrée + AAD liant chaque
  champ à son contexte (anti-réutilisation de ciphertext entre lignes).
- Champ par champ : les métadonnées non sensibles (ids, timestamps) restent
  requêtables ; tout contenu patient est opaque sans la clé (testé).
- L'audit log reste en clair **par construction sans PII**, et chaîné par
  hash pour l'inviolabilité.
- Migrations : schéma SQL embarqué idempotent (executescript) ; Alembic jugé
  surdimensionné pour 6 tables sans ORM — à réévaluer si le schéma grossit.
