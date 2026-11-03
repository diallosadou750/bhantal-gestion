"""Facture et reçu imprimables (page HTML : « Imprimer » → « Enregistrer en PDF »). Toutes les données sont échappées."""
from __future__ import annotations

import sqlite3
from html import escape

from . import sales_ops


def _st(c: sqlite3.Connection) -> dict:
    st = sales_ops.settings_get(c)
    st["has_logo"] = c.execute("SELECT 1 FROM meta WHERE key='logo'").fetchone() is not None
    st["has_signature"] = c.execute("SELECT 1 FROM meta WHERE key='signature'").fetchone() is not None
    return st



def _rendu(c, n) -> str:
    from . import returns
    t = returns.summary(c, n["id"])
    return f" · marchandise remise en stock : {escape(t)}" if t else ""

def _n(v: int) -> str:
    return f"{v:,}".replace(",", " ")


def _d(iso: str | None) -> str:
    """AAAA-MM-JJ -> JJ/MM/AAAA"""
    return "/".join(reversed(iso.split("-"))) if iso else ""


def _page(title: str, body: str) -> str:
    return (f'<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{escape(title)}</title><link rel="stylesheet" href="/print.css"></head><body>'
            f'<div class="bar"><button id="print" type="button">Imprimer / Enregistrer en PDF</button></div><main class="doc">{body}</main>'
            f'<script src="/print.js"></script></body></html>')


def _header(st: dict, title: str, number: str, date_: str) -> str:
    company = escape(st["company_name"] or "Mon entreprise")
    extra = " · ".join(escape(x) for x in (st["address"], st["phone"], ("N° fiscal : " + st["tax_id"]) if st["tax_id"] else "") if x)
    logo = '<img class="doclogo" src="/api/logo" alt="">' if st.get("has_logo") else ""
    return (f'<header><div>{logo}<h1>{company}</h1><p class="muted">{extra}</p></div><div class="right"><h2>{escape(title)}</h2><p><strong>{escape(number)}</strong><br>{escape(date_)}</p></div></header>')


def _rate(st: dict, l: dict) -> int:
    return st["tax_rate"] if l.get("tax_rate") is None else l["tax_rate"]


def _has_tax(st: dict, lines: list) -> bool:
    return any(_rate(st, l) > 0 for l in lines)


def _line_row(st: dict, l: dict, with_tax: bool) -> str:
    tax = f'<td class="num">{_rate(st, l)} %</td>' if with_tax else ""
    return f'<tr><td>{escape(l["name"])}</td><td class="num">{_n(l["qty"])}</td><td class="num">{_n(l["unit_price"])}</td><td class="num">{l["discount_pct"]} %</td>{tax}<td class="num">{_n(l["amount"])}</td></tr>'


def _tax_rows(st: dict, lines: list, total: int) -> str:
    """Total hors taxe puis une ligne de taxe par taux (les prix affichés comprennent la taxe)."""
    if not _has_tax(st, lines): return ""
    groups: dict[int, int] = {}
    for l in lines: groups[_rate(st, l)] = groups.get(_rate(st, l), 0) + l["amount"]
    ht = tva_total = 0; rows = ""
    for rate, amt in sorted(groups.items()):
        h_ = round(amt * 100 / (100 + rate)) if rate > 0 else amt; t_ = amt - h_; ht += h_; tva_total += t_
        if rate > 0: rows += f'<tr><td colspan="5" class="right">Taxe ({rate} %)</td><td class="num">{_n(t_)}</td></tr>'
    return f'<tr><td colspan="5" class="right">Total hors taxe</td><td class="num">{_n(ht)}</td></tr>' + rows


