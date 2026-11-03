"""Marges par produit et par client sur une période.

Méthode (volontairement simple, donc vérifiable) :
- Chiffre d'affaires = lignes des ventes ACTIVES de la période, HORS TAXE (la taxe incluse dans le prix est retirée ligne par ligne, avec le taux figé sur la ligne).
- Coût = quantité vendue × COÛT MOYEN D'ACHAT HORS TAXE du produit (tous les achats actifs jusqu'à la fin de la période : prix d'achat, moins la TVA récupérable des réglages comptables).
  C'est une estimation : ni FIFO ni coût réel du lot, comme le reste de l'application (stock valorisé au coût moyen).
- Un produit jamais acheté, ou une ligne libre, n'a PAS de coût connu : son chiffre d'affaires est compté à part et il n'entre pas dans le calcul de marge (on ne devine pas).
- Les avoirs clients de la période sont montrés à part et NE SONT PAS déduits des marges (on ne sait pas si la marchandise est revenue en stock).
Réservé à ceux qui voient les coûts (propriétaire, comptable, auditeur).
"""
from __future__ import annotations

import csv
import io
import re
import sqlite3
from datetime import date

from . import accounting
from .sales_ops import sale_totals


def _ht(amount: int, rate: int) -> int:
    return amount - (amount - round(amount * 100 / (100 + rate)) if rate > 0 else 0)


def compute(c: sqlite3.Connection, a: str, b: str) -> dict:
    st = accounting.get_settings(c); rs, rp = st["sales_tax_rate"], st["purchase_tax_rate"]
    cost: dict[str, list[int]] = {}
    for r in c.execute("SELECT l.product_id pid, l.qty, l.amount, l.tax_rate FROM purchase_lines l JOIN purchases u ON u.id=l.purchase_id WHERE u.status='active' AND u.purchase_date<=? AND l.product_id IS NOT NULL", (b,)):
        rate = rp if r["tax_rate"] is None else r["tax_rate"]
        x = cost.setdefault(r["pid"], [0, 0]); x[0] += r["qty"]; x[1] += _ht(r["amount"], rate)
    prods: dict[str, dict] = {}; clients: dict[str, dict] = {}
    names = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM products")}
    cnames = {r["id"]: (r["name"], r["code"]) for r in c.execute("SELECT id, name, code FROM clients")}
    sales_by_client: dict[str, set] = {}
    q = """SELECT l.*, s.client_id, s.id sid FROM sale_lines l JOIN sales s ON s.id=l.sale_id WHERE s.status='active' AND s.sale_date>=? AND s.sale_date<=? ORDER BY s.sale_date, s.number"""
    total = {"revenue": 0, "cost": 0, "margin": 0, "revenue_known": 0, "revenue_unknown": 0}
    for l in c.execute(q, (a, b)):
        rate = rs if l["tax_rate"] is None else l["tax_rate"]; rev = _ht(l["amount"], rate)
        pid = l["product_id"]; unit = (cost[pid][1] / cost[pid][0]) if pid in cost and cost[pid][0] else None
        line_cost = round(l["qty"] * unit) if unit is not None else None
        key = pid or "libre"
        p = prods.setdefault(key, {"product_id": pid, "name": names.get(pid, l["name"]) if pid else "Articles libres (sans coût connu)", "qty": 0, "revenue": 0, "cost": 0, "revenue_known": 0, "revenue_unknown": 0, "avg_cost": None if unit is None else round(unit)})
        cl = clients.setdefault(l["client_id"], {"client_id": l["client_id"], "name": cnames.get(l["client_id"], ("—", ""))[0], "code": cnames.get(l["client_id"], ("", ""))[1], "revenue": 0, "cost": 0, "revenue_known": 0, "revenue_unknown": 0})
        sales_by_client.setdefault(l["client_id"], set()).add(l["sid"])
        for g in (p, cl):
            g["revenue"] += rev
            if line_cost is None: g["revenue_unknown"] += rev
            else: g["cost"] += line_cost; g["revenue_known"] += rev
        p["qty"] += l["qty"]
        total["revenue"] += rev
        if line_cost is None: total["revenue_unknown"] += rev
        else: total["cost"] += line_cost; total["revenue_known"] += rev
    total["margin"] = total["revenue_known"] - total["cost"]
    total["margin_pct"] = round(total["margin"] * 100 / total["revenue_known"], 1) if total["revenue_known"] else None

    def finish(g: dict) -> dict:
        g["margin"] = g["revenue_known"] - g["cost"] if g["revenue_known"] else None
        g["margin_pct"] = round(g["margin"] * 100 / g["revenue_known"], 1) if g["revenue_known"] else None
        g["share_of_margin"] = round(g["margin"] * 100 / total["margin"], 1) if g["margin"] is not None and total["margin"] > 0 else None
        return g
    by_product = sorted((finish(g) for g in prods.values()), key=lambda g: (g["margin"] is None, -(g["margin"] or 0), g["name"]))
    from . import insights
    rel = {x["client_id"]: x for x in insights.reliability(c)}
    for cid, g in clients.items():
        g["sales"] = len(sales_by_client[cid])
        g["balance"] = sum(sale_totals(c, s)["balance"] for s in c.execute("SELECT * FROM sales WHERE client_id=? AND status='active'", (cid,)))
        g["reliability"] = rel.get(cid, {}).get("label"); g["reliability_score"] = rel.get(cid, {}).get("score")
    by_client = sorted((finish(g) for g in clients.values()), key=lambda g: (g["margin"] is None, -(g["margin"] or 0), g["name"]))
    notes = c.execute("SELECT COALESCE(SUM(n.amount),0) s FROM credit_notes n JOIN sales s ON s.id=n.sale_id WHERE n.status='active' AND s.status='active' AND n.note_date>=? AND n.note_date<=?", (a, b)).fetchone()["s"]
    return {"from": a, "to": b, "totals": total, "by_product": by_product, "by_client": by_client, "credit_notes_total": notes,
            "note": "Chiffre d'affaires hors taxe ; coût = quantité vendue × coût moyen d'achat hors taxe (estimation). Les produits jamais achetés et les lignes libres n'ont pas de coût connu : leur chiffre d'affaires est compté à part, sans marge. Les avoirs ne sont pas déduits. Si vous facturez de la TVA mais que la TVA récupérable sur achats est à 0 dans les réglages comptables, le coût reste taxe comprise : la marge affichée est alors minorée (réglez la TVA des achats avec votre expert-comptable)."}


