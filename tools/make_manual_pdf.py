"""Génère docs/MANUAL_INSTALLATION.pdf depuis docs/MANUAL_INSTALLATION.md.

Rendu reportlab Platypus (aucune dépendance nouvelle) : titres, paragraphes,
blocs de code, listes, citations, tableaux markdown. La police Unicode
(Segoe UI / DejaVu Sans / Arial) est détectée automatiquement — sans elle,
les caractères hors Latin-1 sont translittérés.

Usage :  py -3.12 tools/make_manual_pdf.py
"""
from __future__ import annotations

import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

RACINE = Path(__file__).resolve().parents[1]
MD = RACINE / "docs" / "MANUAL_INSTALLATION.md"
PDF = RACINE / "docs" / "MANUAL_INSTALLATION.pdf"

# --- Police Unicode : première trouvée sur la machine -----------------------
_CANDIDATS = [
    ("DejaVuSans", [
        r"C:\Windows\Fonts\dejavusans.ttf",
        r"C:\Windows\Fonts\DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]),
    ("SegoeUI", [r"C:\Windows\Fonts\segoeui.ttf"]),
    ("Arial", [r"C:\Windows\Fonts\arial.ttf"]),
    ("Calibri", [r"C:\Windows\Fonts\calibri.ttf"]),
]
_GRAS_CANDIDATS = [
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\calibrib.ttf",
]


def _trouver_police() -> tuple[str, str | None]:
    for nom, chemins in _CANDIDATS:
        for c in chemins:
            if Path(c).is_file():
                gras = next((g for g in _GRAS_CANDIDATS if Path(g).is_file()), None)
                return nom, gras
    return "Helvetica", None  # chute : Latin-1 uniquement


NOM_POLICE, GRAS = _trouver_police()
if GRAS:
    pdfmetrics.registerFont(TTFont(NOM_POLICE, GRAS))

# --- Conversions markdown-inline -> mini-HTML reportlab ----------------------
_RX_GRAS = re.compile(r"\*\*(.+?)\*\*")
_RX_CODE = re.compile(r"`([^`]+)`")
_RX_LIEN = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _echappe(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def inline_html(t: str) -> str:
    t = _echappe(t)
    t = _RX_LIEN.sub(r'<link href="\2" color="blue">\1</link>', t)
    t = _RX_GRAS.sub(r"<b>\1</b>", t)
    t = _RX_CODE.sub(
        r'<font face="Courier" size="8.5" color="#0b3d62">\1</font>', t
    )
    return t


# --- Styles ----------------------------------------------------------------
st = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=st["Heading1"], fontName=NOM_POLICE,
                    fontSize=17, leading=21, spaceAfter=4 * mm, textColor=colors.HexColor("#16537e"))
H2 = ParagraphStyle("H2", parent=st["Heading2"], fontName=NOM_POLICE,
                    fontSize=13, leading=17, spaceBefore=6 * mm, spaceAfter=3 * mm,
                    textColor=colors.HexColor("#16537e"))
CORPS = ParagraphStyle("CORPS", parent=st["BodyText"], fontName=NOM_POLICE,
                       fontSize=9.5, leading=13.5, alignment=TA_LEFT)
CODE = ParagraphStyle("CODE", parent=st["Code"], fontName="Courier", fontSize=8.3,
                      leading=10.5, backColor=colors.HexColor("#f2f4f7"),
                      borderPadding=4, spaceBefore=2 * mm, spaceAfter=2 * mm)
CITE = ParagraphStyle("CITE", parent=CORPS, fontName=NOM_POLICE, fontSize=9,
                      leftIndent=6 * mm, textColor=colors.HexColor("#7a5c00"),
                      backColor=colors.HexColor("#fff8e1"), borderPadding=5,
                      spaceBefore=2 * mm, spaceAfter=2 * mm)
LI = ParagraphStyle("LI", parent=CORPS, leftIndent=5 * mm, bulletIndent=1 * mm,
                    spaceBefore=1 * mm, spaceAfter=1 * mm)


def _en_tete_pied(canvas, doc):
    canvas.saveState()
    canvas.setFont(NOM_POLICE, 7.5)
    canvas.setFillColor(colors.HexColor("#888888"))
    canvas.drawString(18 * mm, 10 * mm, "VSM-OCR — Manuel d'installation")
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"page {doc.page}")
    canvas.restoreState()


def _est_tableau(lignes: list[str], i: int) -> bool:
    return i + 1 < len(lignes) and lignes[i].strip().startswith("|") and \
        re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", lignes[i + 1]) is not None


def _bloc_tableau(lignes: list[str], i: int) -> tuple[Table, int]:
    """Parse un tableau markdown ; retourne (Table, index de fin)."""
    entetes = [c.strip() for c in lignes[i].strip().strip("|").split("|")]
    i += 2  # saute la ligne de séparation
    data = [[inline_html(c.strip()) for c in entetes]]
    while i < len(lignes) and lignes[i].strip().startswith("|"):
        cells = [c.strip() for c in lignes[i].strip().strip("|").split("|")]
        data.append([inline_html(c) for c in cells])
        i += 1
    # largeur : proportionnelle à la page
    table = Table(data, colWidths=[(A4[0] - 36 * mm) / len(entetes)] * len(entetes),
                  repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eaf1f8")),
        ("FONTNAME", (0, 0), (-1, 0), NOM_POLICE),
        ("FONTSIZE", (0, 0), (-1, -1), 8.2),
        ("LEADING", (0, 0), (-1, -1), 10.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#16537e")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c6d3e0")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#f7fafc")]),
    ]))
    return table, i


