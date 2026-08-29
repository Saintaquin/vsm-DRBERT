"""Adaptateur DrBERT-CASM2 — extraction d'entités par ENCODEUR (étape 1).

Remplace le LLM génératif comme moteur d'extraction principal (décision de
l'étape 0, banc d'essai tools/eval_drbert.py) : un encodeur ne GENERE pas de
texte, il ÉTIQUETTE des tokens — donc, par construction :

- aucune hallucination possible (toute valeur est un extrait du texte source) ;
- ancrage XAI exact et gratuit (offsets caractères fournis par le modèle) ;
- plus de JSON à parser (plus aucune troncature).

Modèle : medkit/DrBERT-CASM2 (NER français, étiquettes « problem »,
« treatment », « test », licence MIT — base Dr-BERT Apache 2.0).

Choix d'inférence (mesurés à l'étape 0) : torch fp32, fenêtres de 512 tokens
avec recouvrement de 64, threads = cœurs PHYSIQUES. ONNX int8 est ÉCARTÉ pour
l'instant : plus lent que torch sur le poste de référence et non neutre sur
les prédictions — la structure ci-dessous (id2label lu dans config.json,
décodage par table) permet de le rebrancher plus tard sans refonte.

Filtres appliqués à la sortie (décisions étape 0) :
- BORDS DE MOTS obligatoire : une entité qui commence ou finit au milieu d'un
  mot (« fra » dans « fraude ») est un artefact de sous-mot hors domaine ;
- score softmax >= 0,70 (VSM_DRBERT_MIN_SCORE) ;
- étiquette « test » ÉCARTÉE par défaut (VSM_DRBERT_KEEP_TESTS=1 pour la
  garder : elle alimente alors « points_vigilance » via rubriques.py) ;
- aucune entité ne peut chevaucher un jeton d'anonymisation ([PATIENT_001]…).

Dégradation propre : modèle absent ou illisible → DrBERTIndisponible, que
l'appelant traite (repli règles tracé) — jamais de plantage du traitement.

Voie d'évolution : le passage de 3 étiquettes à 9 (fine-tune sur les 7
rubriques du VSM) ne demandera qu'un changement de modèle et la table de
correspondance de rubriques.py — aucune refonte de ce module.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger("vsm")

# ---------------------------------------------------------------------------
# Constantes et configuration (variables d'environnement lues À CHAQUE appel,
# pour rester testables sans rechargement de module)
# ---------------------------------------------------------------------------

# Fenêtrage (décision étape 0 : 512 tokens, recouvrement 64).
MAX_LENGTH = 512
STRIDE = 64

# Seuil de confiance (décision étape 0) ; VSM_DRBERT_MIN_SCORE pour ajuster.
SEUIL_SCORE_DEFAUT = 0.70

# Fichiers attendus dans le dossier modèle local (aucun réseau ensuite).
FICHIERS_MODELE = ("config.json", "model.safetensors", "tokenizer_config.json")

# Jetons d'anonymisation : [PATIENT_001], [DATE_NAISSANCE_001], [REDACTED:…]…
_RX_JETONS = re.compile(r"\[(?:REDACTED[^\]]*|[A-Z][A-Z0-9_]*_\d+)\]")

_VRAIS = ("1", "true", "vrai", "oui", "yes", "on")


class DrBERTIndisponible(RuntimeError):
    """Modèle DrBERT absent, incomplet ou illisible — l'appelant doit basculer
    sur un autre moteur (repli règles tracé), jamais planter le traitement."""


@dataclass
class Entite:
    """Entité NER : toujours un extrait EXACT du texte source (offsets).

    « texte » n'est jamais reconstruit ni reformulé : c'est
    ``texte_source[debut:fin]`` — garantie anti-hallucination, vérifiée dans
    les tests (test_drbert.py) alors même qu'elle est structurelle.
    """

    label: str
    texte: str
    debut: int
    fin: int
    score: float

    @property
    def longueur(self) -> int:
        return self.fin - self.debut


# ---------------------------------------------------------------------------
# Fonctions pures (fenêtrage, décodage BIO, fusion) — testables sans modèle
# ---------------------------------------------------------------------------


def _type_de_label(label: str) -> str | None:
    """« B-problem » → « problem » ; « O » / « outside » → None (hors entité).

    Tolérant à la casse et aux schémas sans préfixe B-/I- (évolutivité : un
    futur checkpoint à 9 étiquettes sera décodé sans changement de code).
    """
    lab = label.strip().lower()
    if lab in ("", "o", "outside"):
        return None
    if len(lab) > 2 and lab[1] == "-" and lab[0] in ("b", "i"):
        return lab[2:] or None
    return lab


def _est_debut_b(label: str) -> bool:
    """Vrai si l'étiquette commence une nouvelle mention (« B-… »)."""
    return label.strip().lower().startswith("b-")


