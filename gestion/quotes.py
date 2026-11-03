"""Devis : une offre de prix chiffrée, valable jusqu'à une date, qui devient une vente d'un clic quand le client accepte.

Un devis n'est PAS une écriture comptable (rien ne bouge en stock ni en compta tant qu'il n'est pas converti). Les règles de prix et de remise sont celles des ventes
(catalogue imposé, remise limitée pour le commercial). À la conversion, la vente suit toutes les règles d'une vente (limite de crédit, verrou de période, droits) et
le total doit rester identique à celui du devis : si le prix du catalogue a changé entre-temps, la conversion est refusée et il faut refaire le devis.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import date, timedelta

from . import audit, period
from .ops import UUID_RE, OpError, _deny, next_seq, visible_to
from .permissions import can
from .sales_ops import _conflict, _date, _text, next_counter, parse_lines, settings_get, today

DEFAULT_VALID_DAYS = 30


def view(c: sqlite3.Connection, q) -> dict:
    d = {k: q[k] for k in ("id", "number", "client_id", "quote_date", "valid_until", "total", "note", "status", "sale_id", "cancel_reason", "version", "seq", "created_at")}
    d["lines"] = [{"product_id": l["product_id"], "name": l["name"], "qty": l["qty"], "unit_price": l["unit_price"], "discount_pct": l["discount_pct"], "amount": l["amount"], "tax_rate": l["tax_rate"]}
                  for l in c.execute("SELECT * FROM quote_lines WHERE quote_id=? ORDER BY position", (q["id"],))]
    d["expired"] = q["status"] == "open" and q["valid_until"] < today().isoformat()
    s = c.execute("SELECT number FROM sales WHERE id=?", (q["sale_id"],)).fetchone() if q["sale_id"] else None
    d["sale_number"] = s["number"] if s else None
    return d


def pull_extra(c: sqlite3.Connection, user: sqlite3.Row, since: int) -> dict:
    role = user["role"]
    if not (can(role, "sales.read_all") or can(role, "sales.read_own")): return {"quotes": []}
    out = []
    for q in c.execute("SELECT * FROM quotes ORDER BY seq"):
        cl = c.execute("SELECT * FROM clients WHERE id=?", (q["client_id"],)).fetchone()
        if cl is not None and visible_to(role, user["id"], cl): out.append(view(c, q))
    return {"quotes": out}


def _create(c, user, actor, data, role, now):
    if not can(role, "sales.create"): return _deny(c, actor, "quote.create", None, "droit manquant")
    qid = str(data.get("id") or uuid.uuid4())
    if not UUID_RE.match(qid): raise OpError("Identifiant de devis invalide.")
    if c.execute("SELECT 1 FROM quotes WHERE id=?", (qid,)).fetchone(): raise OpError("Ce devis existe déjà.")
    cl = c.execute("SELECT * FROM clients WHERE id=? AND deleted=0", (str(data.get("client_id") or ""),)).fetchone()
    if cl is None or not visible_to(role, user["id"], cl): raise OpError("Client introuvable.")
    qd = _date(data.get("quote_date") or today().isoformat(), "Date du devis")
    if date.fromisoformat(qd) > today() + timedelta(days=1): raise OpError("La date du devis ne peut pas être dans le futur.")
    until = _date(data.get("valid_until") or (date.fromisoformat(qd) + timedelta(days=DEFAULT_VALID_DAYS)).isoformat(), "Validité")
    if date.fromisoformat(until) < date.fromisoformat(qd): raise OpError("La validité ne peut pas précéder la date du devis.")
    if (date.fromisoformat(until) - date.fromisoformat(qd)).days > 365: raise OpError("Validité : un an au plus.")
    lines, total = parse_lines(c, role, data.get("lines"), "Un devis")
    number = f"DV-{next_counter(c, 'quote_no'):04d}"
    c.execute("INSERT INTO quotes(id,number,client_id,quote_date,valid_until,total,note,status,version,seq,created_at,updated_at,created_by,updated_by) VALUES(?,?,?,?,?,?,?,'open',1,?,?,?,?,?)",
              (qid, number, cl["id"], qd, until, total, _text(data.get("note"), "La note", 300), next_seq(c), now, now, user["id"], user["id"]))
    for ln in lines:
        c.execute("INSERT INTO quote_lines(quote_id,position,product_id,name,qty,unit_price,discount_pct,amount,tax_rate) VALUES(?,?,?,?,?,?,?,?,?)", (qid, *ln))
    audit.record(c, actor, "quote.create", "quote", qid, None, {"numéro": number, "client": cl["code"], "total": total, "valable jusqu'au": until, "lignes": [f"{l[3]} × {l[2]}" for l in lines]})
    return {"status": "ok", "quote": view(c, c.execute("SELECT * FROM quotes WHERE id=?", (qid,)).fetchone())}


def _load(c, user, role, data):
    q = c.execute("SELECT * FROM quotes WHERE id=?", (str(data.get("id") or ""),)).fetchone()
    if q is None: return None
    cl = c.execute("SELECT * FROM clients WHERE id=?", (q["client_id"],)).fetchone()
    return q if cl is not None and visible_to(role, user["id"], cl) else None


def _close(c, user, actor, data, role, now):
    if not can(role, "sales.create"): return _deny(c, actor, "quote.close", str(data.get("id") or ""), "droit manquant")
    q = _load(c, user, role, data)
    if q is None or q["status"] != "open": return {"status": "invalid", "message": "Devis introuvable ou déjà clos."}
    outcome = data.get("outcome")
    if outcome not in ("declined", "cancelled"): raise OpError("Refusé par le client ou annulé ?")
    reason = _text(data.get("reason"), "Le motif", 200, True)
    if len(reason) < 3: raise OpError("Le motif est obligatoire.")
    if int(data.get("base_version") or 0) != q["version"]: return _conflict(c, actor, "quote", q["id"], view(c, q), "clôture : devis modifié entre-temps")
    c.execute("UPDATE quotes SET status=?, cancel_reason=?, version=version+1, seq=?, updated_at=?, updated_by=? WHERE id=?", (outcome, reason, next_seq(c), now, user["id"], q["id"]))
    audit.record(c, actor, "quote.close", "quote", q["id"], {"statut": "ouvert"}, {"statut": "refusé par le client" if outcome == "declined" else "annulé", "motif": reason}, note=q["number"])
    return {"status": "ok"}


def _convert(c, user, actor, data, role, now):
    from . import sales_ops
    if not can(role, "sales.create"): return _deny(c, actor, "quote.convert", str(data.get("id") or ""), "droit manquant")
    q = _load(c, user, role, data)
    if q is None or q["status"] != "open": return {"status": "invalid", "message": "Devis introuvable ou déjà clos."}
    if int(data.get("base_version") or 0) != q["version"]: return _conflict(c, actor, "quote", q["id"], view(c, q), "conversion : devis modifié entre-temps")
    if q["valid_until"] < today().isoformat(): raise OpError("Ce devis a expiré : refaites-en un (les prix ont pu changer).")
    lines = [{"product_id": l["product_id"], "name": l["name"], "qty": l["qty"], "unit_price": l["unit_price"], "discount_pct": l["discount_pct"], **({"tax_rate": l["tax_rate"]} if not l["product_id"] else {})} for l in c.execute("SELECT * FROM quote_lines WHERE quote_id=? ORDER BY position", (q["id"],))]
    for l in lines:                                                           # le prix du devis doit toujours être celui qu'on pourra facturer
        if l["product_id"] and not (can(role, "sales.override_price")):
            p = c.execute("SELECT price FROM products WHERE id=?", (l["product_id"],)).fetchone()
            if p is None or p["price"] != l["unit_price"]: raise OpError("Le prix du catalogue a changé depuis ce devis : refaites le devis.")
    sale_id = str(data.get("sale_id") or uuid.uuid4())
    sale_data = {"id": sale_id, "client_id": q["client_id"], "kind": data.get("kind"), "lines": lines, "pay_method": data.get("pay_method"), "due_date": data.get("due_date"), "paid_at_sale": data.get("paid_at_sale", 0),
                 "override_credit": data.get("override_credit"), "note": f"Devis {q['number']}", "delivery_due": data.get("delivery_due")}
    c.execute("SAVEPOINT conv")
    res = sales_ops._sale_create(c, user, actor, sale_data, role, now)
    if res.get("status") != "ok":
        c.execute("RELEASE conv"); return res
    if res["sale"]["total"] != q["total"]:                                      # ne devrait pas arriver après la vérification ci-dessus : on défait la vente
        c.execute("ROLLBACK TO conv"); c.execute("RELEASE conv")
        raise OpError("Le total de la vente diffère de celui du devis : conversion refusée.")
    c.execute("RELEASE conv")
    c.execute("UPDATE quotes SET status='converted', sale_id=?, version=version+1, seq=?, updated_at=?, updated_by=? WHERE id=?", (sale_id, next_seq(c), now, user["id"], q["id"]))
    audit.record(c, actor, "quote.convert", "quote", q["id"], {"statut": "ouvert"}, {"statut": "converti en vente", "vente": res["sale"]["number"]}, note=q["number"])
    return {"status": "ok", "sale": res["sale"], "quote": view(c, c.execute("SELECT * FROM quotes WHERE id=?", (q["id"],)).fetchone())}


HANDLERS = {"quote.create": _create, "quote.close": _close, "quote.convert": _convert}
