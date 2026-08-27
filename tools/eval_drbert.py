"""Banc d'essai DrBERT-CASM2 sur textes OCR bruités — ÉTAPE 0 (go / no-go).

Décision d'architecture étudiée : remplacer l'extraction LLM génératif par un
encodeur DrBERT. Ce script mesure, AVANT toute intégration, si DrBERT-CASM2
tient sur des documents réellement bruités — sinon le reste est inutile.

- Modèle : https://huggingface.co/medkit/DrBERT-CASM2 — NER français, 3 types
  (« problem », « treatment », « test »), licence MIT (fiche HF), base Dr-BERT
  Apache 2.0 : compatible avec la règle du projet (MIT / BSD / Apache).
- Chaque document du dossier d'entrée est traité DEUX FOIS : texte brut, puis
  texte passé par la correction lexicale déterministe du projet
  (src/extraction_nlp/correcteur.py, ~1 ms — remet le texte dans le domaine
  d'entraînement du modèle).
- Affiche pour chaque entité : type, texte, offsets caractères, score softmax.
- Mesure le temps par document (tokenisation / inférence / post-traitement) et
  le débit en tokens/s.
- Tableau comparatif brut / corrigé : nombre d'entités, répartition par type,
  longueur moyenne, distribution des scores.
- Écrit DEUX CSV : détail des entités (pour compter à la main les vrais et
  faux positifs — colonnes « vrai_positif » et « commentaire » à remplir) et
  synthèse par document variante par variante.

Risque principal instrumenté : DrBERT-CASM2 a été fine-tuné sur des cas
cliniques PROPRES (CASM2) ; la tokenisation en sous-mots d'un OCR bruité
(« Consukations Maladies du Foie », « ASPECT DE BULBITE CHRONIGUE NON
SPECIFIQGUE ») est hors domaine. Deux issues opposées — le modèle n'étiquette
rien (comportement souhaitable), ou il étiquette au hasard — et seul ce banc
d'essai le dira.

⚠ Confidentialité : le CSV de détail et la console affichent des extraits du
texte source (c'est leur but : relecture humaine). Fichiers de travail
locaux, à supprimer après analyse ; aucune PII ne doit en sortir.

Usage (depuis la racine du dépôt, Python 3.12 avec torch + transformers) :

    py -3.12 tools/eval_drbert.py --input outputs/contexte_ocr --download
    py -3.12 tools/eval_drbert.py --input mes_documents --backend onnx
    py -3.12 tools/eval_drbert.py --input mes_documents --max-length 128

Backend ONNX (optionnel) : py -3.12 -m pip install "optimum[onnxruntime]"
"onnxruntime==1.20.1" — la quantification int8 dynamique N'EST PAS neutre sur
les prédictions : comparer les deux CSV (torch vs onnx) fait partie du
diagnostic ; sur la machine de dev qui a validé ce script, torch fp32 était
plus RAPIDE que ONNX int8 (8 cœurs) — mesurer sur le poste cible avant de
décider (étape 1).

Le modèle est lu dans models/drbert/ (ou $VSM_DRBERT_PATH) ; le téléchargement
n'a lieu qu'avec --download, jamais implicitement. L'application elle-même
reste 100 % locale : ce script est un outil de développement, l'équivalent
embarqué de son chargement sera vendorisé à la fabrication de l'installeur
(packaging/fetch_models.py, étape 4).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import statistics
import sys
import time
import warnings
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# medkit/DrBERT-CASM2 : NER français (corpus CASM2), 3 types d'entités,
# licence MIT (fiche Hugging Face) — compatible MIT/BSD/Apache du projet.
MODEL_REPO = "medkit/DrBERT-CASM2"
TYPES_ATTENDUS = ("problem", "treatment", "test")

VARIANTE_BRUT = "brut"
VARIANTE_CORRIGE = "corrige"
VARIANTES = (VARIANTE_BRUT, VARIANTE_CORRIGE)

# Fichiers attendus dans le dossier modèle local (aucun accès réseau ensuite).
_FICHIERS_MODELE = ("config.json", "model.safetensors", "tokenizer_config.json")
_FICHIERS_TOKENIZER = ("tokenizer.json", "vocab.txt", "special_tokens_map.json")

# Commandes d'installation des dépendances optionnelles (env. Python 3.12).
# NB ONNX : optimum 2.x sépare son intégration ORT dans « optimum-onnx »
# (extra « optimum[onnxruntime] »). onnxruntime 1.29 exige ml_dtypes, dont la
# DLL peut être bloquée par une stratégie de contrôle d'application Windows —
# la combinaison éprouvée ci-dessous évite ce piège.
_PIP_INSTALL = {
    "torch": "py -3.12 -m pip install torch --index-url "
    "https://download.pytorch.org/whl/cpu",
    "transformers": 'py -3.12 -m pip install "transformers>=4.53,<5"',
    "optimum": 'py -3.12 -m pip install "optimum[onnxruntime]" "onnxruntime==1.20.1"',
    "optimum.onnxruntime": 'py -3.12 -m pip install "optimum[onnxruntime]" '
    '"onnxruntime==1.20.1"',
    "onnxruntime": 'py -3.12 -m pip install "onnxruntime==1.20.1"',
    "huggingface_hub": "py -3.12 -m pip install huggingface_hub",
}


@dataclass
class Entite:
    """Entité NER : toujours un extrait EXACT du texte source (offsets).

    « texte » n'est JAMAIS reconstruit ni reformulé : c'est
    ``texte_source[debut:fin]`` — garantie anti-hallucination, vérifiée à
    l'exécution (voir MoteurDrBERT.annoter).
    """

    label: str
    texte: str
    debut: int
    fin: int
    score: float

    @property
    def longueur(self) -> int:
        return self.fin - self.debut


@dataclass
class ResultatVariante:
    """Résultat d'un document pour une variante (brut ou corrigé)."""

    entites: list[Entite]
    metriques: dict
    texte: str


