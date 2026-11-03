"""Export PDF des rapports (marges, ventes, états financiers de synthèse, journal, balance, grand livre).

Le PDF est construit à partir des MÊMES calculs que l'écran (margins, sales_reports, financials) : un seul jeu de chiffres, deux présentations. Chaque rapport est d'abord décrit par
des « blocs » (titres, indicateurs, tableaux, notes) puis dessiné avec ReportLab. L'en-tête reprend le nom de l'entreprise et son logo, le pied de page porte la date d'édition, la
personne qui l'a éditée et la pagination. Les rapports comptables rappellent qu'ils sont à faire valider par un expert-comptable.
"""
from __future__ import annotations

import base64
import io
from datetime import datetime, timezone
from xml.sax.saxutils import escape

from . import accounting, financials, margins, sales_reports
from .i18n import tr as i18n_tr
from .sales_ops import settings_get

ACCOUNTING_WARNING = "Document de travail inspiré du SYSCOHADA, établi sans accès au texte officiel complet : à faire valider par un expert-comptable avant tout usage officiel ou déclaratif."


def n(v) -> str:
    return "" if v is None else f"{int(v):,}".replace(",", " ")


def pct(v) -> str:
    return "—" if v is None else f"{v} %"


def fd(iso: str | None) -> str:
    return "/".join(reversed(iso.split("-"))) if iso else ""


# ---------------------------------------------------------------- description des rapports (blocs)
def margins_blocks(c, a: str, b: str, kind: str) -> tuple[str, list[dict], bool]:
    r = margins.compute(c, a, b); T = r["totals"]
    blocks = [{"type": "kpis", "items": [("CA hors taxe", n(T["revenue"]) + " GNF"), ("Marge estimée", n(T["margin"]) + " GNF"), ("Taux de marge", pct(T["margin_pct"])), ("CA sans coût connu", n(T["revenue_unknown"]) + " GNF")]}]
    if kind == "product":
        rows = [[g["name"], n(g["qty"]), n(g["revenue"]), "—" if g["avg_cost"] is None else n(g["avg_cost"]), "—" if g["margin"] is None else n(g["margin"]), pct(g["margin_pct"]), pct(g["share_of_margin"])] for g in r["by_product"]]
        blocks.append({"type": "table", "head": ["Produit", "Qté vendue", "CA hors taxe", "Coût moyen unit.", "Marge", "Marge %", "Part de la marge"], "rows": rows, "align": "lrrrrrr", "widths": [4, 1.3, 1.8, 1.8, 1.8, 1.1, 1.4]})
        title = "Marge par produit"
    else:
        rows = [[f"{g['name']}{' (' + g['code'] + ')' if g['code'] else ''}", n(g["sales"]), n(g["revenue"]), "—" if g["margin"] is None else n(g["margin"]), pct(g["margin_pct"]), n(g["balance"]) if g["balance"] else "", g["reliability"] or ""] for g in r["by_client"]]
        blocks.append({"type": "table", "head": ["Client", "Ventes", "CA hors taxe", "Marge", "Marge %", "Reste dû", "Fiabilité de paiement"], "rows": rows, "align": "lrrrrrl", "widths": [4, 1, 1.9, 1.9, 1.1, 1.8, 2]})
        title = "Marge par client"
    if r["credit_notes_total"]: blocks.append({"type": "p", "text": f"Avoirs de la période : {n(r['credit_notes_total'])} GNF (non déduits des marges ci-dessus)."})
    blocks.append({"type": "note", "text": r["note"]})
    return title, blocks, True


