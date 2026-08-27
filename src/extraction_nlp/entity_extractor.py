"""Extraction d'entités médicales structurées depuis le texte OCR.

Deux moteurs, sélectionnables :

- ``rules`` (défaut, toujours disponible) : segmentation par rubriques
  (ANTÉCÉDENTS, ALLERGIES, TRAITEMENTS…) + découpage en items + scoring.
  100% offline, zéro modèle à télécharger — adapté au poste praticien.
- ``llm`` (optionnel) : llama-cpp-python avec un modèle local quantizé
  (Llama 3.1 8B Instruct Q4_K_M, ~5 Go) en extraction JSON contrainte.
  Le modèle est téléchargé par l'admin au premier lancement et caché dans
  ~/.cache/vsm-ocr/ — jamais committé, jamais appelé en cloud.

Chaque entité porte : valeur, confiance, source (passage + offsets), moteur.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass

_log = logging.getLogger("vsm")

NLP_ENGINE_RULES = "rules-fr-v1"
# Nom canonique du moteur LLM local (le modèle exact dépend du poste :
# Qwen 2.5 3B par défaut — voir src/extraction_nlp/llm.py, ADR-0004).
NLP_ENGINE_LLM = "llm-local-q4"

# Rubriques → en-têtes possibles dans les documents français
SECTION_HEADERS = {
    "antecedents": r"ANT[ÉE]C[ÉE]DENTS?",
    "allergies": r"ALLERGIES?",
    "traitements_long_cours": r"TRAITEMENTS?(?:\s+(?:EN\s+COURS|LONG\s+COURS|DE\s+SORTIE))?|ORDONNANCE",
    "vaccinations": r"VACCINATIONS?|VACCINS?",
    "pathologies_actives": r"PATHOLOGIES?\s+ACTIVES?|MOTIF|DIAGNOSTICS?",
    "facteurs_risque": r"FACTEURS?\s+DE\s+RISQUE",
    "points_vigilance": r"POINTS?\s+DE\s+VIGILANCE|PR[ÉE]CAUTIONS?",
}
_HEADER_RX = re.compile(
    r"^\s*("
    + "|".join(f"(?P<{k}>{v})" for k, v in SECTION_HEADERS.items())
    + r")\s*:?\s*",
    re.IGNORECASE | re.MULTILINE,
)

_NEG_ALLERGY = re.compile(
    r"aucune?\s+allergie|pas\s+d'?allergie|sans\s+allergie", re.IGNORECASE
)
_DOSE_RX = re.compile(r"\d+[.,]?\d*\s*(?:mg|g|µg|ug|ml|ui|%)", re.IGNORECASE)

# « CONCLUSION » clôt la dernière rubrique : son contenu n'est pas une
# rubrique VSM et ne doit pas polluer la section précédente.
_CLOSING_RX = re.compile(
    r"^\s*(?:CONCLUSIONS?|OBSERVATIONS?)\b", re.IGNORECASE | re.MULTILINE
)


@dataclass
class ExtractedEntity:
    valeur: str
    section: str
    confiance: float
    passage: str
    offset_debut: int
    offset_fin: int
    moteur_nlp: str = NLP_ENGINE_RULES
    # True si la « valeur » a été corrigée (lexicalement) par rapport au passage.
    correction_ocr: bool = False
    # Origine de l'entité pour la traçabilité : "llm" | "regles" | "drbert".
    origine: str = "regles"

    def to_champ(self) -> dict:
        d = asdict(self)
        return {
            "valeur": d["valeur"],
            "confiance": round(d["confiance"], 3),
            "source": {
                "passage": d["passage"],
                "offset_debut": d["offset_debut"],
                "offset_fin": d["offset_fin"],
            },
            "moteur_nlp": d["moteur_nlp"],
            "correction_ocr": d["correction_ocr"],
            "origine": d["origine"],
        }


def _split_sections(text: str) -> list[tuple[str, int, int]]:
    """Retourne [(section, start, end)] des zones de texte par rubrique.

    Les lignes « CONCLUSION » / « OBSERVATIONS » clôturent la rubrique en
    cours : elles ne créent pas de zone, mais terminent la précédente."""
    hits = []
    for m in _HEADER_RX.finditer(text):
        section = next(k for k in SECTION_HEADERS if m.group(k))
        hits.append((section, m.start(), m.end()))
    for m in _CLOSING_RX.finditer(text):
        hits.append((None, m.start(), m.end()))
    hits.sort(key=lambda h: h[1])
    zones = []
    for i, (section, _, content_start) in enumerate(hits):
        if section is None:
            continue  # terminateur : ne crée pas de zone
        end = hits[i + 1][1] if i + 1 < len(hits) else len(text)
        zones.append((section, content_start, end))
    return zones


def _split_items(chunk: str) -> list[tuple[str, int]]:
    """Découpe une rubrique en items (phrases / segments). Retourne (item, offset_relatif)."""
    items, pos = [], 0
    for part in re.split(r"(?<=[.;])\s+|\n", chunk):
        clean = part.strip(" .;:-•\t")
        if len(clean) >= 3:
            offset = chunk.find(part, pos)
            items.append((clean, max(offset, 0)))
            pos = offset + len(part)
    return items


def extract_entities_rules(text: str) -> list[ExtractedEntity]:
    entities: list[ExtractedEntity] = []
    for section, start, end in _split_sections(text):
        chunk = text[start:end]
        for item, rel in _split_items(chunk):
            conf = 0.8
            if section == "allergies" and _NEG_ALLERGY.search(item):
                conf = 0.9  # information négative utile, gardée telle quelle
            if section == "traitements_long_cours":
                conf = 0.88 if _DOSE_RX.search(item) else 0.6
            if len(item) < 8:
                conf -= 0.15
            abs_start = start + rel
            entities.append(
                ExtractedEntity(
                    valeur=item,
                    section=section,
                    confiance=max(min(conf, 1.0), 0.0),
                    passage=item,
                    offset_debut=abs_start,
                    offset_fin=abs_start + len(item),
                )
            )
    return entities


# ---------------------------------------------------------------------------
# Repli « texte libre » : extraction sur documents non rubriqués (CR de
# laboratoire, comptes-rendus d'anapath, lettres…) qui ne contiennent pas les
# en-têtes ANTÉCÉDENTS/ALLERGIES/TRAITEMENTS attendus par l'extraction par
# rubriques. Règles déterministes, offline, XAI : confiances volontairement
# basses (< 0,7 → « À valider » par le médecin).
# ---------------------------------------------------------------------------

# Marqueurs d'antécédents en texte libre
_ANTECEDENT_MARKER_RX = re.compile(
    r"(?:dans\s+ses\s+ant[ée]c[ée]dents\s+on\s+note|"
    r"ant[ée]c[ée]dents?\s*[:;]|"
    r"sur\s+le\s+plan\s+[a-zà-ÿ]+)",
    re.IGNORECASE,
)
# Allergies en texte libre (« allergie à la pénicilline », « allergique à l'iode »)
_ALLERGY_FREE_RX = re.compile(
    r"(?:allergie(?:s)?\s*(?:[àa]|aux|à la|à l'|:)?\s*|allergique\s+[àa]\s+)"
    r"([A-Za-zÀ-ÿ'\-]+(?:\s+[A-Za-zÀ-ÿ'\-]+){0,3})",
    re.IGNORECASE,
)
_NEG_ALLERGY_FREE = re.compile(r"(?:aucune|pas\s+d'?|sans)\s+allergie", re.IGNORECASE)
# Facteurs de risque
_RISK_FREE_RX = re.compile(
    r"\b(tabagisme|tabac|alcool|ob[ée]sit[ée]|surpoids|s[ée]dentarit[ée]|"
    r"dyslipid[ée]mie)\w*\b",
    re.IGNORECASE,
)
# Médicaments en texte libre : « traitement par X », « traitement : X »,
# ou molécule connue du référentiel ATC
_MED_FREE_RX = re.compile(
    r"(?:traitement\s+(?:par|:)\s*|trait[ée]e?\s+par\s+)"
    r"([A-Za-zÀ-ÿ'\-]+(?:[ \t]+[A-Za-zÀ-ÿ'\-]+){0,2})",
    re.IGNORECASE,
)
# Mots-outils à retirer d'un nom de médicament capturé
_MED_STOP = {
    "et",
    "la",
    "le",
    "les",
    "par",
    "des",
    "a",
    "ont",
    "depuis",
    "avec",
    "dans",
    "une",
    "un",
    "de",
    "du",
    "au",
    "aux",
    "qui",
    "que",
    "se",
}
# Indicateurs de ligne de laboratoire (à exclure des candidats médicaments)
_LAB_LINE_RX = re.compile(
    r"(?:/L|/100|mmol|µmol|T[ée]ra|Giga|pg/mL|ng/mL|UI/L|%|g/100mL)", re.IGNORECASE
)
# Bruit possible juste après un « CONCLUSION : » (dates, signatures, pagination)
_CONCLUSION_NOISE_RX = re.compile(
    r"^\s*(\d|page\b|le\s+\d|docteur\b|m[ée]decin\b|cp/?bp\b|signature\b)",
    re.IGNORECASE,
)


def _entity(
    valeur: str, section: str, conf: float, text: str, start: int, end: int
) -> ExtractedEntity:
    return ExtractedEntity(
        valeur=valeur,
        section=section,
        confiance=max(min(conf, 1.0), 0.0),
        passage=text[start:end][:120],
        offset_debut=start,
        offset_fin=end,
    )


_CONCLUSION_ONLY_RX = re.compile(r"^\s*CONCLUSIONS?\b", re.IGNORECASE | re.MULTILINE)


def _extract_conclusion_free(text: str) -> list[ExtractedEntity]:
    """« CONCLUSION : … » → rubrique Points de vigilance.

    Segment borné (4 segments au plus, bruit filtré) pour ne pas capturer
    la fin entière d'un document multi-pages."""
    out = []
    for m in _CONCLUSION_ONLY_RX.finditer(text):
        seg = text[m.end() :]
        for item, rel in _split_items(seg)[:4]:
            if len(item) < 4 or _CONCLUSION_NOISE_RX.search(item):
                continue
            start = m.end() + rel
            out.append(
                _entity(item, "points_vigilance", 0.75, text, start, start + len(item))
            )
    return out


