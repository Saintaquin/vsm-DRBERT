# -*- coding: utf-8 -*-
"""Rendu PDF du document de conformité 'Licences tiers' depuis le Markdown.

Usage : py -3.12 -m scripts.gen_licences_pdf
Produit outputs/LICENCES_TIERS.pdf (Rapport Markdown -> PDF via ReportLab).
Script utilitaire, non embarqué dans l'application.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "outputs" / "LICENCES_TIERS.md"
OUT = ROOT / "outputs" / "LICENCES_TIERS.pdf"

ACCENT = colors.HexColor("#14b8a6")  # teal
DARK = colors.HexColor("#0f172a")
LIGHT = colors.HexColor("#f1f5f9")

styles = getSampleStyleSheet()
title = ParagraphStyle("ti", parent=styles["Title"], fontName="Helvetica-Bold",
                       fontSize=20, textColor=DARK, spaceAfter=8)
h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontName="Helvetica-Bold",
                    fontSize=15, textColor=ACCENT, spaceBefore=14, spaceAfter=6)
h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontName="Helvetica-Bold",
                    fontSize=12.5, textColor=DARK, spaceBefore=10, spaceAfter=4)
body = ParagraphStyle("body", parent=styles["BodyText"], fontName="Helvetica",
                      fontSize=10, leading=14, alignment=TA_LEFT, spaceAfter=4)
small = ParagraphStyle("small", parent=body, fontSize=9, textColor=colors.HexColor("#334155"))
quote = ParagraphStyle("quote", parent=body, leftIndent=8, textColor=colors.HexColor("#475569"),
                       borderPadding=6, backColor=LIGHT, spaceAfter=6)


def inline(text: str) -> str:
    """Mini-parseur Markdown inline -> balises ReportLab."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"`(.+?)`", r"<font face='Courier'><font size=9>\1</font></font>", text)
    text = re.sub(r"\[(.+?)\]\((.+?)\)", r'<link href="\2"><u>\1</u></link>', text)
    return text


def paragraphs_from_lines(lines: list[str]) -> list:
    out, buf = [], []
    for ln in lines:
        ln = ln.rstrip()
        if not ln.strip():
            if buf:
                out.append(Paragraph(inline(" ".join(buf)), body))
                buf = []
            continue
        buf.append(ln)
    if buf:
        out.append(Paragraph(inline(" ".join(buf)), body))
    return out


def build_flowables(md: str) -> list:
    flow = []
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i].rstrip()
        stripped = ln.strip()
        if not stripped:
            i += 1
            continue
        # Titre
        if re.match(r"^# ", stripped):
            flow.append(Paragraph(inline(stripped[2:]), title)); i += 1; continue
        if re.match(r"^## ", stripped):
            flow.append(Paragraph(inline(stripped[3:]), h1)); i += 1; continue
        if re.match(r"^### ", stripped):
            flow.append(Paragraph(inline(stripped[4:]), h2)); i += 1; continue
        # Ligne de séparation
        if set(stripped) <= {"-"} and len(stripped) >= 3:
            i += 1; continue
        # Blockquote
        if stripped.startswith(">"):
            qb = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                qb.append(lines[i].strip()[1:].lstrip())
                i += 1
            flow.append(Paragraph(inline(" ".join(qb)), quote)); continue
        # Tableau
        if stripped.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip()):
            header = [c.strip() for c in stripped.strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            data = [[Paragraph(inline(h), small) for h in header]]
            for r in rows:
                data.append([Paragraph(inline(c), small) for c in r])
            tbl = Table(data, hAlign="LEFT", repeatRows=1)
            tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            flow.append(tbl)
            flow.append(Spacer(1, 8))
            continue
        # Liste ordonnée
        if re.match(r"^\d+\.\s", stripped):
            items = []
            while i < len(lines) and re.match(r"^\d+\.\s", lines[i].strip()):
                items.append(ListItem(Paragraph(inline(re.sub(r"^\d+\.\s", "", lines[i].strip())), body),
                                      leftIndent=14))
                i += 1
            flow.append(ListFlowable(items, bulletType="1", leftIndent=14))
            continue
        # Liste à puces
        if stripped.startswith("- "):
            items = []
            while i < len(lines) and lines[i].strip().startswith("- "):
                items.append(ListItem(Paragraph(inline(lines[i].strip()[2:]), body), leftIndent=12))
                i += 1
            flow.append(ListFlowable(items, bulletType="bullet", leftIndent=12))
            continue
        # Paragraphe normal (groupe)
        pbuf = [stripped]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(
            r"^(#|\||>|- |\d+\.\s|$)", lines[i].strip()):
            pbuf.append(lines[i].strip())
            i += 1
        flow.append(Paragraph(inline(" ".join(pbuf)), body))
    return flow


def main() -> int:
    if not SRC.exists():
        print(f"Source introuvable : {SRC}")
        return 1
    md = SRC.read_text(encoding="utf-8")
    doc = SimpleDocTemplate(str(OUT), pagesize=A4,
                            leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=18 * mm, bottomMargin=18 * mm,
                            title="Licences tiers — VSM-OCR")
    story = build_flowables(md)
    doc.build(story)
    print(f"PDF généré : {OUT} ({OUT.stat().st_size} octets)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
