"""Résumé du jour : ce qui demande l'attention du propriétaire (retards de paiement, stock à renouveler, livraisons en retard, devis qui expirent, fournisseurs à payer, lots à surveiller).

Mêmes règles que la liste « À faire » du tableau de bord, calculées côté serveur pour pouvoir partir par e-mail ou par WhatsApp sans ouvrir l'application. Par défaut le texte ne
contient que des nombres et des montants ; les noms des clients et des produits n'y figurent que si le propriétaire le demande (un e-mail n'est pas un canal chiffré).
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from . import sales_ops, stock_ops, warehouses
from .sales_ops import settings_get, today

SOON_DAYS = 7


def _money(v: int) -> str:
    return f"{int(v):,}".replace(",", " ") + " GNF"


def _fd(iso: str) -> str:
    return "/".join(reversed(iso.split("-")))


def compute(c: sqlite3.Connection, names: bool = False) -> dict:
    t = today().isoformat(); soon = (today() + timedelta(days=SOON_DAYS)).isoformat()
    items: list[dict] = []

    def add(key: str, label: str, count: int, amount: int | None = None, detail: list[str] | None = None) -> None:
        if count > 0: items.append({"key": key, "label": label, "count": count, "amount": amount, "detail": detail or []})

    # clients en retard de paiement
    by_client: dict[str, dict] = {}
    for s in c.execute("SELECT * FROM sales WHERE status='active' AND due_date IS NOT NULL AND due_date < ?", (t,)):
        bal = sales_ops.sale_totals(c, s)["balance"]
        if bal <= 0: continue
        d = by_client.setdefault(s["client_id"], {"amount": 0, "oldest": s["due_date"]})
        d["amount"] += bal; d["oldest"] = min(d["oldest"], s["due_date"])
    if by_client:
        nm = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM clients")} if names else {}
        top = sorted(by_client.items(), key=lambda kv: -kv[1]["amount"])[:5]
        add("overdue", "Clients en retard de paiement", len(by_client), sum(v["amount"] for v in by_client.values()),
            [f"{nm.get(k, '?')} : {_money(v['amount'])} (depuis le {_fd(v['oldest'])})" for k, v in top] if names else [])
    # fournisseurs : en retard, puis à payer d'ici 7 jours
    late = soon_n = late_amt = soon_amt = 0
    for p in c.execute("SELECT * FROM purchases WHERE status='active' AND due_date IS NOT NULL AND due_date <= ?", (soon,)):
        bal = stock_ops.purchase_totals(c, p)["balance"]
        if bal <= 0: continue
        if p["due_date"] < t: late += 1; late_amt += bal
        else: soon_n += 1; soon_amt += bal
    add("supplier_late", "Achats fournisseurs en retard de paiement", late, late_amt)
    add("supplier_soon", f"Achats fournisseurs à payer d'ici {SOON_DAYS} jours", soon_n, soon_amt)
    # stock à renouveler (mêmes seuils que l'écran Stocks)
    lv = stock_ops.stock_levels(c); low = []
    for p in c.execute("SELECT id, name, reorder_level FROM products WHERE deleted=0 AND track_stock=1 ORDER BY name"):
        q = lv.get(p["id"])
        if q is None: continue
        if q < 0 or q == 0 or (p["reorder_level"] > 0 and q <= p["reorder_level"]): low.append((p["name"], q))
    add("stock_low", "Produits à réapprovisionner (rupture, stock négatif ou sous le seuil)", len(low), None, [f"{n} : {q} en stock" for n, q in low[:5]] if names else [])
    # lots périmés ou qui périment bientôt
    lots = [l for l in warehouses.lot_levels(c) if l["status"] in ("expired", "soon")]
    add("lots", "Lots périmés ou qui périment dans le mois", len(lots), None, [f"{l['product_name']} lot {l['lot_no']} : {l['remaining']} ({_fd(l['expiry_date'])})" for l in lots[:5]] if names else [])
    # livraisons en retard
    add("deliveries", "Livraisons promises en retard", c.execute("SELECT COUNT(*) n FROM sales WHERE status='active' AND delivery_due IS NOT NULL AND delivered_at IS NULL AND delivery_due < ?", (t,)).fetchone()["n"])
    # devis qui expirent cette semaine
    add("porders_late", "Commandes fournisseurs en retard de livraison", c.execute("SELECT COUNT(*) n FROM purchase_orders WHERE status='open' AND expected_date < ?", (t,)).fetchone()["n"])
    add("quotes", "Devis qui expirent cette semaine", c.execute("SELECT COUNT(*) n FROM quotes WHERE status='open' AND valid_until >= ? AND valid_until <= ?", (t, soon)).fetchone()["n"])
    st = settings_get(c)
    return {"date": t, "company": st.get("company_name") or "", "items": items, "empty": not items}


def to_text(d: dict, names: bool = False) -> str:
    titre = f"Résumé du {_fd(d['date'])}" + (f" · {d['company']}" if d["company"] else "")
    if d["empty"]: return f"{titre}\n\nRien d'urgent aujourd'hui."
    lignes = [titre, ""]
    for it in d["items"]:
        lignes.append(f"• {it['label']} : {it['count']}" + (f" ({_money(it['amount'])})" if it["amount"] is not None else ""))
        lignes += [f"    - {x}" for x in it["detail"]]
    lignes += ["", "Ouvrez Gestion libre pour agir. Ce message ne contient aucun mot de passe ; il " + ("cite des noms de clients ou de produits." if names else "ne cite ni client ni produit : seulement des nombres et des montants.")]
    return "\n".join(lignes)


def subject(d: dict) -> str:
    n = len(d["items"])
    return f"Gestion libre : {n} point(s) à voir" + (f" · {d['company']}" if d["company"] else "") if n else "Gestion libre : rien d'urgent"
