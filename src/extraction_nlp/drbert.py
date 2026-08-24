"""Moteur d'extraction d'entités médicales françaises via DrBERT.

DrBERT (biomedical FR, Apache 2.0) + checkpoint NER « DrBERT-MedicalNER-FR »
(token-classification, 23 étiquettes BIO). Le NER est très léger (~150 Mo
quantizé / ~500 Mo fp32) et tourne sur CPU, même sur les postes 4-8 Go sans
GPU — un complément idéal au LLM (machines puissantes) et aux règles (repli).

- Strictement local : modèle téléchargé via transformers (cache Hugging Face)
  — à l'installation, jamais pendant le traitement (art. 9).
- Licence : modèle de base Apache 2.0 ; le checkpoint NER fine-tuné est sous
  OpenRAIL — à valider pour l'annexe 1 (docs/ADR/0010).
- Le NER ÉTIQUETTE : on mappe les étiquettes BIO vers les rubriques du VSM.
"""

from __future__ import annotations

import os

# Modèle NER par défaut (fine-tuné sur DrBERT-7GB). Configurable.
DRBERT_MODEL = os.environ.get("VSM_DRBERT_MODEL", "spideystreet/DrBERT-MedicalNER-FR")

# Étiquettes BIO du NER → rubrique VSM. Les étiquettes hors VSM sont ignorées
# (identité, lieu, organisation, produit…).
_LABEL_MAP = {
    "Disease": "pathologies_actives",  # contexte temporel affiné plus bas
    "Medication/Vaccine": "traitements_long_cours",
    "MedicalProcedure": "antecedents",
    "Symptom": "points_vigilance",
    "CW": "traitements_long_cours",  # substance chimique / médicament
}
# Étiquettes à ignorer
_IGNORED = {"AnatomicalStructure", "PROD", "GRP", "LOC", "ORG", "PER"}

# Confiance des entités DrBERT : sous le seuil 0,7 → « À valider » (XAI).
DRBERT_CONFIDENCE = 0.7
# Nom du moteur pour la provenance (XAI)
NLP_ENGINE_DRBERT = "drbert-nlp-v1"

# Max de tokens par passe (CamemBERT 512) — le texte est découpé en chunks.
_MAX_TOKENS = 512


def is_available() -> bool:
    """DrBERT utilisable ? (torch + transformers + éventuellement le cache)."""
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401

        return True
    except ImportError:
        return False


def map_label(label: str) -> str | None:
    """Étiquette BIO → rubrique VSM (ou None si ignorée). Gère B-/I-."""
    tag = label.split("-")[-1]  # retire le préfixe B-/I-
    if tag in _IGNORED:
        return None
    section = _LABEL_MAP.get(tag)
    if section is None:
        return None
    # Notion d'antécédent : beaucoup de « Disease » sont des antécédents ;
    # on laisse le contexte au post-traitement — par défaut pathologies_actives.
    return section


def label_to_section(label: str, context_window: str = "") -> str | None:
    """Comme map_label mais affiné par le contexte immédiat (antécédent)."""
    section = map_label(label)
    if section == "pathologies_actives" and any(
        w in context_window.lower()
        for w in ("antécédent", "antecedent", "depuis", "historique")
    ):
        return "antecedents"
    return section


def group_bio(tokens: list[str], labels: list[str]) -> list[dict]:
    """Regroupe les mots en entités (fonction pure, testable).

    Le NER est entraîné au niveau du mot mais répète souvent « B- » sur les
    sous-mots : on regroupe donc par ÉTIQUETTE (B-/I- ignorés), en fusionnant
    les mots consécutifs de même étiquette (non-O). Plus robuste que le simple
    B-XX/I-XX*. tokens: liste de mots ; labels: étiquettes BIO alignées.
    Retourne [{text, label, start, end}] avec start/end = indices de mots."""

    def _tag(lab: str) -> str:
        return (
            lab.split("-", 1)[-1] if lab not in ("O", "", None) and "-" in lab else lab
        )

    entities: list[dict] = []
    cur: list[str] = []
    cur_tag, start = "", -1
    for i, (tok, lab) in enumerate(zip(tokens, labels)):
        if lab in ("O", "", None):
            if cur:
                entities.append(
                    {"text": " ".join(cur), "label": cur_tag, "start": start, "end": i}
                )
                cur, cur_tag, start = [], "", -1
            continue
        tag = _tag(lab)
        if tag != cur_tag:
            if cur:
                entities.append(
                    {"text": " ".join(cur), "label": cur_tag, "start": start, "end": i}
                )
            cur, cur_tag, start = [tok], tag, i
        else:
            cur.append(tok)
    if cur:
        entities.append(
            {
                "text": " ".join(cur),
                "label": cur_tag,
                "start": start,
                "end": len(tokens),
            }
        )
    return entities