def invoice_html(c: sqlite3.Connection, s: sqlite3.Row) -> str:
    st = _st(c); v = sales_ops.sale_view(c, s)
    cl = c.execute("SELECT * FROM clients WHERE id=?", (s["client_id"],)).fetchone()
    wt = _has_tax(st, v["lines"]); rows = "".join(_line_row(st, l, wt) for l in v["lines"])
    pays = c.execute("SELECT * FROM payments WHERE sale_id=? AND status='active' ORDER BY pay_date, created_at", (s["id"],)).fetchall()
    notes = c.execute("SELECT * FROM credit_notes WHERE sale_id=? AND status='active' ORDER BY note_date", (s["id"],)).fetchall()
    pay_rows = ""
    if s["paid_at_sale"]: pay_rows += f'<tr><td>{_d(s["sale_date"])}</td><td>Paiement à la vente ({escape(s["pay_method"])})</td><td class="num">{_n(s["paid_at_sale"])}</td></tr>'
    pay_rows += "".join(f'<tr><td>{_d(p["pay_date"])}</td><td>Paiement ({escape(p["method"])}{" · " + escape(p["reference"]) if p["reference"] else ""})</td><td class="num">{_n(p["amount"])}</td></tr>' for p in pays)
    pay_rows += "".join(f'<tr><td>{_d(n["note_date"])}</td><td>Avoir {escape(n["number"])} ({"remboursé" if n["settlement"] == "refund" else "déduit"}) : {escape(n["reason"])}{_rendu(c, n)}</td><td class="num">-{_n(n["amount"])}</td></tr>' for n in notes)
    cancelled = '<p class="stamp">ANNULÉE</p>' if s["status"] == "cancelled" else ""
    due = f'<p>Échéance de paiement : <strong>{_d(s["due_date"])}</strong></p>' if s["kind"] == "credit" and s["due_date"] else ""
    body = (_header(st, "FACTURE", s["number"], _d(s["sale_date"])) + cancelled +
            f'<section class="client"><h3>Client</h3><p><strong>{escape(cl["name"])}</strong> ({escape(cl["code"])})<br>{escape(cl["phone"])} {escape(cl["zone"])}</p></section>'
            f'<table><thead><tr><th>Désignation</th><th>Qté</th><th>Prix unitaire</th><th>Remise</th>{'<th>TVA</th>' if wt else ''}<th>Montant</th></tr></thead><tbody>{rows}</tbody><tfoot>'
            f'{_tax_rows(st, v["lines"], v["total"])}<tr class="total"><td colspan="{5 if wt else 4}" class="right">TOTAL (GNF)</td><td class="num">{_n(v["total"])}</td></tr></tfoot></table>'
            + (f'<h3>Paiements et avoirs</h3><table class="small"><tbody>{pay_rows}</tbody></table>' if pay_rows else "")
            + f'<p class="balance">Reste à payer : <strong>{_n(v["balance"])} GNF</strong></p>{due}'
            f'<footer><p>{escape(st["footer"] or "Merci de votre confiance.")}</p><p class="muted">Vendeur : {escape(v["seller_name"] or "—")}</p>'
            + ('<div class="docsignature"><p>Signature autorisée</p><img src="/api/signature" alt="Signature de l’entreprise"></div>' if st.get("has_signature") else "") + '</footer>')
    return _page(f"Facture {s['number']}", body)


def quote_html(c: sqlite3.Connection, q: sqlite3.Row) -> str:
    from . import quotes
    st = _st(c); v = quotes.view(c, q)
    cl = c.execute("SELECT * FROM clients WHERE id=?", (q["client_id"],)).fetchone()
    wt = _has_tax(st, v["lines"]); rows = "".join(_line_row(st, l, wt) for l in v["lines"])
    state = {"open": "", "converted": '<p class="stamp">ACCEPTÉ</p>', "declined": '<p class="stamp">REFUSÉ</p>', "cancelled": '<p class="stamp">ANNULÉ</p>'}[q["status"]]
    if v["expired"]: state = '<p class="stamp">EXPIRÉ</p>'
    body = (_header(st, "DEVIS", q["number"], _d(q["quote_date"])) + state +
            f'<section class="client"><h3>Client</h3><p><strong>{escape(cl["name"])}</strong> ({escape(cl["code"])})<br>{escape(cl["phone"])} {escape(cl["zone"])}</p></section>'
            f'<table><thead><tr><th>Désignation</th><th>Qté</th><th>Prix unitaire</th><th>Remise</th>{'<th>TVA</th>' if wt else ''}<th>Montant</th></tr></thead><tbody>{rows}</tbody><tfoot>'
            f'{_tax_rows(st, v["lines"], v["total"])}<tr class="total"><td colspan="{5 if wt else 4}" class="right">TOTAL (GNF)</td><td class="num">{_n(v["total"])}</td></tr></tfoot></table>'
            f'<p>Devis valable jusqu\'au <strong>{_d(q["valid_until"])}</strong>. Les prix sont maintenus pendant cette période. Ce document n\'est pas une facture.</p>'
            + (f'<p>{escape(q["note"])}</p>' if q["note"] else "")
            + f'<footer><p>{escape(st["footer"] or "Merci de votre confiance.")}</p></footer>')
    return _page(f"Devis {q['number']}", body)