def sales_blocks(c, user, a: str, b: str, kind: str) -> tuple[str, list[dict], bool]:
    r = sales_reports.compute(c, user, a, b); T = r["totals"]
    blocks = [{"type": "kpis", "items": [("Ventes (TTC)", n(T["revenue"]) + " GNF"), ("Nombre de ventes", n(T["sales"])), ("Encaissé à ce jour", n(T["collected"]) + " GNF"), ("Reste dû", n(T["balance"]) + " GNF")]}]
    if kind == "seller":
        obj = r["has_targets"]
        rows = [[g["name"], n(g["sales"]), n(g["revenue"]), pct(g["share"]), n(g["avg_basket"]), pct(g["credit_share"]), f"{n(g['discount'])} ({g['discount_pct']} %)" if g["discount"] else "", n(g["balance"]) if g["balance"] else "", f"{g['cancelled']} ({n(g['cancelled_amount'])})" if g["cancelled"] else ""]
                + ([n(g["target_period"]) if g["target_period"] else "", pct(g["progress_pct"]) if g["target_period"] else ""] if obj else []) for g in r["by_seller"]]
        blocks.append({"type": "table", "head": ["Vendeur", "Ventes", "Montant TTC", "Part", "Panier moyen", "À crédit", "Remises accordées", "Reste dû", "Annulées"] + (["Objectif de la période", "Atteint"] if obj else []), "rows": rows,
                       "align": "lrrrrrrrr" + ("rr" if obj else ""), "widths": [3.0, 0.9, 1.8, 0.9, 1.5, 1.0, 2.0, 1.6, 1.7] + ([1.9, 1.1] if obj else [])})
        title = "Ventes par vendeur" if r["scope"] == "all" else "Mes ventes"
    elif kind == "category":
        rows = [[g["category"], n(g["qty"]), n(g["sales"]), n(g["revenue"]), n(g["revenue_ht"]), pct(g["share"])] for g in r["by_category"]]
        blocks.append({"type": "table", "head": ["Catégorie de produit", "Quantités", "Ventes", "Montant TTC", "Hors taxe", "Part"], "rows": rows, "align": "lrrrrr", "widths": [4.5, 1.6, 1.2, 2.2, 2.2, 1.2]})
        title = "Ventes par catégorie de produit"
    else:
        rows = [[m["month"], n(m["sales"]), n(m["revenue"]), n(m["revenue_ht"]), n(m["avg_basket"]), "—" if m["growth_pct"] is None else f"{'+' if m['growth_pct'] > 0 else ''}{m['growth_pct']} %", n(m["collected"])] for m in r["by_month"]]
        blocks.append({"type": "table", "head": ["Mois", "Ventes", "Montant TTC", "Hors taxe", "Panier moyen", "Évolution", "Encaissé dans le mois"], "rows": rows, "align": "lrrrrrr", "widths": [1.5, 1, 2, 2, 1.7, 1.3, 2.1]})
        title = "Ventes par mois"
    blocks.append({"type": "note", "text": r["note"]})
    return title, blocks, True


def financials_blocks(c, a: str, b: str) -> tuple[str, list[dict], bool]:
    r = financials.financial_statements(c, a, b); bs, inc, cf = r["balance_sheet"], r["income"], r["cash_flow"]
    blocks = [{"type": "p", "text": ("Contrôles réussis : " if r["all_ok"] else "CONTRÔLE EN ÉCHEC : ") + " · ".join(x["label"] + (" (ok)" if x["ok"] else " (ÉCHEC)") for x in r["checks"])},
              {"type": "h2", "text": f"Bilan au {fd(bs['as_of'])}"},
              {"type": "table", "head": ["ACTIF", "Brut", "Amortissements", "Net"], "align": "lrrr", "widths": [4, 1.6, 1.6, 1.6], "bold": [len(bs["assets"])],
               "rows": [[g["group"], n(g["gross"]), n(-g["dep"]) if g["dep"] else "", n(g["net"])] for g in bs["assets"]] + [["TOTAL ACTIF", n(bs["gross_total"]), n(-bs["dep_total"]) if bs["dep_total"] else "", n(bs["assets_total"])]]},
              {"type": "table", "head": ["PASSIF", "Montant"], "align": "lr", "widths": [6, 2], "bold": [len(bs["liabilities"])],
               "rows": [[g["group"], n(g["amount"])] for g in bs["liabilities"]] + [["TOTAL PASSIF", n(bs["liabilities_total"])]]},
              {"type": "h2", "text": "Compte de résultat par soldes intermédiaires"},
              {"type": "table", "head": ["Rubrique", "Montant"], "align": "lr", "widths": [6, 2], "bold": [i for i, x in enumerate(inc["lines"]) if x["kind"] == "total"], "rows": [[x["label"], n(x["amount"])] for x in inc["lines"]]},
              {"type": "h2", "text": "Tableau des flux de trésorerie"},
              {"type": "table", "head": ["Rubrique", "Montant"], "align": "lr", "widths": [6, 2], "bold": [i for i, x in enumerate(cf["lines"]) if x["kind"] == "total"], "rows": [[x["label"], n(x["amount"])] for x in cf["lines"]]},
              {"type": "note", "text": cf["note"]}, {"type": "h2", "text": "Informations générales"}] + [{"type": "p", "text": "• " + x} for x in r["notes"]]
    return "États financiers de synthèse", blocks, False


