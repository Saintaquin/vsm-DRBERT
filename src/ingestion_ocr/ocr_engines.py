"""Moteurs OCR interchangeables derrière une interface commune.

TesseractEngine est le moteur de référence (léger, CPU, français).
DocTREngine et PaddleEngine sont chargés paresseusement et seulement
s'ils sont installés (lourdes dépendances deep-learning, optionnelles)."""

from __future__ import annotations

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


_ALL_ENGINES = [TesseractEngine, DocTREngine, PaddleEngine]
ENGINES: dict[str, type[OCREngine]] = {
    e.name: e for e in _ALL_ENGINES if e.is_available()
}


def get_engine(name: str = "tesseract") -> OCREngine:
    if name not in ENGINES:
        raise ValueError(
            f"Moteur OCR '{name}' indisponible. Installés : {sorted(ENGINES)}"
        )
    return ENGINES[name]()
