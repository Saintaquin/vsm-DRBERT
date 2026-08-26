"""Gestion du modèle LLM local (100 % offline — aucune donnée ne quitte la
machine, exigence RGPD art. 9 du concours).

- Chemin du modèle : variable d'environnement ``VSM_LLM_MODEL_PATH``, sinon
  ``~/.cache/vsm-ocr/model.gguf`` (défaut).
- Téléchargement : ``python -m src.extraction_nlp.llm`` (ou la fonction
  ``download_model``) récupère un GGUF depuis Hugging Face — à l'installation,
  par l'administrateur ; jamais pendant le traitement de documents.
- Le choix du modèle par défaut est documenté dans docs/ADR/0004 (audit des
  modèles, licences, contraintes matérielles).
"""

from __future__ import annotations

import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

# Référence : ~/.cache/vsm-ocr/ (jamais committé, cf. .gitignore)
CACHE_DIR = Path(os.environ.get("VSM_LLM_CACHE", Path.home() / ".cache" / "vsm-ocr"))

# Modèles candidats (audit ADR-0004 / ADR-0009) : licence, taille GGUF Q4_K_M,
# RAM nécessaire, qualité français, note /5.
# Le PREMIER élément est le modèle UNIVERSEL par défaut (toutes machines,
# y compris < 8 Go sans GPU) : Qwen 2.5 3B Q4 (Apache 2.0, ~2 Go, CPU).
RECOMMENDED_MODELS = [
    {
        "key": "qwen2.5-3b",
        "nom": "Qwen 2.5 3B Instruct — DÉFAUT UNIVERSEL",
        "hf_repo": "Qwen/Qwen2.5-3B-Instruct-GGUF",
        "hf_file": "qwen2.5-3b-instruct-q4_k_m.gguf",
        "url": "https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf",
        "taille_gb": 2.0,
        "ram_min_gb": 4,
        "licence": "Apache 2.0",
        "francais": "Bon (multilingue, FR correct) — CPU, sans GPU",
        "note": 4,
    },
    {
        "key": "qwen2.5-1.5b",
        "nom": "Qwen 2.5 1.5B Instruct (ultra-léger, < 4 Go)",
        "hf_repo": "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
        "hf_file": "qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "url": "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "taille_gb": 1.0,
        "ram_min_gb": 3,
        "licence": "Apache 2.0",
        "francais": "Correct (multilingue) — CPU, machines très légères",
        "note": 3,
    },
    {
        "key": "mistral-nemo-12b",
        "nom": "Mistral NeMo 12B Instruct (haute qualité, 16 Go)",
        "hf_repo": "TheBloke/Mistral-Nemo-Instruct-12B-GGUF",
        "hf_file": "mistral-nemo-instruct-12b.Q4_K_M.gguf",
        "url": "https://huggingface.co/TheBloke/Mistral-Nemo-Instruct-12B-GGUF/resolve/main/mistral-nemo-instruct-12b.Q4_K_M.gguf",
        "taille_gb": 7.2,
        "ram_min_gb": 16,
        "licence": "Apache 2.0",
        "francais": "Excellent (Mistral AI, française)",
        "note": 5,
    },
    {
        "key": "qwen2.5-7b",
        "nom": "Qwen 2.5 7B Instruct (9-14 Go)",
        "hf_repo": "Qwen/Qwen2.5-7B-Instruct-GGUF",
        "hf_file": "qwen2.5-7b-instruct-q4_k_m.gguf",
        "url": "https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/main/qwen2.5-7b-instruct-q4_k_m.gguf",
        "taille_gb": 4.7,
        "ram_min_gb": 9,
        "licence": "Apache 2.0",
        "francais": "Bon (multilingue fort)",
        "note": 4,
    },
    {
        "key": "mistral-7b-v0.3",
        "nom": "Mistral 7B Instruct v0.3",
        "hf_repo": "TheBloke/Mistral-7B-Instruct-v0.3-GGUF",
        "hf_file": "mistral-7b-instruct-v0.3.Q4_K_M.gguf",
        "url": "https://huggingface.co/TheBloke/Mistral-7B-Instruct-v0.3-GGUF/resolve/main/mistral-7b-instruct-v0.3.Q4_K_M.gguf",
        "taille_gb": 4.1,
        "ram_min_gb": 9,
        "licence": "Apache 2.0",
        "francais": "Très bon (française)",
        "note": 4,
    },
    {
        "key": "llama3.2-3b",
        "nom": "Llama 3.2 3B Instruct (ultra-léger, licence Llama)",
        "hf_repo": "TheBloke/Llama-3.2-3B-Instruct-GGUF",
        "hf_file": "llama-3.2-3b-instruct.Q4_K_M.gguf",
        "url": "https://huggingface.co/TheBloke/Llama-3.2-3B-Instruct-GGUF/resolve/main/llama-3.2-3b-instruct.Q4_K_M.gguf",
        "taille_gb": 2.0,
        "ram_min_gb": 4,
        "licence": "Llama Community License",
        "francais": "Correct (moins précis)",
        "note": 2,
    },
    {
        "key": "llama3.1-8b",
        "nom": "Llama 3.1 8B Instruct (licence Llama)",
        "hf_repo": "TheBloke/Llama-3.1-8B-Instruct-GGUF",
        "hf_file": "llama-3.1-8b-instruct.Q4_K_M.gguf",
        "url": "https://huggingface.co/TheBloke/Llama-3.1-8B-Instruct-GGUF/resolve/main/llama-3.1-8b-instruct.Q4_K_M.gguf",
        "taille_gb": 4.9,
        "ram_min_gb": 12,
        "licence": "Llama Community License (non exclusive, <700M MAU)",
        "francais": "Bon",
        "note": 3,
    },
]