def bords_alignes(texte: str, debut: int, fin: int) -> bool:
    """L'entité commence et finit-elle sur des bords de mots ?

    Filtre OBLIGATOIRE (décision étape 0) : une entité « fra » extraite de
    « fraude » est un fragment de sous-mot, symptôme d'étiquetage instable sur
    du texte OCR hors domaine.
    """
    avant = texte[debut - 1] if debut > 0 else " "
    apres = texte[fin] if fin < len(texte) else " "
    return not (avant.isalnum() or apres.isalnum())


def decoder_fenetre(
    texte: str,
    offsets: list[tuple[int, int]],
    types: list[str | None],
    scores: list[float],
    debuts_b: list[bool],
) -> list[Entite]:
    """Décode les étiquettes d'UNE fenêtre en entités BIO (fonction pure).

    ``offsets``/``types``/``scores``/``debuts_b`` : quatre listes alignées
    token par token (les tokens spéciaux ont un offset (0, 0) et sont ignorés).
    Le décodage est IOB2 : une étiquette « B- » commence toujours une nouvelle
    mention ; la fusion inter-fenêtres recoud ensuite les découpes. Les
    offsets renvoyés sont ABSOLUS dans ``texte`` (le texte du document, pas
    la fenêtre) car le tokenizer fournit des offsets du texte complet.
    """
    entites: list[Entite] = []
    type_courant: str | None = None
    debut, fin = 0, 0
    scores_courants: list[float] = []

    def _clore() -> None:
        nonlocal type_courant
        if type_courant is not None and fin > debut:
            entites.append(
                Entite(
                    label=type_courant,
                    texte=texte[debut:fin],
                    debut=debut,
                    fin=fin,
                    score=sum(scores_courants) / len(scores_courants),
                )
            )
        type_courant = None

    for t, (s, e) in enumerate(offsets):
        if s == e:
            continue  # token spécial (<s>, </s>) : aucun texte associé
        typ = types[t] if t < len(types) else None
        if typ is None:
            _clore()
            continue
        nouvelle_mention = (
            type_courant is None
            or typ != type_courant
            or (t < len(debuts_b) and debuts_b[t] and s >= fin)
        )
        if nouvelle_mention:
            _clore()
            type_courant, debut, fin = typ, s, e
            scores_courants = [scores[t] if t < len(scores) else 0.0]
        else:
            fin = e
            scores_courants.append(scores[t] if t < len(scores) else 0.0)
    _clore()
    return entites


def fusionner(entites: list[Entite], texte: str) -> list[Entite]:
    """Recoud les entités d'un même type coupées au bord d'une fenêtre.

    Fusionne deux entités de même type qui se chevauchent ou ne sont séparées
    que par ≤ 2 caractères d'espacement ; une entité incluse dans une autre
    disparaît (déduplication du recouvrement des fenêtres — une entité à
    cheval sur deux fenêtres n'apparaît donc QU'UNE fois). Le score fusionné
    est la moyenne des scores pondérée par la longueur.
    """
    finales: list[Entite] = []
    for label in sorted({e.label for e in entites}):
        groupe = sorted(
            (e for e in entites if e.label == label), key=lambda e: (e.debut, e.fin)
        )
        courante: Entite | None = None
        for ent in groupe:
            if courante is None:
                courante = ent
                continue
            if ent.fin <= courante.fin:
                continue  # incluse : doublon du recouvrement de fenêtres
            ecart = ent.debut - courante.fin
            collage = ecart <= 0 or (
                ecart <= 2 and texte[courante.fin : ent.debut].strip() == ""
            )
            if collage:
                poids = courante.longueur + ent.longueur
                courante = Entite(
                    label=label,
                    texte=texte[courante.debut : ent.fin],
                    debut=courante.debut,
                    fin=ent.fin,
                    score=(
                        courante.score * courante.longueur
                        + ent.score * ent.longueur
                    )
                    / poids,
                )
            else:
                finales.append(courante)
                courante = ent
        if courante is not None:
            finales.append(courante)
    finales.sort(key=lambda e: (e.debut, e.fin))
    return finales