def convertir() -> list:
    lignes = MD.read_text(encoding="utf-8").splitlines()
    flux: list = []
    i = 0
    while i < len(lignes):
        ligne = lignes[i]
        nu = ligne.strip()
        if not nu:
            flux.append(Spacer(1, 2 * mm))
            i += 1
            continue
        if nu.startswith("```"):  # bloc de code
            bloc = []
            i += 1
            while i < len(lignes) and not lignes[i].strip().startswith("```"):
                bloc.append(lignes[i])
                i += 1
            i += 1
            texte = "\n".join(bloc).replace("&", "&amp;").replace("<", "&lt;")
            flux.append(Paragraph(texte.replace("\n", "<br/>"), CODE))
            continue
        if nu.startswith("|") and _est_tableau(lignes, i):
            table, i = _bloc_tableau(lignes, i)
            flux.append(table)
            continue
        if nu.startswith(">"):  # citation
            flux.append(Paragraph(inline_html(nu[1:].strip()), CITE))
            i += 1
            continue
        if nu.startswith("#### "):
            flux.append(Paragraph(inline_html(nu[5:]), ParagraphStyle(
                "H3", parent=H2, fontSize=11, spaceBefore=3 * mm)))
            i += 1
            continue
        if nu.startswith("### "):
            flux.append(Paragraph(inline_html(nu[4:]), H2))
            i += 1
            continue
        if nu.startswith("## "):
            flux.append(Paragraph(inline_html(nu[3:]), H2))
            i += 1
            continue
        if nu.startswith("# "):
            flux.append(Paragraph(inline_html(nu[2:]), H1))
            i += 1
            continue
        m = re.match(r"^(\d+)\.\s+(.*)$", nu)
        if m:
            flux.append(Paragraph(inline_html(m.group(2)), LI,
                                  bulletText=f"{m.group(1)}."))
            i += 1
            continue
        if nu.startswith("- "):
            flux.append(Paragraph(inline_html(nu[2:]), LI, bulletText="•"))
            i += 1
            continue
        flux.append(Paragraph(inline_html(nu), CORPS))
        i += 1
    return flux


def main() -> int:
    if not MD.is_file():
        print(f"[ÉCHEC] {MD} introuvable")
        return 1
    doc = SimpleDocTemplate(
        str(PDF), pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title="VSM-OCR — Manuel d'installation",
        author="VSM-OCR",
    )
    doc.build(convertir(), onFirstPage=_en_tete_pied, onLaterPages=_en_tete_pied)
    taille_kb = PDF.stat().st_size // 1024
    print(f"[OK] {PDF} généré ({taille_kb} Ko) — police : {NOM_POLICE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
