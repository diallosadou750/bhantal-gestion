"""Bons de commande fournisseurs : commander, suivre ce qui est attendu, puis transformer en achat à la réception.

Un bon de commande n'est PAS une écriture comptable (rien ne bouge en stock ni en compta tant que la marchandise n'est pas reçue). À la réception, on peut corriger les
quantités et les prix réellement livrés (une ligne à 0 est retirée) ; l'achat suit alors toutes les règles d'un achat (verrou de période, stock, TVA, dettes).
Réception complète seulement : pour une livraison en plusieurs fois, recevez ce qui est arrivé puis refaites un bon pour le reste.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import date, timedelta

from . import audit
from .ops import UUID_RE, OpError, _deny, next_seq
from .permissions import can
from .sales_ops import _conflict, _date, _int, _text, next_counter, today

DEFAULT_DELAY_DAYS = 7


def view(c: sqlite3.Connection, o) -> dict:
    d = {k: o[k] for k in ("id", "number", "supplier_id", "order_date", "expected_date", "total", "note", "status", "purchase_id", "cancel_reason", "version", "seq", "created_at")}
    d["lines"] = [{"position": l["position"], "product_id": l["product_id"], "name": l["name"], "qty": l["qty"], "unit_cost": l["unit_cost"], "amount": l["amount"]}
                  for l in c.execute("SELECT * FROM purchase_order_lines WHERE order_id=? ORDER BY position", (o["id"],))]
    d["late"] = o["status"] == "open" and o["expected_date"] < today().isoformat()
    p = c.execute("SELECT number FROM purchases WHERE id=?", (o["purchase_id"],)).fetchone() if o["purchase_id"] else None
    d["purchase_number"] = p["number"] if p else None
    return d


def pull_extra(c: sqlite3.Connection, user: sqlite3.Row, since: int) -> dict:
    if not can(user["role"], "purchases.read"): return {"porders": []}
    return {"porders": [view(c, o) for o in c.execute("SELECT * FROM purchase_orders ORDER BY seq")]}


def _create(c, user, actor, data, role, now):
    if not can(role, "purchases.create"): return _deny(c, actor, "porder.create", None, "droit manquant")
    oid = str(data.get("id") or uuid.uuid4())
    if not UUID_RE.match(oid): raise OpError("Identifiant de commande invalide.")
    if c.execute("SELECT 1 FROM purchase_orders WHERE id=?", (oid,)).fetchone(): raise OpError("Cette commande existe déjà.")
    sup = c.execute("SELECT * FROM suppliers WHERE id=? AND deleted=0", (str(data.get("supplier_id") or ""),)).fetchone()
    if sup is None: raise OpError("Fournisseur introuvable.")
    od = _date(data.get("order_date") or today().isoformat(), "Date de la commande")
    if date.fromisoformat(od) > today() + timedelta(days=1): raise OpError("La date de la commande ne peut pas être dans le futur.")
    exp = _date(data.get("expected_date") or (date.fromisoformat(od) + timedelta(days=DEFAULT_DELAY_DAYS)).isoformat(), "Livraison attendue le")
    if date.fromisoformat(exp) < date.fromisoformat(od): raise OpError("La livraison attendue ne peut pas précéder la commande.")
    if (date.fromisoformat(exp) - date.fromisoformat(od)).days > 365: raise OpError("Livraison attendue : un an au plus.")
    raw = data.get("lines")
    if not isinstance(raw, list) or not (1 <= len(raw) <= 30): raise OpError("Une commande comporte de 1 à 30 lignes.")
    lines, total, seen = [], 0, set()
    for i, ln in enumerate(raw, 1):
        if not isinstance(ln, dict): raise OpError(f"Ligne {i} invalide.")
        prod = c.execute("SELECT * FROM products WHERE id=? AND deleted=0", (ln.get("product_id") or "",)).fetchone()
        if prod is None: raise OpError(f"Ligne {i} : produit introuvable.")
        if prod["id"] in seen: raise OpError(f"Ligne {i} : « {prod['name']} » figure déjà dans la commande.")
        seen.add(prod["id"])
        qty = _int(ln.get("qty"), f"Ligne {i} : quantité", 1, 10 ** 7); cost = _int(ln.get("unit_cost"), f"Ligne {i} : prix d'achat", 0, 10 ** 10)
        lines.append((i, prod["id"], prod["name"], qty, cost, qty * cost)); total += qty * cost
    number = f"BC-{next_counter(c, 'po_no'):04d}"
    c.execute("INSERT INTO purchase_orders(id,number,supplier_id,order_date,expected_date,total,note,status,version,seq,created_at,updated_at,created_by,updated_by) VALUES(?,?,?,?,?,?,?,'open',1,?,?,?,?,?)",
              (oid, number, sup["id"], od, exp, total, _text(data.get("note"), "La note", 300), next_seq(c), now, now, user["id"], user["id"]))
    for ln in lines:
        c.execute("INSERT INTO purchase_order_lines(order_id,position,product_id,name,qty,unit_cost,amount) VALUES(?,?,?,?,?,?,?)", (oid, *ln))
    audit.record(c, actor, "porder.create", "porder", oid, None, {"numéro": number, "fournisseur": sup["code"], "total": total, "livraison attendue": exp, "lignes": [f"{l[3]} × {l[2]} à {l[4]}" for l in lines]})
    return {"status": "ok", "porder": view(c, c.execute("SELECT * FROM purchase_orders WHERE id=?", (oid,)).fetchone())}


def _load(c, data):
    return c.execute("SELECT * FROM purchase_orders WHERE id=?", (str(data.get("id") or ""),)).fetchone()


def _close(c, user, actor, data, role, now):
    if not can(role, "purchases.create"): return _deny(c, actor, "porder.close", str(data.get("id") or ""), "droit manquant")
    o = _load(c, data)
    if o is None or o["status"] != "open": return {"status": "invalid", "message": "Commande introuvable ou déjà close."}
    reason = _text(data.get("reason"), "Le motif", 200, True)
    if len(reason) < 3: raise OpError("Le motif est obligatoire.")
    if int(data.get("base_version") or 0) != o["version"]: return _conflict(c, actor, "porder", o["id"], view(c, o), "annulation : commande modifiée entre-temps")
    c.execute("UPDATE purchase_orders SET status='cancelled', cancel_reason=?, version=version+1, seq=?, updated_at=?, updated_by=? WHERE id=?", (reason, next_seq(c), now, user["id"], o["id"]))
    audit.record(c, actor, "porder.close", "porder", o["id"], {"statut": "ouverte"}, {"statut": "annulée", "motif": reason}, note=o["number"])
    return {"status": "ok"}


def _receive(c, user, actor, data, role, now):
    from . import stock_ops
    if not can(role, "purchases.create"): return _deny(c, actor, "porder.receive", str(data.get("id") or ""), "droit manquant")
    o = _load(c, data)
    if o is None or o["status"] != "open": return {"status": "invalid", "message": "Commande introuvable ou déjà reçue ou annulée."}
    if int(data.get("base_version") or 0) != o["version"]: return _conflict(c, actor, "porder", o["id"], view(c, o), "réception : commande modifiée entre-temps")
    over = {}
    for r in (data.get("lines") or []):
        if not isinstance(r, dict) or not isinstance(r.get("position"), int): raise OpError("Ligne de réception invalide.")
        over[r["position"]] = r
    lines, diffs = [], []
    for l in c.execute("SELECT * FROM purchase_order_lines WHERE order_id=? ORDER BY position", (o["id"],)):
        r = over.get(l["position"], {})
        qty = _int(r["qty"], f"Ligne {l['position']} : quantité reçue", 0, 10 ** 7) if "qty" in r else l["qty"]
        cost = _int(r["unit_cost"], f"Ligne {l['position']} : prix", 0, 10 ** 10) if "unit_cost" in r else l["unit_cost"]
        if qty != l["qty"] or cost != l["unit_cost"]: diffs.append(f"{l['name']} : commandé {l['qty']} à {l['unit_cost']}, reçu {qty} à {cost}")
        if qty > 0: lines.append({"product_id": l["product_id"], "qty": qty, "unit_cost": cost, "depot_id": str(r.get("depot_id") or "main")})
    if not lines: raise OpError("Rien n'a été reçu : annulez plutôt la commande.")
    pid = str(data.get("purchase_id") or uuid.uuid4())
    sup = c.execute("SELECT code FROM suppliers WHERE id=?", (o["supplier_id"],)).fetchone()
    pdata = {"id": pid, "supplier_id": o["supplier_id"], "kind": data.get("kind"), "lines": lines, "pay_method": data.get("pay_method"), "due_date": data.get("due_date"), "paid_at_purchase": data.get("paid_at_purchase", 0),
             "supplier_ref": data.get("supplier_ref"), "purchase_date": data.get("purchase_date"), "note": f"Commande {o['number']}"}
    c.execute("SAVEPOINT recv")
    res = stock_ops._purchase_create(c, user, actor, pdata, role, now)
    if res.get("status") != "ok":
        c.execute("ROLLBACK TO recv"); c.execute("RELEASE recv"); return res
    c.execute("RELEASE recv")
    c.execute("UPDATE purchase_orders SET status='received', purchase_id=?, version=version+1, seq=?, updated_at=?, updated_by=? WHERE id=?", (pid, next_seq(c), now, user["id"], o["id"]))
    audit.record(c, actor, "porder.receive", "porder", o["id"], {"statut": "ouverte"}, {"statut": "reçue", "achat": res["purchase"]["number"], "écarts": diffs or None}, note=o["number"])
    return {"status": "ok", "purchase": res["purchase"], "cost_alerts": res.get("cost_alerts", []), "porder": view(c, c.execute("SELECT * FROM purchase_orders WHERE id=?", (o["id"],)).fetchone())}


HANDLERS = {"porder.create": _create, "porder.close": _close, "porder.receive": _receive}