# ---------------------------------------------------------------- dessin
def render(company: dict, logo: bytes | None, title: str, period: str, user_name: str, blocks: list[dict], landscape_mode: bool, warn: bool, lang: str = "fr", signature: bytes | None = None) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    size = landscape(A4) if landscape_mode else A4
    buf = io.BytesIO(); gen = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    ss = getSampleStyleSheet()
    base = ParagraphStyle("b", parent=ss["Normal"], fontName="Helvetica", fontSize=8.5, leading=11)
    muted = ParagraphStyle("m", parent=base, textColor=colors.HexColor("#666666"), fontSize=7.5, leading=10)
    h1 = ParagraphStyle("h1", parent=base, fontName="Helvetica-Bold", fontSize=15, leading=19, spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=base, fontName="Helvetica-Bold", fontSize=11, leading=14, spaceBefore=8, spaceAfter=4)
    cell = ParagraphStyle("c", parent=base, fontSize=8, leading=10)
    cellb = ParagraphStyle("cb", parent=cell, fontName="Helvetica-Bold")
    kpi_l = ParagraphStyle("kl", parent=muted, fontSize=7)
    kpi_v = ParagraphStyle("kv", parent=base, fontName="Helvetica-Bold", fontSize=11, leading=13)

    class Numbered(rl_canvas.Canvas):
        def __init__(self, *a, **k):
            super().__init__(*a, **k); self._saved = []

        def showPage(self):
            self._saved.append(dict(self.__dict__)); self._startPage()

        def save(self):
            total = len(self._saved)
            for st in self._saved:
                self.__dict__.update(st); self._footer(total); super().showPage()
            super().save()

        def _footer(self, total):
            self.setFont("Helvetica", 7); self.setFillColor(colors.HexColor("#666666"))
            self.drawString(14 * mm, 8 * mm, (f"Issued on {gen} by {user_name} · Gestion libre" if lang == "en" else f"Édité le {gen} par {user_name} · Gestion libre"))
            self.drawRightString(size[0] - 14 * mm, 8 * mm, f"Page {self._pageNumber} / {total}")

    doc = SimpleDocTemplate(buf, pagesize=size, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=12 * mm, bottomMargin=16 * mm, title=title, author=company.get("company_name") or "Gestion libre")
    width = size[0] - 28 * mm
    story = []
    head_left = [Paragraph(f"<b>{escape(company.get('company_name') or 'Mon entreprise')}</b>", ParagraphStyle("cn", parent=base, fontSize=12, leading=15)),
                 Paragraph(escape(" · ".join(x for x in (company.get("address"), company.get("phone"), ("N° fiscal : " + company["tax_id"]) if company.get("tax_id") else "") if x)), muted)]
    if logo:
        try:
            from reportlab.lib.utils import ImageReader
            iw, ih = ImageReader(io.BytesIO(logo)).getSize(); hgt = 14 * mm
            story.append(Table([[Image(io.BytesIO(logo), width=hgt * iw / ih, height=hgt), head_left]], colWidths=[hgt * iw / ih + 4 * mm, width - hgt * iw / ih - 4 * mm], style=[("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
        except Exception:
            story.extend(head_left)                                           # logo illisible : on imprime sans
    else:
        story.extend(head_left)
    story += [Spacer(1, 4 * mm), Paragraph(escape(title), h1), Paragraph(escape(period), muted), Spacer(1, 3 * mm)]
    for blk in blocks:
        t = blk["type"]
        if t == "h2": story.append(Paragraph(escape(blk["text"]), h2))
        elif t == "p": story.append(Paragraph(escape(blk["text"]), base)); story.append(Spacer(1, 1.5 * mm))
        elif t == "note": story.append(Spacer(1, 2 * mm)); story.append(Paragraph(escape(blk["text"]), muted))
        elif t == "kpis":
            items = blk["items"]; cw = width / len(items)
            tb = Table([[Paragraph(escape(l), kpi_l) for l, _ in items], [Paragraph(escape(v), kpi_v) for _, v in items]], colWidths=[cw] * len(items))
            tb.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")), ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd")), ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f6f8f7")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            story += [tb, Spacer(1, 3 * mm)]
        elif t == "table":
            align = blk["align"]; rel = blk.get("widths") or [1] * len(blk["head"]); tot = sum(rel); bold = set(blk.get("bold", []))
            hs = [ParagraphStyle(f"h{i}", parent=cellb, alignment=2 if a == "r" else 0, textColor=colors.white) for i, a in enumerate(align)]
            data = [[Paragraph(escape(h_), hs[i]) for i, h_ in enumerate(blk["head"])]]
            for ri, row in enumerate(blk["rows"]):
                data.append([Paragraph(escape(str(v)), ParagraphStyle(f"c{ri}_{ci}", parent=cellb if ri in bold else cell, alignment=2 if align[ci] == "r" else 0)) for ci, v in enumerate(row)])
            if not blk["rows"]: data.append([Paragraph("No data for this period." if lang == "en" else "Aucune donnée sur cette période.", cell)] + [""] * (len(blk["head"]) - 1))
            tb = Table(data, colWidths=[width * w / tot for w in rel], repeatRows=1)
            style = [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2f5d50")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")), ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]
            for ri in range(1, len(data)):
                if ri % 2 == 0: style.append(("BACKGROUND", (0, ri), (-1, ri), colors.HexColor("#f6f8f7")))
            for bi in bold: style.append(("BACKGROUND", (0, bi + 1), (-1, bi + 1), colors.HexColor("#e3ece8")))
            tb.setStyle(TableStyle(style)); story += [tb, Spacer(1, 3 * mm)]
    if warn: story += [Spacer(1, 2 * mm), Paragraph(escape(i18n_tr(ACCOUNTING_WARNING, lang)), muted)]
    if signature:
        try:
            from reportlab.lib.utils import ImageReader
            iw, ih = ImageReader(io.BytesIO(signature)).getSize(); hgt = 18 * mm
            story += [Spacer(1, 5 * mm), KeepTogether([Paragraph("Signature autorisée" if lang == "fr" else "Authorized signature", muted), Image(io.BytesIO(signature), width=min(45 * mm, hgt * iw / ih), height=min(hgt, 45 * mm * ih / iw))])]
        except Exception:
            pass
    doc.build(story, canvasmaker=Numbered)
    return buf.getvalue()


