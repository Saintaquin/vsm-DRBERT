# Changelog — vsm-ocr

Format : [Keep a Changelog] · Versionnage : SemVer.

## [1.0.1] — 2026-08-20

### Corrections d'audit (concours IA & Santé — rapport outputs/AUDIT_CONFORMITE_RAPPORT.md)

- **Anonymisation (critique)** : le détecteur PII ne masquait pas les noms sur
  les documents réels (« Monsieur ABRICOT Anthony », « Mme BANANE Sophie »).
  Corrections dans `src/anonymization/pii_detector.py` :
  - insensibilité à la casse des titres (`(?i:…)`, limitée au titre — les
    titres sont capitalisés dans les vrais documents) ;
  - support du format « NOM-en-MAJUSCULES + prénom » (CR de laboratoire) ;
  - titres étendus : `MR`, `M`, `Bénéficiaire`, `Prénom` ;
  - repli « ligne d'identité » pour les variantes OCR (« ABRICOT Antrony ») ;
  - contextes de naissance `DDN`/`DON`/`naissance`/`netssance` et recherche
    par ligne (dates répétées dans les tableaux) ; années à 2 chiffres ;
  - `NUMERO_DOSSIER` : format « Demande n° X » ; stopwords laboratoire.
- **Remplacement par valeur** (`pseudonymizer.py`, `anonymizer.py`) : une PII
  détectée quelque part dans le document est masquée partout (passe 2).
- **Export HTML** (`src/ui_backend/main.py`) : les GET/HEAD de lecture sont
  exemptés du token CSRF (cookie SameSite=Strict) — le lien « Exporter (HTML) »
  fonctionne (403 → 200).
- **Identité VSM** (`src/extraction_nlp/pipeline.py`) : les blocs `patient` et
  `medecin_traitant` sont remplis depuis les tokens de pseudonymisation.
