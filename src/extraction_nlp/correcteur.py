"""Correction lexicale déterministe des « valeur » du VSM.

Remplace l'ancienne étape 1 (LLM de correction OCR) : corriger un document
entier via un petit modèle génératif est non viable sur CPU (délai dépassé) et
casse l'ancrage XAI (le LLM recopie un passage corrigé, absent du texte brut).
Ici, on corrige uniquement les courtes chaînes retenues comme « valeur » — après
l'extraction — par rapprochement flou (rapidfuzz) contre un lexique embarqué.

- Instantané (~1 ms), sans réseau, sans modèle, reproductible et auditable ;
- ne touche JAMAIS dosage, nombre, unité ni code (dérive clinique interdite) ;
- dans le doute, on ne corrige pas (mieux vaut laisser une faute que deviner
  un médicament ou un diagnostic).

Le module est 100 % local, sans dépendance optionnelle (rapidfuzz est requis).
"""

from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz, process

CONF_MIN_SIM = 88  # similarité minimale pour remplacer
LONGUEUR_MIN = 5  # en dessous, trop risqué

# Jamais modifiés : corriger un dosage serait dangereux.
_PROTEGE = re.compile(r"^[\d.,/:%-]+$|^\d|mg$|^ml$|^ui$|^g$|^mmhg$", re.I)

# Mots courants qu'un rapprochement flou transformerait à tort en terme médical.
_LISTE_BLANCHE = frozenset(
    "dans pour avec sans sous chez vers depuis pendant après avant selon "
    "cette ces les des une aux par sur est sont était patient patiente "
    "docteur médecin monsieur madame gauche droite deux trois matin soir".split()
)

# Lexique embarqué : termes du domaine (dossier gastro-entérologie) et
# vocabulaire clinique général, mal lus par l'OCR.
LEXIQUE = [
    # termes présents dans le document type, mal lus par l'OCR
    "œsophagite",
    "oesophagite",
    "ulcère",
    "bulbaire",
    "bulbite",
    "hernie",
    "hiatale",
    "fibroscopie",
    "endoscopie",
    "endoscopique",
    "duodénum",
    "gastrique",
    "antrale",
    "muqueuse",
    "biopsie",
    "biopsies",
    "métaplasie",
    "pylorique",
    "hélicobacter",
    "pylori",
    "éradication",
    "micronodule",
    "micronodules",
    "pulmonaire",
    "séquellaire",
    "discopathie",
    "leucocyturie",
    "tabagique",
    "intoxication",
    "chronique",
    "asymptomatique",
    "cicatrisé",
    "prémédication",
    "tolérance",
    "rétrécissement",
    "anatomo-pathologique",
    # médicaments du document
    "oméprazole",
    "pantoprazole",
    "clamoxyl",
    "naxy",
    "ogast",
    "maalox",
    "raniplex",
    "amoxicilline",
    "clarithromycine",
    "lansoprazole",
    # vocabulaire clinique général
    "antécédents",
    "allergie",
    "allergies",
    "traitement",
    "traitements",
    "posologie",
    "diagnostic",
    "hypertension",
    "artérielle",
    "diabète",
    "vaccination",
    "antitétanique",
    "surveillance",
    "plaquettes",
    "consultation",
]


def _sans_accent(s: str) -> str:
    s = "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )
    # Ligatures sans décomposition canonique (œ, æ) : on les rapproche de
    # « oe » / « ae » pour que « oesophagite » (OCR) et « œsophagite » (lexique)
    # aboutissent à la même clé d'index.
    s = s.replace("Œ", "OE").replace("œ", "oe")
    s = s.replace("Æ", "AE").replace("æ", "ae")
    return s


_INDEX: dict[str, str] = {}
for _t in LEXIQUE:
    _INDEX.setdefault(_sans_accent(_t.lower()), _t.lower())
_CLES = list(_INDEX)


def _corriger_mot(mot: str) -> str:
    if len(mot) < LONGUEUR_MIN or _PROTEGE.search(mot):
        return mot
    bas = mot.lower()
    norm = _sans_accent(bas)
    if norm in _LISTE_BLANCHE:
        return mot
    if norm in _INDEX:  # bon mot, accents à rétablir
        canon = _INDEX[norm]
        return canon if bas != canon else mot
    meilleur = process.extractOne(norm, _CLES, scorer=fuzz.ratio)
    if meilleur is None or meilleur[1] < CONF_MIN_SIM:
        return mot  # dans le doute, on ne touche pas
    return _INDEX[meilleur[0]]


def _casse(origine: str, corrige: str) -> str:
    if origine.isupper():
        return corrige.upper()
    if origine[:1].isupper():
        return corrige.capitalize()
    return corrige


def corriger_lexical(texte: str) -> str:
    """Corrige les mots d'une courte chaîne (une « valeur » du VSM).

    Ne corrige que les mots de ≥ 5 caractères, jamais les nombres/doses/unités,
    et respecte la casse d'origine. Retourne la chaîne inchangée si aucun mot
    certain ne doit être corrigé.
    """
    return re.sub(
        r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'-]{3,}",
        lambda m: _casse(m.group(0), _corriger_mot(m.group(0))),
        texte,
    )
