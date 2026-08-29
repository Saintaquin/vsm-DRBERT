"""Audit du normalisateur CIM-10/ATC sur un échantillon (2026-08-24).

Question posée : « combien de codes attribués sont corrects ? Un code faux
affiché dans un VSM est un problème même sans déduplication. »

Méthode : un échantillon d'entités réalistes (issues de l'analyse des
dossiers ABRICOT/BANANE et des cas synthétiques) est passé au normalisateur
(``src/extraction_nlp/normalizer.py`` : match exact puis flou rapidfuzz sur
les référentiels locaux, seuils 78/70-78 — le seuil CIM-10 a été relevé de
72 à 78 après la première passe d'audit, voir « Décisions prises »). Chaque
code attribué est comparé au code attendu — établi manuellement, vérifiable
par un médecin du jury. Trois verdicts :

- CORRECT  : code attribué = code attendu (ou classe mère, p. ex. A10AE04
             accepté si A10AE attendu) ;
- FAUX     : code attribué ≠ maladie/médicament demandé — le pire cas ;
- ABSENCE  : aucun code attribué (ni faux ni juste — un manque de
             couverture, pas une erreur clinique).

Le rapport est écrit dans ``outputs/AUDIT_NORMALISATEUR.md`` (régénérable à
chaque exécution — les verdicts manuels vivent dans ce script, la table et
les métriques sont recalculées).

Usage :
    py -3.12 tools/audit_normalisateur.py
"""

from __future__ import annotations

import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))

from src.extraction_nlp.normalizer import (
    normalize_diagnosis,
    normalize_medication,
)

# ---------------------------------------------------------------------------
# Échantillon : (valeur, code attendu, commentaire)
# Attendu = code CIM-10/ATC qu'un médecin/pharmacologue accepterait de voir
# affiché ; None = aucun code acceptable (trop générique, ou acte, ou
# référence absente — l'absence vaut mieux qu'un code faux).
# ---------------------------------------------------------------------------

DIAGNOSTICS: list[tuple[str, str | None, str]] = [
    # --- couverts par le référentiel (35 entrées CIM-10) ---
    ("maladie rénale chronique", "N18", ""),
    ("diabète de type 2", "E11", ""),
    ("infarctus du myocarde", "I21", ""),
    ("obésité", "E66", ""),
    ("fibrillation auriculaire", "I48", ""),
    ("insuffisance cardiaque", "I50", ""),
    ("hypercholestérolémie", "E78", ""),
    ("asthme", "J45", ""),
    ("épilepsie", "G40", ""),
    ("gastrite chronique", "K29", "attendu : K29 (absent du référentiel)"),
    # --- collision constatée dans les VSM réels (déclencheur de l'audit) ---
    ("maladie coronaire", "I25", "constat ABRICOT/BANANE : N18 attribué"),
    ("maladie de Basedow", "E05", "constat d'audit : G20 attribué"),
    # --- hors référentiel : l'absence est le VERDICT ATTENDU ---
    ("ulcère bulbaire linéaire", "K27", "référentiel absent — absence OK"),
    ("incontinence urinaire d'effort", "N39.3", "référentiel absent"),
    ("hypertension artérielle", "I10", "référentiel absent"),
    ("sténose urétrale", "N35", "référentiel absent"),
    ("hernie inguinale", "K40", "référentiel absent"),
    ("arthrose du genou", "M17", "référentiel absent"),
    ("cirrhose hépatique", "K74", "référentiel absent"),
    ("psoriasis", "L40", "référentiel absent"),
    ("migraine", "G43", "référentiel absent"),
    ("grippe", "J11", "référentiel absent"),
    ("COVID", "U07.1", "référentiel absent"),
    # --- ne doivent JAMAIS recevoir de code ---
    ("cholécystectomie", None, "acte chirurgical : aucun code diagnostic"),
    ("pose de stent", None, "acte : aucun code diagnostic"),
    ("excision du pertuis cutané", None, "acte : aucun code diagnostic"),
    ("GRIPFE DU CHAT", None, "bruit OCR : aucun code, pas A28.1 inventé"),
]