- **Dataset** (`generate_dataset.py`) : cas_006 (CR de laboratoire) et cas_007
  (compte-rendu d'anapath) aux formats réels ; police de repli Windows.
- **Tests** : 65 tests (21 ajoutés : formats réels, passe 2, CSRF, identité).

### Format VSM conforme au gabarit HAS (aligné sur des exemples de vrais VSM)

- **Rendu** (`src/vsm_generation/renderer.py`) : le document généré (markdown,
  HTML, PDF) reprend la structure d'un vrai Volet de Synthèse Médicale :
  1. Identification du patient (pseudonymisée) · 2. Médecin traitant ·
  3. Pathologies actives · 4. Antécédents · 5. Allergies · 6. Traitements au
  long cours · 7. Facteurs de risque · 8. Vaccinations · 9. Points de
  vigilance — avec date de génération, statut, avertissement médical,
  badges de confiance XAI, zone de signature et empreinte SHA-256.
- **Identité enrichie** (`src/extraction_nlp/pipeline.py`) : sexe (« Sexe : H/F/
  Masculin/Féminin ») et ADELI ajoutés aux blocs patient/médecin traitant ;
  schéma VSM (`schema/vsm_schema.json`) : `medecin_traitant.adelI`.
- **Extraction** (`entity_extractor.py`) : les lignes « CONCLUSION /
  OBSERVATIONS » clôturent la dernière rubrique au lieu d'y être rattachées
  (qualité de la synthèse).
- **Tests** : 69 tests (rendu HAS numéroté, blocs d'identité, absence de PII
  en clair dans les rendus, non-pollution CONCLUSION).

### Repli « texte libre » — extraction sur documents non rubriqués (P0-4)
- `entity_extractor.py` : nouvelle passe de repli, déclenchée **par rubrique**
  uniquement si l'extraction par en-têtes ne l'a pas remplie (aucun doublon) :
  - « CONCLUSION : … » → **Points de vigilance** (segment borné, bruit filtré) ;
  - « Dans ses antécédents on note : », « Sur le plan (médical/chirurgical/
    familial/…) » → **Antécédents** ;
  - « traitement par X », molécule du référentiel ATC + dosage → **Traitements
    au long cours** ;
  - « allergie à X » (négations « aucune/pas de/sans » exclues) → **Allergies** ;
  - facteurs de risque (tabac, alcool, obésité, sédentarité…) → **Facteurs de
    risque** ; vaccinations (rappel/année) → **Vaccinations** ; diagnostics
    CIM-10 en lignes courtes (match fuzzy) → **Pathologies/antécédents**.
  - Confiances volontairement basses (0,55–0,8) → champs « À valider » par le
    médecin (XAI).
- Résultat sur les vrais dossiers fournis : le VSM de BANANE contient désormais
  antécédents, traitement et conclusions cliniques (avant : vide) ; aucun bruit
  (signatures, pagination) capturé.
- **Tests** : 73 (repli texte libre, bornage, négations d'allergie, non-doublon
  avec les en-têtes).

### Upload de gros documents (> 50 Mo)

- `src/ui_backend/main.py` : la limite d'upload est **configurable** via
  `VSM_MAX_UPLOAD_MB` (Mo, défaut 50) ; le téléversement est lu **par blocs de
  1 Mo** (un fichier trop volumineux est rejeté en cours de flux, sans être
  chargé en mémoire) ; message d'erreur explicite. La limite est exposée par
  `GET /health` (`max_upload_mb`).
- `frontend/src/pages/Dashboard.tsx` : la limite affichée dans l'UI reflète la
  valeur configurée côté serveur.
- **Tests** : 75 (limite configurable — rejet 413 au-delà, acceptation en
  dessous — et exposition via /health) ; docs SECURITY.md/README mises à jour.

### Export PDF du VSM

- `src/ui_backend/main.py` : nouvel export `GET /vsm/{id}/export?fmt=pdf` —
  génération 100 % locale (ReportLab) puis réponse `application/pdf`
  téléchargeable (`Content-Disposition: attachment`).
- `frontend` : bouton « Exporter (PDF) ↓ » dans l'éditeur VSM, à côté de
  « Exporter (HTML) ↗ » ; nouvelle URL `api.exportPdfUrl`.
- **Tests** : 76 (export PDF : statut 200, type `application/pdf`, en-tête
  `%PDF`, contenu > 1 Ko ; format inconnu → 400).

### LLM local (extraction optionnelle, 100 % offline)

- **Audit des modèles** (`docs/ADR/0004-llm-local-choix-modele.md`) : critères
  concours (licence annexe 1, RGPD/offline art. 9, RAM 16 Go, français,
  JSON) → **recommandation : Mistral NeMo 12B Instruct Q4_K_M (Apache 2.0)**,
  repli 8 Go : Qwen 2.5 7B Instruct.
- `src/extraction_nlp/llm.py` : catalogue des modèles (métadonnées
  licence/taille/RAM), `download_model()` (GGUF validé par signature, hors
  flux de traitement), CLI `python -m src.extraction_nlp.llm --list/--model`.
- `entity_extractor.py` : adaptateur LLM renforcé (prompt système, parsing
  JSON tolérant, confiance conservative 0,65 → « À valider », XAI).
- API : `ProcessIn.nlp_engine` (`rules`|`llm`, validation stricte) ; frontend :
  sélecteur « Extraction : Règles / LLM local » ; provenance XAI tracée
  (`moteur_effectif`, repli inclus).
- **Tests** : 86 (10 ajoutés : config, métadonnées, repli, parsing JSON, API).

### Moteur OCR optionnel « Unlimited-OCR » (baidu, licence MIT — GPU NVIDIA)

- Étude de https://github.com/baidu/Unlimited-OCR (OCR documentaire panoptique
  en une passe, marqueurs de structure `<|det|>`) → ADR-0005.
- `src/ingestion_ocr/ocr_engines.py` : adaptateur `UnlimitedOCREngine`
  (torch/transformers, strictement local, sortie nettoyée des marqueurs).
- **Exigence carte NVIDIA** : sans `torch.cuda.is_available()`, le moteur
  **n'existe pas** (absent de `ENGINES`, de `/health`, de l'UI ; API → 400).