def _extract_antecedents_free(text: str) -> list[ExtractedEntity]:
    """« Dans ses antécédents on note : », « Sur le plan … » → Antécédents.

    Chaque segment est borné (250 caractères ou jusqu'au marqueur suivant)
    pour ne pas englober la fin du document."""
    out = []
    for m in _ANTECEDENT_MARKER_RX.finditer(text):
        start = m.end()
        nxt = _ANTECEDENT_MARKER_RX.search(text, start)
        end = nxt.start() if nxt else min(len(text), start + 250)
        for item, rel in _split_items(text[start:end]):
            if len(item) < 3 or item.lower() in ("ras", "rass", "rien"):
                continue
            abs_start = start + rel
            out.append(
                _entity(
                    item, "antecedents", 0.6, text, abs_start, abs_start + len(item)
                )
            )
    return out


def _extract_medications_free(text: str) -> list[ExtractedEntity]:
    """« traitement par X », molécule ATC connue + dosage → Traitements."""
    from .normalizer import _load as _load_ref
    from .normalizer import _norm

    out = []
    rows = _load_ref("atc.tsv")
    choices = []
    for r in rows:
        choices.append(_norm(r["dci"]))
        choices.append(_norm(r["libelle"]))
    choices = sorted({c for c in choices if c}, key=len, reverse=True)

    for m in _MED_FREE_RX.finditer(text):
        tokens = [t for t in m.group(1).split() if t.lower() not in _MED_STOP]
        if not tokens:
            continue
        val = " ".join(tokens).strip(" .;:")
        if len(val) >= 3:
            out.append(
                _entity(val, "traitements_long_cours", 0.55, text, m.start(1), m.end(1))
            )

    for line in text.splitlines():
        if _LAB_LINE_RX.search(line) or len(line.strip()) < 4:
            continue
        norm = _norm(line)
        for dci in choices:
            if dci and dci in norm:
                clean = line.strip(" .;:-•\t")
                conf = 0.7 if _DOSE_RX.search(line) else 0.55
                idx = text.find(line)
                out.append(
                    _entity(
                        clean,
                        "traitements_long_cours",
                        conf,
                        text,
                        idx,
                        idx + len(clean),
                    )
                )
                break
    return out


def _extract_allergies_free(text: str) -> list[ExtractedEntity]:
    out = []
    for m in _ALLERGY_FREE_RX.finditer(text):
        # fenêtre large autour du match pour détecter la négation
        # (« pas d'allergie », « aucune allergie », « sans allergie »)
        window = text[max(0, m.start() - 30) : m.end() + 15]
        if _NEG_ALLERGY_FREE.search(window):
            continue
        val = m.group(1).strip(" .;:")
        if val.lower() in (
            "connue",
            "inconnue",
            "aucune",
            "aucunes",
            "pas",
            "médicamenteuse",
            "medicamenteuse",
            "alimentaire",
        ):
            continue
        if len(val) >= 2:
            out.append(_entity(val, "allergies", 0.8, text, m.start(1), m.end(1)))
    return out


def _extract_risks_free(text: str) -> list[ExtractedEntity]:
    out = []
    for m in _RISK_FREE_RX.finditer(text):
        line_start = text.rfind("\n", 0, m.start()) + 1
        line_end = text.find("\n", m.end())
        if line_end == -1:
            line_end = len(text)
        phrase = text[line_start:line_end].strip(" .;:-•\t")
        if 3 < len(phrase) < 120:
            out.append(
                _entity(phrase, "facteurs_risque", 0.65, text, line_start, line_end)
            )
    return out


