import numpy as np
from PIL import Image

from src.ingestion_ocr.preprocessing import binarize, deskew, preprocess_image


def _doc_image(angle=0.0):
    img = Image.new("L", (400, 300), 255)
    arr = np.asarray(img).copy()
    for y in range(40, 260, 30):
        arr[y : y + 4, 30:370] = 0
    img = Image.fromarray(arr)
    return img.rotate(angle, expand=True, fillcolor=255) if angle else img


def test_preprocess_returns_contract():
    out = preprocess_image(_doc_image())
    assert set(out) == {
        "processed",
        "steps_applied",
        "angle_corrected",
        "processing_time_sec",
    }
    assert out["steps_applied"][0] == "grayscale"


def test_deskew_reduces_angle():
    _, angle = deskew(_doc_image(angle=3.0))
    assert abs(angle) > 0.5


def test_binarize_is_binary():
    arr = np.asarray(binarize(_doc_image()))
    assert set(np.unique(arr)).issubset({0, 255})