# ---------------------------------------------------------------------------
# Configuration lue à l'appel (testable)
# ---------------------------------------------------------------------------


def dossier_modele() -> Path:
    """Dossier du modèle local : $VSM_DRBERT_PATH, sinon models/drbert/."""
    brut = os.environ.get("VSM_DRBERT_PATH", "").strip()
    if brut:
        return Path(brut)
    return Path(__file__).resolve().parents[2] / "models" / "drbert"


def seuil_score() -> float:
    """Seuil de confiance minimal (défaut 0,70 ; VSM_DRBERT_MIN_SCORE)."""
    try:
        return float(os.environ.get("VSM_DRBERT_MIN_SCORE", SEUIL_SCORE_DEFAUT))
    except ValueError:
        return SEUIL_SCORE_DEFAUT


def garder_tests() -> bool:
    """VSM_DRBERT_KEEP_TESTS : garder les entités « test » (défaut : non)."""
    return os.environ.get("VSM_DRBERT_KEEP_TESTS", "").strip().lower() in _VRAIS


def modele_disponible(dossier: Path | None = None) -> bool:
    """Le dossier contient-il un modèle complet et lisible ?"""
    cible = Path(dossier) if dossier is not None else dossier_modele()
    return all((cible / f).is_file() for f in FICHIERS_MODELE)


_DEPS: tuple[bool, str] | None = None


def _dependances() -> tuple[bool, str]:
    """Dépendances d'inférence importables DANS CET INTERPRÉTEUR ?

    Importe AUSSI les classes réellement utilisées et la chaîne
    ``torch._dynamo`` (importée PARESSEUSEMENT par transformers au premier
    ``from_pretrained``). Sans ce pré-chauffage, cette chaîne s'importe pour
    la première fois dans le FIL DU WORKER au milieu du traitement : si un
    autre fil importe torch simultanément, ``torch._dynamo.utils`` peut être
    vu PARTIELLEMENT initialisé (import circulaire utils↔config) — son
    ``except ImportError: pass`` avale alors l'erreur et laisse
    ``NP_SUPPORTED_MODULES`` indéfini, d'où un tardif « cannot import name
    NP_SUPPORTED_MODULES » au chargement du modèle. Pré-chauffé ici,
    mono-thread au démarrage, tous les imports ultérieurs du worker sont des
    no-ops dans ``sys.modules`` : la défaillance disparaît par construction.

    Résultat mis en cache : l'import de torch coûte plusieurs secondes
    quand il réussit (et échoue instantanément quand il manque). C'est le
    piège classique « python ≠ py -3.12 » sous Windows : le modèle est
    présent, mais l'interpréteur de lancement n'a pas torch.
    """
    global _DEPS
    if _DEPS is None:
        try:
            import torch
            import torch._dynamo.eval_frame  # noqa: F401
            import transformers  # noqa: F401
            from transformers import (  # noqa: F401
                AutoModelForTokenClassification,
                AutoTokenizer,
            )
        except ImportError as exc:
            raison = (
                f"dépendance absente de CET interpréteur ({exc}) — "
                "lancer avec py -3.12 -m src.ui_backend.main sous Windows"
            )
            _DEPS = (False, raison)
        else:
            _DEPS = (True, "")
    return _DEPS


def execution_possible(dossier: Path | None = None) -> tuple[bool, str]:
    """DrBERT peut-il VRAIMENT tourner : fichiers du modèle ET dépendances ?

    Retourne (ok, raison) — la raison est vide quand tout va bien. Contraire-
    ment à ``modele_disponible`` (fichiers seuls), cette vérification reflète
    ce que ``/health`` et la dérivation de moteur doivent annoncer AVANT un
    traitement : un modèle présent dans un interpréteur sans torch ne tourne
    jamais.
    """
    if not modele_disponible(dossier):
        cible = dossier_modele() if dossier is None else dossier
        raison = f"modèle absent de {cible} (VSM_DRBERT_PATH)"
        return (False, raison)
    return _dependances()


