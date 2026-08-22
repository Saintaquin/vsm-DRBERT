"""Rendu du VSM : markdown, HTML et PDF conformes au gabarit HAS du Volet de
Synthèse Médicale (voir docs/gabarit_vsm.md et contexte/ pour des exemples).

Structure du document généré (9 rubriques numérotées) :
  1. Identification du patient (pseudonymisée)
  2. Médecin traitant
  3. Pathologies actives
  4. Antécédents médicaux et chirurgicaux
  5. Allergies et intolérances
  6. Traitements au long cours
  7. Facteurs de risque
  8. Vaccinations
  9. Points de vigilance

Exigences règlementaires (concours IA & Santé) :
- Aucune PII en clair : seules les valeurs pseudonymisées du JSON sont rendues ;
- Avertissement médical obligatoire (« à valider par un médecin ») ;
- Explicabilité (XAI) : confiance et source sur chaque champ, badge « À valider »
  pour les champs sous le seuil ;
- Zone de signature médecin + date ;
- Date de génération et statut du document.

Tout champ marqué a_valider est rendu avec un signal visuel distinct."""

from __future__ import annotations

import html as _html
from pathlib import Path

from .vsm_builder import ORDRE_HAS, TITRES

# Rubriques d'identité (objets champ_trace, rendues avant les sections cliniques)
IDENTITE_CHAMPS = ("identite", "date_naissance", "sexe", "ins")
MEDECIN_CHAMPS = ("identite", "rpps", "adelI")
TITRES_IDENTITE = {
    "identite": "Identité",
    "date_naissance": "Date de naissance",
    "sexe": "Sexe",
    "ins": "INS / NIR",
}
TITRES_MEDECIN = {
    "identite": "Identité",
    "rpps": "RPPS",
    "adelI": "ADELI",
}

AVERTISSEMENT_HTML = (
    "Document généré automatiquement à partir de documents scannés. "
    "Le contenu n'a PAS été vérifié médicalement : il doit être relu, "
    "corrigé si nécessaire et validé par un médecin avant tout usage clinique."
)


def _badge(champ: dict) -> str:
    conf = champ.get("confiance", 0)
    return f"⚠ À valider ({conf:.0%})" if champ.get("a_valider") else f"✓ {conf:.0%}"


def _code_txt(it: dict) -> str:
    code = it.get("code_normalise")
    if not code:
        return ""
    return f" — **{code['systeme']} {code['code']}** ({code['libelle_officiel']})"


def _identite_lignes(vsm: dict, champs: tuple[str, ...], titres: dict) -> list[str]:
    """Lignes des champs d'identité présents (identité/naissance/sexe/INS…)."""
    bloc = vsm.get("patient" if champs is IDENTITE_CHAMPS else "medecin_traitant", {})
    lignes = []
    for key in champs:
        champ = bloc.get(key)
        if not champ:
            continue
        valeur = champ.get("valeur", "")
        lignes.append(f"- **{titres[key]} :** {valeur} · {_badge(champ)}")
    return lignes


def render_markdown(vsm: dict) -> str:
    lines = [
        "# Volet de Synthèse Médicale (VSM)",
        "",
        f"*Généré le {vsm.get('date_generation', '')} — statut : {vsm.get('statut', '')}*",
        "",
        f"> {vsm.get('avertissement', '')}",
        "",
        "## 1. Identification du patient",
    ]
    id_lignes = _identite_lignes(vsm, IDENTITE_CHAMPS, TITRES_IDENTITE)
    lines += id_lignes or ["_Non renseignée (mode strict ou document non identifié)._"]
    lines += ["", "## 2. Médecin traitant"]
    med_lignes = _identite_lignes(vsm, MEDECIN_CHAMPS, TITRES_MEDECIN)
    lines += med_lignes or ["_Non renseigné._"]

    for i, key in enumerate(ORDRE_HAS, start=3):
        items = vsm["sections"].get(key, [])
        comp = vsm.get("completude", {}).get(key, 0)
        lines.append("")
        lines.append(f"## {i}. {TITRES[key]}  `complétude {comp:.0%}`")
        if not items:
            lines.append(
                "_Aucune information extraite — section à compléter manuellement._"
            )
        for it in items:
            lines.append(f"- {it['valeur']}{_code_txt(it)} · {_badge(it)}")

    sig = vsm.get("signature")
    lines += ["", "---", ""]
    if sig:
        lines.append(
            f"**Signé par** {sig.get('signe_par', '')} le "
            f"{sig.get('date_signature', '')} — empreinte `{sig.get('empreinte_vsm', '')[:16]}…`"
        )
    else:
        lines.append(
            "**Signature du médecin :** ____________________    Date : ____ / ____ / ________"
        )
    return "\n".join(lines)


