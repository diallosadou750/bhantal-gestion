"""Analyses pour décider : fiabilité de paiement des clients et prévision de trésorerie sur quatre semaines.

Ce sont des REPÈRES, pas des vérités : la fiabilité résume le passé de chaque client (retards constatés) et la prévision suppose que chacun paie à son échéance, ce qui est
optimiste pour les clients peu fiables (voir leur note). Tout est calculé à partir des documents de l'application, sans rien enregistrer.
Fiabilité (0 à 100) = 100 − 40 × (part des factures à crédit payées en retard ou toujours en retard) − min(30, 0,5 × retard moyen en jours) − 30 si une facture est en retard aujourd'hui.
« Fiable » à partir de 80, « à surveiller » de 50 à 79, « risqué » en dessous ; moins de 2 factures échues : « peu d'historique », pas de note.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from .sales_ops import sale_totals, today

MIN_HISTORY = 2


def _settled_date(c, s, net: int) -> str | None:
    if net <= 0: return s["sale_date"]
    cum = s["paid_at_sale"]
    if cum >= net: return s["sale_date"]
    for p in c.execute("SELECT pay_date, amount FROM payments WHERE sale_id=? AND status='active' ORDER BY pay_date, created_at", (s["id"],)):
        cum += p["amount"]
        if cum >= net: return p["pay_date"]
    return None


def reliability(c: sqlite3.Connection) -> list[dict]:
    t = today(); out = []
    for cl in c.execute("SELECT * FROM clients WHERE deleted=0 ORDER BY name"):
        counted = late = 0; delays: list[int] = []; overdue = 0; balance = 0; n_credit = 0
        for s in c.execute("SELECT * FROM sales WHERE client_id=? AND status='active' AND kind='credit' AND due_date IS NOT NULL", (cl["id"],)):
            n_credit += 1
            tot = sale_totals(c, s); balance += tot["balance"]
            due = date.fromisoformat(s["due_date"]); settled = _settled_date(c, s, tot["net_total"])
            if settled:
                counted += 1; d = max(0, (date.fromisoformat(settled) - due).days); delays.append(d); late += 1 if d > 0 else 0
            elif due < t:
                counted += 1; d = (t - due).days; delays.append(d); late += 1; overdue += tot["balance"]
        if not n_credit: continue
        avg = round(sum(delays) / len(delays), 1) if delays else 0
        if counted < MIN_HISTORY: score, label = None, "Peu d'historique"
        else:
            score = max(0, round(100 - 40 * late / counted - min(30, 0.5 * avg) - (30 if overdue > 0 else 0)))
            label = "Fiable" if score >= 80 else "À surveiller" if score >= 50 else "Risqué"
        out.append({"client_id": cl["id"], "name": cl["name"], "code": cl["code"], "credit_sales": n_credit, "counted": counted, "late": late, "avg_delay_days": avg, "overdue_amount": overdue, "balance": balance, "score": score, "label": label})
    out.sort(key=lambda x: (x["score"] is None, x["score"] if x["score"] is not None else 0, -x["balance"]))
    return out


def forecast(c: sqlite3.Connection, weeks: int = 4) -> dict:
    from . import bank_ops, cash_ops, recurring, stock_ops
    t = today(); buckets = [{"label": "En retard", "from": None, "to": (t - timedelta(days=1)).isoformat(), "in": 0, "out": 0}]
    for w in range(weeks):
        a, b = t + timedelta(days=7 * w), t + timedelta(days=7 * w + 6)
        buckets.append({"label": "Cette semaine" if w == 0 else f"Semaine +{w}", "from": a.isoformat(), "to": b.isoformat(), "in": 0, "out": 0})
    end = t + timedelta(days=7 * weeks - 1)

    def put(d: str, key: str, amount: int):
        for bk in buckets:
            if (bk["from"] is None or d >= bk["from"]) and d <= bk["to"]: bk[key] += amount; return
        # au-delà de l'horizon : ignoré
    for s in c.execute("SELECT * FROM sales WHERE status='active' AND kind='credit' AND due_date IS NOT NULL"):
        bal = sale_totals(c, s)["balance"]
        if bal > 0: put(s["due_date"], "in", bal)
    for p in c.execute("SELECT * FROM purchases WHERE status='active' AND due_date IS NOT NULL"):
        bal = stock_ops.purchase_totals(c, p)["balance"]
        if bal > 0: put(p["due_date"], "out", bal)
    for r in c.execute("SELECT * FROM recurring_expenses WHERE active=1"):
        for d in recurring.pending(r): put(max(d, t.isoformat()), "out", r["amount"])                    # échéances déjà dues, pas encore générées
        y, m = t.year, t.month
        while (y, m) <= (end.year, end.month):
            d = recurring._due(y, m, r["day"])
            ym = f"{y}-{m:02d}"
            if d > t and d <= end and d >= date.fromisoformat(r["start_date"]) and (not r["end_date"] or d <= date.fromisoformat(r["end_date"])) and (not r["last_generated"] or ym > r["last_generated"]): put(d.isoformat(), "out", r["amount"])
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    cash = cash_ops.status(c)["expected"]
    banks = sum(bank_ops.compute(c, k, t.isoformat())["book_balance"] for k, v in bank_ops.accounts(c).items() if v["active"])
    pos = cash + banks
    for bk in buckets:
        bk["net"] = bk["in"] - bk["out"]; pos += bk["net"]; bk["position"] = pos
    return {"start": {"cash": cash, "banks": banks, "total": cash + banks}, "buckets": buckets, "horizon_end": end.isoformat(),
            "note": "Hypothèse : chaque facture client est payée à son échéance (optimiste pour les clients peu fiables) et chaque dette fournisseur réglée à la sienne ; ne comprend ni les ventes futures ni les dépenses non récurrentes."}
