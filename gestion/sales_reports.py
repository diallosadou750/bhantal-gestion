"""Rapports de ventes : par vendeur (avec objectifs mensuels), par mois et par catégorie de produit.

Ventes ACTIVES de la période (une vente annulée ne compte pas, mais le nombre de ventes annulées est signalé). « Ventes » = montant TTC facturé ; « hors taxe » retire la taxe incluse,
ligne par ligne, avec le taux figé sur la ligne. « Encaissé » = acompte à la vente + paiements enregistrés ; « reste dû » = ce qui n'est pas encore payé aujourd'hui sur ces ventes.
Un commercial ne voit QUE ses propres ventes (celles dont il est le vendeur) ; le classement de l'équipe est réservé au propriétaire, au comptable et à l'auditeur.
Les remises sont calculées par rapport au prix catalogue des lignes (prix unitaire × quantité) : c'est le coût commercial de la remise accordée.
"""
from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
from datetime import date

from . import accounting
from .permissions import can
from .sales_ops import sale_totals


def _month_list(a: str, b: str) -> list[str]:
    y, m = int(a[:4]), int(a[5:7]); out = []
    while (y, m) <= (int(b[:4]), int(b[5:7])):
        out.append(f"{y}-{m:02d}"); y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _ht(amount: int, rate: int) -> int:
    return round(amount * 100 / (100 + rate)) if rate > 0 else amount


TARGETS_KEY = "sales_targets"


def get_targets(c: sqlite3.Connection) -> dict[int, int]:
    """Objectif de ventes MENSUEL (TTC, en GNF) de chaque vendeur : {identifiant du vendeur : montant}."""
    r = c.execute("SELECT value FROM meta WHERE key=?", (TARGETS_KEY,)).fetchone()
    return {int(k): int(v) for k, v in json.loads(r["value"]).items()} if r else {}


def sellers_list(c: sqlite3.Connection) -> list[dict]:
    t = get_targets(c)
    return [{"id": u["id"], "name": u["full_name"], "target": t.get(u["id"], 0)} for u in c.execute("SELECT id, full_name FROM users WHERE role='sales' AND active=1 ORDER BY full_name")]


def set_targets(c: sqlite3.Connection, targets: dict) -> dict[int, int]:
    """Enregistre les objectifs (0 ou vide = pas d'objectif). Refuse tout identifiant qui n'est pas un commercial actif et tout montant hors limites."""
    ok = {s["id"] for s in sellers_list(c)}; new: dict[int, int] = {}
    for k, v in (targets or {}).items():
        try: uid, amount = int(k), int(v or 0)
        except (TypeError, ValueError): raise ValueError("Objectif : un nombre entier de GNF par commercial.")
        if uid not in ok: raise ValueError("Objectif : ce vendeur n'est pas un commercial actif.")
        if not 0 <= amount <= 10 ** 12: raise ValueError("Objectif : montant hors limites.")
        if amount: new[uid] = amount
    c.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (TARGETS_KEY, json.dumps({str(k): v for k, v in new.items()})))
    return new


def scope_of(user: sqlite3.Row) -> str | None:
    """'all' : toute l'équipe ; 'own' : ses propres ventes ; None : pas d'accès."""
    role = user["role"]
    if can(role, "reports.read"): return "all"
    if can(role, "sales.read_own") and not can(role, "sales.read_all"): return "own"
    return None