def _extract_diagnoses_free(text: str) -> list[ExtractedEntity]:
    """Diagnostics CIM-10 en texte libre : lignes courtes, match fuzzy contre
    le référentiel local. Assignation : antécédents si contexte temporel,
    sinon pathologies actives."""
    from rapidfuzz import fuzz

    from .normalizer import _load as _load_ref
    from .normalizer import _norm

    rows = _load_ref("cim10_fr.tsv")
    labels = sorted({_norm(r["libelle"]) for r in rows}, key=len, reverse=True)
    out = []
    for line in text.splitlines():
        clean = line.strip()
        if not (3 < len(clean) < 60) or _LAB_LINE_RX.search(clean):
            continue
        norm = _norm(clean)
        best, best_score = None, 0.0
        for lab in labels:
            s = fuzz.token_set_ratio(norm, lab) / 100.0
            if s > best_score:
                best, best_score = lab, s
        if best is None or best_score < 0.8:
            continue
        section = (
            "antecedents"
            if re.search(
                r"ant[ée]c[ée]dent|historique|depuis|en\s+(?:19|20)\d{2}",
                clean,
                re.IGNORECASE,
            )
            else "pathologies_actives"
        )
        idx = text.find(line)
        out.append(_entity(clean, section, 0.55, text, idx, idx + len(clean)))
    return out


def _extract_vaccinations_free(text: str) -> list[ExtractedEntity]:
    out = []
    for line in text.splitlines():
        low = line.lower()
        if re.search(r"vaccin|rappel", low) and re.search(r"\b(?:19|20)\d{2}\b", line):
            clean = line.strip(" .;:-•\t")
            if 3 < len(clean) < 80:
                idx = text.find(line)
                out.append(
                    _entity(clean, "vaccinations", 0.6, text, idx, idx + len(clean))
                )
    return out


def extract_entities_free_text_fallback(
    text: str, existing: list[ExtractedEntity]
) -> list[ExtractedEntity]:
    """Repli par rubrique : n'extrait en texte libre que les sections encore
    vides après l'extraction par rubriques (pas de doublons)."""
    present = {e.section for e in existing}
    extra: list[ExtractedEntity] = []
    if "points_vigilance" not in present:
        extra += _extract_conclusion_free(text)
    if "antecedents" not in present:
        extra += _extract_antecedents_free(text)
        extra += _extract_diagnoses_free(text)
    elif "pathologies_actives" not in present:
        extra += _extract_diagnoses_free(text)
    if "allergies" not in present:
        extra += _extract_allergies_free(text)
    if "traitements_long_cours" not in present:
        extra += _extract_medications_free(text)
    if "facteurs_risque" not in present:
        extra += _extract_risks_free(text)
    if "vaccinations" not in present:
        extra += _extract_vaccinations_free(text)

    # dédoublonnage global : les entités par rubriques priment, les entités
    # du repli ne sont ajoutées que si elles ne doublonnent pas.
    seen: set[tuple[str, str]] = {
        (e.valeur.strip().lower(), e.section) for e in existing
    }
    kept: list[ExtractedEntity] = list(existing)
    for e in extra:
        key = (e.valeur.strip().lower(), e.section)
        if key in seen:
            continue
        seen.add(key)
        kept.append(e)
    return kept


# ---------------------------------------------------------------------------
# Adaptateur LLM local (PAR DÉFAUT — docs/ADR/0004 & ADR-0009)
# Chaque document passe par une PHASE LLM OBLIGATOIRE en deux étapes :
#   1. correction OCR (prompt dédié ci-dessous) — réduit le taux d'erreur OCR
#      avant extraction ;
#   2. extraction structurée VSM (schéma JSON strict, anti-hallucination,
#      normalisation, négations, few-shot).
# Le moteur règles reste un REPLI VISIBLE (jamais silencieux : le rapport
# d'exécution est renvoyé dans provenance.nlp et affiché au médecin).
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Étape 2 — EXTRACTION structurée du VSM.
# Note : l'ancienne « étape 1 » (LLM de correction OCR) a été supprimée. Elle
# exigeait de réécrire tout le document (non viable sur CPU, délai dépassé) et
# cassait l'ancrage XAI (le LLM recopiait un passage corrigé, absent du texte
# brut). La correction est désormais DÉTERMINISTE et appliquée après extraction
# aux seules « valeur » retenues — voir src/extraction_nlp/correcteur.py.
# ---------------------------------------------------------------------------
# Étiquettes de rubrique renvoyées par le modèle → clés de section du VSM.
_R2SECTION = {
    "patho": "pathologies_actives",
    "antec": "antecedents",
    "allerg": "allergies",
    "traitement": "traitements_long_cours",
    "risque": "facteurs_risque",
    "vaccin": "vaccinations",
    "vigilance": "points_vigilance",
}
_VSM_SECTIONS = tuple(_R2SECTION.values())  # les 7 rubriques canoniques

_LLM_SYSTEM = (
    "Tu lis un extrait de document médical français scanné, avec ses fautes "
    "d'OCR.\n"
    "Tu en tires les informations cliniques, et seulement celles qui y sont "
    "écrites.\n"
    "RÉPONDS UNIQUEMENT par un objet JSON valide, exactement :\n"
    '{"items": [{"r": "...", "v": "...", "p": "..."}]}\n'
    "Si l'extrait ne contient aucune information clinique, réponds exactement : "
    '{"items": []}\n'
    "Maximum 8 éléments par réponse. S'il y en a plus, garde les 8 plus "
    "importants : la réponse doit rester courte.\n"
    '"r" = une seule de ces étiquettes :\n'
    "patho | antec | allerg | traitement | risque | vaccin | vigilance\n"
    "\"p\" = un passage COPIÉ MOT POUR MOT depuis l'extrait, fautes d'OCR "
    'COMPRISES, entre 5 et 150 caractères. Ne corrige rien dans "p".\n'
    '"v" = la même information, orthographe corrigée, 80 caractères maximum, '
    "une seule information.\n"
    "N'ÉCRIS AUCUN ÉLÉMENT pour :\n"
    "1. un en-tête, une adresse, un téléphone, un fax, un numéro de dossier, "
    "un nom d'hôpital, de service, de laboratoire ou d'appareil, une date "
    "d'édition, un numéro de page ;\n"
    "2. une formule de correspondance : « Cher Confrère », « Bien "
    "amicalement », « En te remerciant », « Croyez à mes sentiments », "
    "« Tu trouveras ci-joint » ;\n"
    "3. un rendez-vous, une convocation, une consultation à programmer ;\n"
    "4. un résultat chiffré d'analyse biologique ;\n"
    "5. un mot isolé, une phrase coupée, une suite de caractères illisibles ;\n"
    "6. une famille de médicaments sans nom de produit ;\n"
    "7. le nom ou la signature d'un médecin ;\n"
    "8. tout ce qui n'est pas écrit dans l'extrait fourni.\n"
    "[] est une réponse fréquente et correcte : la plupart des extraits d'un "
    "document scanné ne contiennent aucune information clinique. Rendre [] "
    "vaut mieux qu'écrire un élément douteux — ce document sera lu par un "
    "médecin, et une erreur coûte plus cher qu'un oubli.\n"
    "ÉTIQUETTES :\n"
    "patho      = maladie ou problème de santé actuel, diagnostic posé ici\n"
    "antec      = maladie passée, opération subie, antécédent familial\n"
    "allerg     = produit auquel le patient réagit, nommé explicitement\n"
    "traitement = médicament pris au long cours : il faut un NOM de "
    "médicament, un seul par élément, avec sa posologie si elle est écrite\n"
    "risque     = ce que le texte dit des consommations, du poids, de "
    "l'activité physique, des expositions\n"
    "vaccin     = vaccin ou rappel, même s'il est seulement à prévoir\n"
    "vigilance  = conclusion, surveillance à prévoir, alerte clinique, ou "
    "traitement de durée courte explicitement limitée\n"
)

