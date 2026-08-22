"""Préprocessing d'images de documents médicaux scannés.

Implémentation auto-suffisante (PIL + numpy) : fonctionne sur un poste
praticien sans GPU ni OpenCV. Si OpenCV est présent, le deskew utilise la
transformée de Hough (plus robuste) ; sinon, repli sur la méthode des
profils de projection.

API : preprocess_image(img) -> {processed, steps_applied, angle_corrected,
processing_time_sec}"""

from __future__ import annotations

import time

import numpy as np
from PIL import Image, ImageFilter, ImageOps

try:  # OpenCV optionnel
    import cv2

    _HAS_CV2 = True
except ImportError:  # pragma: no cover
    _HAS_CV2 = False


# ---------------------------------------------------------------------------
def to_grayscale(img: Image.Image) -> Image.Image:
    return img.convert("L")


def _profile_score(pil_img, angle: float) -> float:
    """Variance du profil de projection horizontal après rotation d'essai."""
    rotated = np.asarray(
        pil_img.rotate(angle, expand=False, fillcolor=0), dtype=np.float32
    )
    return float(np.var(rotated.sum(axis=1)))


def _projection_skew_angle(
    arr: np.ndarray, max_angle: float = 5.0, step: float = 0.25
) -> float:
    """Angle maximisant la variance du profil de projection horizontal.
    Échantillonnage isotrope (thumbnail) pour ne pas déformer les angles."""
    from PIL import Image as _I

    binary = ((arr < 128) * 255).astype(np.uint8)
    pil = _I.fromarray(binary)
    pil.thumbnail((500, 500))  # conserve le ratio → angles non distordus
    best_angle, best_score = 0.0, _profile_score(pil, 0.0)
    for angle in np.arange(-max_angle, max_angle + step, step):
        score = _profile_score(pil, float(angle))
        if score > best_score:
            best_score, best_angle = score, float(angle)
    # Garde-fou : on ne retient l'angle que s'il améliore nettement le profil
    # par rapport à l'image non tournée (sinon risque d'aggraver un scan droit).
    if best_angle != 0.0 and best_score < 1.05 * _profile_score(pil, 0.0):
        return 0.0
    return best_angle


def deskew(img: Image.Image) -> tuple[Image.Image, float]:
    """Corrige l'inclinaison. Retourne (image, angle_corrigé en degrés)."""
    arr = np.asarray(img.convert("L"))
    if _HAS_CV2:
        edges = cv2.Canny(arr, 50, 150)
        lines = cv2.HoughLinesP(
            edges,
            1,
            np.pi / 180,
            threshold=100,
            minLineLength=arr.shape[1] // 4,
            maxLineGap=20,
        )
        if lines is not None and len(lines):
            angles = []
            for x1, y1, x2, y2 in lines[:, 0]:
                a = np.degrees(np.arctan2(y2 - y1, x2 - x1))
                if abs(a) < 15:
                    angles.append(a)
            angle = float(np.median(angles)) if angles else 0.0
        else:
            angle = 0.0
    else:
        angle = _projection_skew_angle(arr)
    if abs(angle) < 0.1:
        return img, 0.0
    # Vérification empirique du sens : on applique l'angle qui maximise
    # réellement le score de profil sur l'image complète (à échelle réduite),
    # ce qui neutralise toute ambiguïté de convention de signe.
    from PIL import Image as _I

    thumb = _I.fromarray(((arr < 128) * 255).astype(np.uint8))
    thumb.thumbnail((500, 500))
    candidates = {
        0.0: _profile_score(thumb, 0.0),
        angle: _profile_score(thumb, angle),
        -angle: _profile_score(thumb, -angle),
    }
    applied = max(candidates, key=candidates.get)  # type: ignore[arg-type]
    if applied == 0.0:
        return img, 0.0
    return img.rotate(applied, expand=True, fillcolor=255), applied


def denoise(img: Image.Image) -> Image.Image:
    if _HAS_CV2:
        arr = np.asarray(img.convert("L"))
        return Image.fromarray(cv2.fastNlMeansDenoising(arr, h=10))
    return img.filter(ImageFilter.MedianFilter(size=3))


def enhance_contrast(img: Image.Image) -> Image.Image:
    """CLAHE si OpenCV, sinon égalisation adaptative simple (autocontrast)."""
    if _HAS_CV2:
        arr = np.asarray(img.convert("L"))
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        return Image.fromarray(clahe.apply(arr))
    return ImageOps.autocontrast(img.convert("L"), cutoff=1)


def binarize(img: Image.Image, window: int = 25, k: float = 0.2) -> Image.Image:
    """Binarisation locale de Sauvola via images intégrales (numpy pur)."""
    arr = np.asarray(img.convert("L"), dtype=np.float64)
    h, w = arr.shape
    pad = window // 2
    padded = np.pad(arr, pad, mode="reflect")
    integ = np.cumsum(np.cumsum(np.pad(padded, ((1, 0), (1, 0))), axis=0), axis=1)
    integ_sq = np.cumsum(np.cumsum(np.pad(padded**2, ((1, 0), (1, 0))), axis=0), axis=1)

    def window_sum(ii):
        return (
            ii[window : window + h, window : window + w]
            - ii[window : window + h, 0:w]
            - ii[0:h, window : window + w]
            + ii[0:h, 0:w]
        )

    n = window * window
    mean = window_sum(integ) / n
    var = window_sum(integ_sq) / n - mean**2
    std = np.sqrt(np.clip(var, 0, None))
    threshold = mean * (1 + k * (std / 128.0 - 1))
    return Image.fromarray(((arr > threshold) * 255).astype(np.uint8))


# ---------------------------------------------------------------------------
def preprocess_image(img: Image.Image, steps: list[str] | None = None) -> dict:
    """Orchestrateur. steps par défaut : grayscale → deskew → denoise →
    contrast → binarize."""
    t0 = time.perf_counter()
    steps = steps or ["grayscale", "deskew", "denoise", "contrast", "binarize"]
    applied, angle = [], 0.0
    out = img
    for step in steps:
        if step == "grayscale":
            out = to_grayscale(out)
        elif step == "deskew":
            out, angle = deskew(out)
        elif step == "denoise":
            out = denoise(out)
        elif step == "contrast":
            out = enhance_contrast(out)
        elif step == "binarize":
            out = binarize(out)
        else:
            continue
        applied.append(step)
    return {
        "processed": out,
        "steps_applied": applied,
        "angle_corrected": round(angle, 2),
        "processing_time_sec": round(time.perf_counter() - t0, 3),
    }