def detect_ram_gb() -> float:
    """RAM totale détectée (Go) — Windows (ctypes) et POSIX (/proc/meminfo)."""
    try:
        if os.name == "nt":  # pragma: no cover
            import ctypes

            class _MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            ms = _MEMORYSTATUSEX(dwLength=ctypes.sizeof(_MEMORYSTATUSEX))
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
            return ms.ullTotalPhys / (1024**3)
        with open("/proc/meminfo") as f:  # pragma: no cover - POSIX
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / 1024 / 1024
    except Exception:  # pragma: no cover - détection best-effort
        pass
    return 16.0


def suggest_model() -> str:
    """Modèle conseillé selon la RAM détectée (voir --list).

    Règles prudentes : modèle + contexte + OS + application doivent tenir en
    mémoire sans swap (un 3B Q4 ≈ 2 Go à l'inférence). Le modèle universel
    (Qwen 2.5 3B) convient à partir de 4 Go, sans GPU."""
    ram = detect_ram_gb()
    if ram >= 14:
        return "mistral-nemo-12b"
    if ram >= 9:
        return "qwen2.5-7b"
    if ram >= 4:
        return "qwen2.5-3b"
    return "qwen2.5-1.5b"


def default_model_path() -> Path:
    """Chemin du GGUF : VSM_LLM_MODEL_PATH sinon ~/.cache/vsm-ocr/model.gguf."""
    env = os.environ.get("VSM_LLM_MODEL_PATH")
    if env:
        return Path(env)
    return CACHE_DIR / "model.gguf"


def model_available() -> bool:
    path = default_model_path()
    return path.exists() and path.stat().st_size > 1_000_000  # ≥ ~1 Mo (en-tête)


_LLAMA_CPP_CACHE: bool | None = None


def _llama_cpp_available() -> bool:
    """llama-cpp-python est-il importable ? (résultat mis en cache)."""
    global _LLAMA_CPP_CACHE
    if _LLAMA_CPP_CACHE is None:
        try:
            import llama_cpp  # noqa: F401

            _LLAMA_CPP_CACHE = True
        except Exception:  # noqa: BLE001 - ImportError ou wheel cassé
            _LLAMA_CPP_CACHE = False
    return _LLAMA_CPP_CACHE


def llm_unavailability_reason() -> str:
    """Raison POURQUOI le LLM n'est pas utilisable (affichée à l'UI et dans
    provenance.nlp) — sinon chaîne vide."""
    if model_available() and not _llama_cpp_available():
        return (
            "Le modèle GGUF est présent mais la bibliothèque "
            "llama-cpp-python n'est pas installée — pip install llama-cpp-python"
        )
    if not model_available():
        return (
            "Modèle LLM local absent — téléchargez-le : "
            "python -m src.extraction_nlp.llm"
        )
    return ""