def compute(c: sqlite3.Connection, user: sqlite3.Row, a: str, b: str) -> dict:
    scope = scope_of(user)
    rs = accounting.get_settings(c)["sales_tax_rate"]
    names = {r["id"]: r["full_name"] for r in c.execute("SELECT id, full_name FROM users")}
    months = _month_list(a, b)
    sellers: dict[object, dict] = {}; per_month = {m: {"month": m, "sales": 0, "revenue": 0, "revenue_ht": 0, "collected": 0} for m in months}; matrix: dict[object, dict[str, int]] = {}
    scope_ids: set[str] = set()
    cats = {r["id"]: (r["category"] or "").strip() or "Sans catégorie" for r in c.execute("SELECT id, category FROM products")}; by_cat: dict[str, dict] = {}
    for s in c.execute("SELECT * FROM sales WHERE sale_date>=? AND sale_date<=? ORDER BY sale_date, number", (a, b)):
        if scope == "own" and s["seller_user_id"] != user["id"]: continue
        key = s["seller_user_id"]
        g = sellers.setdefault(key, {"seller_id": key, "name": names.get(key, "Non attribué") if key else "Non attribué", "sales": 0, "revenue": 0, "revenue_ht": 0, "credit_sales": 0, "collected": 0, "balance": 0, "discount": 0, "gross": 0, "cancelled": 0, "cancelled_amount": 0})
        if s["status"] != "active": g["cancelled"] += 1; g["cancelled_amount"] += s["total"]; continue
        scope_ids.add(s["id"])
        lines = c.execute("SELECT product_id, qty, unit_price, amount, tax_rate FROM sale_lines WHERE sale_id=?", (s["id"],)).fetchall()
        ht = sum(_ht(l["amount"], rs if l["tax_rate"] is None else l["tax_rate"]) for l in lines)
        for l in lines:
            k = cats.get(l["product_id"], "Sans catégorie") if l["product_id"] else "Articles libres"
            gc = by_cat.setdefault(k, {"category": k, "qty": 0, "revenue": 0, "revenue_ht": 0, "sale_ids": set()})
            gc["qty"] += l["qty"]; gc["revenue"] += l["amount"]; gc["revenue_ht"] += _ht(l["amount"], rs if l["tax_rate"] is None else l["tax_rate"]); gc["sale_ids"].add(s["id"])
        t = sale_totals(c, s); ym = s["sale_date"][:7]
        g["sales"] += 1; g["revenue"] += s["total"]; g["revenue_ht"] += ht; g["credit_sales"] += 1 if s["kind"] == "credit" else 0
        g["collected"] += t["paid_total"]; g["balance"] += t["balance"]
        g["gross"] += sum(l["qty"] * l["unit_price"] for l in lines); g["discount"] += sum(l["qty"] * l["unit_price"] - l["amount"] for l in lines)
        pm = per_month[ym]; pm["sales"] += 1; pm["revenue"] += s["total"]; pm["revenue_ht"] += ht
        matrix.setdefault(key, {}); matrix[key][ym] = matrix[key].get(ym, 0) + s["total"]
    # encaissements du mois : acomptes à la vente + paiements, sur les ventes du périmètre (quelle que soit la date de la vente)
    visible = {r["id"]: r for r in c.execute("SELECT id, seller_user_id, status FROM sales")}
    for s in c.execute("SELECT id, sale_date, paid_at_sale, seller_user_id, status FROM sales WHERE sale_date>=? AND sale_date<=? AND status='active' AND paid_at_sale>0", (a, b)):
        if scope == "own" and s["seller_user_id"] != user["id"]: continue
        per_month[s["sale_date"][:7]]["collected"] += s["paid_at_sale"]
    for p in c.execute("SELECT p.sale_id, p.pay_date, p.amount FROM payments p WHERE p.status='active' AND p.pay_date>=? AND p.pay_date<=?", (a, b)):
        v = visible.get(p["sale_id"])
        if v is None or v["status"] != "active" or (scope == "own" and v["seller_user_id"] != user["id"]): continue
        per_month[p["pay_date"][:7]]["collected"] += p["amount"]
    targets = get_targets(c)
    if scope == "all":                                                                     # un commercial qui a un objectif apparaît même sans vente sur la période
        for sid, _t in targets.items():
            if sid not in sellers and names.get(sid): sellers[sid] = {"seller_id": sid, "name": names[sid], "sales": 0, "revenue": 0, "revenue_ht": 0, "credit_sales": 0, "collected": 0, "balance": 0, "discount": 0, "gross": 0, "cancelled": 0, "cancelled_amount": 0}
    rows = []
    for g in sellers.values():
        tg = targets.get(g["seller_id"]) if g["seller_id"] and (scope == "all" or g["seller_id"] == user["id"]) else None
        g["target_month"] = tg; g["target_period"] = tg * len(months) if tg else None
        g["progress_pct"] = round(g["revenue"] * 100 / g["target_period"], 1) if g["target_period"] else None
        g["avg_basket"] = round(g["revenue"] / g["sales"]) if g["sales"] else 0
        g["credit_share"] = round(g["credit_sales"] * 100 / g["sales"], 1) if g["sales"] else None
        g["discount_pct"] = round(g["discount"] * 100 / g["gross"], 1) if g["gross"] else 0.0
        g["by_month"] = matrix.get(g["seller_id"], {})
        rows.append(g)
    rows.sort(key=lambda g: (-g["revenue"], g["name"]))
    total_rev = sum(g["revenue"] for g in rows)
    for g in rows: g["share"] = round(g["revenue"] * 100 / total_rev, 1) if total_rev else None
    crows = sorted(({"category": g["category"], "qty": g["qty"], "revenue": g["revenue"], "revenue_ht": g["revenue_ht"], "sales": len(g["sale_ids"]),
                     "share": round(g["revenue"] * 100 / sum(x["revenue"] for x in by_cat.values()), 1) if sum(x["revenue"] for x in by_cat.values()) else None} for g in by_cat.values()), key=lambda g: (-g["revenue"], g["category"]))
    mrows = list(per_month.values()); prev = None
    for m in mrows:
        m["avg_basket"] = round(m["revenue"] / m["sales"]) if m["sales"] else 0
        m["growth_pct"] = round((m["revenue"] - prev) * 100 / prev, 1) if prev else None
        prev = m["revenue"]
    totals = {"sales": sum(g["sales"] for g in rows), "revenue": total_rev, "revenue_ht": sum(g["revenue_ht"] for g in rows), "collected": sum(g["collected"] for g in rows), "balance": sum(g["balance"] for g in rows),
              "cancelled": sum(g["cancelled"] for g in rows), "discount": sum(g["discount"] for g in rows)}
    return {"from": a, "to": b, "scope": scope, "totals": totals, "by_seller": rows, "by_month": mrows, "by_category": crows, "months": months, "has_targets": any(g["target_month"] for g in rows),
            "note": ("Vos ventes uniquement. " if scope == "own" else "") + "Ventes actives de la période, montants TTC (hors taxe : taxe incluse retirée ligne par ligne). « Encaissé » et « reste dû » valent à ce jour. Les remises se mesurent au prix catalogue."}