- `GET /health` expose `available_engines` ; sélecteur OCR dans l'UI
  (n'affiche « Unlimited-OCR (NVIDIA) » que si disponible).
- **Tests** : 93 (+5 : nettoyage marqueurs, gating GPU, rejet API, /health).
- Recette sur poste NVIDIA documentée (benchmark CER/WER français) —
  `outputs/AUDIT_OCR_UNLIMITED.md` ; Tesseract+fra reste le défaut.

### Accessibilité — page « Paramètres » (malvoyants, aveugles, clavier)

- Audit (outputs/AUDIT_ACCESSIBILITE.md) → **fonctionnalités validées** :
  A1 taille de texte (100/112,5/125 %) · A2 contraste renforcé (AAA) ·
  A3 thème sombre · A4 police **Atkinson Hyperlegible embarquée** (SIL OFL,
  aucun CDN) · A5 animations réduites · B1 mode lecteur d'écran (annonces
  `aria-live`) · B2 **focus trap** Palette/aide corrigé (WCAG 2.4.3) ·
  C1 raccourcis `?` (aide) et `Ctrl+,` (Paramètres) · D1 persistance
  `localStorage` + réinitialisation + détection préférences système.
- Fichiers : `frontend/src/accessibility.ts`, `pages/Accessibility.tsx`,
  `App.tsx`, `index.css`, `AuditSettings.tsx`, `Dashboard.tsx`, `VSMEditor.tsx`.
- Conformité : art. 7 (ergonomie 10 %), RGPD neutre (préférences locales,
  aucune donnée patient), 100 % local ; backend inchangé (93 tests verts).
- ADR-0006 ; checklist de recette D3 dans le rapport d'audit.

### Statistiques anonymes — visualisations locales (POC validé)

- Audit de faisabilité (règlement art. 9 + RGPD/CNIL + HAS) → **POC implémenté**.
- `GET /stats` (`src/ui_backend/main.py`) : nb VSM (total/statut/période),
  récurrences **CIM-10** (pathologies) et **ATC** (traitements), complétude —
  agrégats uniquement, **sans jamais lire le coffre de mapping** (aucun lien
  identité), **masquage n < 5** (secret statistique CNIL, seuil configurable
  `VSM_STATS_MIN_COUNT`), **recalcul à la demande** (le droit à l'oubli
  met à jour les stats — testé), aucun croisement externe, avertissement
  « descriptif, non représentatif ».
- Frontend : page « Statistiques » (cartes + graphiques **SVG maison**, aucun
  CDN) — `pages/Stats.tsx`, entrée de navigation.
- ADR-0007 ; rapport : `outputs/AUDIT_STATISTIQUES.md`.
- **Tests** : 96 (+3 : agrégats/masquage, absence de détail patient, oubli).

### Logging structuré local + redaction (audit FastAPI/logs validé)

- Audit de faisabilité (outputs/AUDIT_FASTAPI_LOGS.md) — FastAPI est confirmé
  comme backend (uvicorn 127.0.0.1, `/audit` existant, docs désactivés).
- `src/ui_backend/logging_setup.py` : fichiers `<VSM_DATA_DIR>/logs/app.log`
  (rotation 1 Mo × 5, niveau `VSM_LOG_LEVEL`), **filtre de redaction**
  systématique (NIR, téléphone, email, RPPS/ADELI, tokens de pseudonymisation
  masqués dans TOUTE entrée — défense en profondeur), **aucun handler réseau**
  (art. 9 : pas de logs cloud).
- Points de log sans PII dans `main.py` : démarrage, upload, traitement
  (moteur OCR/NLP, nb PII), changement de statut VSM, export, erreur moteur.
