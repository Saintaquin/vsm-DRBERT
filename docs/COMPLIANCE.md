# Conformité — matrice RGPD / HDS / XAI

## RGPD

| Exigence | Mise en œuvre | Fichier(s) | Vérifié par |
|---|---|---|---|
| Minimisation | Seul le texte nécessaire au VSM est extrait ; le reste n'est ni indexé ni transmis | `src/extraction_nlp/` | revue de code |
| Pseudonymisation configurable | Modes `pseudo` (réversible, coffre chiffré) / `strict` (irréversible) ; clé maître hors application (`VSM_VAULT_PASSPHRASE`) | `src/anonymization/` | `tests/test_anonymization/` |
| Anonymisation non désactivable | Le pipeline n'expose que `pseudo` ou `strict` côté API (`off` réservé aux tests internes, jamais exposé) | `src/ui_backend/main.py` (`ProcessIn`) | `tests/test_ui_backend/` |
| Droit à l'oubli | `DELETE /documents/{dossier_id}` : documents + résultats + VSM + mapping en une transaction | `encrypted_store.delete_dossier`, `mapping_vault.forget` | `tests/test_storage/`, E2E |
| Traçabilité | Audit log chaîné par hash (qui, quand, quel document — par SHA-256 —, quel résultat) | `src/storage/encrypted_store.py` | `verify_audit_chain` + tests |
| Pas d'envoi hors machine | Aucune dépendance réseau au runtime ; backend lié à 127.0.0.1 ; CSP Tauri restrictive | `ui_backend/main.py`, `src-tauri/tauri.conf.json` | revue + garde-fou CI |
| Pas de PII dans les logs | Hashs tronqués / IDs uniquement, y compris en audit | `src/anonymization/audit.py` | `test_vault_and_audit.py` |

## Sécurité des données de santé

| Exigence | Mise en œuvre |
|---|---|
| Chiffrement au repos AES-256-GCM | Champ par champ dans SQLite, AAD liant chaque champ à sa table/clé ; base illisible sans clé (testé) |
| Clés Argon2id | Dérivation du mot de passe maître ; clé jamais persistée ; sel stocké séparément |
| Clé en mémoire bornée | `SessionKey` : timeout 15 min d'inactivité, zéroïsation explicite (`ctypes.memset`) à la fermeture/expiration |
| Authentification locale | Multi-utilisateurs, rôles `medecin`/`secretaire`/`admin`, Argon2id, verrouillage après N tentatives |
| Intégrité documentaire | SHA-256 de chaque document d'entrée dans l'audit log ; empreinte SHA-256 du VSM à la signature |

## HDS (Hébergeur de Données de Santé)

L'application est **on-premises** : les données ne quittent jamais
l'établissement, l'agrément HDS n'est donc pas requis dans ce mode.

Éléments à prévoir si un déploiement SaaS était envisagé un jour :
1. Hébergement chez un hébergeur certifié HDS (activités 1 à 6 selon le périmètre).
2. Chiffrement en transit (TLS 1.3) en plus du chiffrement au repos déjà présent.
3. Gestion des clés par HSM ou KMS qualifié ; rotation documentée.
4. Journalisation centralisée horodatée qualifiée (eIDAS) ; conservation réglementaire.
5. PRA/PCA documentés, sauvegardes chiffrées testées.
6. Analyse d'impact (AIPD) mise à jour, contrat de sous-traitance art. 28 RGPD.
7. Authentification renforcée (MFA), revue périodique des habilitations.

## XAI — explicabilité

| Exigence | Mise en œuvre |
|---|---|
| Source de chaque champ | `champ_trace.source` : document_id, page, passage d'origine |
| Score de confiance | `champ_trace.confiance` ∈ [0,1], affiché sur chaque champ |
| Moteurs utilisés | `champ_trace.moteurs` : OCR + NLP (ex. `tesseract` / `rules-v1`) |
| Clic → passage source | VSMEditor « Voir le passage source » → DocumentViewer avec surlignage |
| Seuil « À valider » | < 0,7 (configurable dans `vsm_builder`) → `a_valider: true`, fond ambre distinct |
| Avertissement médical | Présent dans le schéma (champ obligatoire), tous les rendus et l'UI |

## Limites assumées

- La cohérence **médicale** du contenu n'est pas vérifiée par la machine :
  validation humaine obligatoire (statut `signe` réservé au rôle médecin).
- La zéroïsation mémoire en CPython est *best effort* (copies transitoires
  possibles par l'interpréteur) — documenté dans `docs/SECURITY.md`.
- Les référentiels CIM-10/ATC embarqués sont des extraits de démonstration ;
  charger les référentiels complets ATIH/WHOCC avant production.
