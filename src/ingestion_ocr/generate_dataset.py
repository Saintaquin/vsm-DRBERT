"""Génère le dataset de test : 5 cas cliniques 100% synthétiques (aucun
patient réel — identités fictives générées), rendus en A4 PNG 200 DPI,
chacun dégradé en 4 variantes (clean, skewed, blurred, noisy) = 20 images,
plus la vérité terrain texte. Reproductible (seed 42).

Usage : python -m src.ingestion_ocr.generate_dataset"""

from __future__ import annotations

import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

SEED = 42
OUT_DIR = Path(__file__).resolve().parents[2] / "data" / "synthetic"

CASES = [
    {
        "id": "cas_001",
        "lines": [
            "CENTRE HOSPITALIER SAINT-FICTIF — SERVICE DE MEDECINE INTERNE",
            "Compte rendu de consultation — 12/03/2024",
            "Patient : Jean DUPONT    Ne le : 14/05/1956",
            "N. Securite Sociale : 1 56 05 75 123 456 78",
            "Medecin : Dr Marie LAURENT — RPPS : 10001234567",
            "",
            "ANTECEDENTS : Diabete de type 2 depuis 2010. Hypertension arterielle.",
            "Infarctus du myocarde en 2018 avec pose de stent.",
            "ALLERGIES : Penicilline (eruption cutanee).",
            "TRAITEMENTS EN COURS : Metformine 1000 mg matin et soir.",
            "Ramipril 5 mg le matin. Atorvastatine 20 mg le soir.",
            "Aspirine 75 mg par jour.",
            "VACCINATIONS : Grippe 10/2023. COVID-19 rappel 09/2023. DTP a jour.",
            "CONCLUSION : Equilibre glycemique satisfaisant. Poursuite du traitement.",
        ],
    },
    {
        "id": "cas_002",
        "lines": [
            "CABINET DU DR PIERRE MOREAU — MEDECINE GENERALE",
            "Ordonnance — 05/01/2024",
            "Patiente : Sophie MARTIN    Nee le : 22/11/1972",
            "Adresse : 14 rue des Lilas, 75011 Paris",
            "Tel : 06 12 34 56 78",
            "",
            "ANTECEDENTS : Asthme depuis l'enfance. Hypothyroidie (2015).",
            "Appendicectomie en 1990.",
            "ALLERGIES : Aucune allergie connue.",
            "TRAITEMENTS : Levothyrox 75 µg le matin a jeun.",
            "Ventoline en cas de crise. Seretide 250 matin et soir.",
            "VACCINATIONS : Grippe annuelle. DTP rappel 2021.",
            "FACTEURS DE RISQUE : Tabagisme sevre depuis 2019.",
        ],
    },
    {
        "id": "cas_003",
        "lines": [
            "HOPITAL UNIVERSITAIRE IMAGINAIRE — CARDIOLOGIE",
            "Lettre de sortie — 28/02/2024",
            "Patient : Michel BERNARD    Ne le : 03/07/1948",
            "N. dossier : CARD-2024-0892",
            "Medecin referent : Pr Claire DUBOIS — RPPS : 10009876543",
            "",
            "MOTIF : Decompensation cardiaque sur fibrillation auriculaire.",
            "ANTECEDENTS : Insuffisance cardiaque. Fibrillation auriculaire (2020).",
            "Hypercholesterolemie. Arthrose des genoux.",
            "ALLERGIES : Iode (reaction cutanee lors d'un scanner en 2019).",
            "TRAITEMENT DE SORTIE : Bisoprolol 5 mg le matin.",
            "Apixaban 5 mg matin et soir. Furosemide 40 mg le matin.",
            "Rosuvastatine 10 mg le soir.",
            "POINTS DE VIGILANCE : Surveillance du poids. Controle INR inutile",
            "sous apixaban. Revoir en consultation dans 1 mois.",
        ],
    },
    {
        "id": "cas_004",
        "lines": [
            "MAISON DE SANTE DES OLIVIERS — SUIVI PNEUMOLOGIE",
            "Compte rendu — 17/04/2024",
            "Patiente : Fatima BENALI    Nee le : 09/09/1965",
            "Email : fatima.exemple@mail.fr",
            "",
            "ANTECEDENTS : BPCO stade II (2017). Reflux gastro-oesophagien.",
            "Depression traitee depuis 2021.",
            "ALLERGIES : Aspirine (asthme induit).",
            "TRAITEMENTS LONG COURS : Seretide 500 matin et soir.",
            "Omeprazole 20 mg le matin. Sertraline 50 mg le matin.",
            "VACCINATIONS : Pneumocoque 2022. Grippe 11/2023.",
            "FACTEURS DE RISQUE : Ancien tabagisme 30 paquets-annees.",
            "POINTS DE VIGILANCE : Eviter AINS et aspirine (allergie).",
        ],
    },
    {
        "id": "cas_005",
        "lines": [
            "CLINIQUE DES GLYCINES — GERIATRIE",
            "Volet de synthese — 02/05/2024",
            "Patient : Andre PETIT    Ne le : 30/01/1939",
            "Medecin traitant : Dr Karim HADDAD — RPPS : 10005551234",
            "",
            "PATHOLOGIES ACTIVES : Maladie de Parkinson (2016). Osteoporose.",
            "Glaucome chronique bilateral.",
            "ANTECEDENTS : Prothese totale de hanche droite (2012).",
            "AVC ischemique mineur en 2019 sans sequelle.",
            "ALLERGIES : Codeine (nausees severes).",
            "TRAITEMENTS : Levodopa 100 mg trois fois par jour.",
            "Acide alendronique 70 mg par semaine. Calcium vitamine D quotidien.",
            "VACCINATIONS : Grippe 10/2023. Zona 2022.",
            "POINTS DE VIGILANCE : Risque de chute eleve. Eviter neuroleptiques.",
        ],
    },
    # Cas aux formats RÉELS de laboratoire / anatomopathologie (régression
    # audit 2026-08-20) : « Monsieur NOM Prénom » (nom en MAJUSCULES avant le
    # prénom), « DDN : le … », dates à 2 chiffres — formats qui fuyaient la
    # détection PII avant la correction de pii_detector.py.
    {
        "id": "cas_006",
        "lines": [
            "LABORATOIRE D'ANALYSES MEDICALES DU PARC",
            "Compte rendu de resultats — 15/03/2024",
            "Monsieur DURAND Pascal    DDN : le 15/03/1968",
            "N. Securite Sociale : 1 68 03 75 246 135 79",
            "Demande n° LAB-2024-0117",
            "",
            "HEMATOLOGIE : Hemoglobine 14,2 g/100mL. Leucocytes 6,8 Giga/L.",
            "Plaquettes 250 Giga/L. V.S. 12 mm.",
            "BIOCHIMIE : Glycemie a jeun 1,28 g/L. Creatinine 82 umol/L.",
            "ANTECEDENTS : Hypertension arterielle. Diabete de type 2 (2015).",
            "TRAITEMENTS EN COURS : Metformine 1000 mg matin et soir.",
            "Ramipril 5 mg le matin.",
            "CONCLUSION : Bilan stable. Poursuite du traitement.",
        ],
    },
    {
        "id": "cas_007",
        "lines": [
            "CABINET D'ANATOMIE ET DE CYTOLOGIE PATHOLOGIQUES",
            "Examen n° ACP-2014-0923 concernant",
            "Mme LEFEBVRE Claire",
            "Prescrit le 12/11/2014",
            "FROTTIS CERVICO-VAGINAUX (depistage conventionnel, 3 lames).",
            "Population malpighienne de densite cellulaire moderee, fond propre.",
            "Flore microbienne peu abondante. Absence de cellule endocervicale.",
            "CONCLUSION : Frottis satisfaisant. Absence de cellule suspecte.",
        ],
    },
]


