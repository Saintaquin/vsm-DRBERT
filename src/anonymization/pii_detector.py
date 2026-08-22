"""Détection de PII (informations personnelles identifiantes) médicales françaises.

Approche hybride : expressions régulières (identifiants à format fixe),
dictionnaires (prénoms INSEE), heuristiques contextuelles ("Patient :",
"Né(e) le :", etc.). Un adaptateur spaCy optionnel (fr_core_news_lg) peut
enrichir la détection de noms propres s'il est installé — il n'est PAS
requis pour le fonctionnement nominal (offline-first, poste léger).

Chaque détection retourne un ``PIIMatch`` : type, span, valeur, confiance,
méthode de détection. Aucune PII n'est journalisée en clair (cf. audit.py).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

REFERENTIALS_DIR = Path(__file__).resolve().parents[2] / "data" / "referentials"

# ---------------------------------------------------------------------------
# Types de PII reconnus
# ---------------------------------------------------------------------------
PII_TYPES = (
    "NOM_PERSONNE",
    "NIR",  # n° sécurité sociale (15 chiffres dont clé)
    "INS",  # identifiant national de santé (basé NIR)
    "RPPS",  # 11 chiffres — professionnel de santé
    "ADELI",  # 9 chiffres
    "FINESS",  # 9 chiffres — établissement
    "DATE_NAISSANCE",
    "DATE_DECES",
    "TELEPHONE",
    "EMAIL",
    "ADRESSE",
    "NUMERO_DOSSIER",
    "NUMERO_SEJOUR",
)


@dataclass
class PIIMatch:
    pii_type: str
    start: int
    end: int
    value: str
    confidence: float
    method: str  # "regex" | "dictionnaire" | "heuristique" | "spacy"
    context: str = ""

    def overlaps(self, other: "PIIMatch") -> bool:
        return self.start < other.end and other.start < self.end


# ---------------------------------------------------------------------------
# Regex à format fixe
# ---------------------------------------------------------------------------
_RX = {
    # NIR : sexe(1) année(2) mois(2) dept(2) commune(3) ordre(3) [clé(2)]
    "NIR": re.compile(
        r"\b[12]\s?\d{2}\s?(?:0[1-9]|1[0-2])\s?(?:\d{2}|2[AB])\s?\d{3}\s?\d{3}(?:\s?\d{2})?\b"
    ),
    "RPPS": re.compile(r"(?:RPPS\s*:?\s*)(\d{11})\b", re.IGNORECASE),
    "RPPS_BARE": re.compile(r"\b10\d{9}\b"),  # les RPPS commencent par 10
    "ADELI": re.compile(r"(?:ADELI\s*:?\s*)(\d{9})\b", re.IGNORECASE),
    "FINESS": re.compile(r"(?:FINESS\s*:?\s*)(\d{9})\b", re.IGNORECASE),
    "TELEPHONE": re.compile(r"\b0[1-9](?:[\s.\-]?\d{2}){4}\b"),
    "EMAIL": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"),
    "DATE": re.compile(
        # années sur 2 ou 4 chiffres : 04/12/14 (format laboratoire) et 24/07/1963
        r"\b(?:0?[1-9]|[12]\d|3[01])[/\-.](?:0?[1-9]|1[0-2])[/\-.](?:19|20)?\d{2}\b"
    ),
    "ADRESSE": re.compile(
        r"\b\d{1,4}\s?(?:bis|ter)?,?\s+(?:rue|avenue|av\.?|boulevard|bd|impasse|all[ée]e|chemin|place|quai)\s+[A-Za-zÀ-ÿ'’\- ]{3,60}",
        re.IGNORECASE,
    ),
    "CODE_POSTAL_VILLE": re.compile(r"\b\d{5}\s+[A-ZÀ-Ý][A-Za-zÀ-ÿ\-']{2,30}\b"),
    "NUMERO_DOSSIER": re.compile(
        # « Demande n° X » (format labo) ou « N° [de ]dossier/demande : X »
        r"(?:(?:demande|dossier)\s+n[°o.]?|"
        r"n[°o.]?\s*(?:de\s*)?(?:dossier|demande))\s*:?\s*([A-Z0-9\-]{4,15})",
        re.IGNORECASE,
    ),
    "NUMERO_SEJOUR": re.compile(
        r"(?:n[°o.]?\s*(?:de\s*)?s[ée]jour\s*:?\s*)([A-Z0-9\-]{4,15})", re.IGNORECASE
    ),
    "INS": re.compile(r"(?:INS(?:-NIR)?\s*:?\s*)(\d[\d\s]{12,20}\d)", re.IGNORECASE),
}

# Heuristiques contextuelles : libellé → type, le nom suit le libellé.
# re.IGNORECASE est OBLIGATOIRE : les titres sont capitalisés dans les vrais
# documents (« Monsieur », « Patient », « Mme ») ; sans lui, le heuristique
# ne se déclenche que sur titres en minuscules et les noms fuient.
# L'écart titre→nom reste sur la même ligne ([ \t]) pour éviter d'attraper
# du contenu des lignes suivantes (faux positifs).
_CTX_NAME = re.compile(
    # (?i:…) : insensibilité à la casse limitée au titre (capitalisé dans les
    # vrais documents) ; (?-i:…) : le groupe nom reste sensible à la casse
    # (mots en minuscules type « est », « suivi » non capturés).
    r"\b(?P<title>(?i:patient(?:e)?|nom|pr[ée]nom|madame|monsieur|mme|mlle|m\.|mr|m|"
    r"b[ée]n[ée]ficiaire|dr|docteur|pr|professeur))"
    r"[ \t]*:?[ \t]+"
    r"(?P<name>(?-i:"
    # NOM en MAJUSCULES suivi du/des prénom(s) — format standard des
    # comptes-rendus de laboratoire (« Monsieur ABRICOT Anthony »)
    r"[A-ZÀ-Ý]{2,}[A-ZÀ-Ý\-]*(?:[ \t]+[A-ZÀ-Ý][a-zà-ÿ'\-]+){1,3}"
    r"|"
    # Prénom (TitleCase) suivi du nom (« Dr Marie LAURENT »)
    r"[A-ZÀ-Ý][a-zà-ÿ'\-]+(?:[ \t]+[A-ZÀ-Ý][A-Za-zà-ÿ'\-]+){0,3}"
    r"))",
    re.UNICODE,
)
_CTX_BIRTH = re.compile(
    r"(?:\bn[ée]\(?e?\)?\s+le\b|\bddn\b|\bdon\b|\bnaissance\b|\bnetssance\b)\s*:?\s*",
    re.IGNORECASE,
)
_CTX_DEATH = re.compile(r"\bd[ée]c[ée]d[ée]\(?e?\)?\s+le\b", re.IGNORECASE)

# Titres d'identité « forts » : le texte qui suit est très probablement un nom.
# Pour les autres titres (dr, docteur, pr, professeur), on n'accepte que les
# noms confirmés par le dictionnaire de prénoms ou au format NOM-en-MAJUSCULES,
# ce qui élimine les faux positifs sur du texte de laboratoire (« dr Sodium … »).
_STRONG_IDENTITY_TITLES = {
    "patient",
    "patiente",
    "nom",
    "madame",
    "monsieur",
    "mme",
    "mlle",
    "m.",
    "mr",
    "m",
    "bénéficiaire",
    "beneficiaire",
}

# Repli pour les lignes à contexte d'identité (formats OCR dégradés) :
# « Nom et Rang du bénéficiaire / … ABRICOT Antrony », « Bénéficiaire : … ».
# On n'active le patron NOM-MAJUSCULES + prénom que si la ligne contient un
# mot-clé d'identité, ce qui borne fortement les faux positifs.
_IDENTITY_LINE_RX = re.compile(
    r"\b(?:nom|pr[ée]nom|b[ée]n[ée]ficiaire|b[ée]nef|concernant|patient(?:e)?|examen|demande)\b",
    re.IGNORECASE,
)
_CAPS_NAME_RX = re.compile(
    r"(?<![A-ZÀ-Ý\-])([A-ZÀ-Ý]{2,}[A-ZÀ-Ý\-]*)[ \t]+"
    r"([A-ZÀ-Ý][a-zà-ÿ'\-]+(?:\-[A-ZÀ-Ý][a-zà-ÿ'\-]+)?)"
)


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def _load_firstnames() -> set[str]:
    path = REFERENTIALS_DIR / "prenoms_fr.txt"
    if not path.exists():
        return set()
    return {
        _strip_accents(line.strip().lower())
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


_FIRSTNAMES = _load_firstnames()

# Mots à ne jamais traiter comme noms de personne (vocabulaire médical fréquent)
_MEDICAL_STOPWORDS = {
    "diabete",
    "hypertension",
    "asthme",
    "allergie",
    "traitement",
    "ordonnance",
    "consultation",
    "consultations",
    "hopital",
    "service",
    "cardiologie",
    "urgences",
    "examen",
    "resultat",
    "resultats",
    "bilan",
    "scanner",
    "radiographie",
    "vaccination",
    "antecedents",
    # vocabulaire courant de laboratoire / comptes-rendus
    "sodium",
    "potassium",
    "calcium",
    "glycemie",
    "hemoglobine",
    "hematies",
    "leucocytes",
    "plaquettes",
    "lymphocytes",
    "monocytes",
    "creatinine",
    "uree",
    "cholesterol",
    "triglycerides",
    "enregistre",
    "copie",
    "compte",
    "rendu",
    "beneficiaire",
    "rang",
    "prescrit",
    "realise",
    "realisee",
}


class PIIDetector:
    """Détecteur hybride de PII. Sans dépendance lourde par défaut."""

    def __init__(self, use_spacy: bool = False):
        self._nlp = None
        if use_spacy:
            try:  # pragma: no cover - optionnel
                import spacy

                self._nlp = spacy.load("fr_core_news_lg")
            except Exception:
                self._nlp = None  # dégradation silencieuse, documentée

    # ------------------------------------------------------------------
    def detect(self, text: str) -> list[PIIMatch]:
        matches: list[PIIMatch] = []
        matches += self._detect_regex(text)
        matches += self._detect_names(text)
        if self._nlp is not None:  # pragma: no cover
            matches += self._detect_spacy(text)
        return self._dedupe(matches)

    # ------------------------------------------------------------------
    def _detect_regex(self, text: str) -> list[PIIMatch]:
        out: list[PIIMatch] = []

        def add(t, m, conf, group=0):
            out.append(
                PIIMatch(t, m.start(group), m.end(group), m.group(group), conf, "regex")
            )

        for m in _RX["INS"].finditer(text):
            add("INS", m, 0.97, 1)
        for m in _RX["NIR"].finditer(text):
            add("NIR", m, 0.95)
        for m in _RX["RPPS"].finditer(text):
            add("RPPS", m, 0.98, 1)
        for m in _RX["RPPS_BARE"].finditer(text):
            add("RPPS", m, 0.7)
        for m in _RX["ADELI"].finditer(text):
            add("ADELI", m, 0.95, 1)
        for m in _RX["FINESS"].finditer(text):
            add("FINESS", m, 0.95, 1)
        for m in _RX["TELEPHONE"].finditer(text):
            add("TELEPHONE", m, 0.9)
        for m in _RX["EMAIL"].finditer(text):
            add("EMAIL", m, 0.97)
        for m in _RX["ADRESSE"].finditer(text):
            add("ADRESSE", m, 0.8)
        for m in _RX["CODE_POSTAL_VILLE"].finditer(text):
            add("ADRESSE", m, 0.6)
        for m in _RX["NUMERO_DOSSIER"].finditer(text):
            add("NUMERO_DOSSIER", m, 0.9, 1)
        for m in _RX["NUMERO_SEJOUR"].finditer(text):
            add("NUMERO_SEJOUR", m, 0.9, 1)

        # Dates : naissance / décès si contexte, sinon date "suspecte" faible.
        # Le contexte est cherché dans la fenêtre précédant la date ET dans
        # toute la ligne (dates répétées dans des tableaux OCR dégradés).
        for m in _RX["DATE"].finditer(text):
            window = text[max(0, m.start() - 30) : m.start()]
            line_start = text.rfind("\n", 0, m.start()) + 1
            line_end = text.find("\n", m.end())
            if line_end == -1:
                line_end = len(text)
            line = text[line_start:line_end]
            if _CTX_BIRTH.search(window) or _CTX_BIRTH.search(line):
                out.append(
                    PIIMatch(
                        "DATE_NAISSANCE",
                        m.start(),
                        m.end(),
                        m.group(),
                        0.95,
                        "heuristique",
                    )
                )
            elif _CTX_DEATH.search(window):
                out.append(
                    PIIMatch(
                        "DATE_DECES", m.start(), m.end(), m.group(), 0.95, "heuristique"
                    )
                )
        return out

    # ------------------------------------------------------------------
    def _detect_names(self, text: str) -> list[PIIMatch]:
        out: list[PIIMatch] = []
        for m in _CTX_NAME.finditer(text):
            candidate = m.group("name")
            norm = _strip_accents(candidate.lower())
            if any(w in _MEDICAL_STOPWORDS for w in norm.split()):
                continue
            title = m.group("title").lower()
            first_word = candidate.split()[0]
            is_caps_surname = bool(re.fullmatch(r"[A-ZÀ-Ý]{2,}[A-ZÀ-Ý\-]*", first_word))
            has_known_firstname = any(tok in _FIRSTNAMES for tok in norm.split())
            strong_title = title in _STRONG_IDENTITY_TITLES
            # On ne garde que les candidatures plausibles : nom en MAJUSCULES
            # (format labo), prénom connu, ou titre d'identité fort.
            if not (is_caps_surname or has_known_firstname or strong_title):
                continue
            if has_known_firstname:
                conf = 0.95
            elif is_caps_surname:
                conf = 0.9
            else:
                conf = 0.85
            out.append(
                PIIMatch(
                    "NOM_PERSONNE",
                    m.start("name"),
                    m.end("name"),
                    candidate,
                    conf,
                    "heuristique",
                    context=m.group(0)[:20],
                )
            )

        # Dictionnaire : "Prénom NOM" en capitales typiques
        for m in re.finditer(
            r"\b([A-ZÀ-Ý][a-zà-ÿ\-]+)\s+([A-ZÀ-Ý]{2,}[A-ZÀ-Ý\-]*)\b", text
        ):
            if _strip_accents(m.group(1).lower()) in _FIRSTNAMES:
                out.append(
                    PIIMatch(
                        "NOM_PERSONNE",
                        m.start(),
                        m.end(),
                        m.group(),
                        0.9,
                        "dictionnaire",
                    )
                )

        # Repli : lignes à contexte d'identité (« Bénéficiaire : … »,
        # « Nom et Rang du bénéficiaire / … ») — attrape les variantes OCR
        # du nom (ex. « ABRICOT Antrony ») sans titre directement adjacent.
        for line in text.splitlines():
            if not _IDENTITY_LINE_RX.search(line):
                continue
            for m in _CAPS_NAME_RX.finditer(line):
                norm = _strip_accents((m.group(1) + " " + m.group(2)).lower())
                if any(w in _MEDICAL_STOPWORDS for w in norm.split()):
                    continue
                if len(m.group(1)) < 3:
                    continue
                out.append(
                    PIIMatch(
                        "NOM_PERSONNE",
                        m.start(),
                        m.end(),
                        m.group(1) + " " + m.group(2),
                        0.85,
                        "heuristique",
                        context=line[:20],
                    )
                )
        return out

    # ------------------------------------------------------------------
    def _detect_spacy(self, text: str) -> list[PIIMatch]:  # pragma: no cover
        out = []
        for ent in self._nlp(text).ents:
            if ent.label_ == "PER":
                out.append(
                    PIIMatch(
                        "NOM_PERSONNE",
                        ent.start_char,
                        ent.end_char,
                        ent.text,
                        0.8,
                        "spacy",
                    )
                )
        return out

    # ------------------------------------------------------------------
    @staticmethod
    def _dedupe(matches: list[PIIMatch]) -> list[PIIMatch]:
        """En cas de chevauchement, garder le match le plus confiant puis le plus long."""
        kept: list[PIIMatch] = []
        for m in sorted(matches, key=lambda x: (-x.confidence, -(x.end - x.start))):
            if not any(m.overlaps(k) for k in kept):
                kept.append(m)
        return sorted(kept, key=lambda x: x.start)


def detect_pii(text: str, use_spacy: bool = False) -> list[PIIMatch]:
    """Raccourci fonctionnel."""
    return PIIDetector(use_spacy=use_spacy).detect(text)