_LLM_FEW_SHOT = """
Exemple 1 — extrait sans information clinique exploitable :
Extrait : "CENTRE HOSPITALIER — Sce de Gastro-entérologie
Tél [TEL_001] — Fax : [TEL_002]
Allergie(s) :
Hémogramme BC-6800 Mindray"
Réponse : {"items": []}

Exemple 2 — extrait avec des informations réelles :
Extrait : "ANTECEDENTS : appendicectomie en 1998. Tabagisme actif, 20 cigarettes par jour.
TRAITEMENT DE FOND : OGAST 1 gélule par jour en permanence.
Cure de 7 jours : CLAMOXYL 500, 2 gélules matin et soir.
CONCLUSION : contrôle endoscopique à prévoir dans deux mois."
Réponse : {"items": [{"r": "antec", "v": "Appendicectomie en 1998", "p": "appendicectomie en 1998"}, {"r": "risque", "v": "Tabagisme actif, 20 cigarettes par jour", "p": "Tabagisme actif, 20 cigarettes par jour"}, {"r": "traitement", "v": "OGAST 1 gélule par jour", "p": "OGAST 1 gélule par jour en permanence"}, {"r": "vigilance", "v": "Contrôle endoscopique à prévoir dans deux mois", "p": "contrôle endoscopique à prévoir dans deux mois"}, {"r": "vigilance", "v": "Cure de 7 jours : CLAMOXYL 500, 2 gélules matin et soir", "p": "Cure de 7 jours : CLAMOXYL 500, 2 gélules matin et soir"}]}
"""


def build_llm_messages(text: str, max_chars: int = 6000) -> list[dict]:
    """Messages système + utilisateur pour l'extraction LLM (fonction pure,
    testable sans GPU). L'extraction lit le texte OCR BRUT — « p » doit être
    reproduit à l'identique depuis ce texte (ancrage XAI) ; « v » est la forme
    normalisée (correction lexicale appliquée en aval)."""
    user = (
        "Extrait du document :\n```\n"
        + text[:max_chars]
        + "\n```\nEn appliquant les règles, extrais les informations cliniques."
    )
    return [
        {"role": "system", "content": _LLM_SYSTEM + _LLM_FEW_SHOT},
        {"role": "user", "content": user},
    ]


# ---------------------------------------------------------------------------
# Génération contrainte : la sortie du LLM est pilotée par une GRAMMAIRE GBNF
# (dérivée de ce schéma JSON) et lue en STREAMING. Le modèle ne peut donc
# JAMAIS produire de JSON invalide (fini les « Expecting ',' delimiter »), et
# la génération s'arrête dès que le JSON est complet — plus de threads
# orphelins qui gardent le verrou du modèle après un dépassement de délai.
# ---------------------------------------------------------------------------
_LLM_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "r": {"type": "string"},
                    "v": {"type": "string"},
                    "p": {"type": "string"},
                },
                "required": ["r", "v", "p"],
            },
        },
    },
    "required": ["items"],
}
_LLM_GRAMMAR_CACHE = None


def _llm_output_grammar():
    """Grammaire GBNF de la sortie (générée UNE fois par processus)."""
    global _LLM_GRAMMAR_CACHE
    if _LLM_GRAMMAR_CACHE is None:
        import json as _json

        from llama_cpp import LlamaGrammar

        _LLM_GRAMMAR_CACHE = LlamaGrammar.from_json_schema(
            _json.dumps(_LLM_OUTPUT_SCHEMA)
        )
    return _LLM_GRAMMAR_CACHE


def _repair_truncated_json(buf: str) -> dict | None:
    """Répare une réponse tronquée (génération arrêtée au délai ou à la limite).

    La grammaire garantit que ``buf`` est un PREFIXE valide du schéma : on
    ferme les structures manquantes, et si le dernier item est incomplet, on
    le coupe. Retourne le dict parsé, ou None si rien n'est récupérable."""
    import json

    buf = buf.strip()
    if not buf:
        return None
    try:
        return json.loads(buf)
    except json.JSONDecodeError:
        pass
    base = buf.rstrip(", \t\r\n")
    # fermetures possibles : fin après le dernier item complet
    for closer in ("]}]}", "]}]", "]}"):
        try:
            return json.loads(base + closer)
        except json.JSONDecodeError:
            continue
    # tronqué au milieu d'une chaîne (nombre impair de guillemets)
    if base.count('"') % 2 == 1:
        try:
            return json.loads(base + '"}]}')
        except json.JSONDecodeError:
            pass
    # couper au dernier item COMPLET (le dernier item est tronqué)
    cut = base.rfind("}, {")
    if cut != -1:
        try:
            return json.loads(base[: cut + 1] + "]}")
        except json.JSONDecodeError:
            pass
    return None


# ---------------------------------------------------------------------------
# Garde-fou AVAL : validation de la sortie de l'étape 2 (avant _anchor).
# Un prompt réduit la probabilité d'erreur ; ce validateur la met à ZÉRO sur
# les classes qu'il couvre (fuite de few-shot, pseudonymes, en-têtes, bruit
# OCR, classes sans produit, cures courtes, valeurs biologiques). Le rejet est
# la valeur par défaut : un élément douteux est supprimé plutôt que présenté
# à un médecin avec un score de confiance rassurant.
# ---------------------------------------------------------------------------

# Termes qui trahissent un extrait non clinique (en-tête, labo, administratif).
_BLOCKLIST = (
    "diffusion",
    "référence",
    "reference",
    "cedex",
    "tél",
    "tel ",
    "fax",
    "dossier n",
    "page ",
    "expédition",
    "expedition",
    "commercial",
    "menarini",
    "mindray",
    "automate",
    "hémogramme",
    "hemogramme",
    "laboratoire",
    "vidéo-endoscope",
    "video-endoscope",
    "olympus",
    "centre hospitalier",
    "adresse",
    "prélèvement",
    "prelevement",
)
# Familles de médicaments : invalides seules, valides avec un nom de produit.
_CLASSES_SEULES = (
    "antibiotique",
    "antibiotiques",
    "inhibiteur",
    "inhibiteurs",
    "anti h",
    "anti-h",
    "ipp",
    "corticoïde",
    "corticoide",
)
_RX_PSEUDO = re.compile(r"\[[A-Z_]+_\d+\]")
_RX_BIO = re.compile(r"\d+[,.]\d+\s*(g/?100\s*mL|%|g/dL|mmol|UI/L|µ)", re.I)
_RX_DUREE_COURTE = re.compile(
    r"\b(cure|pendant|pour)(?:\s+de)?\s+\d+\s*(jours?|semaines?)|"
    r"\b\d+\s*(jours|semaines)\s+de\s+traitement",
    re.I,
)


