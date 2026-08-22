# Gabarit HAS du Volet de Synthèse Médicale

Ordre canonique des rubriques (contrat `schema/vsm_schema.json` v1.1.0) :

1. **Identification patient** (pseudonymisée par défaut)
2. **Médecin traitant**
3. **Pathologies actives**
4. **Antécédents médicaux et chirurgicaux**
5. **Allergies et intolérances**
6. **Traitements au long cours** (avec code ATC + dosage parsé)
7. **Facteurs de risque**
8. **Vaccinations**
9. **Points de vigilance**

Chaque élément est un `champ_trace` : valeur, confiance [0–1], source
(document/page/passage), moteurs (OCR/NLP), code normalisé (CIM-10/ATC),
indicateur `a_valider` (confiance < 0,7).

Tout rendu (markdown/HTML/PDF) inclut obligatoirement : la date de génération,
le statut, les indicateurs de confiance, une zone de signature médecin, et
l'avertissement « Document généré automatiquement […] à valider par un médecin ».
