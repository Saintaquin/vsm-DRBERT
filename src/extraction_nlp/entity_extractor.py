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
NLP_ENGINE_LLM = "llama-3.1-8b-q4-local"

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
# Système de prompt : rôle, schéma JSON strict, anti-hallucination,
# normalisation, négations, few-shot. Le moteur règles reste le repli.
# ---------------------------------------------------------------------------
_LLM_SECTIONS_DEF = {
    "pathologies_actives": "maladies actuelles, motifs, diagnostics du jour",
    "antecedents": "maladies passées, interventions chirurgicales, antécédents médicaux/chirurgicaux/familiaux",
    "allergies": "allergies et intolérances (si « aucune allergie » → laisser [])",
    "traitements_long_cours": "traitements au long cours, ordonnances (garder le dosage s'il est écrit)",
    "facteurs_risque": "tabac, alcool, obésité, sédentarité, facteurs de risque cardiovasculaire",
    "vaccinations": "vaccins et rappels (avec la date si présente)",
    "points_vigilance": "conclusions, recommandations, alertes cliniques, points d'attention",
}

_LLM_SYSTEM = (
    "Tu es un assistant médical français qui remplit un Volet de Synthèse "
    "Médicale (VSM) à partir du texte OCR d'un document médical (anonymisé).\n"
    "Règles STRICTES :\n"
    "1. Réponds UNIQUEMENT en JSON valide, exactement ce schéma :\n"
    '{"pathologies_actives": [], "antecedents": [], "allergies": [], '
    '"traitements_long_cours": [], "facteurs_risque": [], "vaccinations": [], '
    '"points_vigilance": []}\n'
    '2. Chaque élément est {"valeur": str, "passage": str} : « passage » '
    "est le texte source REPRODUIT À L'IDENTIQUE (orthographe d'origine, sans "
    "correction) ; « valeur » est l'information normalisée (orthographe "
    "corrigée, dosage conservé).\n"
    "3. N'INVENTE RIEN : si une rubrique est absente du texte, laisse []. "
    "N'ajoute jamais un diagnostic, un traitement ou une donnée qui n'est pas "
    "écrite dans le texte.\n"
    "4. Normalisation : « Diabete de type 2 » → valeur « Diabète de type 2 » ; "
    "« Metformine 1000 mg matin et soir » → valeur identique (dosage conservé).\n"
    "5. Contenu des rubriques :\n"
    + "\n".join(f"   - {k} : {v}" for k, v in _LLM_SECTIONS_DEF.items())
    + "\n"
    "6. Négations : « pas d'allergie », « aucune allergie » → allergies = [] "
    "(sauf si une allergie précise est citée).\n"
    "7. Les pseudonymes ([PATIENT_001], [DATE_NAISSANCE_001]…) ne vont JAMAIS "
    "dans « valeur » ; ignore-les pour les rubriques.\n"
    "8. Si le texte est illisible ou vide, renvoie le schéma avec des listes "
    "vides (jamais de texte libre hors JSON)."
)

_LLM_FEW_SHOT = (
    "\nExemple :\n"
    'Texte : "ANTECEDENTS : Diabete de type 2 depuis 2010. Hypertension.\n'
    "ALLERGIES : Penicilline (eruption cutanee).\n"
    'TRAITEMENTS : Metformine 1000 mg matin et soir."\n'
    'Réponse : {"pathologies_actives": [], "antecedents": '
    '[{"valeur": "Diabète de type 2 depuis 2010", '
    '"passage": "Diabete de type 2 depuis 2010"}, '
    '{"valeur": "Hypertension artérielle", "passage": "Hypertension"}], '
    '"allergies": [{"valeur": "Pénicilline", '
    '"passage": "Penicilline (eruption cutanee)"}], '
    '"traitements_long_cours": [{"valeur": "Metformine 1000 mg matin et '
    'soir", "passage": "Metformine 1000 mg matin et soir"}], '
    '"facteurs_risque": [], "vaccinations": [], "points_vigilance": []}\n'
)


def build_llm_messages(text: str, max_chars: int = 6000) -> list[dict]:
    """Messages système + utilisateur pour l'extraction LLM (fonction pure,
    testable sans GPU). Le texte est tronqué pour rester dans le contexte."""
    user = (
        "Texte OCR du document (extrait) :\n```\n"
        + text[:max_chars]
        + "\n```\nExtrais le JSON des rubriques du VSM en appliquant les règles."
    )
    return [
        {"role": "system", "content": _LLM_SYSTEM + _LLM_FEW_SHOT},
        {"role": "user", "content": user},
    ]


