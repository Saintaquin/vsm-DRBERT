"""Moteurs OCR interchangeables derrière une interface commune.

TesseractEngine est le moteur de référence (léger, CPU, français).
DocTREngine, PaddleEngine et UnlimitedOCREngine sont chargés paresseusement et
seulement s'ils sont installés (lourdes dépendances deep-learning, optionnelles).

UnlimitedOCREngine (baidu/Unlimited-OCR, licence MIT — docs/ADR/0005) est en
plus conditionné à la présence d'une **carte graphique NVIDIA** (CUDA) : sans
GPU NVIDIA, la fonctionnalité n'existe pas (moteur absent de ENGINES)."""

from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from PIL import Image


@dataclass
class OCRResult:
    text: str
    confidence: float  # moyenne 0..1
    words: list[dict] = field(default_factory=list)  # {text, conf, bbox:[x,y,w,h]}
    engine: str = ""


class OCREngine(ABC):
    name: str = "abstract"

    @abstractmethod
    def recognize(self, image: Image.Image, lang: str = "fra") -> OCRResult: ...

    @classmethod
    def is_available(cls) -> bool:
        return False


class TesseractEngine(OCREngine):
    name = "tesseract"

    def recognize(self, image: Image.Image, lang: str = "fra") -> OCRResult:
        import pytesseract

        data = pytesseract.image_to_data(
            image, lang=lang, output_type=pytesseract.Output.DICT
        )
        words, confs = [], []
        for i, txt in enumerate(data["text"]):
            txt = txt.strip()
            conf = float(data["conf"][i])
            if not txt or conf < 0:
                continue
            words.append(
                {
                    "text": txt,
                    "conf": round(conf / 100.0, 3),
                    "bbox": [
                        data["left"][i],
                        data["top"][i],
                        data["width"][i],
                        data["height"][i],
                    ],
                    "line": (
                        data["block_num"][i],
                        data["par_num"][i],
                        data["line_num"][i],
                    ),
                }
            )
            confs.append(conf / 100.0)
        # Reconstruction du texte ligne par ligne
        lines: dict = {}
        for w in words:
            lines.setdefault(w["line"], []).append(w["text"])
        text = "\n".join(" ".join(ws) for _, ws in sorted(lines.items()))
        for w in words:
            w.pop("line")
        return OCRResult(
            text=text,
            confidence=round(sum(confs) / len(confs), 3) if confs else 0.0,
            words=words,
            engine=self.name,
        )

    @classmethod
    def is_available(cls) -> bool:
        try:
            import pytesseract

            pytesseract.get_tesseract_version()
            return True
        except Exception:
            return False


class DocTREngine(OCREngine):  # pragma: no cover - dépendance optionnelle
    name = "doctr"
    _model = None

    def recognize(self, image: Image.Image, lang: str = "fra") -> OCRResult:
        import numpy as np
        from doctr.models import ocr_predictor

        if DocTREngine._model is None:
            DocTREngine._model = ocr_predictor(pretrained=True)
        result = DocTREngine._model([np.asarray(image.convert("RGB"))])
        words, confs, lines_txt = [], [], []
        h, w0 = image.height, image.width
        for page in result.pages:
            for block in page.blocks:
                for line in block.lines:
                    lines_txt.append(" ".join(wd.value for wd in line.words))
                    for wd in line.words:
                        (x0, y0), (x1, y1) = wd.geometry
                        words.append(
                            {
                                "text": wd.value,
                                "conf": round(float(wd.confidence), 3),
                                "bbox": [
                                    int(x0 * w0),
                                    int(y0 * h),
                                    int((x1 - x0) * w0),
                                    int((y1 - y0) * h),
                                ],
                            }
                        )
                        confs.append(float(wd.confidence))
        return OCRResult(
            "\n".join(lines_txt),
            round(sum(confs) / len(confs), 3) if confs else 0.0,
            words,
            self.name,
        )

    @classmethod
    def is_available(cls) -> bool:
        try:
            import doctr  # noqa: F401

            return True
        except ImportError:
            return False


class PaddleEngine(OCREngine):  # pragma: no cover - dépendance optionnelle
    name = "paddle"
    _model = None

    def recognize(self, image: Image.Image, lang: str = "fra") -> OCRResult:
        import numpy as np
        from paddleocr import PaddleOCR

        if PaddleEngine._model is None:
            PaddleEngine._model = PaddleOCR(
                use_angle_cls=True, lang="fr", show_log=False
            )
        result = PaddleEngine._model.ocr(np.asarray(image.convert("RGB")), cls=True)
        words, confs, lines = [], [], []
        for page in result or []:
            for box, (txt, conf) in page or []:
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                words.append(
                    {
                        "text": txt,
                        "conf": round(float(conf), 3),
                        "bbox": [
                            int(min(xs)),
                            int(min(ys)),
                            int(max(xs) - min(xs)),
                            int(max(ys) - min(ys)),
                        ],
                    }
                )
                confs.append(float(conf))
                lines.append(txt)
        return OCRResult(
            "\n".join(lines),
            round(sum(confs) / len(confs), 3) if confs else 0.0,
            words,
            self.name,
        )

    @classmethod
    def is_available(cls) -> bool:
        try:
            import paddleocr  # noqa: F401

            return True
        except ImportError:
            return False