# ---------------------------------------------------------------------------
# Moteur (chargement paresseux, SINGLETON, strictement local)
# ---------------------------------------------------------------------------


class _MoteurDrBERT:
    """DrBERT-CASM2 chargé depuis un dossier LOCAL, sans aucun accès réseau.

    torch fp32 (décision étape 0), threads = cœurs PHYSIQUES. Le tokenizer
    DOIT être fast : les offsets caractères sont la garantie d'ancrage XAI.
    """

    def __init__(self, dossier: Path) -> None:
        import torch
        from transformers import AutoModelForTokenClassification, AutoTokenizer

        self._torch = torch
        torch.set_num_threads(_coeurs_physiques())

        self.tokenizer = AutoTokenizer.from_pretrained(str(dossier), local_files_only=True)
        if not getattr(self.tokenizer, "is_fast", False):
            raise DrBERTIndisponible(
                "tokenizer lent (sans offsets) : ancrage exact impossible"
            )
        self._modele = AutoModelForTokenClassification.from_pretrained(
            str(dossier), local_files_only=True
        )
        self._modele.eval()

        # Tables id → type / « commence une mention », lues dans config.json :
        # indépendantes du backend et prêtes pour un futur modèle à 9 étiquettes.
        cfg = json.loads((dossier / "config.json").read_text(encoding="utf-8"))
        id2label = {int(k): v for k, v in cfg.get("id2label", {}).items()}
        self.id2label = id2label
        self._types = [_type_de_label(id2label.get(i, "O")) for i in range(len(id2label))]
        self._debuts_b = [_est_debut_b(id2label.get(i, "")) for i in range(len(id2label))]
        self.nom_modele = "medkit/DrBERT-CASM2"

    def annoter(self, texte: str) -> list[Entite]:
        """Étiquette ``texte`` par fenêtres 512/64 et fusionne les recousures.

        Le score de chaque entité est la moyenne des probabilités softmax de
        ses tokens — une vraie mesure du modèle, pas une constante.
        """
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with _VERROU:  # inférence sérialisée (jobs concurrents du backend)
                enc = self.tokenizer(
                    texte,
                    return_offsets_mapping=True,
                    return_attention_mask=True,
                    truncation=True,
                    max_length=MAX_LENGTH,
                    stride=STRIDE,
                    return_overflowing_tokens=True,
                )
                entites: list[Entite] = []
                for i in range(len(enc["input_ids"])):
                    logits = self._inference_fenetre(enc, i)
                    probs = self._torch.softmax(logits, dim=-1)[0]
                    preds = probs.argmax(dim=-1).tolist()
                    offsets = [tuple(o) for o in enc["offset_mapping"][i]]
                    types = [
                        self._types[p] if p < len(self._types) else None for p in preds
                    ]
                    scores = [float(probs[t, p]) for t, p in enumerate(preds)]
                    debuts_b = [
                        self._debuts_b[p] if p < len(self._debuts_b) else False
                        for p in preds
                    ]
                    entites.extend(
                        decoder_fenetre(texte, offsets, types, scores, debuts_b)
                    )
        return fusionner(entites, texte)

    def _inference_fenetre(self, enc: dict, i: int):
        """Une passe avant sur la fenêtre i ; renvoie les logits [1, T, L]."""
        torch = self._torch
        entrees = {
            "input_ids": torch.tensor([enc["input_ids"][i]], dtype=torch.long),
            "attention_mask": torch.tensor(
                [enc["attention_mask"][i]], dtype=torch.long
            ),
        }
        with torch.no_grad():
            return self._modele(**entrees).logits


_VERROU = threading.Lock()
_MOTEUR: _MoteurDrBERT | None = None


def _coeurs_physiques() -> int:
    """Nombre de cœurs PHYSIQUES (psutil si présent, sinon repli logiques)."""
    try:
        import psutil

        n = psutil.cpu_count(logical=False)
        if n:
            return n
    except ImportError:
        pass
    return os.cpu_count() or 1