# Confiance des entités LLM : volontairement conservatrice (< seuil 0,7) —
# la sortie d'un LLM doit toujours être relue par le médecin (XAI honnête).
LLM_CONFIDENCE = 0.65


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


def extract_entities_llm(
    text: str, model_path: str | None = None
) -> list[ExtractedEntity]:  # pragma: no cover — nécessite llama-cpp + GGUF
    """Extraction par LLM local (llama-cpp-python + GGUF, jamais en cloud).

    Nécessite un modèle téléchargé : python -m src.extraction_nlp.llm"""
    from llama_cpp import Llama

    from .llm import default_model_path

    model_path = model_path or str(default_model_path())
    llm = Llama(model_path=model_path, n_ctx=8192, verbose=False)
    out = llm.create_chat_completion(
        messages=build_llm_messages(text),
        response_format={"type": "json_object"},
        temperature=0.0,
        max_tokens=2048,
    )
    data = _extract_json_llm(out["choices"][0]["message"]["content"])
    entities = []
    for section, items in data.items():
        if section not in SECTION_HEADERS:
            continue
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            passage = str(it.get("passage", it.get("valeur", "")))
            valeur = str(it.get("valeur", ""))
            if not valeur:
                continue
            idx = text.find(passage)
            if idx == -1:
                idx = max(text.find(valeur), 0)
            entities.append(
                ExtractedEntity(
                    valeur=valeur,
                    section=section,
                    confiance=LLM_CONFIDENCE,
                    passage=passage or valeur,
                    offset_debut=idx,
                    offset_fin=idx + len(passage or valeur),
                    moteur_nlp=NLP_ENGINE_LLM,
                )
            )
    return entities


# Drapeau global : si une inférence LLM dépasse le délai autorisé, on n'essaie
# PLUS le LLM dans cette session (le modèle 2 Go ne serait pas fini de charger
# → évite d'empiler plusieurs tentatives en mémoire). Repli règles ensuite.
_LLM_ABORTED = False


def _extract_llm_with_timeout(text: str, timeout: float):
    """Exécute l'inférence LLM dans un thread daemon et attend `timeout` s.

    Retourne (entités, a_temporisé). Sur timeout, le thread continue en
    arrière-plan (il finira ou mourra) mais on bascule sur les règles —
    jamais de blocage infini sur un poste lent."""
    import threading

    box: dict = {"done": False, "ents": None, "err": None}

    def _run():  # pragma: no cover - nécessite llama.cpp + GGUF
        try:
            box["ents"] = extract_entities_llm(text)
        except Exception as exc:  # noqa: BLE001 - rapporté en cas de timeout
            box["err"] = exc
        finally:
            box["done"] = True

    from .llm import LLM_INFERENCE_TIMEOUT_SEC

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout if timeout else LLM_INFERENCE_TIMEOUT_SEC)
    if not box["done"]:
        return None, True  # durée dépassée
    if box["err"] is not None:
        raise box["err"]
    return box["ents"], False


def extract_entities(text: str, engine: str = "rules") -> list[ExtractedEntity]:
    global _LLM_ABORTED  # déclaration en tête (utilisé ci-dessous)

    if engine == "llm":
        # Exigence : le LLM est TENTÉ sur toutes les machines dès que le modèle
        # est présent (la RAM ne bloque plus). Deux garde-fous évitent le
        # blocage infini : (1) timeout sur l'inférence → repli règles si la
        # machine est trop lente ; (2) si le LLM renvoie une sortie vide, les
        # règles prennent le relais (hybride).
        from .llm import llm_attemptable

        if llm_attemptable() and not _LLM_ABORTED:
            try:
                ents, timed_out = _extract_llm_with_timeout(text, timeout=None)
                if timed_out:
                    _LLM_ABORTED = True  # ne plus retenter cette session
                    _log.warning(
                        "LLM dépassé le délai d'inférence (machine lente) — repli règles"
                    )
                elif ents:
                    return ents
                else:
                    _log.info("LLM a renvoyé une sortie vide — repli règles (hybride)")
                # sortie vide → hybride : on tente les règles ci-dessous
            except Exception:
                _log.warning("Erreur d'inférence LLM — repli règles")
                pass  # repli documenté sur les règles (erreur d'inférence)
        elif not _LLM_ABORTED:
            _log.info("LLM non tenté (modèle absent) — moteur de règles")
    return extract_entities_free_text_fallback(text, extract_entities_rules(text))