def to_csv(data: dict, kind: str) -> str:
    buf = io.StringIO(); w = csv.writer(buf, delimiter=";")

    def safe(v):
        s = "" if v is None else str(v)
        return "'" + s if s[:1] in ("=", "+", "-", "@") and not re.fullmatch(r"-?\d+(\.\d+)?", s) else s
    if kind == "client":
        w.writerow(["client", "code", "ventes", "CA hors taxe", "coût (estimé)", "marge", "marge %", "CA sans coût connu", "reste dû", "fiabilité"])
        for g in data["by_client"]: w.writerow([safe(g["name"]), safe(g["code"]), g["sales"], g["revenue"], g["cost"], g["margin"], g["margin_pct"], g["revenue_unknown"], g["balance"], safe(g["reliability"])])
    else:
        w.writerow(["produit", "quantité vendue", "CA hors taxe", "coût moyen unitaire", "coût (estimé)", "marge", "marge %", "part de la marge %", "CA sans coût connu"])
        for g in data["by_product"]: w.writerow([safe(g["name"]), g["qty"], g["revenue"], g["avg_cost"], g["cost"], g["margin"], g["margin_pct"], g["share_of_margin"], g["revenue_unknown"]])
    return "﻿" + buf.getvalue()


def parse_period(frm: str | None, to: str | None) -> tuple[str, str]:
    today = date.today()
    a = frm or f"{today.year}-01-01"; b = to or today.isoformat()
    for d in (a, b):
        try: date.fromisoformat(d)
        except ValueError: raise ValueError("Date invalide.")
    if a > b: raise ValueError("La fin précède le début.")
    return a, b