def _normalise(texte: str) -> str:
    """Comparaison tolérante aux accents, à la casse et aux espaces."""
    import unicodedata

    sans_accent = "".join(
        c
        for c in unicodedata.normalize("NFD", texte)
        if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"\s+", " ", sans_accent).strip().lower()


def valider_element(item: dict, texte_source: str, rubrique: str) -> dict | None:
    """Renvoie l'élément nettoyé, ou None s'il doit être rejeté.

    Le rejet est la valeur par défaut : un élément douteux est supprimé plutôt
    que présenté à un médecin avec un score de confiance rassurant.
    """
    valeur = (item.get("valeur") or "").strip()
    passage = (item.get("passage") or "").strip()

    # 1. Ancrage : le passage doit exister mot pour mot dans le document.
    #    C'est ce test qui rend toute fuite de few-shot impossible.
    if not passage or _normalise(passage) not in _normalise(texte_source):
        return None

    # 2. Longueurs : ni fragment, ni paragraphe.
    if not 3 <= len(valeur) <= 120 or len(passage) > 250:
        return None
    if valeur.count(".") >= 2 or len(valeur.split()) > 18:
        return None

    # 3. Aucun pseudonyme ne doit atteindre le VSM.
    if _RX_PSEUDO.search(valeur):
        return None

    # 4. En-tête, coordonnées, matériel de laboratoire.
    bas = _normalise(valeur)
    if any(mot in bas for mot in _BLOCKLIST):
        return None

    # 5. Fragments sans sens : trop peu de lettres, ou libellé vide.
    lettres = sum(c.isalpha() for c in valeur)
    if lettres < 3 or lettres / max(len(valeur), 1) < 0.45:
        return None
    if bas in ("(s)", "s", "familiaux", "aucun", "aucune", "1 docteur"):
        return None

    # 6. Règles propres aux traitements.
    if rubrique == "traitements_long_cours":
        if bas in _CLASSES_SEULES:  # « antibiotique », « inhibiteur »
            return None
        if " et " in bas or "/" in valeur:  # « MAALOX et RANIPLEX »
            return None
        if not re.search(r"[A-Za-zÀ-ÿ]{4,}", valeur):
            return None
        if _RX_DUREE_COURTE.search(passage):
            # Cure courte : information vraie, rubrique fausse.
            return {
                "valeur": valeur,
                "passage": passage,
                "_reclasser": "points_vigilance",
            }

    # 7. Valeurs biologiques chiffrées : hors périmètre du VSM.
    if rubrique == "points_vigilance" and _RX_BIO.search(valeur):
        return None

    return {"valeur": valeur, "passage": passage}


def valider_sortie(brut: dict, texte_source: str) -> dict:
    """Filtre, reclasse et dédoublonne la sortie brute de l'étape 2."""
    propre = {k: [] for k in _VSM_SECTIONS}
    vus = set()
    for rubrique, items in brut.items():
        if rubrique not in propre or not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            ok = valider_element(item, texte_source, rubrique)
            if ok is None:
                continue
            cible = ok.pop("_reclasser", rubrique)
            cle = (cible, _normalise(ok["valeur"]))
            if cle in vus:
                continue
            vus.add(cle)
            propre[cible].append(ok)
    return propre


# Confiance des entités LLM (XAI honnête) — proportionnelle à l'ANCRAGE dans
# le texte source, et non plus une constante pessimiste (0,65) qui marquait
# « À valider » même un champ parfaitement extrait :
#  - passage reproduit à l'identique (niveau 2) → 0,9 : le LLM a travaillé et
#    s'appuie sur du texte réel → pas de badge « À valider » automatique ;
#  - seule la valeur normalisée est retrouvée (niveau 1) → 0,8 ;
#  - introuvable (possible hallucination) → 0,6 → « À valider ».
# La relecture clinique du médecin reste OBLIGATOIRE (avertissement du VSM),
# mais le badge reflète désormais la qualité d'extraction, pas le moteur.
LLM_CONFIDENCE_ANCHORED = 0.9
LLM_CONFIDENCE_VALEUR_TROUVEE = 0.8
LLM_CONFIDENCE_UNANCHORED = 0.6
LLM_CONFIDENCE = LLM_CONFIDENCE_UNANCHORED  # rétro-compatibilité


def _extract_json_llm(content: str) -> dict:
    """Parse le JSON de la réponse LLM, même si entouré de texte/fences."""
    import json
    import re

    content = content.strip()
    # retirer les fences markdown ```json … ```
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.MULTILINE)
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start, end = content.find("{"), content.rfind("}")
        if start != -1 and end > start:
            return json.loads(content[start : end + 1])
        raise


def _flatten(s: str) -> str:
    """Minuscules + suppression des accents et de la ponctuation
    (comparaisons tolérantes aux erreurs OCR : « Pénicilline » ≈
    « Penicilline », « satisfaisant. » ≈ « satisfaisant »)."""
    import unicodedata

    s = "".join(
        c
        for c in unicodedata.normalize("NFD", s or "")
        if unicodedata.category(c) != "Mn"
    )
    s = re.sub(r"[^\w\s]", " ", s)  # ponctuation → espace
    return re.sub(r"\s+", " ", s.lower()).strip()


def _anchor(text: str, passage: str, valeur: str) -> tuple[int, int, int, str]:
    """Localise l'entité dans le texte BRUT. Retourne (offset, longueur,
    niveau, passage_effectif) :

    - niveau 2 : passage trouvé à l'identique ET contenant la valeur (le
      plus fiable) — passage_effectif = le passage brut ;
    - niveau 1 : seule la valeur est retrouvée (ou correspondance floue de
      ligne, tolérante aux erreurs OCR) — passage_effectif = segment BRUT
      (le visualiseur « Voir le passage source » ne sait surligner que du
      texte présent tel quel dans l'OCR) ;
    - niveau 0 : introuvable (hallucination ?) — passage_effectif = "".

    Garde-fou : un passage reproduit mais qui ne CONTIENT pas la valeur
    (passage recyclé par le LLM) est déclassé au niveau 0."""
    from rapidfuzz import fuzz

    if passage:
        idx = text.find(passage)
        if idx == -1:
            idx = text.lower().find(passage.lower())
        if idx != -1:
            region = text[idx : idx + len(passage)]
            if fuzz.partial_ratio(_flatten(valeur), _flatten(region)) >= 60:
                return idx, len(passage), 2, passage
            # passage trouvé mais incohérent avec la valeur (recyclé par le
            # LLM) → non fiable, pas de source affichable
            return idx, len(passage), 0, ""
    if valeur:
        idx = text.find(valeur)
        if idx == -1:
            idx = text.lower().find(valeur.lower())
        if idx != -1:
            return idx, len(valeur), 1, valeur
    # Correspondance floue contre les lignes du texte brut : le LLM cite
    # parfois le texte CORRIGÉ (« Pénicilline (éruption cutanée) ») — on
    # retrouve alors la ligne brute correspondante (source réelle du médecin).
    flat_val = _flatten(valeur)
    best_line, best_score, best_idx = None, 0.0, -1
    for line in text.splitlines():
        score = fuzz.token_set_ratio(flat_val, _flatten(line)) / 100.0
        if score > best_score:
            best_line, best_score, best_idx = line, score, text.find(line)
    if best_line is not None and best_score >= 0.85:
        return best_idx, len(best_line), 1, best_line
    return -1, 0, 0, ""