- **Tests** : 102 (+6 : écriture/rotation, idempotence, absence de handler
  réseau, redaction des PII, application à l'écrit).

### Traitement asynchrone des documents (correction « temps infini »)

- **Problème** : le traitement (OCR) s'exécutait dans la requête HTTP → sur
  les gros PDF, attente sans progression ; au-delà de ~15 min, la session
  expirait pendant le traitement → le rafraîchissement échouait (401) et le
  VSM n'apparaissait qu'après reconnexion.
- **Correction** : `POST /documents/{id}/process` répond **immédiatement**
  (`job_id`, statut 202) ; le pipeline s'exécute dans un **thread d'arrière-
  plan** ; `GET /documents/process/{job_id}` expose la **progression** puis le
  résultat (ou l'erreur). Chaque interrogation « touche » la session et la
  clé de chiffrement → **aucune expiration pendant le traitement** ; la clé
  est aussi rafraîchie avant les écritures chiffrées du job.
- Frontend : l'UI interroge l'état toutes les 2 s (avec étape affichée), puis
  rafraîchit la liste — **le VSM apparaît sans reconnexion** ; si l'onglet est
  fermé, le job se termine en arrière-plan et le VSM est présent au
  prochain accès.
- **Tests** : 103 (+1 : flux asynchrone processing → done, VSM visible sans
  nouvelle session).

### Correction « Voir le passage source » (incohérence d'identifiants)

- **Problème** : `run_pipeline()` générait son propre `document_id` interne à
  chaque exécution, différent de l'id du document uploadé → le VSM référençait
  un id fantôme (`source.document_id`) → `GET /documents/{id}/ocr` renvoyait
  404 dans le visualiseur. Bug latent depuis l'origine.
- **Correction** : `run_pipeline(document_id=…)` accepte l'id externe à
  préserver ; le job de traitement lui passe l'id d'upload. `source.document_id`
  == id uploadé == clé de stockage OCR → le surlignage fonctionne.
- **Tests** : 104 (+1 : cohérence des ids + passage retrouvable via
  `/documents/{id}/ocr`).

## [1.0.0] — 2026-06-12

### Phase 1 — Anonymisation
- `src/anonymization/` : détecteur PII hybride (13 types : noms, NIR, INS,
  RPPS, ADELI, FINESS, dates naissance/décès, tél, email, adresse, n° dossier,
  n° séjour), pseudonymiseur à tokens stables, anonymiseur strict, coffre-fort
  de mapping chiffré (AES-256-GCM / Argon2id, `forget()` = droit à l'oubli),
  audit sans PII. Adaptateur spaCy optionnel.

### Phase 2 — Pipeline OCR
- `pipeline.run_pipeline()` : PDF/image → JSON contractuel (sha256,
  anonymisation `pseudo`/`strict` intégrée, tolérance pages corrompues,
  rapport de traitement).
- Benchmark CER/WER (jiwer) avec/sans preprocessing → `outputs/benchmark.csv`
  + `BENCHMARK_REPORT.md`. Deskew rendu robuste (vérification du gain de
  profil, choix du sens empirique).
- Dataset synthétique reproductible (5 cas × 4 dégradations, seed 42).

### Phase 3 — Extraction NLP
- Extraction par rubriques (règles, offline) + adaptateur LLM local optionnel ;
  normalisation CIM-10/ATC (rapidfuzz) avec parsing dosage/fréquence ;
  sortie conforme `schema/vsm_schema.json` (v1.1.0).

### Phase 4 — Génération VSM
- Assemblage ordre canonique HAS, complétude par section, validation
  jsonschema, avertissement médical obligatoire ; rendus markdown / HTML / PDF
  (ReportLab) avec badges de confiance, fond distinct « À valider », zone de
  signature.

### Phase 5 — Stockage chiffré
- SQLite + AES-256-GCM champ par champ (AAD), `SessionKey` Argon2id avec
  timeout 15 min et zéroïsation, auth multi-rôles avec verrouillage,
  audit log chaîné par hash (`verify_audit_chain`), `delete_dossier()`.

### Phase 6 — UI
- Backend FastAPI 127.0.0.1:8741 (cookies httpOnly SameSite=Strict + CSRF,
  Pydantic partout) ; endpoints auth/documents/vsm/audit/oubli/export.
- Frontend React+TS+Vite+Tailwind (style shadcn, WCAG AA) : Login, Dashboard,
  DocumentViewer (surlignage source XAI), VSMEditor (9 rubriques, badges
  confiance, signature médecin), AuditTrail, Settings ; raccourcis Ctrl+K,
  Tab, ↵, Ctrl+↵.
- Wrapper Tauri 2 (backend en sous-process, bundles msi/AppImage/deb, sans
  télémétrie ni auto-update).

### Phase 7 — Docs & CI
- README, USER_MANUAL, ANONYMIZATION, COMPLIANCE, SECURITY, 3 ADR (MADR),
  gabarit VSM ; CI GitHub Actions (pytest, ruff, bandit, pip-audit, garde-fou
  anti-PII, build frontend, build Tauri sur tag) ; 44 tests verts ; E2E validé.