# ---------------------------------------------------------------------------
# Unlimited-OCR (baidu, licence MIT — docs/ADR/0005)
# Moteur OCR documentaire panoptique (parsing en une passe). EXIGE une carte
# NVIDIA (CUDA) : sans GPU NVIDIA, is_available() renvoie False et le moteur
# n'apparaît pas dans ENGINES (fonctionnalité inexistante, pas seulement
# désactivée — exigence produit). Strictement local (modèle en cache HF).
# ---------------------------------------------------------------------------
_DET_MARKER_RX = re.compile(r"<\|/?det\|>|\[[^\]]*\]")


def strip_det_markers(text: str) -> str:
    """Retire les marqueurs de structure <|det|>type [bbox]<|/det|> du modèle.

    Fonction pure (testable sans GPU) : le modèle renvoie des blocs balisés ;
    on conserve uniquement le contenu textuel, bloc par bloc."""
    out: list[str] = []
    for line in text.splitlines():
        line = line.rstrip()
        if not line:
            continue
        cleaned = _DET_MARKER_RX.sub("", line).strip()
        if cleaned:
            out.append(cleaned)
    return "\n".join(out)


def nvidia_gpu_available() -> bool:
    """True si une carte graphique NVIDIA + CUDA est détectée (via torch)."""
    try:
        import torch

        return bool(torch.cuda.is_available()) and torch.cuda.device_count() > 0
    except Exception:  # noqa: BLE001 - détection best-effort, ne doit jamais faire tomber l'app
        return False


class UnlimitedOCREngine(OCREngine):  # pragma: no cover - nécessite GPU NVIDIA
    name = "unlimited"
    _model = None
    _tokenizer = None

    @classmethod
    def _load(cls):
        """Charge le modèle (cache Hugging Face ; strictement local)."""
        if cls._model is None:
            import torch
            from transformers import AutoModel, AutoTokenizer

            model_name = os.environ.get("VSM_UNLIMITED_MODEL", "baidu/Unlimited-OCR")
            cls._tokenizer = AutoTokenizer.from_pretrained(
                model_name, trust_remote_code=True
            )
            cls._model = (
                AutoModel.from_pretrained(
                    model_name,
                    trust_remote_code=True,
                    use_safetensors=True,
                    torch_dtype=torch.bfloat16,
                )
                .eval()
                .cuda()
            )
        return cls._model, cls._tokenizer

    @staticmethod
    def _read_outputs(out_dir) -> str:
        """Rassemble le texte parsé produit par model.infer (fichiers *.txt)."""
        from pathlib import Path

        paths = sorted(Path(out_dir).rglob("*.txt"))
        if not paths:
            raise RuntimeError(
                f"Aucune sortie texte d'Unlimited-OCR dans {out_dir} "
                f"(contenu : {[p.name for p in Path(out_dir).rglob('*')][:10]})"
            )
        return "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in paths)

    def recognize(self, image: Image.Image, lang: str = "fra") -> OCRResult:
        import tempfile
        from pathlib import Path

        model, tokenizer = self._load()
        with tempfile.TemporaryDirectory() as td:
            img_path = Path(td) / "page.png"
            image.save(img_path)
            out_dir = Path(td) / "out"
            model.infer(
                tokenizer,
                prompt="<image>document parsing.",
                image_file=str(img_path),
                output_path=str(out_dir),
                base_size=1024,
                image_size=640,
                crop_mode=True,
                max_length=32768,
                no_repeat_ngram_size=35,
                ngram_window=128,
                save_results=True,
            )
            raw = self._read_outputs(out_dir)
        text = strip_det_markers(raw).strip()
        # Le modèle ne fournit pas de confiance mot à mot : valeur neutre
        # documentée (0,9) ; les champs restent « À valider » via le NLP.
        return OCRResult(
            text=text,
            confidence=0.9 if text else 0.0,
            words=[],
            engine=self.name,
        )

    @classmethod
    def is_available(cls) -> bool:
        # EXIGENCE : carte NVIDIA obligatoire — sinon la fonctionnalité
        # n'existe pas (moteur absent de ENGINES, API/UI le refusent).
        if not nvidia_gpu_available():
            return False
        try:
            import torch  # noqa: F401
            import transformers  # noqa: F401

            return True
        except ImportError:
            return False


_ALL_ENGINES = [TesseractEngine, DocTREngine, PaddleEngine, UnlimitedOCREngine]
ENGINES: dict[str, type[OCREngine]] = {
    e.name: e for e in _ALL_ENGINES if e.is_available()
}


def get_engine(name: str = "tesseract") -> OCREngine:
    if name not in ENGINES:
        raise ValueError(
            f"Moteur OCR '{name}' indisponible. Installés : {sorted(ENGINES)}"
        )
    return ENGINES[name]()