def extract_entities_llm(
    text: str,
    model_path: str | None = None,
    llm=None,
    segment: int | None = None,
    timeout: float | None = None,
) -> list[ExtractedEntity]:  # pragma: no cover — nécessite llama-cpp + GGUF
    """Extraction par LLM local (llama-cpp-python + GGUF, jamais en cloud).

    Réutilise l'instance partagée du modèle (singleton — chargée UNE fois par
    processus, cf. src/extraction_nlp/llm.get_llm_instance). La génération est
    en STREAMING avec grammaire GBNF : le modèle ne peut pas sortir de JSON
    invalide, et la génération s'arrête dès que le JSON est complet — pas de
    threads orphelins gardant le verrou après le délai. ``segment`` identifie
    le segment en cours (ligne de log par échec de parsing). Lève TimeoutError
    si le délai est dépassé SANS rien produire d'exploitable."""
    import json
    import time

    t0 = time.perf_counter()
    from .correcteur import corriger_lexical
    from .llm import LLM_TIMEOUT_S, get_llm_instance

    if llm is None:
        if model_path:
            from llama_cpp import Llama

            from .llm import LLM_N_BATCH, LLM_N_CTX, _physical_cores

            llm = Llama(
                model_path=model_path,
                n_ctx=LLM_N_CTX,
                n_threads=_physical_cores(),
                n_batch=LLM_N_BATCH,
                verbose=False,
            )
        else:
            llm = get_llm_instance()
    from .llm import LLM_INFERENCE_LOCK

    deadline = t0 + (timeout if timeout is not None else LLM_TIMEOUT_S)
    buf = ""
    timed_out = False
    with LLM_INFERENCE_LOCK:  # le modèle partagé n'est pas thread-safe
        stream = llm.create_chat_completion(
            messages=build_llm_messages(text),
            grammar=_llm_output_grammar(),
            temperature=0.0,
            repeat_penalty=1.0,  # une pénalité de répétition pousse à varier → inventer
            max_tokens=512,  # borne dure (le JSON complet s'arrête bien avant)
            stream=True,
        )
        for chunk in stream:
            buf += chunk["choices"][0]["delta"].get("content") or ""
            # Arrêt précoce : dès que le JSON est complet et valide, on coupe
            # la génération (économie de tokens ET de temps).
            try:
                json.loads(buf.strip())
                break
            except json.JSONDecodeError:
                pass
            if time.perf_counter() > deadline:
                timed_out = True
                break
            if len(buf) > 3000:  # ceinture de sécurité
                break
    data = _repair_truncated_json(buf)
    if data is None or not isinstance(data, dict) or "items" not in data:
        # UNE ligne de log par échec de parsing : cause, durée, taille et début
        # de la réponse — la grammaire rend ce cas très rare (réponse illisible).
        _log.warning(
            "repli règles | segment=%d | cause=%s | durée=%.1fs | "
            "longueur_réponse=%d | début=%r",
            segment if segment is not None else 0,
            "JSONDecodeError",
            time.perf_counter() - t0,
            len(buf),
            buf[:120],
        )
        raise json.JSONDecodeError("réponse illisible", buf, 0)
    if timed_out and not data.get("items"):
        raise TimeoutError(
            f"délai d'extraction LLM dépassé ({LLM_TIMEOUT_S} s) sans résultat exploitable"
        )
    # Sortie au format {"items": [{"r","v","p"}]} → on normalise en
    # {rubrique: [{"valeur","passage"}]} via l'adaptateur _R2SECTION.
    brut: dict[str, list[dict]] = {}
    brut_items = data.get("items", [])
    for item in brut_items if isinstance(brut_items, list) else []:
        if not isinstance(item, dict):
            continue
        section = _R2SECTION.get(str(item.get("r", "")))
        if section is None:
            continue
        brut.setdefault(section, []).append(
            {"valeur": str(item.get("v", "")), "passage": str(item.get("p", ""))}
        )
    # Garde-fou AVAL : filtre, reclasse (cures courtes → points_vigilance) et
    # dédoublonne. Rejette toute « valeur » dont le « passage » n'est pas une
    # sous-chaîne exacte du texte source — rend la fuite de few-shot impossible.
    data = valider_sortie(brut, text)
    entities = []
    for section, items in data.items():
        for it in items:
            valeur_brut = str(it.get("valeur", ""))
            passage = str(it.get("passage", ""))
            if not valeur_brut or not passage:
                continue
            idx, length, niveau, passage_effectif = _anchor(text, passage, valeur_brut)
            confiance = (
                LLM_CONFIDENCE_ANCHORED
                if niveau == 2
                else LLM_CONFIDENCE_VALEUR_TROUVEE
                if niveau == 1
                else LLM_CONFIDENCE_UNANCHORED
            )
            # Correction lexicale déterministe de la VALEUR (pas du passage) :
            # le passage reste une sous-chaîne exacte du texte brut (ancrage).
            valeur_corrigee = corriger_lexical(valeur_brut)
            # « passage » stocké = segment du texte BRUT (surlignable dans le
            # visualiseur source). Niveau 0 (non ancré) → pas de source
            # affichable : un passage recyclé par le LLM induirait le médecin
            # en erreur.
            passage_final = passage_effectif if niveau > 0 else ""
            entities.append(
                ExtractedEntity(
                    valeur=valeur_corrigee,
                    section=section,
                    confiance=confiance,
                    passage=passage_final,
                    offset_debut=max(idx, 0),
                    offset_fin=max(idx, 0) + (length or len(passage_final)),
                    moteur_nlp=NLP_ENGINE_LLM,
                    correction_ocr=(valeur_corrigee != valeur_brut),
                    origine="llm",
                )
            )
    return entities


# ---------------------------------------------------------------------------
# Exécution de la phase LLM par document — avec RAPPORT d'exécution visible.
# Un dépassement de délai ou une erreur ne désactive JAMAIS le LLM pour la
# suite de la session (le modèle reste chargé en singleton) : le document
# concerné bascule sur les règles et le rapport explique pourquoi.
# ---------------------------------------------------------------------------