_CSS = """
body{font-family:'Segoe UI',Arial,sans-serif;max-width:820px;margin:2rem auto;color:#1a1a2e;line-height:1.55}
h1{border-bottom:3px solid #16537e;padding-bottom:.4rem;color:#16537e}
.subtitle{color:#555;font-size:.9rem;margin-top:-.4rem}
.warn{background:#fff8e1;border-left:4px solid #f9a825;padding:.7rem 1rem;font-size:.92rem}
.identite{background:#f4f8fb;border:1px solid #d0d7de;border-radius:6px;padding:.7rem 1rem;margin:.6rem 0}
.identite ul{margin:.3rem 0;padding-left:1.2rem;list-style:none}
.identite li{margin:.15rem 0}
section{margin:1.3rem 0}
h2{color:#16537e;font-size:1.02rem;display:flex;justify-content:space-between;border-bottom:1px solid #d0d7de;padding-bottom:.25rem}
.comp{font-size:.8rem;color:#555;font-weight:normal}
ul{padding-left:1.2rem}li{margin:.35rem 0}
.tovalidate{background:#fdecea;border-left:3px solid #c0392b;padding:.25rem .5rem;border-radius:3px}
.ok{color:#1e7e34;font-size:.82rem}.warn-badge{color:#c0392b;font-size:.82rem;font-weight:600}
.code{background:#eef3f8;border-radius:3px;padding:0 .35rem;font-size:.85rem}
.empty{color:#888;font-style:italic}
.signature{margin-top:3rem;border-top:1px dashed #999;padding-top:1rem}
.footer{font-size:.78rem;color:#666;margin-top:2rem;border-top:1px solid #ddd;padding-top:.6rem}
"""


def _identite_html(vsm: dict, champs: tuple[str, ...], titres: dict, titre: str) -> str:
    e = _html.escape
    bloc = vsm.get("patient" if champs is IDENTITE_CHAMPS else "medecin_traitant", {})
    items = []
    for key in champs:
        champ = bloc.get(key)
        if not champ:
            continue
        badge = (
            f"<span class='warn-badge'>⚠ À valider ({champ.get('confiance', 0):.0%})</span>"
            if champ.get("a_valider")
            else f"<span class='ok'>✓ {champ.get('confiance', 0):.0%}</span>"
        )
        items.append(
            f"<li><strong>{e(titres[key])} :</strong> {e(str(champ.get('valeur', '')))} · {badge}</li>"
        )
    if not items:
        items.append("<li class='empty'>Non renseigné.</li>")
    return f"<section class='identite'><h2>{e(titre)}</h2><ul>{''.join(items)}</ul></section>"


def render_html(vsm: dict) -> str:
    e = _html.escape
    parts = [
        (
            f"<!doctype html><html lang='fr'><head><meta charset='utf-8'>"
            f"<title>Volet de Synthèse Médicale</title><style>{_CSS}</style></head><body>"
        ),
        "<h1>Volet de Synthèse Médicale (VSM)</h1>",
        (
            f"<p class='subtitle'>Généré le {e(str(vsm.get('date_generation', '')))} "
            f"— statut : {e(vsm.get('statut', ''))}</p>"
        ),
        f"<div class='warn'>{e(vsm.get('avertissement', ''))}</div>",
        _identite_html(
            vsm, IDENTITE_CHAMPS, TITRES_IDENTITE, "1. Identification du patient"
        ),
        _identite_html(vsm, MEDECIN_CHAMPS, TITRES_MEDECIN, "2. Médecin traitant"),
    ]
    for i, key in enumerate(ORDRE_HAS, start=3):
        items = vsm["sections"].get(key, [])
        comp = vsm.get("completude", {}).get(key, 0)
        parts.append(
            f"<section><h2>{i}. {e(TITRES[key])}<span class='comp'>complétude {comp:.0%}</span></h2>"
        )
        if not items:
            parts.append(
                "<p class='empty'>Aucune information extraite — à compléter manuellement.</p>"
            )
        else:
            parts.append("<ul>")
            for it in items:
                cls = " class='tovalidate'" if it.get("a_valider") else ""
                badge = (
                    f"<span class='warn-badge'>⚠ À valider ({it.get('confiance', 0):.0%})</span>"
                    if it.get("a_valider")
                    else f"<span class='ok'>✓ {it.get('confiance', 0):.0%}</span>"
                )
                code = it.get("code_normalise")
                code_html = (
                    f" <span class='code'>{e(code['systeme'])} {e(code['code'])} — {e(code['libelle_officiel'])}</span>"
                    if code
                    else ""
                )
                src = it.get("source", {}).get("passage", "")
                parts.append(
                    f"<li{cls} title='Source : {e(src)}'>{e(it['valeur'])}{code_html} · {badge}</li>"
                )
            parts.append("</ul>")
        parts.append("</section>")

    sig = vsm.get("signature")
    if sig:
        parts.append(
            f"<div class='signature'><strong>Signé par {e(str(sig.get('signe_par', '')))}</strong> "
            f"le {e(str(sig.get('date_signature', '')))} — empreinte "
            f"<code>{e(str(sig.get('empreinte_vsm', '')))[:16]}…</code></div>"
        )
    else:
        parts.append(
            "<div class='signature'><strong>Signature du médecin :</strong> "
            "____________________ &nbsp;&nbsp; Date : ____ / ____ / ________</div>"
        )
    parts.append(f"<div class='footer'>{e(AVERTISSEMENT_HTML)}</div></body></html>")
    return "".join(parts)


