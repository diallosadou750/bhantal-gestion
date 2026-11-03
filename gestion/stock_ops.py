"""Stocks, fournisseurs, achats, règlements fournisseurs, ajustements et inventaires.

Le stock n'est jamais saisi à la main : il se déduit des mouvements, donc il est toujours explicable.
    stock = achats reçus − ventes + ajustements (stock initial, retours, casse, écarts d'inventaire)
Une vente ou un achat annulé ne compte plus. Une correction passe par un ajustement ou un inventaire, tous deux journalisés.
Les prix d'achat, fournisseurs, achats et corrections sont réservés au propriétaire et au comptable.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import date, timedelta

from . import audit, period, security
from .audit import Actor
from .ops import UUID_RE, OpError, _deny, next_seq
from .methods import valid_method
from .permissions import COST_ALERT_PCT, METHODS, can
from .sales_ops import _conflict, _date, _int, _text, next_counter, today


# ---------------------------------------------------------------- calculs
def stock_levels(c: sqlite3.Connection) -> dict[str, int]:
    rows = c.execute("""SELECT p.id,
        COALESCE((SELECT SUM(l.qty) FROM purchase_lines l JOIN purchases u ON u.id=l.purchase_id WHERE l.product_id=p.id AND u.status='active'),0)
      - COALESCE((SELECT SUM(l.qty) FROM sale_lines l JOIN sales s ON s.id=l.sale_id WHERE l.product_id=p.id AND s.status='active'),0)
      - COALESCE((SELECT SUM(l.qty) FROM supplier_note_lines l JOIN supplier_notes n ON n.id=l.note_id JOIN purchases u ON u.id=n.purchase_id WHERE l.product_id=p.id AND n.status='active' AND u.status='active'),0)
      + COALESCE((SELECT SUM(l.qty) FROM credit_note_lines l JOIN credit_notes n ON n.id=l.note_id JOIN sales s ON s.id=n.sale_id WHERE l.product_id=p.id AND n.status='active' AND s.status='active'),0)
      + COALESCE((SELECT SUM(a.qty) FROM stock_adjustments a WHERE a.product_id=p.id AND a.status='active'),0) AS qty
        FROM products p WHERE p.deleted=0 AND p.track_stock=1""").fetchall()
    return {r["id"]: r["qty"] for r in rows}


def cost_info(c: sqlite3.Connection) -> dict[str, dict]:
    out = {}
    for r in c.execute("""SELECT l.product_id, SUM(l.amount) amt, SUM(l.qty) qty FROM purchase_lines l JOIN purchases u ON u.id=l.purchase_id
                          WHERE u.status='active' GROUP BY l.product_id"""):
        out[r["product_id"]] = {"avg_cost": round(r["amt"] / r["qty"]) if r["qty"] else 0}
    for pid in out:
        last = _last_cost(c, pid)
        out[pid]["last_cost"] = last
    return out


def _last_cost(c: sqlite3.Connection, product_id: str):
    r = c.execute("""SELECT l.unit_cost FROM purchase_lines l JOIN purchases u ON u.id=l.purchase_id WHERE l.product_id=? AND u.status='active'
                     ORDER BY u.purchase_date DESC, u.created_at DESC LIMIT 1""", (product_id,)).fetchone()
    return r["unit_cost"] if r else None


def purchase_totals(c: sqlite3.Connection, p: sqlite3.Row) -> dict:
    pay = c.execute("SELECT COALESCE(SUM(amount),0) s FROM supplier_payments WHERE purchase_id=? AND status='active'", (p["id"],)).fetchone()["s"]
    notes = {r["settlement"]: r["s"] for r in c.execute("SELECT settlement, SUM(amount) s FROM supplier_notes WHERE purchase_id=? AND status='active' GROUP BY settlement", (p["id"],))}
    notes_total = sum(notes.values()); refunds = notes.get("refund", 0)
    net = p["total"] - notes_total
    paid = p["paid_at_purchase"] + pay - refunds
    return {"payments_total": pay, "paid_total": paid, "balance": 0 if p["status"] == "cancelled" else net - paid, "net_total": net, "notes_total": notes_total, "refunds_total": refunds}


def supplier_balance(c: sqlite3.Connection, supplier_id: str) -> int:
    return sum(purchase_totals(c, p)["balance"] for p in c.execute("SELECT * FROM purchases WHERE supplier_id=? AND status='active'", (supplier_id,)))


def supplier_view(s: sqlite3.Row) -> dict:
    d = {k: s[k] for k in ("id", "code", "name", "phone", "zone", "note", "version", "seq", "created_at")}; d["deleted"] = bool(s["deleted"]); return d


def purchase_view(c: sqlite3.Connection, p: sqlite3.Row) -> dict:
    d = {k: p[k] for k in ("id", "number", "supplier_id", "purchase_date", "kind", "due_date", "supplier_ref", "total", "paid_at_purchase", "pay_method", "note", "status", "cancel_reason", "version", "seq", "created_at")}
    d["lines"] = [{"product_id": l["product_id"], "name": l["name"], "qty": l["qty"], "unit_cost": l["unit_cost"], "amount": l["amount"], "prev_cost": l["prev_cost"], "tax_rate": l["tax_rate"], "depot_id": l["depot_id"], "lot_id": l["lot_id"]}
                  for l in c.execute("SELECT * FROM purchase_lines WHERE purchase_id=? ORDER BY position", (p["id"],))]
    d.update(purchase_totals(c, p)); return d


def spay_view(p: sqlite3.Row) -> dict:
    return {k: p[k] for k in ("id", "purchase_id", "supplier_id", "pay_date", "amount", "method", "reference", "status", "cancel_reason", "version", "seq", "created_at")}


def adj_view(a: sqlite3.Row) -> dict:
    return {k: a[k] for k in ("id", "product_id", "adj_date", "qty", "reason", "note", "theoretical", "counted", "batch_id", "depot_id", "lot_id", "status", "version", "seq", "created_at")}


def pull_extra(c: sqlite3.Connection, user: sqlite3.Row, since: int) -> dict:
    role, out = user["role"], {"stock": {}, "suppliers": [], "purchases": [], "supplier_payments": [], "adjustments": []}
    if can(role, "stock.read"):
        levels = stock_levels(c)
        reorder = {r["id"]: r["reorder_level"] for r in c.execute("SELECT id, reorder_level FROM products WHERE deleted=0")}
        costs = cost_info(c) if can(role, "costs.read") else {}
        out["stock"] = {pid: {"qty": q, "reorder": reorder.get(pid, 0), **costs.get(pid, {})} for pid, q in levels.items()}
    if can(role, "suppliers.read"):
        out["suppliers"] = [supplier_view(s) for s in c.execute("SELECT * FROM suppliers WHERE seq>? ORDER BY seq", (since,))]
    if can(role, "purchases.read"):
        touched = {r["purchase_id"] for r in c.execute("SELECT purchase_id FROM supplier_payments WHERE seq>?", (since,))}
        rows = c.execute("SELECT * FROM purchases WHERE seq>? ORDER BY seq", (since,)).fetchall()
        rows += [p for pid in touched for p in c.execute("SELECT * FROM purchases WHERE id=? AND seq<=?", (pid, since))]
        out["purchases"] = [purchase_view(c, p) for p in rows]
        out["supplier_payments"] = [spay_view(p) for p in c.execute("SELECT * FROM supplier_payments WHERE seq>? ORDER BY seq", (since,))]
    if can(role, "stock.adjust"):
        out["adjustments"] = [adj_view(a) for a in c.execute("SELECT * FROM stock_adjustments WHERE seq>? ORDER BY seq", (since,))]
    return out


# ---------------------------------------------------------------- fournisseurs
def _supplier_fields(data: dict, partial: bool) -> dict:
    out = {}
    if "name" in data or not partial: out["name"] = _text(data.get("name"), "Le nom du fournisseur", 120, True)
    if "phone" in data: out["phone"] = "".join(ch for ch in str(data.get("phone") or "") if ch.isdigit() or ch == "+")[:20]
    if "zone" in data: out["zone"] = _text(data.get("zone"), "La zone", 60)
    if "note" in data: out["note"] = _text(data.get("note"), "La note", 300)
    return out


def _supplier_create(c, user, actor, data, role, now):
    if not can(role, "suppliers.write"): return _deny(c, actor, "supplier.create", None, "droit manquant")
    f = _supplier_fields(data, False)
    sid = str(data.get("id") or uuid.uuid4())
    if not UUID_RE.match(sid): raise OpError("Identifiant de fournisseur invalide.")
    if c.execute("SELECT 1 FROM suppliers WHERE id=?", (sid,)).fetchone(): raise OpError("Ce fournisseur existe déjà.")
    code = f"F{next_counter(c, 'supplier_no'):03d}"
    c.execute("INSERT INTO suppliers(id,code,name,phone,zone,note,version,seq,deleted,created_at,updated_at,created_by,updated_by) VALUES(?,?,?,?,?,?,1,?,0,?,?,?,?)",
              (sid, code, f["name"], f.get("phone", ""), f.get("zone", ""), f.get("note", ""), next_seq(c), now, now, user["id"], user["id"]))
    r = c.execute("SELECT * FROM suppliers WHERE id=?", (sid,)).fetchone()
    audit.record(c, actor, "supplier.create", "supplier", sid, None, {"code": code, "name": r["name"], "phone": r["phone"]})
    return {"status": "ok", "supplier": supplier_view(r)}


def _supplier_update(c, user, actor, data, role, now):
    if not can(role, "suppliers.write"): return _deny(c, actor, "supplier.update", None, "droit manquant")
    r = c.execute("SELECT * FROM suppliers WHERE id=? AND deleted=0", (str(data.get("id") or ""),)).fetchone()
    if r is None: return {"status": "invalid", "message": "Fournisseur introuvable."}
    f = _supplier_fields(data.get("changes") or {}, True)
    changed = {k: v for k, v in f.items() if r[k] != v}
    if int(data.get("base_version") or 0) != r["version"]:
        return _conflict(c, actor, "supplier", r["id"], supplier_view(r), f"version attendue {data.get('base_version')}, actuelle {r['version']}")
    if not changed: return {"status": "ok", "supplier": supplier_view(r), "unchanged": True}
    c.execute(f"UPDATE suppliers SET {', '.join(k + '=?' for k in changed)}, version=version+1, seq=?, updated_at=?, updated_by=? WHERE id=?", (*changed.values(), next_seq(c), now, user["id"], r["id"]))
    audit.record(c, actor, "supplier.update", "supplier", r["id"], {k: r[k] for k in changed}, changed)
    return {"status": "ok", "supplier": supplier_view(c.execute("SELECT * FROM suppliers WHERE id=?", (r["id"],)).fetchone())}


def _supplier_delete(c, user, actor, data, role, now):
    if not can(role, "suppliers.write"): return _deny(c, actor, "supplier.delete", None, "droit manquant")
    r = c.execute("SELECT * FROM suppliers WHERE id=? AND deleted=0", (str(data.get("id") or ""),)).fetchone()
    if r is None: return {"status": "invalid", "message": "Fournisseur introuvable."}
    if int(data.get("base_version") or 0) != r["version"]:
        return _conflict(c, actor, "supplier", r["id"], supplier_view(r), "suppression : fournisseur modifié entre-temps")
    bal = supplier_balance(c, r["id"])
    if bal > 0: return {"status": "invalid", "message": f"On doit encore {bal} à ce fournisseur : réglez ou annulez ses achats d'abord."}
    c.execute("UPDATE suppliers SET deleted=1, version=version+1, seq=?, updated_at=?, updated_by=? WHERE id=?", (next_seq(c), now, user["id"], r["id"]))
    audit.record(c, actor, "supplier.delete", "supplier", r["id"], {"code": r["code"], "name": r["name"]}, {"deleted": True}, note="suppression logique : l'historique des achats est conservé")
    return {"status": "ok"}


# ---------------------------------------------------------------- achats
def _purchase_create(c, user, actor, data, role, now):
    if not can(role, "purchases.create"): return _deny(c, actor, "purchase.create", None, "droit manquant")
    kind = data.get("kind")
    if kind not in ("cash", "credit"): raise OpError("Type d'achat : comptant ou crédit.")
    pid = str(data.get("id") or uuid.uuid4())
    if not UUID_RE.match(pid): raise OpError("Identifiant d'achat invalide.")
    if c.execute("SELECT 1 FROM purchases WHERE id=?", (pid,)).fetchone(): raise OpError("Cet achat existe déjà.")
    sup = c.execute("SELECT * FROM suppliers WHERE id=? AND deleted=0", (str(data.get("supplier_id") or ""),)).fetchone()
    if sup is None: raise OpError("Fournisseur introuvable.")
    pdate = _date(data.get("purchase_date") or today().isoformat(), "Date de l'achat")
    d = date.fromisoformat(pdate)
    if d > today() + timedelta(days=1): raise OpError("La date de l'achat ne peut pas être dans le futur.")
    period.ensure_open(c, pdate)
    raw = data.get("lines")
    if not isinstance(raw, list) or not (1 <= len(raw) <= 30): raise OpError("Un achat comporte de 1 à 30 lignes.")
    from . import warehouses
    lines, total, alerts, extras, seen_lots = [], 0, [], [], {}
    for i, ln in enumerate(raw, 1):
        if not isinstance(ln, dict): raise OpError(f"Ligne {i} invalide.")
        prod = c.execute("SELECT * FROM products WHERE id=? AND deleted=0", (ln.get("product_id") or "",)).fetchone()
        if prod is None: raise OpError(f"Ligne {i} : produit introuvable.")
        qty = _int(ln.get("qty"), f"Ligne {i} : quantité", 1, 10 ** 7)
        cost = _int(ln.get("unit_cost"), f"Ligne {i} : prix d'achat", 0, 10 ** 10)
        dep = warehouses.active_depot(c, ln.get("depot_id")); lot_no = _text(ln.get("lot_no"), f"Ligne {i} : n° de lot", 40)
        expiry = _date(ln.get("expiry_date") or None, f"Ligne {i} : péremption", required=False)
        if expiry and expiry < pdate: raise OpError(f"Ligne {i} : la date de péremption précède l'achat.")
        warehouses.check_lot(c, prod["id"], lot_no, expiry, seen_lots); extras.append((dep, lot_no, expiry))
        tr = ln.get("tax_rate"); tax = None if tr in (None, "") else _int(tr, f"Ligne {i} : TVA", 0, 30)          # vide : taux de TVA sur achats des réglages comptables
        prev = _last_cost(c, prod["id"])
        if prev and prev > 0 and (cost - prev) * 100 > COST_ALERT_PCT * prev:
            alerts.append({"product_id": prod["id"], "name": prod["name"], "prev": prev, "new": cost, "pct": round((cost - prev) * 100 / prev, 1)})
        lines.append((i, prod["id"], prod["name"], qty, cost, qty * cost, prev, tax)); total += qty * cost
    method = _text(data.get("pay_method"), "Mode de paiement", 30)
    if method and not valid_method(c, method): raise OpError("Mode de paiement inconnu.")
    due = _date(data.get("due_date"), "Échéance", required=False)
    if kind == "cash":
        paid, due = total, None
        if total > 0 and not method: raise OpError("Un achat au comptant demande un mode de paiement.")
    else:
        if due is None: raise OpError("Un achat à crédit demande une date d'échéance.")
        if date.fromisoformat(due) < d: raise OpError("L'échéance ne peut pas précéder l'achat.")
        paid = _int(data.get("paid_at_purchase", 0), "Acompte", 0, total)
        if paid > 0 and not method: raise OpError("Un acompte demande un mode de paiement.")
    number = f"AC-{next_counter(c, 'purchase_no'):04d}"
    c.execute("""INSERT INTO purchases(id,number,supplier_id,purchase_date,kind,due_date,supplier_ref,total,paid_at_purchase,pay_method,note,status,version,seq,created_at,updated_at,created_by,updated_by)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,'active',1,?,?,?,?,?)""",
              (pid, number, sup["id"], pdate, kind, due, _text(data.get("supplier_ref"), "N° de facture fournisseur", 60), total, paid, method, _text(data.get("note"), "Note", 300), next_seq(c), now, now, user["id"], user["id"]))
    for ln, (dep, lot_no, expiry) in zip(lines, extras):
        lot_id = warehouses.upsert_lot(c, ln[1], lot_no, expiry, f"L{number[3:]}-{ln[0]}", now)
        c.execute("INSERT INTO purchase_lines(purchase_id,position,product_id,name,qty,unit_cost,amount,prev_cost,tax_rate,depot_id,lot_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (pid, *ln, dep, lot_id))
    p = c.execute("SELECT * FROM purchases WHERE id=?", (pid,)).fetchone()
    note = "; ".join(f"hausse de {a['pct']} % sur {a['name']} ({a['prev']} → {a['new']})" for a in alerts) or None
    audit.record(c, actor, "purchase.create", "purchase", pid, None, {"number": number, "supplier": sup["code"], "kind": kind, "total": total, "paid_at_purchase": paid, "due_date": due, "lines": [f"{l[3]} × {l[2]} à {l[4]}" for l in lines]}, note=note)
    return {"status": "ok", "purchase": purchase_view(c, p), "cost_alerts": alerts}


def _purchase_cancel(c, user, actor, data, role, now):
    if not can(role, "purchases.cancel"): return _deny(c, actor, "purchase.cancel", str(data.get("id") or ""), "droit manquant")
    p = c.execute("SELECT * FROM purchases WHERE id=? AND status='active'", (str(data.get("id") or ""),)).fetchone()
    if p is None: return {"status": "invalid", "message": "Achat introuvable ou déjà annulé."}
    period.ensure_open(c, p["purchase_date"])
    reason = _text(data.get("reason"), "Le motif", 200, True)
    if len(reason) < 3: raise OpError("Le motif d'annulation est obligatoire.")
    if int(data.get("base_version") or 0) != p["version"]:
        return _conflict(c, actor, "purchase", p["id"], purchase_view(c, p), "annulation : achat modifié entre-temps")
    if purchase_totals(c, p)["payments_total"]: return {"status": "invalid", "message": "Cet achat a des règlements : annulez-les d'abord."}
    levels = stock_levels(c)
    for l in c.execute("SELECT product_id, name, SUM(qty) q FROM purchase_lines WHERE purchase_id=? GROUP BY product_id, name", (p["id"],)):
        if l["product_id"] in levels and levels[l["product_id"]] - l["q"] < 0:
            return {"status": "invalid", "message": f"Annulation impossible : « {l['name']} » est déjà vendu ou sorti (stock {levels[l['product_id']]}, reçu {l['q']}). Corrigez par un ajustement."}
    c.execute("UPDATE purchases SET status='cancelled', cancel_reason=?, version=version+1, seq=?, updated_at=?, updated_by=? WHERE id=?", (reason, next_seq(c), now, user["id"], p["id"]))
    note = p["number"] + (f" : {p['paid_at_purchase']} payés à l'achat, à récupérer auprès du fournisseur" if p["paid_at_purchase"] else "")
    audit.record(c, actor, "purchase.cancel", "purchase", p["id"], {"status": "active"}, {"status": "cancelled", "reason": reason}, note=note)
    return {"status": "ok", "purchase": purchase_view(c, c.execute("SELECT * FROM purchases WHERE id=?", (p["id"],)).fetchone()), "refund_due": p["paid_at_purchase"]}


# ---------------------------------------------------------------- règlements fournisseurs
def _spay_create(c, user, actor, data, role, now):
    if not can(role, "supplierpay.create"): return _deny(c, actor, "supplierpay.create", None, "droit manquant")
    p = c.execute("SELECT * FROM purchases WHERE id=?", (str(data.get("purchase_id") or ""),)).fetchone()
    if p is None or p["status"] != "active": return {"status": "invalid", "message": "Achat introuvable."}
    sid = str(data.get("id") or uuid.uuid4())
    if not UUID_RE.match(sid): raise OpError("Identifiant de règlement invalide.")
    if c.execute("SELECT 1 FROM supplier_payments WHERE id=?", (sid,)).fetchone(): raise OpError("Ce règlement existe déjà.")
    amount = _int(data.get("amount"), "Le montant", 1, 10 ** 12)
    method = _text(data.get("method"), "Le mode de paiement", 30, True)
    if not valid_method(c, method): raise OpError("Mode de paiement inconnu.")
    ref = _text(data.get("reference"), "La référence", 60)
    pay_date = _date(data.get("pay_date") or today().isoformat(), "Date du règlement")
    if date.fromisoformat(pay_date) > today() + timedelta(days=1): raise OpError("La date du règlement ne peut pas être dans le futur.")
    period.ensure_open(c, pay_date)
    balance = purchase_totals(c, p)["balance"]
    if amount > balance: return {"status": "invalid", "message": f"Le règlement dépasse le reste à payer ({balance})."}
    if ref and c.execute("SELECT 1 FROM supplier_payments WHERE purchase_id=? AND reference=? AND method=? AND amount=? AND status='active'", (p["id"], ref, method, amount)).fetchone():
        return {"status": "invalid", "message": "Ce règlement (même référence, même montant) est déjà enregistré."}
    c.execute("INSERT INTO supplier_payments(id,purchase_id,supplier_id,pay_date,amount,method,reference,status,version,seq,created_at,updated_at,created_by,updated_by) VALUES(?,?,?,?,?,?,?,'active',1,?,?,?,?,?)",
              (sid, p["id"], p["supplier_id"], pay_date, amount, method, ref, next_seq(c), now, now, user["id"], user["id"]))
    r = c.execute("SELECT * FROM supplier_payments WHERE id=?", (sid,)).fetchone()
    audit.record(c, actor, "supplierpay.create", "supplierpay", sid, None, {"purchase": p["number"], "amount": amount, "method": method, "reference": ref, "date": pay_date})
    return {"status": "ok", "supplier_payment": spay_view(r), "purchase": purchase_view(c, p)}


def _spay_cancel(c, user, actor, data, role, now):
    if not can(role, "supplierpay.cancel"): return _deny(c, actor, "supplierpay.cancel", str(data.get("id") or ""), "droit manquant")
    r = c.execute("SELECT * FROM supplier_payments WHERE id=? AND status='active'", (str(data.get("id") or ""),)).fetchone()
    if r is None: return {"status": "invalid", "message": "Règlement introuvable ou déjà annulé."}
    period.ensure_open(c, r["pay_date"])
    reason = _text(data.get("reason"), "Le motif", 200, True)
    if len(reason) < 3: raise OpError("Le motif d'annulation est obligatoire.")
    if int(data.get("base_version") or 0) != r["version"]:
        return _conflict(c, actor, "supplierpay", r["id"], spay_view(r), "annulation : règlement modifié entre-temps")
    c.execute("UPDATE supplier_payments SET status='cancelled', cancel_reason=?, version=version+1, seq=?, updated_at=?, updated_by=? WHERE id=?", (reason, next_seq(c), now, user["id"], r["id"]))
    num = c.execute("SELECT number FROM purchases WHERE id=?", (r["purchase_id"],)).fetchone()["number"]
    audit.record(c, actor, "supplierpay.cancel", "supplierpay", r["id"], {"status": "active", "amount": r["amount"]}, {"status": "cancelled", "reason": reason}, note=num)
    return {"status": "ok", "supplier_payment": spay_view(c.execute("SELECT * FROM supplier_payments WHERE id=?", (r["id"],)).fetchone())}


# ---------------------------------------------------------------- ajustements et inventaire
def _tracked_product(c, pid):
    return c.execute("SELECT * FROM products WHERE id=? AND deleted=0 AND track_stock=1", (str(pid or ""),)).fetchone()


def _stock_adjust(c, user, actor, data, role, now):
    if not can(role, "stock.adjust"): return _deny(c, actor, "stock.adjust", None, "droit manquant")
    prod = _tracked_product(c, data.get("product_id"))
    if prod is None: return {"status": "invalid", "message": "Produit introuvable ou sans suivi de stock."}
    aid = str(data.get("id") or uuid.uuid4())
    if not UUID_RE.match(aid): raise OpError("Identifiant d'ajustement invalide.")
    if c.execute("SELECT 1 FROM stock_adjustments WHERE id=?", (aid,)).fetchone(): raise OpError("Cet ajustement existe déjà.")
    qty = _int(data.get("qty"), "La quantité", -10 ** 7, 10 ** 7)
    if qty == 0: raise OpError("La quantité ne peut pas être nulle.")
    reason = data.get("reason")
    if reason not in ("initial", "return", "loss", "other"): raise OpError("Motif : stock initial, retour client, casse ou perte, autre.")
    note = _text(data.get("note"), "La note", 200)
    if reason in ("initial", "return") and qty < 0: raise OpError("Cette raison ajoute du stock : quantité positive.")
    if reason == "loss" and qty > 0: raise OpError("Une casse ou perte retire du stock : quantité négative.")
    if reason in ("loss", "other") and len(note) < 3: raise OpError("Expliquez l'ajustement dans la note.")
    adj_date = _date(data.get("adj_date") or today().isoformat(), "Date")
    period.ensure_open(c, adj_date)
    from . import warehouses
    dep = warehouses.active_depot(c, data.get("depot_id"))
    level = warehouses.depot_level(c, prod["id"], dep)
    if level + qty < 0: return {"status": "invalid", "message": f"Le stock de ce dépôt deviendrait négatif ({level} en stock). Faites un inventaire pour corriger."}
    lot_id = None
    if data.get("lot_id"):
        lot = next((l for l in warehouses.lot_levels(c) if l["id"] == str(data["lot_id"]) and l["product_id"] == prod["id"]), None)
        if lot is None: raise OpError("Lot introuvable pour ce produit.")
        if qty > 0: raise OpError("Un ajustement sur un lot ne peut que retirer du stock (casse, péremption).")
        if -qty > lot["remaining"]: return {"status": "invalid", "message": f"Il ne reste que {lot['remaining']} dans le lot « {lot['lot_no']} »."}
        lot_id = lot["id"]
    c.execute("INSERT INTO stock_adjustments(id,product_id,adj_date,qty,reason,note,depot_id,lot_id,status,version,seq,created_at,updated_at,created_by,updated_by) VALUES(?,?,?,?,?,?,?,?,'active',1,?,?,?,?,?)",
              (aid, prod["id"], adj_date, qty, reason, note, dep, lot_id, next_seq(c), now, now, user["id"], user["id"]))
    a = c.execute("SELECT * FROM stock_adjustments WHERE id=?", (aid,)).fetchone()
    audit.record(c, actor, "stock.adjust", "product", prod["id"], {"stock": level}, {"stock": level + qty, "reason": reason, "note": note}, note=prod["name"])
    return {"status": "ok", "adjustment": adj_view(a), "stock": level + qty}


def _stock_inventory(c, user, actor, data, role, now):
    if not can(role, "stock.adjust"): return _deny(c, actor, "stock.inventory", None, "droit manquant")
    batch = str(data.get("id") or uuid.uuid4())
    if not UUID_RE.match(batch): raise OpError("Identifiant d'inventaire invalide.")
    if c.execute("SELECT 1 FROM stock_adjustments WHERE batch_id=?", (batch,)).fetchone(): raise OpError("Cet inventaire est déjà enregistré.")
    counts = data.get("counts")
    if not isinstance(counts, list) or not (1 <= len(counts) <= 300): raise OpError("Un inventaire compte de 1 à 300 produits.")
    adj_date = _date(data.get("date") or today().isoformat(), "Date de l'inventaire")
    period.ensure_open(c, adj_date)
    note = _text(data.get("note"), "La note", 200)
    from . import warehouses
    inv_depot = warehouses.active_depot(c, data.get("depot_id"))                 # on inventorie UN dépôt à la fois (le principal par défaut)
    by_depot = warehouses.stock_by_depot(c)
    levels, costs, seen, gaps, n_comp = {pid: d.get(inv_depot, 0) for pid, d in by_depot.items()}, cost_info(c), set(), [], 0
    for i, ct in enumerate(counts, 1):
        if not isinstance(ct, dict): raise OpError(f"Ligne {i} invalide.")
        prod = _tracked_product(c, ct.get("product_id"))
        if prod is None: raise OpError(f"Ligne {i} : produit introuvable ou sans suivi de stock.")
        if prod["id"] in seen: raise OpError(f"Ligne {i} : produit compté deux fois.")
        seen.add(prod["id"]); counted = _int(ct.get("counted"), f"Ligne {i} : quantité comptée", 0, 10 ** 7); n_comp += 1
        theo = levels.get(prod["id"], 0); delta = counted - theo
        if delta == 0: continue
        c.execute("INSERT INTO stock_adjustments(id,product_id,adj_date,qty,reason,note,theoretical,counted,batch_id,depot_id,status,version,seq,created_at,updated_at,created_by,updated_by) VALUES(?,?,?,?,'inventory',?,?,?,?,?,'active',1,?,?,?,?,?)",
                  (str(uuid.uuid4()), prod["id"], adj_date, delta, note, theo, counted, batch, inv_depot, next_seq(c), now, now, user["id"], user["id"]))
        value = delta * costs.get(prod["id"], {}).get("avg_cost", 0)
        gaps.append({"product_id": prod["id"], "name": prod["name"], "theoretical": theo, "counted": counted, "delta": delta, "value": value})
    if n_comp == 0: raise OpError("Aucun produit compté.")
    loss = sum(g["value"] for g in gaps if g["value"] < 0); gain = sum(g["value"] for g in gaps if g["value"] > 0)
    audit.record(c, actor, "stock.inventory", "inventory", batch, None, {"produits comptés": n_comp, "écarts": [f"{g['name']} : {g['theoretical']} → {g['counted']}" for g in gaps[:30]], "valeur des manques": loss, "valeur des excédents": gain}, note=note or None)
    return {"status": "ok", "gaps": gaps, "counted": n_comp, "loss_value": loss, "gain_value": gain}


HANDLERS = {"supplier.create": _supplier_create, "supplier.update": _supplier_update, "supplier.delete": _supplier_delete, "purchase.create": _purchase_create, "purchase.cancel": _purchase_cancel,
            "supplierpay.create": _spay_create, "supplierpay.cancel": _spay_cancel, "stock.adjust": _stock_adjust, "stock.inventory": _stock_inventory}
