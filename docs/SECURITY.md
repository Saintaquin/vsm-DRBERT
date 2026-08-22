# Sécurité — modèle de menace, mesures, procédure d'incident

## Périmètre

Application locale mono-poste ou serveur on-premises. Surface réseau :
`127.0.0.1:8741` exclusivement (le binding `0.0.0.0` est interdit par
construction — voir `src/ui_backend/main.py:main`).

## Modèle de menace

| # | Menace | Vraisemblance | Mesures |
|---|---|---|---|
| T1 | Vol du disque / de la machine | Moyenne | Chiffrement AES-256-GCM champ par champ ; clé dérivée Argon2id, jamais persistée ; sans mot de passe, base et coffre illisibles (tests dédiés) |
| T2 | Accès au poste déverrouillé | Moyenne | Session 15 min d'inactivité → clé zéroïsée + déconnexion ; login obligatoire |
| T3 | Utilisateur interne malveillant | Faible | Rôles (signature réservée médecin, audit réservé médecin/admin) ; audit chaîné par hash : modification rétroactive détectée (`verify_audit_chain`) |
| T4 | Vol de mot de passe par force brute | Moyenne | Argon2id (coût mémoire), verrouillage après N tentatives, mot de passe ≥ 12 caractères |
| T5 | CSRF depuis un site web visité | Moyenne | Cookie `httpOnly` + `SameSite=Strict` + token CSRF double-submit obligatoire sur tous les endpoints authentifiés |
| T6 | Fichier malveillant téléversé | Moyenne | Types restreints (PDF/PNG/JPG/TIFF), taille limitée par défaut à 50 Mo (configurable via `VSM_MAX_UPLOAD_MB`), lecture par blocs (rejet précoce sans chargement mémoire), traitement par bibliothèques d'image (pas d'exécution), pages corrompues isolées |
| T7 | Injection SQL | Faible | Requêtes 100 % paramétrées (aucune concaténation de valeurs) |
| T8 | Exfiltration réseau | Faible | Aucun appel sortant au runtime ; CSP Tauri `connect-src 'self' http://127.0.0.1:8741` ; pas de télémétrie |
| T9 | PII dans les journaux | Moyenne | Audit/logs : hashs tronqués et IDs uniquement, garanti par construction (`audit.py`) et testé |
| T10 | Falsification d'un VSM signé | Faible | Empreinte SHA-256 des sections au moment de la signature, conservée chiffrée + événement d'audit |

## Limites connues

- **Zéroïsation mémoire CPython** : `SessionKey` écrase son tampon via
  `ctypes.memset`, mais l'interpréteur peut avoir créé des copies transitoires
  (immutabilité des `bytes`). Mitigation partielle : durée de vie courte de la
  clé, `bytearray` mutable comme stockage primaire. Une garantie forte
  exigerait un module natif ou un HSM.
- **Pas de protection contre un administrateur système malveillant** ayant un
  accès root pendant qu'une session est ouverte (lecture de la RAM).
- L'icône Tauri fournie est un placeholder ; remplacer avant distribution.

## Durcissement recommandé en production

1. Chiffrement disque complet (BitLocker / LUKS) en plus du chiffrement applicatif.
2. `VSM_VAULT_PASSPHRASE` injectée par un gestionnaire de secrets, pas en clair
   dans un script de lancement.
3. Compte OS dédié à l'application, sans droits d'administration.
4. Sauvegardes chiffrées de `~/.vsm-ocr/` testées régulièrement (la perte du
   mot de passe maître = perte des données, par conception).

## Procédure d'incident

1. **Détection** — `chain_valid: false` sur `/audit`, comportement anormal, ou
   signalement utilisateur.
2. **Confinement** — fermer l'application (tue le backend, efface les clés de
   session) ; isoler le poste du réseau si compromission suspectée.
3. **Préservation** — copier `~/.vsm-ocr/` (bases chiffrées + audit) sur support
   scellé ; noter l'horodatage et les utilisateurs actifs.
4. **Analyse** — vérifier la chaîne d'audit entrée par entrée ; identifier la
   première entrée invalide ; recouper avec les connexions OS.
5. **Notification** — si violation de données personnelles : notification CNIL
   sous 72 h (art. 33 RGPD) et information des personnes concernées si risque
   élevé (art. 34) ; informer le DPO de l'établissement.
6. **Remédiation** — rotation du mot de passe maître et de la passphrase du
   coffre (re-chiffrement), revue des comptes, post-mortem documenté.

## Vérifications automatisées (CI)

- `bandit -r src/` (analyse statique sécurité), `ruff` (lint), `pip-audit`
  (CVE des dépendances), garde-fou anti-PII (pattern NIR hors dataset
  synthétique), suite pytest complète.