def _run_with_timeout(fn, timeout: float, *args, **kwargs):
    """Exécute ``fn(*args, **kwargs)`` dans un thread daemon, attend au plus
    ``timeout`` s. Retourne (résultat, a_temporisé). Sur timeout, le thread
    continue en arrière-plan (il finira ou mourra) mais l'appelant bascule —
    jamais de blocage infini sur un poste lent."""
    import threading

    box: dict = {"done": False, "result": None, "err": None}

    def _run():  # pragma: no cover - nécessite llama.cpp + GGUF
        try:
            box["result"] = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - relancé dans le thread appelant
            box["err"] = exc
        finally:
            box["done"] = True

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout)
    if not box["done"]:
        return None, True  # durée dépassée
    if box["err"] is not None:
        raise box["err"]
    return box["result"], False


def _extract_llm_with_timeout(text: str, timeout: float):
    """Rétro-compatibilité : inférence d'extraction avec délai. Retourne
    (entités, a_temporisé)."""
    from .llm import LLM_INFERENCE_TIMEOUT_SEC

    return _run_with_timeout(
        extract_entities_llm, timeout if timeout else LLM_INFERENCE_TIMEOUT_SEC, text
    )


def _charger_modele(report: dict):
    """Charge (une seule fois) l'instance partagée du modèle et renseigne le
    rapport. Lève TimeoutError si le chargement dépasse son budget (il
    continue en arrière-plan pour le document suivant)."""
    from .llm import LLM_LOAD_TIMEOUT_SEC, get_llm_instance, llm_model_name

    llm = get_llm_instance(LLM_LOAD_TIMEOUT_SEC)
    report["modele"] = llm_model_name()
    return llm


def _extraction_phase(
    text: str, llm, segment: int | None = None
) -> tuple[list[ExtractedEntity], float | None]:
    """Phase d'extraction structurée VSM (sur le texte BRUT).

    L'appel est SYNCHRONE : le délai est appliqué DANS la génération (streaming
    avec arrêt) — plus de thread orphelin qui continue en arrière-plan et garde
    le verrou du modèle après le dépassement. Lève TimeoutError en cas de délai
    sans résultat exploitable. Retourne (entités, duree_sec)."""
    import time

    t0 = time.perf_counter()
    ents = extract_entities_llm(text, llm=llm, segment=segment)
    return ents or [], round(time.perf_counter() - t0, 3)


def _drbert_entities_to_extracted(
    text: str, added: list[dict]
) -> list[ExtractedEntity]:
    """Convertit les dicts d'entités DrBERT en ExtractedEntity (moteur tracé)."""
    from .drbert import NLP_ENGINE_DRBERT

    return [
        ExtractedEntity(
            valeur=d["valeur"],
            section=d["section"],
            confiance=d["confiance"],
            passage=d.get("passage", d["valeur"]),
            offset_debut=d.get("offset_debut", 0),
            offset_fin=d.get("offset_fin", 0),
            moteur_nlp=NLP_ENGINE_DRBERT,
            origine="drbert",
        )
        for d in added
    ]


def _augment_with_drbert(
    base: list[ExtractedEntity], text: str
) -> list[ExtractedEntity]:
    """Ajoute les entités détectées par DrBERT (NER médical FR) manquantes.

    Non destructif : ne remplace rien, complète le rappel sur les documents
    non rubricables. Léger (CPU) → utile sur les petites machines. Silencieux
    si torch/transformers absents ou si le modèle échoue (repli = sortie base)."""
    from .drbert import extract_entities_drbert, is_available

    if not is_available() or not text.strip():
        return base
    try:
        added = extract_entities_drbert(text)
    except Exception:
        return base  # DrBERT indisponible/erreur → on garde la sortie de base
    existing = {(e.valeur.strip().lower(), e.section) for e in base}
    out = list(base)
    for ent in _drbert_entities_to_extracted(text, added):
        key = (ent.valeur.strip().lower(), ent.section)
        if key in existing:
            continue
        existing.add(key)
        out.append(ent)
    return out


def _hard_split(text: str, max_chars: int) -> list[str]:
    """Découpe un paragraphe trop long en segments ≤ max_chars (sans couper un
    mot)."""
    out, cur = [], ""
    for w in text.split():
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= max_chars:
            cur += " " + w
        else:
            out.append(cur)
            cur = w
    if cur:
        out.append(cur)
    return out


def _chunk_text(text: str, max_chars: int, overlap: int = 0) -> list[str]:
    """Découpe le texte OCR en segments bornés (≈ max_chars) en respectant les
    paragraphes — un titre de rubrique et ses items restent ensemble. Chaque
    segment est assez court pour qu'une inférence reste rapide, même sur un
    CPU lent sans GPU. ``overlap`` (caractères) reporte la fin du segment
    précédent en tête du suivant pour ne pas couper une information en deux
    (le dédoublonnage aval absorbe les répétitions)."""
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    base, buf = [], ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip("\n")
        if not para:
            continue
        pieces = [para] if len(para) <= max_chars else _hard_split(para, max_chars)
        for piece in pieces:
            if not buf:
                buf = piece
            elif len(buf) + 2 + len(piece) <= max_chars:
                buf += "\n\n" + piece
            else:
                base.append(buf)
                buf = piece
    if buf:
        base.append(buf)
    if not base:
        return [text]
    if overlap <= 0:
        return base
    chunks = [base[0]]
    for prev, cur in zip(base, base[1:]):
        # recouvrement à la frontière de mot : on reporte la fin du segment
        # précédent en contexte du suivant.
        tail = prev[-overlap:]
        tail = tail.split(" ", 1)[-1] if " " in tail else tail
        chunks.append((tail + "\n\n" + cur).strip())
    return chunks


# ---------------------------------------------------------------------------
# Filtre de pertinence : ne pas appeler le LLM sur un segment sans intérêt
# clinique (en-têtes, adresses, formules de politesse). Coût ~1 ms ; supprime
# du temps de calcul ET des faux positifs. Un segment écarté renvoie RIEN
# (pas un repli sur les règles — sur un en-tête, la bonne réponse est « rien »,
# et c'est précisément là que les règles produisent « | Diffusion Médicale »).
# ---------------------------------------------------------------------------
_INDICES_CLINIQUES = re.compile(
    r"\b(antécédent|antecedent|allergi|traitement|ordonnance|diagnostic|"
    r"pathologi|maladie|opération|operation|chirurgi|vaccin|tabac|alcool|"
    r"mg\b|ml\b|comprimé|gélule|gelule|posologie|prescri|ulcère|ulcere|"
    r"hernie|scanner|biopsie|conclusion|surveillance)",
    re.I,
)
_SEGMENT_ADMINISTRATIF = re.compile(
    r"(cedex|tél|tel |fax|dossier n|page \d|cher confrère|cher confrere|"
    r"amicalement|en te remerciant|croyez|ci-joint|rdv|hospitalier général)",
    re.I,
)


def merite_appel_llm(segment: str) -> bool:
    """Filtre bon marché : un segment sans indice clinique ne vaut pas 45 s."""
    if len(segment.strip()) < 40:
        return False
    if not _INDICES_CLINIQUES.search(segment):
        return False
    # Segment purement administratif, sans aucun signal clinique fort.
    if (
        _SEGMENT_ADMINISTRATIF.search(segment)
        and len(_INDICES_CLINIQUES.findall(segment)) < 2
    ):
        return False
    return True