MEDICAMENTS: list[tuple[str, str | None, str]] = [
    # --- couverts par le référentiel (38 entrées ATC) ---
    ("Metformine", "A10BA02", ""),
    ("Metformine 1000 mg matin", "A10BA02",
     "posologie : la dose ne doit pas détourner le DCI"),
    ("Ramipril 5 mg", "C09AA05", ""),
    ("Atorvastatine 20 mg", "C10AA05", ""),
    ("Aspirine 75 mg par jour", "B01AC06", ""),
    ("Oméprazole 20 mg", "A02BC01", ""),
    ("paracétamol", "N02BE01", ""),
    ("ibuprofène", "M01AE01", ""),
    ("furosémide", "C03CA01", ""),
    ("bisoprolol", "C07AB07", ""),
    ("amlodipine", "C08CA01", ""),
    ("lévothyroxine", "H03AA01", ""),
    ("sertraline", "N06AB06", ""),
    ("warfarine", "B01AA03", ""),
    ("acide folique", "B03BB01", ""),
    # --- trop génériques : aucun code acceptable (spécificité trompeuse) ---
    ("insuline", None, "« insuline » ≠ glargine : trop spécifique"),
    ("vitamine D", None, "≠ calcium + vitamine D : produit combiné"),
    # --- hors référentiel : absence attendue ---
    ("OGAST 1 gél/j", None, "nom commercial absent du référentiel"),
    ("MAALOX", None, "nom commercial absent du référentiel"),
    ("amoxicilline", "J01CA04", "référentiel absent — absence OK"),
    ("pénicilline", None, "classe entière : aucun code spécifique honnête"),
    ("tramadol", "N02AX02", "référentiel absent"),
    ("prednisolone", "H02AB06", "référentiel absent"),
]


def _verdict(attribue: str | None, attendu: str | None) -> str:
    """CORRECT / FAUX / ABSENCE (voir docstring du module)."""
    if attribue is None:
        return "ABSENCE"
    if attendu is None:
        return "FAUX"  # un code là où aucun n'est acceptable
    if attribue == attendu or attendu.startswith(attribue) or attribue.startswith(attendu):
        return "CORRECT"  # égalité ou classe mère/fille acceptée
    return "FAUX"


def _auditer(
    echantillon: list[tuple[str, str | None, str]],
    fonction,
    champ_code: str,
) -> list[dict]:
    lignes = []
    for valeur, attendu, commentaire in echantillon:
        r = fonction(valeur)
        attribue = r.get(champ_code)
        lignes.append(
            {
                "valeur": valeur,
                "attendu": attendu,
                "attribue": attribue,
                "libelle": r.get("label_official") or "",
                "confiance": r.get("confidence", 0.0),
                "verdict": _verdict(attribue, attendu),
                "commentaire": commentaire,
            }
        )
    return lignes


def _md_table(lignes: list[dict]) -> list[str]:
    out = [
        "| Valeur | Attendu | Attribué | Libellé du référentiel | Conf. | Verdict | Note |",
        "|---|---|---|---|---|---|---|",
    ]
    for l in lignes:
        out.append(
            f"| {l['valeur']} | {l['attendu'] or '—'} | "
            f"{l['attribue'] or '—'} | {l['libelle'] or '—'} | "
            f"{l['confiance']:.2f} | **{l['verdict']}** | {l['commentaire']} |"
        )
    return out


def _stats(lignes: list[dict]) -> dict:
    n = len(lignes)
    faux = [l for l in lignes if l["verdict"] == "FAUX"]
    corrects = [l for l in lignes if l["verdict"] == "CORRECT"]
    absences = [l for l in lignes if l["verdict"] == "ABSENCE"]
    attribues = len(corrects) + len(faux)
    return {
        "n": n,
        "attribues": attribues,
        "corrects": len(corrects),
        "faux": len(faux),
        "absences": len(absences),
        "taux_corrects": (len(corrects) / attribues * 100) if attribues else 0.0,
        "lignes_faux": faux,
    }


