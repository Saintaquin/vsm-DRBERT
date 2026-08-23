# ADR-0008 — Modèles de compréhension de documents : veille et décision

- Statut : accepté · Date : 2026-08-22 · Décision : **ne pas intégrer pour la
  soutenance** ; piste documentée (veille : outputs/AUDIT_LLM_DOCMODELS.md).

## Contexte

Veille technologique demandée sur 5 modèles de compréhension de documents :
LayoutLM, LayoutLMv2, LayoutXLM, LayoutLMv3 (Microsoft) et LFM2-VL (Liquid AI).

## Décision

**Aucun de ces modèles n'est intégré à la version de soutenance.**

| Modèle | Licence (vérifiée) | Verdict | Motif |
|---|---|---|---|
| LayoutLM / v2 / v3 | MIT | ❌ Écarté | Entraînés en anglais ; **fine-tuning FR médical indispensable** (aucun checkpoint existant ; pas de données labellisées dans le périmètre concours) |
| LayoutXLM | MIT | 🟡 **Piste P2** | Seul multilingue (XLM-R, FR inclus) — candidat pour un futur fine-tuning FR médical, documenté au dossier scientifique |
| LFM2-VL (Liquid AI) | ⚠️ **LFM Open License v1.0** (custom) | ❌ Écarté | **Licence non standard** → risque pour l'annexe 1 ; doublon avec Unlimited-OCR (MIT, GPU, déjà intégré) |

## Justification

- La compréhension de documents est déjà couverte : **Unlimited-OCR (MIT, GPU)**
  pour la lecture et **règles + LLM local (Apache 2.0)** pour l'extraction.
- Les modèles LayoutLM exigent (1) les **boîtes OCR** (actuellement retirées
  après anonymisation) et (2) un **fine-tuning** hors périmètre concours
  (données anonymisées synthétiques uniquement) → ROI faible pour la soutenance.
- **LFM2-VL** : capacité intéressante (vision-langage, FR) mais **licence
  custom non validée juridiquement** — l'annexe 1 exige un prototype sans
  contrainte de droits de tiers ; si la licence était clarifiée, il
  resterait un doublon d'Unlimited-OCR.

## Conséquences

- + Aucun risque licence (annexe 1) ; aucun ajout de dépendance.
- + Piste **LayoutXLM** documentée (benchmark de fine-tuning FR médical futur)
  → crédibilité du dossier scientifique sans engagement.
- − Les capacités « mise en page » des LayoutLM restent non exploitées
  (piste future si des données labellisées françaises émergent).