def extract_entities_with_report(
    text: str, engine: str = "rules", progress=None
) -> tuple[list[ExtractedEntity], dict]:
    """Extraction + RAPPORT du moteur réellement utilisé.

    Pour ``engine="llm"``, la phase LLM locale est OBLIGATOIRE dès que le
    modèle est présent : extraction structurée sur le texte BRUT (ancrage XAI
    préservé), correction lexicale déterministe des « valeur » en aval. Le
    texte est découpé en SEGMENTS bornés (grands documents) ; chaque segment
    est traité indépendamment, avec REPLI PAR SEGMENT (jamais global) sur les
    règles en cas d'échec. ``progress(done, total)`` (optionnel) est appelé
    entre les segments pour l'affichage.

    Rapport : {"moteur", "statut", "raison", "nb_corrections_ocr",
    "duree_extraction_sec", "modele", "nb_chunks"}."""
    report: dict = {
        "moteur": NLP_ENGINE_RULES,
        "statut": "regles",
        "raison": None,
        "nb_corrections_ocr": 0,
        "duree_extraction_sec": None,
        "modele": None,
        "nb_chunks": 0,
    }

    def _regles() -> list[ExtractedEntity]:
        return _augment_with_drbert(
            extract_entities_free_text_fallback(text, extract_entities_rules(text)),
            text,
        )

    if engine != "llm":
        return _regles(), report

    from .llm import llm_attemptable, llm_unavailability_reason

    if not llm_attemptable():
        _log.info(
            "LLM non disponible — moteur de règles : %s", llm_unavailability_reason()
        )
        report["statut"] = "modele_absent"
        report["raison"] = llm_unavailability_reason() or (
            "Modèle LLM local absent — téléchargez-le : "
            "python -m src.extraction_nlp.llm"
        )
        return _regles(), report

    try:
        llm = _charger_modele(report)
    except Exception as exc:  # noqa: BLE001 - tracé dans le rapport
        _log.warning("Modèle LLM indisponible — repli règles : %s", exc)
        report["statut"] = "repli_regles"
        report["raison"] = f"Modèle LLM indisponible ({exc})"
        return _regles(), report

    # Découpage en segments bornés : chaque inférence reste rapide (PC lent).
    from .llm import LLM_CHUNK_CHARS, LLM_CHUNK_OVERLAP

    chunks = _chunk_text(text, LLM_CHUNK_CHARS, LLM_CHUNK_OVERLAP)
    report["nb_chunks"] = len(chunks)

    llm_ents: list[ExtractedEntity] = []
    rules_ents: list[ExtractedEntity] = []
    n_regles = 0
    echecs = 0
    total_extr = 0.0
    total_n_corr = 0
    reasons: list[str] = []

    from .llm import ECHECS_MAX, LLM_TIMEOUT_S

    for ci, chunk in enumerate(chunks):
        if progress:
            progress(ci + 1, len(chunks))
        # Filtre : pas d'appel LLM sur un segment sans intérêt clinique. La
        # bonne réponse sur un en-tête est « rien » (les règles y produiraient
        # « | Diffusion Médicale ») — donc pas de repli règles ici.
        if not merite_appel_llm(chunk):
            continue
        # Coupe-circuit : modèle manifestement indisponible → règles pour le reste.
        if echecs >= ECHECS_MAX:
            n_regles += 1
            rules_ents.extend(
                extract_entities_free_text_fallback(
                    chunk, extract_entities_rules(chunk)
                )
            )
            continue
        # Repli PAR SEGMENT, jamais global : un segment trop lent ou en échec
        # bascule sur les règles pour CE segment, mais les suivants continuent
        # d'être traités par le LLM.
        try:
            ents, d_extr = _extraction_phase(chunk, llm, segment=ci + 1)
            if d_extr is not None:
                total_extr += d_extr
            if ents:
                llm_ents.extend(ents)
                total_n_corr += sum(1 for e in ents if e.correction_ocr)
            else:
                n_regles += 1
                rules_ents.extend(
                    extract_entities_free_text_fallback(
                        chunk, extract_entities_rules(chunk)
                    )
                )
        except TimeoutError:  # noqa: BLE001 - délai dépassé : ce segment seul
            echecs += 1
            n_regles += 1
            reasons.append(f"segment {ci + 1} : délai LLM dépassé ({LLM_TIMEOUT_S} s)")
            rules_ents.extend(
                extract_entities_free_text_fallback(
                    chunk, extract_entities_rules(chunk)
                )
            )
            _log.warning(
                "segment %d/%d — délai LLM dépassé (%d s) ; ce segment seul passe "
                "par les règles (échecs : %d/%d)",
                ci + 1,
                len(chunks),
                LLM_TIMEOUT_S,
                echecs,
                ECHECS_MAX,
            )
        except Exception as exc:  # noqa: BLE001 - erreur : ce segment seul
            echecs += 1
            n_regles += 1
            reasons.append(f"segment {ci + 1} : {exc}")
            rules_ents.extend(
                extract_entities_free_text_fallback(
                    chunk, extract_entities_rules(chunk)
                )
            )
            _log.warning(
                "segment %d/%d — extraction LLM échouée (%s) → règles (échecs : %d/%d)",
                ci + 1,
                len(chunks),
                exc,
                echecs,
                ECHECS_MAX,
            )

    report["nb_corrections_ocr"] = total_n_corr
    report["duree_extraction_sec"] = round(total_extr, 3) if total_extr else None
    if echecs > 0:
        reasons.insert(0, f"{echecs} segment(s) en échec (coupe-circuit {ECHECS_MAX})")

    if llm_ents:
        # Fusion des segments + dédoublonnage (les entités LLM priment).
        merged = list(llm_ents)
        seen = {(e.valeur.strip().lower(), e.section) for e in merged}
        for e in rules_ents:
            key = (e.valeur.strip().lower(), e.section)
            if key not in seen:
                seen.add(key)
                merged.append(e)
        report["moteur"] = NLP_ENGINE_LLM
        report["statut"] = "llm_complet" if n_regles == 0 else "llm_partiel"
        if n_regles:
            report["raison"] = (
                f"{n_regles}/{len(chunks)} segment(s) replié(s) sur les règles"
            )
        return merged, report

    # Aucune entité LLM → repli règles sur le texte COMPLET (plus complet que
    # le per-segment), tracé dans le rapport.
    _log.info("LLM n'a produit aucune entité — repli règles (hybride)")
    report["moteur"] = NLP_ENGINE_RULES
    report["statut"] = "repli_regles"
    report["raison"] = "; ".join(reasons) if reasons else "Sortie LLM vide"
    return _regles(), report


def extract_entities(text: str, engine: str = "rules") -> list[ExtractedEntity]:
    """Extraction seule (sans rapport) — rétro-compatibilité."""
    return extract_entities_with_report(text, engine=engine)[0]