def render_pdf(vsm: dict, out_path: str | Path) -> Path:
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    out_path = Path(out_path)
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Title"], textColor=HexColor("#16537e"))
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], textColor=HexColor("#16537e"))
    warn = ParagraphStyle(
        "warn", parent=styles["Normal"], backColor=HexColor("#fff8e1"), fontSize=8.5
    )
    ident = ParagraphStyle(
        "ident", parent=styles["Normal"], backColor=HexColor("#f4f8fb"), fontSize=9
    )
    item_ok = ParagraphStyle("item", parent=styles["Normal"], leftIndent=8 * mm)
    item_warn = ParagraphStyle("itemw", parent=item_ok, backColor=HexColor("#fdecea"))

    def champ_lignes(bloc: dict, champs: tuple[str, ...], titres: dict) -> list[str]:
        lignes = []
        for key in champs:
            champ = bloc.get(key)
            if not champ:
                continue
            badge = (
                f"À VALIDER ({champ.get('confiance', 0):.0%})"
                if champ.get("a_valider")
                else f"{champ.get('confiance', 0):.0%}"
            )
            lignes.append(
                f"• <b>{_html.escape(titres[key])} :</b> "
                f"{_html.escape(str(champ.get('valeur', '')))} — {badge}"
            )
        return lignes or ["<i>Non renseigné.</i>"]

    flow = [
        Paragraph("Volet de Synthèse Médicale (VSM)", h1),
        Paragraph(
            f"Généré le {_html.escape(str(vsm.get('date_generation', '')))} — "
            f"statut : {_html.escape(vsm.get('statut', ''))}",
            styles["Normal"],
        ),
        Paragraph(_html.escape(vsm.get("avertissement", "")), warn),
        Spacer(1, 4 * mm),
    ]
    # 1. Identification du patient
    flow.append(Paragraph("1. Identification du patient", h2))
    for ligne in champ_lignes(vsm.get("patient", {}), IDENTITE_CHAMPS, TITRES_IDENTITE):
        flow.append(Paragraph(ligne, ident))
    flow.append(Spacer(1, 2 * mm))
    # 2. Médecin traitant
    flow.append(Paragraph("2. Médecin traitant", h2))
    for ligne in champ_lignes(
        vsm.get("medecin_traitant", {}), MEDECIN_CHAMPS, TITRES_MEDECIN
    ):
        flow.append(Paragraph(ligne, ident))
    flow.append(Spacer(1, 3 * mm))
    # 3..9 — sections cliniques
    for i, key in enumerate(ORDRE_HAS, start=3):
        items = vsm["sections"].get(key, [])
        comp = vsm.get("completude", {}).get(key, 0)
        flow.append(Paragraph(f"{i}. {TITRES[key]} — complétude {comp:.0%}", h2))
        if not items:
            flow.append(Paragraph("<i>Aucune information extraite.</i>", item_ok))
        for it in items:
            code = it.get("code_normalise")
            code_txt = f" [{code['systeme']} {code['code']}]" if code else ""
            badge = (
                f"À VALIDER ({it.get('confiance', 0):.0%})"
                if it.get("a_valider")
                else f"{it.get('confiance', 0):.0%}"
            )
            style = item_warn if it.get("a_valider") else item_ok
            flow.append(
                Paragraph(f"• {_html.escape(it['valeur'])}{code_txt} — {badge}", style)
            )
        flow.append(Spacer(1, 3 * mm))

    sig = vsm.get("signature")
    flow += [Spacer(1, 10 * mm)]
    if sig:
        flow.append(
            Paragraph(
                f"<b>Signé par {_html.escape(str(sig.get('signe_par', '')))}</b> le "
                f"{_html.escape(str(sig.get('date_signature', '')))} — empreinte "
                f"{_html.escape(str(sig.get('empreinte_vsm', '')))[:16]}…",
                styles["Normal"],
            )
        )
    else:
        flow.append(
            Paragraph(
                "Signature du médecin : ____________________ Date : ____/____/________",
                styles["Normal"],
            )
        )
    flow.append(
        Paragraph(
            f"<font size=8 color='#666666'>{_html.escape(AVERTISSEMENT_HTML)}</font>",
            styles["Normal"],
        )
    )
    SimpleDocTemplate(str(out_path), pagesize=A4).build(flow)
    return out_path


def render_vsm(vsm: dict, fmt: str = "markdown", out_path: str | Path | None = None):
    if fmt == "markdown":
        return render_markdown(vsm)
    if fmt == "html":
        return render_html(vsm)
    if fmt == "pdf":
        if out_path is None:
            raise ValueError("out_path requis pour le format pdf")
        return render_pdf(vsm, out_path)
    raise ValueError(f"Format inconnu : {fmt}")