def _aggregate_subwords(
    tokens: list[str],
    labels: list[str],
    scores: list[float],
    offsets: list[tuple[int, int]],
    word_ids: list[int | None],
) -> list[dict]:
    """Sous-mots → mots, en gardant l'étiquette du 1er sous-mot (le B-).

    Le NER est entraîné au niveau du MOT (pas du sous-mot) : on regroupe les
    tokens ici, puis on regroupe les mots en entités (group_bio). Fonction pure.
    Chaque mot = {text, label, score, start, end, wid} en offsets caractères."""
    words: list[dict] = []
    cur: dict | None = None
    for tok, lab, sc, (s, e), wid in zip(tokens, labels, scores, offsets, word_ids):
        if wid is None or s == e:
            continue  # tokens spéciaux (<s>, </s>) ou vides
        piece = tok.replace("▁", "")
        if cur is not None and cur["wid"] == wid:
            cur["text"] += piece
            cur["end"] = e
            cur["score"] = max(cur["score"], sc)
        else:
            if cur:
                words.append(cur)
            cur = {
                "wid": wid,
                "text": piece,
                "label": lab,
                "score": sc,
                "start": s,
                "end": e,
            }
    if cur:
        words.append(cur)
    return words


def _strip_trailing_punct(text: str) -> str:
    "Retire la ponctuation finale parasite ('.', ',', ';', '…')."
    return text.rstrip(" .,;:!?…\"'»)")


def extract_entities_drbert(text: str) -> list[dict]:
    """Étiquette le texte avec le NER DrBERT et renvoie des dicts d'entités.

    Retourne [{valeur, section, confiance, passage, offset_debut, offset_fin}].
    Nécessite torch + transformers ; le modèle est chargé en cache (module).
    La confiance est la probabilité softmax du label (varsio réelle, pas une
    constante) — sous DRBERT_CONFIDENCE → « À valider » en aval (XAI)."""
    import torch
    from transformers import AutoModelForTokenClassification, AutoTokenizer

    global _model, _tokenizer  # cache module
    if _model is None:
        _tokenizer = AutoTokenizer.from_pretrained(DRBERT_MODEL)
        _model = AutoModelForTokenClassification.from_pretrained(DRBERT_MODEL)
        _model.eval()

    id2label = _model.config.id2label
    tokenizer = _tokenizer
    enc = tokenizer(
        text,
        return_tensors="pt",
        truncation=True,
        max_length=_MAX_TOKENS,
        return_offsets_mapping=True,
    )
    model_input = enc.copy()
    model_input.pop("offset_mapping", None)
    with torch.no_grad():
        logits = _model(**model_input).logits
    probs = torch.softmax(logits, dim=-1)[0]  # [tokens, n_labels]
    preds = probs.argmax(dim=-1).tolist()
    scores = [float(probs[i, p]) for i, p in enumerate(preds)]
    tokens = tokenizer.convert_ids_to_tokens(enc["input_ids"][0])
    labels = [id2label[p] for p in preds]
    offsets = [tuple(o) for o in enc["offset_mapping"][0].tolist()]
    word_ids = enc.word_ids()

    words = _aggregate_subwords(tokens, labels, scores, offsets, word_ids)
    raw = group_bio([w["text"] for w in words], [w["label"] for w in words])
    entities = []
    for ent in raw:
        section = label_to_section(ent["label"])
        if not section:
            continue
        valeur = _strip_trailing_punct(ent["text"])
        if len(valeur) < 2:
            continue
        # offsets caractères du groupe de mots (ent["start"]/["end"] = indices mots)
        w_start, w_end = ent["start"], ent["end"]
        char_start = words[w_start]["start"] if w_start < len(words) else 0
        char_end = (
            words[w_end - 1]["end"]
            if 0 < w_end <= len(words)
            else char_start + len(valeur)
        )
        # confiance = max des mots du groupe
        conf = max(
            (w["score"] for w in words[w_start:w_end]), default=DRBERT_CONFIDENCE
        )
        idx = text.find(valeur, char_start)
        if idx == -1:
            idx = char_start
        entities.append(
            {
                "valeur": valeur,
                "section": section,
                "confiance": round(conf, 3),
                "passage": valeur,
                "offset_debut": idx,
                "offset_fin": idx + len(valeur),
            }
        )
    return entities


_model = None
_tokenizer = None


def main() -> int:
    """python -m src.extraction_nlp.drbert → pré-télécharge le modèle."""
    print(f"Pré-téléchargement de {DRBERT_MODEL}… (transformers, cache HF)")
    from transformers import AutoModelForTokenClassification, AutoTokenizer

    AutoTokenizer.from_pretrained(DRBERT_MODEL)
    AutoModelForTokenClassification.from_pretrained(DRBERT_MODEL)
    print("Modèle DrBERT prêt (cache Hugging Face local).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