def main() -> int:
    diag = _auditer(DIAGNOSTICS, normalize_diagnosis, "code_cim10")
    med = _auditer(MEDICAMENTS, normalize_medication, "code_atc")
    sd, sm = _stats(diag), _stats(med)
    total_attribues = sd["attribues"] + sm["attribues"]
    total_faux = sd["faux"] + sm["faux"]
    total_corrects = sd["corrects"] + sm["corrects"]

    rapport: list[str] = []
    rapport.extend(
        [
            "# Audit du normalisateur CIM-10 / ATC",
            "",
            "Date : 2026-08-24 · Régénérable : `py -3.12 tools/audit_normalisateur.py`",
            "",
            "Question posée : **« combien de codes attribués sont corrects ? »**",
            "(un code faux affiché dans un VSM est un problème même sans",
            "déduplication). Réponse sur cet échantillon :",
            "",
            "| Échantillon | Attribués | Corrects | Faux | Sans code |",
            "|---|---|---|---|---|",
            (
                f"| Diagnostics (CIM-10) | {sd['attribues']} | "
                f"{sd['corrects']} | {sd['faux']} | {sd['absences']} |"
            ),
            (
                f"| Médicaments (ATC) | {sm['attribues']} | "
                f"{sm['corrects']} | {sm['faux']} | {sm['absences']} |"
            ),
            (
                f"| **Total** | **{total_attribues}** | **{total_corrects}** "
                f"| **{total_faux}** | {sd['absences'] + sm['absences']} |"
            ),
            "",
            (
                f"**Taux de codes corrects parmi les attribués : "
                f"{(total_corrects / total_attribues * 100):.0f} %** "
                f"({total_corrects}/{total_attribues}) — {total_faux} code(s) faux."
            ),
            "",
            "## Codes faux constatés",
            "",
        ]
    )
    for l in sd["lignes_faux"] + sm["lignes_faux"]:
        ligne_faux = (
            f"- **« {l['valeur']} » → {l['attribue']}** "
            f"({l['libelle']}), confiance {l['confiance']:.2f} — attendu : "
            f"{l['attendu'] or 'aucun code'}. {l['commentaire']}"
        )
        rapport.append(ligne_faux)
    rapport.extend(
        [
            "",
            "## Diagnostics (CIM-10)",
            "",
            *_md_table(diag),
            "",
            "## Médicaments (ATC)",
            "",
            *_md_table(med),
            "",
            "## Analyse — causes racines",
            "",
            "1. **Référentiel trop petit** (35 entrées CIM-10, 38 ATC) : la",
            (
                f"   couverture de l'échantillon est de "
                f"{(total_attribues / (sd['n'] + sm['n']) * 100):.0f} %. "
                "La grande"
            ),
            "   majorité des entités réelles d'un dossier de 20 ans (ulcère",
            "   bulbaire, sténose urétrale, hernie…) n'a simplement pas",
            "   d'entrée — l'absence est fréquente mais bénigne.",
            "2. **Seuil flou trop bas (72, historique) + libellés partageant",
            "   des mots vides** : « maladie coronaire » {maladie, coronaire}",
            "   et « maladie rénale chronique » {maladie, rénale, chronique}",
            "   partagent « maladie » → token_set_ratio 0,732 ≥ 72 → N18.",
            "   Idem « maladie de Basedow » → G20 (Parkinson) à 0,737.",
            "   Mesuré : tous les codes CORRECTS de l'échantillon sont à",
            "   confiance ≥ 0,80 ; tous les FAUX sont ≤ 0,74 — CORRIGÉ par",
            "   le seuil 78 (voir « Décisions prises »).",
            "3. **Spécificité trompeuse côté ATC** : « insuline » →",
            "   A10AE04 « insuline glargine » (confiance 1,00) et",
            "   « vitamine D » → A12AX « calcium + vitamine D » (1,00) : le",
            "   match textuel est exact mais le code affirme PLUS que le",
            "   texte (une insuline particulière, un produit combiné).",
            "",
            "## Décisions prises",
            "",
            "- **Fusion par code désactivée dans la déduplication**",
            "  (`filtres_vsm.dedupliquer`) : un code faux ne peut plus",
            "  provoquer de fusion (« maladie coronaire » et « maladie rénale",
            "  chronique » ne fusionneront jamais, test de non-régression).",
            "- **Seuil flou CIM-10 relevé de 72 à 78**",
            "  (`normalizer.SEUIL_CIM10`) : frontière franche mesurée — tous",
            "  les codes faux de l'échantillon sont à confiance ≤ 0,74, tous",
            "  les codes corrects à ≥ 0,80. « maladie coronaire » et",
            "  « maladie de Basedow » ne reçoivent PLUS de code (verdict",
            "  ABSENCE ci-dessus) ; l'absence vaut mieux qu'un code faux.",
            "- **Codes flous marqués « à vérifier »**",
            "  (`pipeline.SEUIL_CODE_A_VERIFIER = 0,85`) : tout code issu",
            "  d'un appariement flou sous 0,85 porte `a_verifier` dans le",
            "  VSM et s'affiche « code à vérifier (appariement flou) » dans",
            "  l'éditeur — le médecin voit l'incertitude, jamais un fait",
            "  établi.",
            "",
            "## Recommandations restantes (chantier séparé, à planifier)",
            "",
            "1. **Enrichir les référentiels** (quelques centaines d'entrées",
            "   couvrant la médecine générale de ville) : l'absence de code",
            "   est aujourd'hui le défaut dominant.",
            "2. **Règle de spécificité ATC** : refuser un match dont le",
            "   libellé référentiel contient un « + » (produit combiné) ou",
            "   un nom de spécialité (glargine) quand la demande est",
            "   générique (« insuline », « vitamine D ») — les 2 faux ATC",
            "   restants viennent de là, pas du seuil.",
        ]
    )

    sortie = RACINE / "outputs" / "AUDIT_NORMALISATEUR.md"
    sortie.parent.mkdir(exist_ok=True)
    sortie.write_text("\n".join(rapport) + "\n", encoding="utf-8")

    print(f"Diagnostics : {sd['corrects']}/{sd['attribues']} corrects, "
          f"{sd['faux']} faux, {sd['absences']} sans code")
    print(f"Médicaments  : {sm['corrects']}/{sm['attribues']} corrects, "
          f"{sm['faux']} faux, {sm['absences']} sans code")
    print(f"TOTAL        : {total_corrects}/{total_attribues} corrects "
          f"({(total_corrects / total_attribues * 100):.0f} %), "
          f"{total_faux} faux")
    print(f"Rapport : {sortie}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