def _find_font(size: int) -> ImageFont.FreeTypeFont:
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "C:/Windows/Fonts/arial.ttf",  # poste Windows (développement local)
        "C:/Windows/Fonts/segoeui.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_case(lines: list[str], dpi: int = 200) -> Image.Image:
    w, h = int(8.27 * dpi), int(11.69 * dpi)  # A4
    img = Image.new("L", (w, h), 255)
    draw = ImageDraw.Draw(img)
    font = _find_font(int(dpi * 0.16))
    y = int(dpi * 0.6)
    for line in lines:
        draw.text((int(dpi * 0.6), y), line, fill=0, font=font)
        y += int(dpi * 0.26)
    return img


def degrade(img: Image.Image, kind: str, rng: random.Random) -> Image.Image:
    if kind == "clean":
        return img
    if kind == "skewed":
        return img.rotate(
            rng.uniform(1.5, 3.5) * rng.choice([-1, 1]), expand=True, fillcolor=255
        )
    if kind == "blurred":
        return img.filter(ImageFilter.GaussianBlur(radius=1.2))
    if kind == "noisy":
        arr = np.asarray(img, dtype=np.int16)
        noise = np.random.default_rng(SEED).normal(0, 22, arr.shape)
        return Image.fromarray(np.clip(arr + noise, 0, 255).astype(np.uint8))
    raise ValueError(kind)


def main(out_dir: Path = OUT_DIR) -> list[Path]:
    rng = random.Random(SEED)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for case in CASES:
        base = render_case(case["lines"])
        gt = out_dir / f"{case['id']}_ground_truth.txt"
        gt.write_text("\n".join(case["lines"]), encoding="utf-8")
        written.append(gt)
        for kind in ("clean", "skewed", "blurred", "noisy"):
            p = out_dir / f"{case['id']}_{kind}.png"
            degrade(base, kind, rng).save(p)
            written.append(p)
    return written


if __name__ == "__main__":
    files = main()
    print(f"{len(files)} fichiers générés dans {OUT_DIR}")