def delivery_html(c: sqlite3.Connection, n: sqlite3.Row) -> str:
    from . import deliveries
    st = _st(c); v = deliveries.view(c, n)
    s = c.execute("SELECT * FROM sales WHERE id=?", (n["sale_id"],)).fetchone()
    cl = c.execute("SELECT * FROM clients WHERE id=?", (s["client_id"],)).fetchone()
    ordered = {l["position"]: l["qty"] for l in c.execute("SELECT position, qty FROM sale_lines WHERE sale_id=?", (s["id"],))}
    before = {r["sale_position"]: r["q"] for r in c.execute("SELECT l.sale_position, SUM(l.qty) q FROM delivery_lines l JOIN delivery_notes x ON x.id=l.note_id WHERE x.sale_id=? AND x.status='active' AND (x.delivery_date < ? OR (x.delivery_date = ? AND x.created_at <= ?)) GROUP BY l.sale_position", (s["id"], n["delivery_date"], n["delivery_date"], n["created_at"]))}
    rows = "".join(f'<tr><td>{escape(l["name"])}</td><td class="num">{_n(ordered.get(l["sale_position"], 0))}</td><td class="num"><strong>{_n(l["qty"])}</strong></td><td class="num">{_n(max(0, ordered.get(l["sale_position"], 0) - before.get(l["sale_position"], 0)))}</td></tr>' for l in v["lines"])
    stamp = '<p class="stamp">ANNULÉ</p>' if n["status"] == "cancelled" else ""
    body = (_header(st, "BON DE LIVRAISON", n["number"], _d(n["delivery_date"])) + stamp +
            f'<section class="client"><h3>Livré à</h3><p><strong>{escape(cl["name"])}</strong> ({escape(cl["code"])})<br>{escape(cl["phone"])} {escape(cl["zone"])}</p><p class="muted">Vente {escape(s["number"])} du {_d(s["sale_date"])}</p></section>'
            f'<table><thead><tr><th>Désignation</th><th>Commandé</th><th>Livré ce jour</th><th>Reste à livrer après ce bon</th></tr></thead><tbody>{rows}</tbody></table>'
            + (f'<p>{escape(n["note"])}</p>' if n["note"] else "")
            + f'<p>Reçu par : <strong>{escape(n["recipient"] or "………………………………")}</strong></p>'
            f'<table class="small"><tbody><tr><td class="sign">Signature du client (« reçu en bon état »)</td><td>Signature du livreur</td></tr></tbody></table>'
            f'<footer><p class="muted">Ce document atteste de la remise de la marchandise ; il n\'est pas une facture.</p></footer>')
    return _page(f"Bon de livraison {n['number']}", body)


def porder_html(c: sqlite3.Connection, o: sqlite3.Row) -> str:
    from . import porders
    st = _st(c); v = porders.view(c, o)
    sup = c.execute("SELECT * FROM suppliers WHERE id=?", (o["supplier_id"],)).fetchone()
    rows = "".join(f'<tr><td>{escape(l["name"])}</td><td class="num">{_n(l["qty"])}</td><td class="num">{_n(l["unit_cost"])}</td><td class="num">{_n(l["amount"])}</td></tr>' for l in v["lines"])
    stamp = '<p class="stamp">ANNULÉ</p>' if o["status"] == "cancelled" else ""
    body = (_header(st, "BON DE COMMANDE", o["number"], _d(o["order_date"])) + stamp +
            f'<section class="client"><h3>Fournisseur</h3><p><strong>{escape(sup["name"])}</strong> ({escape(sup["code"])})<br>{escape(sup["phone"])} {escape(sup["zone"])}</p></section>'
            f'<p>Livraison souhaitée le <strong>{_d(o["expected_date"])}</strong></p>'
            f'<table><thead><tr><th>Désignation</th><th>Quantité</th><th>Prix prévu</th><th>Montant</th></tr></thead><tbody>{rows}</tbody>'
            f'<tfoot><tr><td colspan="3"><strong>Total prévu</strong></td><td class="num"><strong>{_n(o["total"])} GNF</strong></td></tr></tfoot></table>'
            + (f'<p>{escape(o["note"])}</p>' if o["note"] else "")
            + '<table class="small"><tbody><tr><td class="sign">Signature de l\'acheteur</td><td>Accusé de réception du fournisseur</td></tr></tbody></table>'
            '<footer><p class="muted">Ce bon de commande n\'est pas une facture ; les quantités et prix sont ceux prévus à la commande.</p></footer>')
    return _page(f"Bon de commande {o['number']}", body)


def receipt_html(c: sqlite3.Connection, p: sqlite3.Row) -> str:
    st = _st(c)
    s = c.execute("SELECT * FROM sales WHERE id=?", (p["sale_id"],)).fetchone()
    cl = c.execute("SELECT * FROM clients WHERE id=?", (p["client_id"],)).fetchone()
    t = sales_ops.sale_totals(c, s)
    cancelled = '<p class="stamp">ANNULÉ</p>' if p["status"] == "cancelled" else ""
    body = (_header(st, "REÇU DE PAIEMENT", "Facture " + s["number"], _d(p["pay_date"])) + cancelled +
            f'<section class="client"><h3>Reçu de</h3><p><strong>{escape(cl["name"])}</strong> ({escape(cl["code"])})</p></section>'
            f'<p class="big">Somme reçue : <strong>{_n(p["amount"])} GNF</strong></p>'
            f'<p>Mode : {escape(p["method"])}{" · référence " + escape(p["reference"]) if p["reference"] else ""}</p>'
            f'<p>Reste à payer sur la facture {escape(s["number"])} : <strong>{_n(t["balance"])} GNF</strong></p>'
            f'<footer><p>{escape(st["footer"] or "Merci.")}</p></footer>')
    return _page(f"Reçu {s['number']}", body)
