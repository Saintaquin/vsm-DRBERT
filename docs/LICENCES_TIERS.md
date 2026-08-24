# Licences tiers — VSM-OCR

> **À coller dans le dossier scientifique (volet éthique / propriété
> intellectuelle).** Cette section inventorie les composants tiers utilisés par
> le prototype et démontre la conformité de l'Annexe 1 du règlement du concours
> (garantie « ne contrefait aucun droit de tiers », licence concédée aux
> Organisateurs couvrant le prototype, ses modèles d'IA, les scripts et la
> documentation).
>
> Principe directeur : **toutes les briques sont locales** (aucun appel réseau au
> runtime, art. 9) et **les licences sont soit permissives (Apache-2.0, MIT,
> BSD, CC-BY-4.0), soit déjà évaluées** pour la conformité aux usages du modèle.
> Aucun composant sous licence copyleft « forte » (GPL/AGPL) n'est **lié** au
> prototype — les seuls éléments GPL sont des **binaires externes** appelés en
> sous-process, sans contribution à la licence du prototype (cf. §4).

---

## 1. Modèles d'IA (le point sensible de l'annexe 1)

Le prototype embarque **trois moteurs d'extraction** hiérarchisés : règles →
**DrBERT** (NER médical léger) → **LLM local** (machines puissantes). Chaque
moteur est traçable (XAI, champ `moteur_nlp`) et le moteur règles (notre code)
reste le **repli automatique** — aucune régression si un modèle est absent.

### 1.1 DrBERT-MedicalNER-FR — NER médical français