def available_ram_gb() -> float:
    """RAM DISPONIBLE (Go) — Windows (GlobalMemoryStatusEx) / POSIX MemAvailable.
    Diffère de detect_ram_gb() (RAM totale) : c'est la mémoire réellement
    utilisable pour charger le modèle."""
    try:
        if os.name == "nt":  # pragma: no cover
            import ctypes

            class _MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            ms = _MEMORYSTATUSEX(dwLength=ctypes.sizeof(_MEMORYSTATUSEX))
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
            return ms.ullAvailPhys / (1024**3)
        with open("/proc/meminfo") as f:  # pragma: no cover - POSIX
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024 / 1024
    except Exception:  # noqa: BLE001 - détection best-effort
        pass
    return detect_ram_gb()


# Marge de référence au-delà du poids du modèle (contexte, overhead, OS+app) :
# sert uniquement à produire un AVERTISSEMENT « le LLM risque d'être lent » —
# ne BLOQUE PLUS le LLM (exigence : LLM sur toutes les machines, même lentes).
_LLM_RAM_MARGIN_GB = 1.5

# ---------------------------------------------------------------------------
# Budgets temporels SÉPARÉS. Le chargement n'a lieu qu'UNE FOIS par processus
# (singleton) ; un dépassement sur un segment ne désactive JAMAIS le LLM pour
# la session — le segment concerné bascule sur les règles.
# ---------------------------------------------------------------------------
# Délai maximal d'une inférence LLM en secondes (configurable) — historique.
LLM_INFERENCE_TIMEOUT_SEC = int(os.environ.get("VSM_LLM_TIMEOUT_SEC", "300"))
# Budget de CHARGEMENT du modèle (premier document uniquement, singleton ensuite).
LLM_LOAD_TIMEOUT_SEC = int(os.environ.get("VSM_LLM_LOAD_TIMEOUT_SEC", "900"))
# Budget d'EXTRACTION d'un SEGMENT : court (45 s) — un segment lent est un
# segment perdu, pas une raison d'arrêter le LLM (repli par segment).
# NB : sur un CPU très lent, un segment clinique peut dépasser 45 s — relever
# VSM_LLM_TIMEOUT_S si besoin ; le coupe-circuit ECHECS_MAX borne le pire cas.
LLM_TIMEOUT_S = int(os.environ.get("VSM_LLM_TIMEOUT_S", "45"))
# Coupe-circuit : au-delà de ce nombre de segments consécutifs en échec, on
# cesse d'appeler le LLM (modèle vraiment indisponible) → règles pour le reste.
ECHECS_MAX = int(os.environ.get("VSM_ECHECS_MAX", "15"))

# ---------------------------------------------------------------------------
# Singleton du modèle : le GGUF (≈ 2 Go) est chargé UNE SEULE FOIS par
# processus, puis réutilisé par tous les documents. Le chargement est
# déclenché à la première demande ET préchargé au démarrage du backend
# (preload_llm) : le premier document ne paie plus le coût de chargement.
# ---------------------------------------------------------------------------

# Taille du contexte (configurable) : 8192 par défaut — l'extraction reçoit le
# texte brut ET le texte corrigé (≈ 12 000 caractères au total).
LLM_N_CTX = int(os.environ.get("VSM_LLM_N_CTX", "8192"))

_LLM_STATE: dict = {
    "model": None,  # instance llama_cpp.Llama partagée (ou None)
    "name": "",  # nom du fichier modèle (rapport XAI)
    "loader": None,  # thread de chargement en cours (ou None)
    "loaded": threading.Event(),  # le chargement est terminé (succès ou échec)
    "load_error": None,  # exception levée pendant le chargement
}
_LLM_LOCK = threading.Lock()
# Sérialise les appels d'inférence sur le modèle partagé (llama-cpp n'est pas
# thread-safe) : évite qu'une inférence chronométrée partie en arrière-plan
# entre en concurrence avec la suivante.
LLM_INFERENCE_LOCK = threading.Lock()
# Taille maximale d'un SEGMENT de texte OCR envoyé au LLM (caractères). Les
# gros documents sont découpés et traités segment par segment : chaque
# inférence reste bornée en temps, même sur un CPU lent sans GPU.
# 1200 caractères ≈ une fenêtre confortable pour un petit modèle (1,5B) sans
# dépasser les budgets de temps ; les segments se recouvrent de
# LLM_CHUNK_OVERLAP caractères pour ne pas couper une information en deux.
LLM_CHUNK_CHARS = int(os.environ.get("VSM_LLM_CHUNK_CHARS", "1200"))
LLM_CHUNK_OVERLAP = int(os.environ.get("VSM_LLM_CHUNK_OVERLAP", "150"))