def _charger() -> _MoteurDrBERT:
    """Charge le modèle depuis le dossier local — ou lève DrBERTIndisponible."""
    dossier = dossier_modele()
    if not modele_disponible(dossier):
        raise DrBERTIndisponible(
            f"modèle DrBERT-CASM2 absent de {dossier} — le récupérer avec "
            "`py -3.12 packaging/fetch_models.py` (fabrication de l'installeur) "
            "ou `py -3.12 tools/eval_drbert.py --input <dossier> --download` "
            "(développement), ou pointer VSM_DRBERT_PATH"
        )
    try:
        return _MoteurDrBERT(dossier)
    except DrBERTIndisponible:
        raise
    except Exception as exc:  # dossier présent mais illisible → repli, pas crash
        # Pile complète dans le journal : un simple str(exc) a déjà masqué
        # la vraie cause d'un échec d'import torch/transformers (ex. course
        # d'initialisation torch._dynamo). Sans PII : chemins de code seuls.
        _log.exception("chargement DrBERT échoué — repli règles (%s)", exc)
        raise DrBERTIndisponible(f"modèle DrBERT illisible ({exc})") from exc


def _get_moteur() -> _MoteurDrBERT:
    """Singleton : chargé UNE fois par processus (les tests peuvent l'écraser)."""
    global _MOTEUR
    if _MOTEUR is None:
        _MOTEUR = _charger()
    return _MOTEUR


def reinitialiser() -> None:
    """Force le rechargement du modèle au prochain appel (tests)."""
    global _MOTEUR
    _MOTEUR = None


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------


def extraire_entites(texte: str, journal: list | None = None) -> list[Entite]:
    """Extrait les entités DrBERT-CASM2, filtres de l'étape 0 appliqués.

    - offsets ABSOLUS dans ``texte`` (le texte du document) ;
    - score = probabilité softmax réelle du modèle ;
    - filtres : bords de mots (obligatoire), score >= seuil, étiquette
      « test » écartée sauf VSM_DRBERT_KEEP_TESTS, aucun chevauchement avec
      un jeton d'anonymisation.

    ``journal`` (optionnel) : chaque entité écartée y est TRACÉE avec la
    règle responsable (filtres_vsm.tracer_rejet) — plus jamais de rejet
    invisible. Les valeurs viennent du texte anonymisé : aucune PII dans le
    journal. ``journal=None`` → comportement inchangé.

    Lève DrBERTIndisponible si le modèle est absent/illisible — jamais de
    plantage silencieux : l'appelant bascule et le trace.
    """
    from .filtres_vsm import tracer_rejet

    if not texte or not texte.strip():
        return []
    moteur = _get_moteur()
    entites = moteur.annoter(texte)

    seuil = seuil_score()
    garder = garder_tests()
    jetons = [(m.start(), m.end()) for m in _RX_JETONS.finditer(texte)]

    gardees: list[Entite] = []
    for ent in entites:
        if ent.score < seuil:
            tracer_rejet(
                journal, ent.texte, ent.score, "extracteur_score",
                f"score {ent.score:.2f} < seuil {seuil:.2f}",
                offset_debut=ent.debut,
            )
            continue
        if not bords_alignes(texte, ent.debut, ent.fin):
            tracer_rejet(
                journal, ent.texte, ent.score, "extracteur_bord_mot",
                "entité au milieu d'un mot (fragment de sous-mot)",
                offset_debut=ent.debut,
            )
            continue
        if ent.label == "test" and not garder:
            tracer_rejet(
                journal, ent.texte, ent.score, "extracteur_label_test",
                "étiquette « test » écartée (VSM_DRBERT_KEEP_TESTS pour garder)",
                offset_debut=ent.debut,
            )
            continue
        if any(ent.debut < fin_j and debut_j < ent.fin for debut_j, fin_j in jetons):
            tracer_rejet(
                journal, ent.texte, ent.score, "extracteur_anonymisation",
                "chevauche un jeton d'anonymisation",
                offset_debut=ent.debut,
            )
            continue
        gardees.append(ent)
    if len(gardees) < len(entites):
        # Aucune PII dans les logs : compteurs uniquement (le détail par
        # entité, sans PII, est dans le journal des rejets).
        _log.debug(
            "drbert : %d entité(s) brute(s) → %d après filtres (seuil=%.2f, "
            "tests=%s)",
            len(entites),
            len(gardees),
            seuil,
            garder,
        )
    return gardees


def nom_moteur() -> str:
    """Nom du moteur pour la traçabilité XAI (identifiant stable)."""
    return "drbert-casm2-v1"