| Composant | Licence | Source vérifiée |
|---|---|---|
| Base `Dr-BERT/DrBERT-7GB` | **Apache-2.0** | fiche Hugging Face [`Dr-BERT/DrBERT-7GB`](https://huggingface.co/Dr-BERT/DrBERT-7GB) |
| Checkpoint `spideystreet/DrBERT-MedicalNER-FR` | **Licence « style OpenRAIL » personnalisée** | fichier [`LICENSE`](https://huggingface.co/spideystreet/DrBERT-MedicalNER-FR/blob/main/LICENSE) du repo |
| Dataset d'entraînement `TypicaAI/MedicalNER_Fr` | **CC-BY-4.0** | carte Hugging Face [`TypicaAI/MedicalNER_Fr`](https://huggingface.co/datasets/TypicaAI/MedicalNER_Fr) |

**Analyse de la licence du checkpoint** (point décisif) :

Le fichier `LICENSE` du checkpoint est une licence **personnalisée « style
OpenRAIL »**, et **non** la licence officielle **OpenRAIL-M**
(CreativeML/BigScience). Cette distinction est déterminante :

- **§2 Usages permis** : recherche, **applications commerciales**, éducation,
  projets personnels → l'usage du prototype (concours puis projet opérationnel
  de la CPTS) est **expressément autorisé**.
- **§3 Restrictions** : activités illégales, contenu nocif, violation de lois,
  infraction de droits, violence/discrimination. **Aucune ne concerne l'usage
  NER médical** du prototype.
- **§4 Disclaimer médical** : *non approuvé pour usage clinique ; ne pas
  utiliser pour soins directs, diagnostic, décisions de traitement, aide à la
  décision clinique. Tout usage médical exige une validation par des
  professionnels qualifiés et la conformité aux règlementations sur les
  dispositifs médicaux.* → il s'agit d'un **avertissement**, pas d'une
  interdiction.
- **§5 Attribution** : attribuer aux créateurs, lier la licence, conserver les
  mentions.

**Pourquoi c'est compatible (et pourquoi l'OpenRAIL-M officielle ne le serait
pas)** : la licence OpenRAIL-M officielle contient, en *Attachment A*, une
restriction **prohibitive** : `(l) To provide medical advice and medical
results interpretation` (interdiction d'usage). **Cette clause `(l)` est absente
du `LICENSE` du checkpoint.** Si elle avait été reprise, production d'un VSM
(interprétation de résultats médicaux) aurait été interdite — elle ne l'est pas.
L'usage médical n'y est traité que par un **disclaimer**.

**Respect du §4** : VSM-OCR produit un **brouillon de synthèse** qui **doit**
être relu, corrigé et signé par un médecin (rôle `medecin` requis pour la
signature ; avertissement médical affiché dans tous les rendus ; seuil de
confiance < 0,7 → champs « À valider »). L'application **n'est pas un
dispositif médical certifié** et ne prend **aucune décision** de soin : elle
assiste le clinicien dans la mise en forme d'un document déjà établi. Cela est
**conforme** au §4 (« validation par des professionnels qualifiés »).

**Attribution** (conforme §5) : le prototype cite le checkpoint
`spideystreet/DrBERT-MedicalNER-FR`, son auteur (projet MediNotes) et l'article
de référence DrBERT (Labrak et al., « DrBERT: A Robust Pre-trained Model in
French for Biomedical and Clinical domains », ACL'23), qui accompagne la
documentation et le dossier.

### 1.2 LLM local (Qwen 2.5)

| Modèle | Licence | Source |
|---|---|---|
| Qwen 2.5 3B / 1.5B Instruct (défaut + repli < 4 Go) | **Apache-2.0** | catalogue `src/extraction_nlp/llm.py`, docs/ADR-0009 |

- Modèle téléchargé **à l'installation** (`python -m src.extraction_nlp.llm`),
  jamais pendant le traitement (art. 9). **100 % local** (llama.cpp).
- Licence **Apache-2.0** (permissive, aucune contrainte de droits de tiers).
  Voir `docs/ADR/0009-llm-par-defaut-universel.md`.

### 1.3 OCR « Unlimited » (baidu, optionnel — GPU NVIDIA)

| Composant | Licence |
|---|---|
| Unlimited-OCR (baidu) | **MIT** |

- Moteur **optionnel**, **gated par GPU** (`nvidia_gpu_available`) ; absent si
  aucune carte NVIDIA. Licence **MIT** (permissive). Voir `docs/ADR/0005-ocr-unlimited-gpu.md`.

---

## 2. Bibliothèques Python (runtime)

Toutes les dépendances Python du prototype (`requirements.txt`) sont des
licences **permissives OSI** — aucune n'impose de contrainte copyleft sur
le prototype.

| Dépendance | Licence | Rôle |
|---|---|---|
| FastAPI | MIT | API HTTP |
| uvicorn | BSD-3-Clause | serveur |
| pydantic | MIT | validation |
| python-multipart | Apache-2.0 | upload fichiers |
| cryptography | Apache-2.0 / BSD-3-Clause | AES-256-GCM |
| argon2-cffi | MIT | dérivation de clé Argon2id |
| rapidfuzz | MIT | normalisation CIM-10 / ATC |
| jsonschema | MIT | validation contrat `vsm_schema.json` |
| pytesseract | Apache-2.0 | OCR (via Tesseract) |
| pdf2image | MIT | PDF → images (via Poppler) |
| pillow | MIT-CMU (ex-HPND) | traitement image |
| numpy | BSD-3-Clause | calcul numérique |
| reportlab | BSD-3-Clause | rendu PDF du VSM |
| jiwer | Apache-2.0 | métriques CER/WER (benchmark) |

**Optionnelles / adaptateurs** (activées selon l'environnement) :

| Dépendance | Licence | Rôle |
|---|---|---|
| llama-cpp-python | MIT | inférence LLM locale |
| torch | BSD-3-Clause | DrBERT + OCR GPU |
| transformers | Apache-2.0 | chargement DrBERT |
| spacy | MIT | détection PII renforcée |
| python-doctr / paddleocr | Apache-2.0 | OCR alternatif (benchmark) |

> Note : `torch` et `transformers` n'existent que dans l'environnement Python
> 3.12 (celui qui exécute l'extraction). `transformers` est épinglé
> `>=4.53,<5` (la 5.x casse le tokenizer CamemBERT).

---

## 3. Binaires système (non liés, appelés en sous-process)

| Binaire | Licence | Remarque d'usage |
|---|---|---|
| Tesseract OCR (+ pack `-fra`) | **Apache-2.0** | appelé via `pytesseract` |
| Poppler (`pdftoppm`/`pdfinfo`) | **GPL-2.0** | appelé via `pdf2image` |

**Note de conformité** : le prototype **importe** ces outils en **sous-process
(pipelines externes)**, sans les **lier** dans le code du prototype. Cette
interopérabilité par exécution n'en fait pas des œuvres dérivées : elle
n'impose **pas** la licence GPL sur le prototype (prérequis d'installation
système, non distribué avec l'application). Aucune dépendance GPL n'est
embarquée dans le code, les scripts ou la documentation concédés aux
Organisateurs.

---

## 4. Frontend et outillage de build

| Composant | Licence |
|---|---|
| React, TypeScript, Vite, Tailwind | MIT |

- Bibliothèques de build **permissives (MIT)**, intégrées dans l'artefact
  `frontend/dist` servi localement. Aucune donnée n'est transmise à un CDN
  (build servie en local ; aucune télémétrie).

---

## 5. Données

| Donnée | Licence / statut |
|---|---|
| Dataset de démonstration `data/synthetic/` | **100 % fictif** (identités générées, seed 42) — aucune donnée patient réelle |
| Dataset d'entraînement DrBERT `MedicalNER_Fr` | **CC-BY-4.0** (attribution respectée) — utilisé uniquement en amont, pas distribué |

---

## 6. Synthèse de conformité (Annexe 1)

| Article Annexe 1 | Analyse | Verdict |
|---|---|---|
| **Art. 1** — licence non exclusive, gratuite, mondiale sur le prototype (application, modèles d'IA, scripts, docs) | L'application, les scripts et la documentation sont notre œuvre → concédés librement. Les **modèles tiers** (DrBERT, Qwen) sont fournis **sous leur licence amont** (usage commercial permis pour DrBERT ; Apache-2.0 pour Qwen), **non** re-licenciés comme notre œuvre. | ✅ Compatible (formulation : fourni sous licence amont) |
| **Art. 2** — intégration des briques au projet opérationnel | Couvert par l'usage **commercial** permis (§2 du `LICENSE` DrBERT) et Apache-2.0 (Qwen). Le disclaimer médical vaut note de responsabilité, pas interdiction. | ✅ |
| **Art. 4** — « ne contrefait aucun droit de tiers » | Les modèles sont utilisés **sous licences valides** → pas une contrefaçon. Condition respectée (attribution, usages permis, gestion du disclaimer). | ✅ |
| **Art. 4** — RGPD / données anonymisées uniquement | Indépendant de la licence ; satisfait (art. 9 du règlement : uniquement les documents anonymisés fournis, aucune réidentification). | ✅ |
| **Art. 7** — RGPD / sécurité / performance | Aucun composant n'envoie de données hors machine (art. 9) ; licences documentées. | ✅ |

### Conditions vérifiées pour le jury

1. **Attribution** : le checkpoint DrBERT, son auteur et l'article DrBERT sont
   cités dans la documentation et le dossier (§1.1).
2. **Disclaimer médical** : le VSM est un **brouillon à valider par un médecin**,
   pas un dispositif médical certifié — cohérent avec le §4 du `LICENSE` et
   l'avertissement affiché par l'application.
3. **Transmission sous licence amont** : les modèles tiers sont fournis **sous
   leur licence d'origine** (usage commercial + attribution) ; seule l'application
   (code) est concédée sous notre licence.

---

## 7. Références

- Règlement du concours — [Annexe 1 : contrat de licence](#) (fourni par les
  Organisateurs).
- `docs/ADR/0010-drbert-extraction.md` — décision d'architecture DrBERT.
- `docs/ADR/0009-llm-par-defaut-universel.md` — LLM local par défaut.
- `docs/ADR/0005-ocr-unlimited-gpu.md` — OCR optionnel.
- `outputs/AUDIT_DRBERT.md` — audit de faisabilité & analyse détaillée de la
  licence (§2a).