def logo_bytes(c) -> bytes | None:
    r = c.execute("SELECT value FROM meta WHERE key='logo'").fetchone()
    if r is None: return None
    try: return base64.b64decode(r["value"])
    except Exception: return None


def signature_bytes(c) -> bytes | None:
    r = c.execute("SELECT value FROM meta WHERE key='signature'").fetchone()
    if r is None: return None
    try: return base64.b64decode(r["value"])
    except Exception: return None


def journal_blocks(c, a: str, b: str, code: str | None) -> tuple[str, list[dict], bool]:
    r = accounting.journal(c, a, b, code)
    rows, td, tc = [], 0, 0
    for e in r["entries"]:
        for i, ln in enumerate(e["lines"]):
            rows.append([fd(e["date"]) if i == 0 else "", e["journal"] if i == 0 else "", e["ref"] if i == 0 else "", e["label"] if i == 0 else "", ln["account"], ln["name"], n(ln["debit"]) if ln["debit"] else "", n(ln["credit"]) if ln["credit"] else ""])
            td += ln["debit"]; tc += ln["credit"]
    rows.append(["", "", "", "TOTAL", "", "", n(td), n(tc)])
    blocks = [{"type": "kpis", "items": [("Écritures", n(len(r["entries"]))), ("Total des débits", n(td) + " GNF"), ("Total des crédits", n(tc) + " GNF"), ("Journal", code or "tous")]},
              {"type": "table", "head": ["Date", "Jnl", "Pièce", "Libellé", "Compte", "Intitulé", "Débit", "Crédit"], "rows": rows, "align": "llllllrr", "widths": [1.5, 0.8, 1.7, 4.5, 1.2, 3.8, 1.7, 1.7], "bold": [len(rows) - 1]},
              {"type": "p", "text": "✔ Débits et crédits égaux." if td == tc else "⚠ Débits et crédits différents : signalez-le."}]
    if not r["entries"]: blocks[1]["rows"] = []
    return ("Journal" + (f" {code}" if code else " général")), blocks, True