def to_csv(data: dict, kind: str) -> str:
    buf = io.StringIO(); w = csv.writer(buf, delimiter=";")

    def safe(v):
        s = "" if v is None else str(v)
        return "'" + s if s[:1] in ("=", "+", "-", "@") and not re.fullmatch(r"-?\d+(\.\d+)?", s) else s
    if kind == "category":
        w.writerow(["catégorie", "quantités", "montant TTC", "montant hors taxe", "part %", "ventes"])
        for g in data["by_category"]: w.writerow([safe(g["category"]), g["qty"], g["revenue"], g["revenue_ht"], g["share"], g["sales"]])
    elif kind == "month":
        w.writerow(["mois", "ventes", "montant TTC", "montant hors taxe", "panier moyen", "évolution %", "encaissé dans le mois"])
        for m in data["by_month"]: w.writerow([m["month"], m["sales"], m["revenue"], m["revenue_ht"], m["avg_basket"], m["growth_pct"], m["collected"]])
    else:
        w.writerow(["vendeur", "ventes", "montant TTC", "montant hors taxe", "part %", "panier moyen", "ventes à crédit %", "remises accordées", "remise %", "encaissé", "reste dû", "ventes annulées", "objectif mensuel", "objectif de la période", "objectif atteint %"])
        for g in data["by_seller"]: w.writerow([safe(g["name"]), g["sales"], g["revenue"], g["revenue_ht"], g["share"], g["avg_basket"], g["credit_share"], g["discount"], g["discount_pct"], g["collected"], g["balance"], g["cancelled"], g["target_month"], g["target_period"], g["progress_pct"]])
    return "﻿" + buf.getvalue()