# ---------------------------------------------------------------------------
# Petites briques
# ---------------------------------------------------------------------------


def _configurer_console() -> None:
    """Console en UTF-8 (PowerShell sous Windows casse les accents sinon)."""
    for flux in (sys.stdout, sys.stderr):
        try:
            flux.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


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


def _type_de_label(label: str) -> str | None:
    """« B-problem » → « problem » ; « O » / « outside » → None (hors entité).

    Tolérant à la casse et aux schémas sans préfixe B-/I-.
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


def _bords_alignes(texte: str, debut: int, fin: int) -> bool:
    """L'entité commence et finit-elle sur des bords de mots ?

    Une entité « fra » extraite de « fraude » est un fragment de sous-mot :
    symptôme d'étiquetage instable sur du texte hors domaine (OCR bruité).
    Compter ces fragments fait partie du diagnostic go / no-go — un filtre
    « bords de mots » dans le validateur de l'étape 3 pourra les éliminer.
    """
    avant = texte[debut - 1] if debut > 0 else " "
    apres = texte[fin] if fin < len(texte) else " "
    return not (avant.isalnum() or apres.isalnum())


def _ligne_install(exc: ImportError) -> str:
    cmd = _PIP_INSTALL.get(exc.name or "", f"py -3.12 -m pip install {exc.name}")
    return f"Dépendance manquante ({exc.name}). Installer : {cmd}"


# ---------------------------------------------------------------------------
# Moteur DrBERT
# ---------------------------------------------------------------------------


class MoteurDrBERT:
    """DrBERT-CASM2 chargé depuis un dossier LOCAL, sans aucun accès réseau.

    Backends :
    - ``torch``  : PyTorch fp32 (référence de mesure, comme le benchmark
      « 512 tokens → 755 ms » cité dans l'étude) ;
    - ``onnx``   : ONNX Runtime via optimum, quantification dynamique int8
      (la cible de production — repli torch prévu à l'étape 1).

    Le texte est découpé en fenêtres de ``max_length`` tokens avec un
    recouvrement de ``stride`` tokens ; les entités coupées au bord d'une
    fenêtre sont recousues par fusion. Les offsets renvoyés sont des offsets
    ABSOLUS dans le texte du document (pas dans la fenêtre).
    """

    def __init__(
        self,
        dossier: Path,
        backend: str = "torch",
        onnx_int8: bool = True,
        threads: int = 0,
        max_length: int = 512,
        stride: int = 64,
    ) -> None:
        if threads <= 0:
            threads = _coeurs_physiques()
        self.threads = threads
        self.max_length = max_length
        self.stride = stride
        self.backend = backend

        import torch  # torch est requis par les deux backends (tenseurs)

        self._torch = torch

        from transformers import AutoTokenizer
        from transformers.utils import logging as journal_hf

        # L'avertissement « sequence length > model_max_length (128) » est
        # attendu : les fenêtres sont explicites (max_length), le tokenizer
        # CASM2 déclare 128 par défaut. On le tait pour garder une lisible.
        journal_hf.set_verbosity_error()

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.tokenizer = AutoTokenizer.from_pretrained(
                str(dossier), local_files_only=True
            )
        if not getattr(self.tokenizer, "is_fast", False):
            raise RuntimeError(
                "tokenizer lent (sans offsets) : impossible de garantir "
                "l'ancrage exact des entités — utiliser un tokenizer fast."
            )

        # id2label lus dans config.json, indépendamment du backend.
        cfg = json.loads((dossier / "config.json").read_text(encoding="utf-8"))
        id2label = {int(k): v for k, v in cfg.get("id2label", {}).items()}
        self.id2label = id2label
        self._types = [_type_de_label(id2label.get(i, "O")) for i in range(len(id2label))]
        self._debuts_b = [_est_debut_b(id2label.get(i, "")) for i in range(len(id2label))]

        if backend == "onnx":
            self._modele = _charger_onnx(dossier, int8=onnx_int8, threads=threads)
        else:
            from transformers import AutoModelForTokenClassification

            torch.set_num_threads(threads)
            self._modele = AutoModelForTokenClassification.from_pretrained(
                str(dossier), local_files_only=True
            )
            self._modele.eval()

        # Étiquettes inattendues (p.ex. changement de checkpoint à l'avenir) :
        # prévenir plutôt que laisser des colonnes vides inexplicables.
        inattendus = {
            t for t in self._types if t is not None and t not in TYPES_ATTENDUS
        }
        if inattendus:
            print(
                f"ATTENTION : étiquettes hors {TYPES_ATTENDUS} rencontrées : "
                f"{sorted(inattendus)} — elles seront mesurées telles quelles."
            )

    # -- inférence ----------------------------------------------------------

    def annoter(self, texte: str) -> tuple[list[Entite], dict]:
        """Étiquette ``texte`` ; renvoie (entités fusionnées, métriques).

        Le score de chaque entité est la moyenne des probabilités softmax de
        ses tokens — une vraie mesure du modèle, pas une constante.
        """
        debut_t = time.perf_counter()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            enc = self.tokenizer(
                texte,
                return_offsets_mapping=True,
                return_attention_mask=True,
                truncation=True,
                max_length=self.max_length,
                stride=self.stride,
                return_overflowing_tokens=True,
            )
        duree_tokenisation = time.perf_counter() - debut_t

        # Nombre de sous-mots du document complet (hors recouvrements), pour
        # information — hors chronométrage.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tokens_document = len(self.tokenizer(texte, add_special_tokens=False)[
                "input_ids"
            ])

        nb_fenetres = len(enc["input_ids"])
        debut_t = time.perf_counter()
        logits = [self._inference_fenetre(enc, i) for i in range(nb_fenetres)]
        duree_inference = time.perf_counter() - debut_t

        debut_t = time.perf_counter()
        entites: list[Entite] = []
        tokens_traites = 0
        for i in range(nb_fenetres):
            entites.extend(self._decoder_fenetre(texte, enc, i, logits[i]))
            tokens_traites += sum(1 for s, e in enc["offset_mapping"][i] if s != e)
        entites = fusionner(entites, texte)
        duree_post = time.perf_counter() - debut_t

        # Garantie anti-hallucination, VÉRIFIÉE et pas seulement supposée :
        # toute valeur doit être un extrait exact du texte donné au modèle.
        for ent in entites:
            if texte[ent.debut : ent.fin] != ent.texte:
                raise RuntimeError(
                    f"offsets incohérents ({ent.label} {ent.debut}-{ent.fin}) : "
                    "bug interne du banc d'essai — résultats non exploitables."
                )

        metriques = {
            "caracteres": len(texte),
            "nb_fenetres": nb_fenetres,
            "tokens_document": tokens_document,
            "tokens_traites": tokens_traites,
            "duree_tokenisation_s": duree_tokenisation,
            "duree_inference_s": duree_inference,
            "duree_post_s": duree_post,
        }
        return entites, metriques

    def _inference_fenetre(self, enc: dict, i: int):
        """Une passe avant sur la fenêtre i ; renvoie les logits [1, T, L]."""
        torch = self._torch
        entrees = {
            "input_ids": torch.tensor([enc["input_ids"][i]], dtype=torch.long),
            "attention_mask": torch.tensor(
                [enc["attention_mask"][i]], dtype=torch.long
            ),
        }
        if self.backend == "onnx":
            return self._modele(**entrees).logits
        with torch.no_grad():
            return self._modele(**entrees).logits

    def _decoder_fenetre(self, texte: str, enc: dict, i: int, logits) -> list[Entite]:
        """Logits d'une fenêtre → entités BIO en offsets ABSOLUS du document.

        Les tokens spéciaux (<s>, </s>) ont un offset (0, 0) : ignorés. Une
        étiquette « B- » commence toujours une nouvelle mention (IOB2) ; la
        fusion inter-fenêtres recoud ensuite les découpes éventuelles.
        """
        probs = self._torch.softmax(logits, dim=-1)[0]  # [T, nb_labels]
        preds = probs.argmax(dim=-1).tolist()
        scores = [float(probs[t, p]) for t, p in enumerate(preds)]

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

        for t, (s, e) in enumerate(enc["offset_mapping"][i]):
            if s == e:
                continue  # token spécial (<s>, </s>) : aucun texte associé
            p = preds[t] if t < len(preds) else 0
            typ = self._types[p] if p < len(self._types) else None
            if typ is None:
                _clore()
                continue
            nouvelle_mention = (
                type_courant is None
                or typ != type_courant
                or (self._debuts_b[p] and s >= fin)
            )
            if nouvelle_mention:
                _clore()
                type_courant, debut, fin = typ, s, e
                scores_courants = [scores[t]]
            else:
                fin = e
                scores_courants.append(scores[t])
        _clore()
        return entites


def fusionner(entites: list[Entite], texte: str) -> list[Entite]:
    """Recoud les entités d'un même type coupées au bord d'une fenêtre.

    Fusionne deux entités de même type qui se chevauchent ou ne sont séparées
    que par ≤ 2 caractères d'espacement ; une entité incluse dans une autre
    disparaît (déduplication du recouvrement des fenêtres). Le score fusionné
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
# Chargement / export du modèle
# ---------------------------------------------------------------------------


def _modele_present(dossier: Path) -> bool:
    """Le dossier contient-il un modèle complet et lisible ?"""
    return all((dossier / f).is_file() for f in _FICHIERS_MODELE)


def _telecharger(dossier: Path) -> None:
    """Télécharge medkit/DrBERT-CASM2 vers le dossier local (UNE fois).

    Outil de développement : l'application ne télécharge jamais à l'exécution
    (contrainte 100 % local) ; en production le modèle est vendorisé à la
    fabrication de l'installeur (packaging/fetch_models.py, étape 4).
    """
    from huggingface_hub import snapshot_download

    print(f"Téléchargement de {MODEL_REPO} vers {dossier} …")
    dossier.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=MODEL_REPO,
        local_dir=str(dossier),
        allow_patterns=["*.json", "*.txt", "*.md", "*.yml", "model.safetensors"],
    )
    print("Téléchargement terminé (model.safetensors ; le .bin redondant est ignoré).")


def _charger_onnx(dossier_src: Path, int8: bool, threads: int):
    """Export ONNX (+ quantification dynamique int8) puis chargement optimum.

    L'export et la quantification ne sont faits qu'UNE fois, puis cachés à
    côté du dossier torch : models/drbert-onnx/ (fp32) et
    models/drbert-onnx-int8/. C'est exactement ce que fera
    packaging/fetch_models.py à l'étape 4 — jamais chez l'utilisateur.
    """
    from optimum.onnxruntime import ORTModelForTokenClassification

    base = dossier_src.parent / "drbert-onnx"
    cible = dossier_src.parent / "drbert-onnx-int8" if int8 else base
    if not (cible / "model.onnx").is_file():
        if not (base / "model.onnx").is_file():
            print("Export ONNX fp32 (une seule fois) …")
            base.mkdir(parents=True, exist_ok=True)
            ort = ORTModelForTokenClassification.from_pretrained(
                str(dossier_src), export=True
            )
            ort.save_pretrained(str(base))
        for f in _FICHIERS_TOKENIZER:
            if (dossier_src / f).is_file() and not (base / f).is_file():
                shutil.copy2(dossier_src / f, base / f)
        if int8:
            print("Quantification dynamique int8 (une seule fois) …")
            from onnxruntime.quantization import QuantType, quantize_dynamic

            cible.mkdir(parents=True, exist_ok=True)
            quantize_dynamic(
                str(base / "model.onnx"),
                str(cible / "model.onnx"),
                weight_type=QuantType.QInt8,
            )
            for f in ("config.json", *_FICHIERS_TOKENIZER):
                src = base / f if (base / f).is_file() else dossier_src / f
                if src.is_file():
                    shutil.copy2(src, cible / f)

    # Threads CPU pour la session ONNX Runtime (cœurs physiques).
    options = None
    try:
        import onnxruntime

        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
    except Exception:  # noqa: BLE001 — repli : threads par défaut d'ORT
        options = None
    try:
        return ORTModelForTokenClassification.from_pretrained(
            str(cible), session_options=options
        )
    except TypeError:  # ancienne version d'optimum sans session_options
        return ORTModelForTokenClassification.from_pretrained(str(cible))


# ---------------------------------------------------------------------------
# Entrées : documents OCR + correction lexicale du projet
# ---------------------------------------------------------------------------


def _charger_documents(chemin: Path, motif: str) -> list[Path]:
    """Documents d'entrée : un dossier (récursif, glob) ou un fichier unique."""
    if chemin.is_file():
        return [chemin]
    if chemin.is_dir():
        docs = sorted(p for p in chemin.rglob(motif) if p.is_file())
        if not docs:
            raise SystemExit(f"Aucun document « {motif} » dans {chemin}")
        return docs
    raise SystemExit(f"Introuvable : {chemin}")


def _lire_texte(chemin: Path) -> str:
    """Lit un document OCR : UTF-8 d'abord, puis cp1252 / latin-1 en repli."""
    brut = chemin.read_bytes()
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return brut.decode(enc)
        except UnicodeDecodeError:
            continue
    return brut.decode("utf-8", errors="replace")


def _importer_correcteur():
    """Correction lexicale déterministe du projet (extraction_nlp.correcteur).

    NB : le prompt de l'étude cite « src/ingestion_ocr/lexicon.py » ; dans ce
    dépôt, la correction lexicale vit dans src/extraction_nlp/correcteur.py
    (fonction « corriger_lexical ») — c'est elle qui est utilisée ici.
    """
    racine = str(PROJECT_ROOT)
    if racine not in sys.path:
        sys.path.insert(0, racine)
    from src.extraction_nlp.correcteur import corriger_lexical

    return corriger_lexical


# ---------------------------------------------------------------------------
# Sorties : console + CSV
# ---------------------------------------------------------------------------


def _ligne_entite(doc: str, variante: str, ent: Entite, texte: str) -> str:
    """Une entité sur une ligne console (texte tronqué à 60 caractères).

    « [frag.] » signale une entité qui ne s'aligne pas sur des bords de mots
    (fragment de sous-mot — voir _bords_alignes).
    """
    texte_affiche = " ".join(ent.texte.split())
    if len(texte_affiche) > 60:
        texte_affiche = texte_affiche[:57] + "…"
    marque = " [frag.]" if not _bords_alignes(texte, ent.debut, ent.fin) else ""
    return (
        f"    {doc:<22} {variante:<8} {ent.label:<10} {ent.score:5.2f} "
        f"{ent.debut:>6}-{ent.fin:<6} {texte_affiche}{marque}"
    )


def _afficher_variante(doc: str, variante: str, res: ResultatVariante, quiet: bool) -> None:
    """Bilan console d'une variante + liste détaillée des entités."""
    m = res.metriques
    par_type = Counter(e.label for e in res.entites)
    duree = m["duree_inference_s"]
    debit = m["tokens_traites"] / duree if duree > 0 else 0.0
    print(
        f"  [{variante:<7}] {len(res.entites)} entités "
        f"(problem={par_type.get('problem', 0)}, "
        f"treatment={par_type.get('treatment', 0)}, "
        f"test={par_type.get('test', 0)}) — "
        f"inférence {duree:.2f} s, {debit:.0f} tokens/s, "
        f"{m['nb_fenetres']} fenêtre(s)"
    )
    if not quiet:
        for ent in res.entites:
            print(_ligne_entite(doc, variante, ent, res.texte))


def _ecrire_csv_entites(path: Path, resultats: dict, ordre_docs: list[str]) -> None:
    """CSV de détail : une ligne par entité, pour compter à la main les vrais
    et faux positifs (remplir « vrai_positif » : oui/non, et « commentaire »).

    La colonne « contexte » donne ±60 caractères autour de l'entité, DANS le
    texte de la variante concernée (brut ou corrigé) : le relecteur voit la
    phrase sans ouvrir le document.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(
            [
                "document",
                "variante",
                "label",
                "texte",
                "debut",
                "fin",
                "longueur",
                "score",
                "bords_mots",
                "contexte",
                "vrai_positif",
                "commentaire",
            ]
        )
        for nom in ordre_docs:
            for variante in VARIANTES:
                res = resultats[(nom, variante)]
                for ent in res.entites:
                    ctx = res.texte[max(0, ent.debut - 60) : ent.fin + 60]
                    ctx = " ".join(ctx.split())
                    aligne = _bords_alignes(res.texte, ent.debut, ent.fin)
                    w.writerow(
                        [
                            nom,
                            variante,
                            ent.label,
                            ent.texte,
                            ent.debut,
                            ent.fin,
                            ent.longueur,
                            f"{ent.score:.3f}",
                            "oui" if aligne else "non",
                            ctx,
                            "",
                            "",
                        ]
                    )
    print(f"CSV de détail écrit : {path}")


def _ecrire_csv_synthese(path: Path, resultats: dict, ordre_docs: list[str]) -> None:
    """CSV de synthèse : une ligne par (document, variante) avec les métriques."""
    path.parent.mkdir(parents=True, exist_ok=True)
    colonnes = [
        "document",
        "variante",
        "caracteres",
        "tokens_document",
        "nb_fenetres",
        "tokens_traites",
        "nb_entites",
        "nb_problem",
        "nb_treatment",
        "nb_test",
        "nb_fragments",
        "longueur_moyenne",
        "score_moyen",
        "nb_score_lt_050",
        "nb_score_050_070",
        "nb_score_ge_070",
        "duree_correction_s",
        "duree_tokenisation_s",
        "duree_inference_s",
        "duree_post_s",
        "tokens_par_s",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(colonnes)
        for nom in ordre_docs:
            for variante in VARIANTES:
                res = resultats[(nom, variante)]
                entites = res.entites
                m = res.metriques
                par_type = Counter(e.label for e in entites)
                n_frags = sum(
                    1
                    for e in entites
                    if not _bords_alignes(res.texte, e.debut, e.fin)
                )
                longs = [e.longueur for e in entites]
                scores = [e.score for e in entites]
                duree = m["duree_inference_s"]
                w.writerow(
                    [
                        nom,
                        variante,
                        m["caracteres"],
                        m["tokens_document"],
                        m["nb_fenetres"],
                        m["tokens_traites"],
                        len(entites),
                        par_type.get("problem", 0),
                        par_type.get("treatment", 0),
                        par_type.get("test", 0),
                        n_frags,
                        f"{statistics.mean(longs):.1f}" if longs else "0",
                        f"{statistics.mean(scores):.3f}" if scores else "0",
                        sum(1 for s in scores if s < 0.5),
                        sum(1 for s in scores if 0.5 <= s < 0.7),
                        sum(1 for s in scores if s >= 0.7),
                        f"{m.get('duree_correction_s', 0.0):.3f}",
                        f"{m['duree_tokenisation_s']:.3f}",
                        f"{duree:.3f}",
                        f"{m['duree_post_s']:.3f}",
                        f"{m['tokens_traites'] / duree:.0f}" if duree > 0 else "0",
                    ]
                )
    print(f"CSV de synthèse écrit : {path}")


def _afficher_synthese(resultats: dict, ordre_docs: list[str]) -> None:
    """Tableaux comparatifs console : par document, puis par variante."""
    print("\n=== Comparatif brut / corrigé (nombre d'entités, par document) ===")
    print(f"{'Document':<30}{'Brut':>10}{'Corrigé':>10}{'Delta':>10}")
    for nom in ordre_docs:
        brut = len(resultats[(nom, VARIANTE_BRUT)].entites)
        corr = len(resultats[(nom, VARIANTE_CORRIGE)].entites)
        print(f"{nom[:29]:<30}{brut:>10}{corr:>10}{corr - brut:>+10}")

    print("\n=== Synthèse globale par variante ===")
    for variante in VARIANTES:
        resultats_var = [resultats[(n, variante)] for n in ordre_docs]
        entites = [e for r in resultats_var for e in r.entites]
        par_type = Counter(e.label for e in entites)
        n_frags = sum(
            1
            for r in resultats_var
            for e in r.entites
            if not _bords_alignes(r.texte, e.debut, e.fin)
        )
        tokens = sum(r.metriques["tokens_traites"] for r in resultats_var)
        duree = sum(r.metriques["duree_inference_s"] for r in resultats_var)
        debit = tokens / duree if duree > 0 else 0.0
        long_moy = statistics.mean(e.longueur for e in entites) if entites else 0.0
        score_moy = statistics.mean(e.score for e in entites) if entites else 0.0
        n_faible = sum(1 for e in entites if e.score < 0.5)
        n_moyen = sum(1 for e in entites if 0.5 <= e.score < 0.7)
        n_fort = sum(1 for e in entites if e.score >= 0.7)
        print(f"[{variante}]")
        print(
            f"  entités          : {len(entites)} "
            f"(problem={par_type.get('problem', 0)}, "
            f"treatment={par_type.get('treatment', 0)}, "
            f"test={par_type.get('test', 0)}) — "
            f"dont {n_frags} fragment(s) de sous-mot"
        )
        print(f"  longueur moyenne : {long_moy:.1f} caractères")
        print(
            f"  scores           : moyen {score_moy:.2f} — "
            f"<0,50 : {n_faible} | 0,50-0,70 : {n_moyen} | >=0,70 : {n_fort}"
        )
        print(
            f"  inférence        : {duree:.2f} s au total — "
            f"{debit:.0f} tokens/s ({tokens} tokens traités, fenêtres incluses)"
        )


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parseur = argparse.ArgumentParser(
        prog="eval_drbert",
        description=(
            "Banc d'essai DrBERT-CASM2 sur textes OCR bruités (étape 0, "
            "décision go / no-go) : entités brut vs corrigé, offsets, scores, "
            "temps par document et débit tokens/s."
        ),
    )
    parseur.add_argument(
        "--input",
        required=True,
        type=Path,
        help="Dossier de textes OCR (récursif, --glob) ou un fichier unique",
    )
    parseur.add_argument(
        "--glob",
        default="*.txt",
        help="Motif des documents dans le dossier (défaut : *.txt)",
    )
    parseur.add_argument(
        "--model-dir",
        type=Path,
        default=None,
        help="Dossier du modèle local (défaut : $VSM_DRBERT_PATH ou models/drbert)",
    )
    parseur.add_argument(
        "--download",
        action="store_true",
        help="Télécharger medkit/DrBERT-CASM2 vers --model-dir (une fois, développement)",
    )
    parseur.add_argument(
        "--backend",
        choices=("torch", "onnx"),
        default="torch",
        help="Moteur d'inférence (défaut : torch fp32 ; onnx = optimum + onnxruntime)",
    )
    parseur.add_argument(
        "--onnx-precision",
        choices=("int8", "fp32"),
        default="int8",
        help="Précision ONNX (défaut : int8, quantification dynamique)",
    )
    parseur.add_argument(
        "--max-length",
        type=int,
        default=512,
        help=(
            "Taille de fenêtre en tokens, spéciaux inclus (défaut : 512). "
            "NB : le checkpoint CASM2 est fine-tuné avec model_max_length=128 — "
            "comparer aussi --max-length 128."
        ),
    )
    parseur.add_argument(
        "--stride",
        type=int,
        default=64,
        help="Recouvrement entre fenêtres en tokens (défaut : 64)",
    )
    parseur.add_argument(
        "--threads",
        type=int,
        default=0,
        help="Threads CPU (défaut : nombre de cœurs physiques)",
    )
    parseur.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Ne traiter que les N premiers documents",
    )
    parseur.add_argument(
        "--min-score",
        type=float,
        default=0.0,
        help="Ne garder que les entités de score >= seuil (défaut : 0 = tout garder)",
    )
    parseur.add_argument(
        "--output",
        type=Path,
        default=Path("eval_drbert_entites.csv"),
        help="CSV de détail (défaut : eval_drbert_entites.csv ; synthèse à côté)",
    )
    parseur.add_argument(
        "--quiet",
        action="store_true",
        help="Ne pas lister les entités une à une (tables et CSV seulement)",
    )
    return parseur.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    _configurer_console()  # avant argparse : l'aide aussi doit être lisible
    args = _parse_args(argv)

    docs = _charger_documents(args.input.resolve(), args.glob)
    if args.limit > 0:
        docs = docs[: args.limit]

    dossier = args.model_dir
    if dossier is None:
        dossier = Path(
            os.environ.get("VSM_DRBERT_PATH", str(PROJECT_ROOT / "models" / "drbert"))
        )
    dossier = dossier.resolve()

    if not _modele_present(dossier):
        if args.download:
            _telecharger(dossier)
        else:
            print(
                f"Modèle absent de {dossier}.\n"
                f"  Lancer d'abord : "
                f"py -3.12 tools/eval_drbert.py --input <dossier> --download\n"
                f"  ou préciser --model-dir / la variable VSM_DRBERT_PATH."
            )
            return 2
    if not _modele_present(dossier):
        print(f"Modèle incomplet dans {dossier} (fichiers manquants).")
        return 2

    try:
        moteur = MoteurDrBERT(
            dossier,
            backend=args.backend,
            onnx_int8=args.onnx_precision == "int8",
            threads=args.threads,
            max_length=args.max_length,
            stride=args.stride,
        )
    except ImportError as exc:
        print(_ligne_install(exc))
        return 2

    print("=== Banc d'essai DrBERT-CASM2 (étape 0 — go / no-go) ===")
    print(f"Modèle    : {dossier}")
    print(f"Backend   : {args.backend}"
          + (" int8" if args.backend == "onnx" and args.onnx_precision == "int8" else ""))
    print(f"Threads   : {moteur.threads} "
          "(cœurs physiques ; repli cœurs logiques si psutil absent)")
    print(f"Fenêtres  : {args.max_length} tokens, recouvrement {args.stride}")
    print(f"id2label  : {moteur.id2label}")
    print(f"Documents : {len(docs)} (motif « {args.glob} ») depuis {args.input}")

    # Échauffement (chargement paresseux, JIT) : une passe non comptée.
    moteur.annoter("Échauffement : œsophagite chronique, oméprazole 20 mg.")
    print("(échauffement effectué, non compté dans les mesures)")

    corriger = _importer_correcteur()

    resultats: dict[tuple[str, str], ResultatVariante] = {}
    ordre_docs: list[str] = []
    for indice, chemin in enumerate(docs, 1):
        texte = _lire_texte(chemin)
        if not texte.strip():
            print(f"[{indice}/{len(docs)}] {chemin.name} : vide, ignoré")
            continue
        print(f"[{indice}/{len(docs)}] {chemin.name} ({len(texte)} caractères)")
        ordre_docs.append(chemin.name)

        t0 = time.perf_counter()
        texte_corrige = corriger(texte)
        duree_correction = time.perf_counter() - t0

        for variante, texte_variante in (
            (VARIANTE_BRUT, texte),
            (VARIANTE_CORRIGE, texte_corrige),
        ):
            entites, metriques = moteur.annoter(texte_variante)
            if args.min_score > 0:
                entites = [e for e in entites if e.score >= args.min_score]
            metriques["duree_correction_s"] = (
                duree_correction if variante == VARIANTE_CORRIGE else 0.0
            )
            res = ResultatVariante(
                entites=entites, metriques=metriques, texte=texte_variante
            )
            resultats[(chemin.name, variante)] = res
            _afficher_variante(chemin.name, variante, res, args.quiet)

    if not ordre_docs:
        print("Aucun document exploitable.")
        return 1

    _afficher_synthese(resultats, ordre_docs)
    sortie = args.output
    _ecrire_csv_entites(sortie, resultats, ordre_docs)
    synthese = sortie.with_name(sortie.stem + "_synthese.csv")
    _ecrire_csv_synthese(synthese, resultats, ordre_docs)
    print(
        "\nProchaine étape : remplir la colonne « vrai_positif » (oui/non) du "
        "CSV de détail, puis comparer les taux brut / corrigé."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