def _do_load_model() -> None:
    """Charge le GGUF (thread dédié) — une seule fois par processus."""
    try:
        from llama_cpp import Llama

        path = str(default_model_path())
        t0 = time.perf_counter()
        # Réglages CPU conservateurs (machines lentes sans GPU) : threads =
        # cœurs, batch 512 (meilleur débit CPU que le défaut).
        model = Llama(
            model_path=path,
            n_ctx=LLM_N_CTX,
            n_threads=os.cpu_count() or 4,
            n_batch=1024,  # évaluation du prompt par lots plus grands (plus rapide)
            verbose=False,
        )
        with _LLM_LOCK:
            _LLM_STATE["model"] = model
            _LLM_STATE["name"] = Path(path).name
        import logging

        logging.getLogger("vsm").info(
            "modèle LLM chargé en %.1f s (%s)",
            time.perf_counter() - t0,
            Path(path).name,
        )
    except Exception as exc:  # noqa: BLE001 - rapporté à l'appelant
        with _LLM_LOCK:
            _LLM_STATE["load_error"] = exc
    finally:
        _LLM_STATE["loaded"].set()


def get_llm_instance(wait_timeout: float | None = None):
    """Instance partagée du modèle (chargée une seule fois).

    - Déclenche le chargement au premier appel s'il n'est pas en cours.
    - Attend au plus ``wait_timeout`` s (défaut : LLM_LOAD_TIMEOUT_SEC).
    - Lève TimeoutError si le chargement dépasse le délai : il CONTINUE en
      arrière-plan et sera disponible à l'appel suivant (document suivant) —
      jamais d'échec définitif de session.
    - Lève l'erreur de chargement (fichier invalide, llama_cpp absent…) pour
      que l'appelant bascule visiblement sur les règles.
    """
    if wait_timeout is None:
        wait_timeout = LLM_LOAD_TIMEOUT_SEC
    with _LLM_LOCK:
        if _LLM_STATE["model"] is not None:
            return _LLM_STATE["model"]
        if _LLM_STATE["loader"] is None:
            _LLM_STATE["loaded"].clear()
            _LLM_STATE["load_error"] = None
            _LLM_STATE["loader"] = threading.Thread(
                target=_do_load_model, daemon=True, name="vsm-llm-load"
            )
            _LLM_STATE["loader"].start()
    if not _LLM_STATE["loaded"].wait(timeout=wait_timeout):
        raise TimeoutError(
            "chargement du modèle LLM toujours en cours (dépasse le délai) — "
            "il reste disponible pour le document suivant"
        )
    with _LLM_LOCK:
        if _LLM_STATE["load_error"] is not None:
            err = _LLM_STATE["load_error"]
            _LLM_STATE["loader"] = None  # autorise une nouvelle tentative
            raise err
        model = _LLM_STATE["model"]
        if model is None:
            raise TimeoutError("chargement du modèle LLM abandonné")
        return model


def llm_model_name() -> str:
    """Nom du modèle pour les rapports (fichier GGUF ou clé du catalogue)."""
    if _LLM_STATE["name"]:
        return _LLM_STATE["name"]
    env = os.environ.get("VSM_LLM_MODEL")
    if env:
        return env
    return default_model_path().name


def preload_llm() -> None:
    """Préchargement en arrière-plan au démarrage du backend : le coût de
    chargement (~minutes sur PC lent) est payé AVANT le premier document."""
    if not llm_attemptable():
        return

    def _preload():  # pragma: no cover - nécessite llama.cpp + GGUF
        try:
            get_llm_instance()
        except Exception:  # noqa: BLE001 - le préchargement est best-effort
            pass

    threading.Thread(target=_preload, daemon=True, name="vsm-llm-preload").start()


def llm_attemptable() -> bool:
    """Le LLM doit-il être TENTÉ ? Oui dès que le modèle EST présent ET que
    llama-cpp-python est importable. (Exigence : LLM sur toutes les machines,
    même lentes — la RAM ne bloque plus ; un timeout + repli règles préviennent
    le blocage infini.)"""
    return model_available() and _llama_cpp_available()


def llm_ram_warning() -> str:
    """Avertissement (non bloquant) si la RAM disponible est juste pour le
    modèle : le LLM sera tenté mais risque d'être lent (swap)."""
    if not model_available():
        return ""
    taille_gb = default_model_path().stat().st_size / (1024**3)
    besoin = taille_gb + _LLM_RAM_MARGIN_GB
    libre = available_ram_gb()
    if libre < besoin:
        return (
            f"RAM libre {libre:.1f} Go pour un besoin ≈ {besoin:.1f} Go "
            f"(modèle {taille_gb:.1f} Go). Le LLM sera utilisé mais peut être "
            "lent (échange disque) ; repli règles en cas de dépassement du délai."
        )
    return ""