def balance_blocks(c, a: str, b: str) -> tuple[str, list[dict], bool]:
    r = accounting.trial_balance(c, a, b); T = r["totals"]
    rows = [[x["account"], x["name"], n(x["open_debit"]) if x["open_debit"] else "", n(x["open_credit"]) if x["open_credit"] else "", n(x["debit"]) if x["debit"] else "", n(x["credit"]) if x["credit"] else "",
             n(x["close_debit"]) if x["close_debit"] else "", n(x["close_credit"]) if x["close_credit"] else ""] for x in r["rows"]]
    rows.append(["", "TOTAUX", n(T["od"]), n(T["oc"]), n(T["md"]), n(T["mc"]), n(T["cd"]), n(T["cc"])])
    blocks = [{"type": "p", "text": "✔ Balance équilibrée : total des débits = total des crédits." if r["balanced"] else "⚠ Balance déséquilibrée : signalez-le."},
              {"type": "table", "head": ["Compte", "Intitulé", "Ouv. débit", "Ouv. crédit", "Mvts débit", "Mvts crédit", "Solde débit", "Solde crédit"], "rows": rows, "align": "llrrrrrr", "widths": [1.2, 4.6, 1.8, 1.8, 1.8, 1.8, 1.9, 1.9], "bold": [len(rows) - 1]}]
    return "Balance des comptes", blocks, True


def ledger_blocks(c, a: str, b: str, account: str) -> tuple[str, list[dict], bool]:
    r = accounting.ledger(c, account, a, b)
    rows = [["", "", "", "Solde d'ouverture", "", "", n(r["opening"])]] + [[fd(x["date"]), x["journal"], x["ref"], x["label"], n(x["debit"]) if x["debit"] else "", n(x["credit"]) if x["credit"] else "", n(x["balance"])] for x in r["rows"]]
    rows.append(["", "", "", "Solde de clôture", n(sum(x["debit"] for x in r["rows"])), n(sum(x["credit"] for x in r["rows"])), n(r["closing"])])
    blocks = [{"type": "p", "text": f"Compte {account} · {r['name']}"},
              {"type": "table", "head": ["Date", "Jnl", "Pièce", "Libellé", "Débit", "Crédit", "Solde"], "rows": rows, "align": "llllrrr", "widths": [1.6, 0.9, 1.9, 5.5, 2, 2, 2.2], "bold": [0, len(rows) - 1]}]
    return f"Grand livre : compte {account}", blocks, True


ACCT_REPORTS = ("financials", "journal", "balance", "ledger")
REPORTS = {"margins_product": ("costs.read", "margins"), "margins_client": ("costs.read", "margins"), "sales_seller": (None, "sales"), "sales_month": (None, "sales"), "sales_category": (None, "sales"), "financials": ("accounting.read", "fin"), "journal": ("accounting.read", "acct"), "balance": ("accounting.read", "acct"), "ledger": ("accounting.read", "acct")}


def translate_blocks(blocks: list[dict], lang: str) -> list[dict]:
    t = lambda x: i18n_tr(x, lang) if isinstance(x, str) else x
    out = []
    for b in blocks:
        b = dict(b)
        for k in ("text",):
            if k in b: b[k] = t(b[k])
        if b["type"] == "kpis": b["items"] = [(t(l), t(v)) for l, v in b["items"]]
        if b["type"] == "table": b["head"] = [t(x) for x in b["head"]]; b["rows"] = [[t(c) for c in r] for r in b["rows"]]
        out.append(b)
    return out


def build(c, user, report: str, a: str, b: str, lang: str = "fr", extra: dict | None = None) -> tuple[bytes, str]:
    """Renvoie (octets du PDF, nom de fichier). Les droits sont vérifiés par l'appelant (voir REPORTS)."""
    if report in ("margins_product", "margins_client"): title, blocks, wide = margins_blocks(c, a, b, report.split("_")[1])
    elif report in ("sales_seller", "sales_month", "sales_category"): title, blocks, wide = sales_blocks(c, user, a, b, report.split("_")[1])
    elif report == "journal": title, blocks, wide = journal_blocks(c, a, b, (extra or {}).get("journal") or None)
    elif report == "balance": title, blocks, wide = balance_blocks(c, a, b)
    elif report == "ledger": title, blocks, wide = ledger_blocks(c, a, b, (extra or {}).get("account") or "")
    else: title, blocks, wide = financials_blocks(c, a, b)
    if lang != "fr": blocks = translate_blocks(blocks, lang)
    pdf = render(settings_get(c), logo_bytes(c), i18n_tr(title, lang), i18n_tr(f"Période du {fd(a)} au {fd(b)}.", lang).rstrip("."), user["full_name"], blocks, wide, report in ACCT_REPORTS, lang, signature_bytes(c))
    return pdf, f"{report}{'_' + extra['account'] if report == 'ledger' and extra else ''}_{a}_{b}.pdf"
