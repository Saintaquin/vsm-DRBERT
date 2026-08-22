# Anonymisation — stratégies, limites et procédures RGPD

## Stratégies disponibles

### 1. Pseudonymisation (mode `pseudo`, par défaut)

Chaque PII détectée est remplacée par un **token stable au sein du dossier** :
`[PATIENT_001]`, `[DATE_NAISSANCE_001]`, `[NIR_001]`, `[RPPS_001]`, etc.
La même valeur produit toujours le même token dans un dossier donné, ce qui
préserve la cohérence du texte pour l'extraction NLP.

Le mapping token ↔ valeur réelle est conservé dans un **coffre-fort chiffré**
(`mapping_vault.py`, AES-256-GCM) dont la clé est dérivée (Argon2id) d'une
phrase secrète **hors application** : la variable d'environnement
`VSM_VAULT_PASSPHRASE`, gérée par l'administrateur/DSI. Sans cette variable,
les mappings ne sont **pas conservés** (comportement équivalent au mode strict).

Réversibilité contrôlée : seule une session disposant de la passphrase du
coffre peut ré-injecter les vraies identités (par ex. pour imprimer le VSM
final au nom du patient).

### 2. Anonymisation stricte (mode `strict`)

Remplacement irréversible par `[REDACTED:TYPE]`. Aucun mapping n'est conservé.
À utiliser pour toute exportation de données vers la recherche, les tests, etc.

### Niveau d'application

- **Niveau texte (implémenté, systématique)** : appliqué sur la sortie OCR
  **avant** toute extraction NLP et tout stockage de résultat.
- **Niveau image (optionnel, non activé par défaut)** : masquage des zones
  d'en-tête via les coordonnées `image_to_data` de Tesseract. Prévu comme
  extension ; le niveau texte couvre déjà l'exigence réglementaire car le
  document image original n'est stocké que chiffré et n'est jamais transmis.

## Types de PII détectés

Identité (noms + prénoms via dictionnaire INSEE réduit + heuristiques
contextuelles « Patient : », « Né(e) le : »), NIR/n° sécurité sociale (avec
clé de contrôle), INS, dates de naissance/décès, adresse postale, téléphone,
email, RPPS, ADELI, FINESS, n° de dossier, n° de séjour.

Approche **hybride** : regex + dictionnaires + heuristiques de contexte, avec
un **adaptateur spaCy optionnel** (`fr_core_news_lg`) qui, s'il est installé,
ajoute la NER statistique pour les noms hors dictionnaire.

## Limites connues (à lire avant tout déploiement)

- **Rappel non garanti à 100 %.** Sur le dataset synthétique, le rappel des
  identifiants connus est de 100 %, mais sur de vrais scans dégradés, des PII
  mal OCRisées (ex. « DUP0NT » avec un zéro) peuvent échapper aux détecteurs.
  → Le document original reste chiffré ; le texte anonymisé doit être relu.
- **Faux positifs probables** : noms de médicaments ou d'éponymes médicaux
  (maladie de Parkinson, signe de Babinski) ressemblant à des noms propres.
  Les heuristiques de contexte en éliminent la majorité, pas la totalité.
- **Noms hors dictionnaire** sans contexte explicite : détection dégradée si
  spaCy n'est pas installé.
- La détection opère sur le texte OCR : un OCR de très mauvaise qualité
  dégrade mécaniquement la détection (voir benchmark).

## Procédure RGPD — droit à l'oubli

1. Dans le tableau de bord, bouton **« Oublier »** sur le dossier (ou
   `DELETE /documents/{dossier_id}`).
2. Sont supprimés **en une action** : document chiffré, résultats OCR/NLP,
   VSM associés, et l'entrée du coffre-fort de mapping (`MappingVault.forget`).
3. Un événement `dossier_deleted` est journalisé (sans PII) dans l'audit log —
   la trace de la suppression est conservée, pas les données.

⚠️ Effacer un mapping du coffre rend **définitivement impossible** la
ré-identification des tokens du dossier concerné : les VSM conservés ailleurs
sous forme pseudonymisée deviennent anonymes de fait.

## Audit

Chaque passage du détecteur produit une entrée d'audit : type de PII, position,
confiance, action prise (pseudonymisé/masqué). **Jamais la valeur en clair** —
seulement un hash SHA-256 tronqué à 16 hexadécimaux pour le recoupement.