def _validate_gguf(path: Path) -> bool:
    """L'en-tête d'un GGUF commence par la signature « GGUF »."""
    try:
        with path.open("rb") as f:
            return f.read(4) == b"GGUF"
    except OSError:
        return False


def download_model(
    key: str | None = None, dest: str | Path | None = None, force: bool = False
) -> Path:
    """Télécharge le modèle recommandé (ou celui de la clé donnée) vers dest.

    Strictement hors flux de traitement : à lancer par l'administrateur, au
    préalable (aucune donnée patient n'est impliquée)."""
    if key is None:
        key = os.environ.get("VSM_LLM_MODEL") or suggest_model()
        ram = detect_ram_gb()
        print(
            f"RAM détectée : {ram:.0f} Go → modèle conseillé : "
            f"{key} ({next(m['nom'] for m in RECOMMENDED_MODELS if m['key'] == key)})"
        )
    try:
        meta = next(m for m in RECOMMENDED_MODELS if m["key"] == key)
    except StopIteration:
        raise ValueError(
            f"Modèle inconnu : {key!r}. Disponibles : "
            + ", ".join(m["key"] for m in RECOMMENDED_MODELS)
        ) from None

    dest = Path(dest) if dest else default_model_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not force:
        if _validate_gguf(dest):
            print(f"Modèle déjà présent : {dest} ({dest.stat().st_size / 1e6:.0f} Mo)")
            return dest
        print(f"Fichier existant invalide ({dest}) — re-téléchargement.")

    print(
        f"Téléchargement de {meta['nom']} ({meta['taille_gb']:.1f} Go, "
        f"licence {meta['licence']}) depuis Hugging Face…"
    )
    print("  " + meta["url"])
    tmp = dest.with_suffix(".part")
    req = urllib.request.Request(meta["url"], headers={"User-Agent": "vsm-ocr/1.0"})
    with urllib.request.urlopen(req) as resp, tmp.open("wb") as f:
        total = int(resp.headers.get("Content-Length", 0))
        done = 0
        while chunk := resp.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            if total:
                pct = done * 100 // total
                print(f"\r  {done / 1e6:.0f} / {total / 1e6:.0f} Mo ({pct} %)", end="")
    print("\n")
    if not _validate_gguf(tmp):
        tmp.unlink(missing_ok=True)
        raise RuntimeError("Fichier téléchargé invalide (signature GGUF absente).")
    tmp.replace(dest)
    print(f"Modèle prêt : {dest} ({dest.stat().st_size / 1e6:.0f} Mo)")
    return dest


def main() -> int:
    """python -m src.extraction_nlp.llm [--model KEY] [--dest CHEMIN] [--force]"""
    import argparse

    # Console Windows (cp1252) : forcer UTF-8 pour l'affichage (→, …)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="Télécharge le modèle LLM local VSM-OCR")
    ap.add_argument(
        "--model",
        default=os.environ.get("VSM_LLM_MODEL"),
        help="clé du modèle (défaut : le modèle recommandé)",
    )
    ap.add_argument("--dest", default=None, help="chemin de destination du GGUF")
    ap.add_argument(
        "--force", action="store_true", help="re-télécharger même si présent"
    )
    ap.add_argument(
        "--list", action="store_true", help="lister les modèles disponibles"
    )
    args = ap.parse_args()

    if args.list:
        print(
            f"RAM détectée : {detect_ram_gb():.0f} Go → modèle conseillé : "
            f"{suggest_model()} (note {next(m['note'] for m in RECOMMENDED_MODELS if m['key'] == suggest_model())}/5)"
        )
        print("Modèles disponibles (audit ADR-0004) :")
        for m in RECOMMENDED_MODELS:
            star = (
                " *"
                if m["key"] == (os.environ.get("VSM_LLM_MODEL") or suggest_model())
                else ""
            )
            print(
                f"  - {m['key']:16s} {m['nom']:32s} {m['taille_gb']:.1f} Go | "
                f"{m['licence']:35s} | RAM ≥ {m['ram_min_gb']} Go | note {m['note']}/5{star}"
            )
        return 0
    download_model(args.model, args.dest, args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
