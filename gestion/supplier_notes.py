"""Avoirs fournisseurs : marchandise rendue ou prix corrigé par un fournisseur.

Un avoir fournisseur diminue ce qu'on lui doit (« déduit de la dette ») ou donne lieu à un remboursement encaissé (espèces, banque, mobile money : « remboursé »). S'il porte sur
de la marchandise rendue, le stock diminue d'autant. En comptabilité : dette fournisseur (401) contre achats (601) et TVA récupérable ; le remboursement encaissé solde la dette.
Même logique que l'avoir client, de l'autre côté : jamais au-delà de la dette restante (déduit) ni de ce qui a été payé (remboursé).
"""
from __future__ import annotations

import sqlite3
import uuid

from . import audit, period
from .methods import valid_method
from .ops import UUID_RE, OpError, _deny, next_seq
from .permissions import can
from .sales_ops import _conflict, _int, _text, next_counter, today


def view(c: sqlite3.Connection, n) -> dict:
    d = {k: n[k] for k in ("id", "number", "purchase_id", "supplier_id", "note_date", "amount", "reason", "settlement", "refund_method", "status", "cancel_reason", "version", "seq", "created_at")}
    d["lines"] = [{"product_id": l["product_id"], "name": l["name"], "qty": l["qty"], "unit_cost": l["unit_cost"]} for l in c.execute("SELECT * FROM supplier_note_lines WHERE note_id=? ORDER BY position", (n["id"],))]
    return d


def pull_extra(c: sqlite3.Connection, user: sqlite3.Row, since: int) -> dict:
    if not can(user["role"], "purchases.read"): return {"supplier_notes": []}
    return {"supplier_notes": [view(c, n) for n in c.execute("SELECT * FROM supplier_notes ORDER BY seq")]}


def returned_qty(c: sqlite3.Connection, purchase_id: str) -> dict[str, int]:
    return {r["product_id"]: r["q"] for r in c.execute("SELECT l.product_id, SUM(l.qty) q FROM supplier_note_lines l JOIN supplier_notes n ON n.id=l.note_id WHERE n.purchase_id=? AND n.status='active' AND l.product_id IS NOT NULL GROUP BY l.product_id", (purchase_id,))}


def entries(c: sqlite3.Connection, mp: dict, split_for, pay_account) -> list[dict]:
    out = []
    for n in c.execute("SELECT n.*, u.number pnum FROM supplier_notes n JOIN purchases u ON u.id=n.purchase_id WHERE n.status='active' AND u.status='active' ORDER BY n.note_date, n.number"):
        ht, tva = split_for(n["purchase_id"], n["amount"])
        out.append({"key": f"snote:{n['id']}", "date": n["note_date"], "journal": "AC", "ref": n["number"], "label": f"Avoir fournisseur {n['number']} sur {n['pnum']} : {n['reason']}",
                    "lines": [(mp["suppliers"], n["amount"], 0), (mp["purchases"], 0, ht), (mp["vat_deductible"], 0, tva)], "auto": True})
        if n["settlement"] == "refund":
            a, j = pay_account(n["refund_method"])
            out.append({"key": f"snoterefund:{n['id']}", "date": n["note_date"], "journal": j, "ref": n["number"], "label": f"Remboursement du fournisseur (avoir {n['number']})", "lines": [(a, n["amount"], 0), (mp["suppliers"], 0, n["amount"])], "auto": True})
    return out


def _create(c, user, actor, data, role, now):
    from .stock_ops import purchase_totals, purchase_view, stock_levels
    if not can(role, "purchases.create"): return _deny(c, actor, "snote.create", None, "droit manquant")
    p = c.execute("SELECT * FROM purchases WHERE id=? AND status='active'", (str(data.get("purchase_id") or ""),)).fetchone()
    if p is None: return {"status": "invalid", "message": "Achat introuvable ou annulé."}
    nid = str(data.get("id") or uuid.uuid4())
    if not UUID_RE.match(nid): raise OpError("Identifiant d'avoir invalide.")
    if c.execute("SELECT 1 FROM supplier_notes WHERE id=?", (nid,)).fetchone(): raise OpError("Cet avoir existe déjà.")
    period.ensure_open(c, today().isoformat())
    reason = _text(data.get("reason"), "Le motif", 200, True)
    if len(reason) < 3: raise OpError("Le motif est obligatoire (marchandise défectueuse, erreur de prix…).")
    settlement = data.get("settlement")
    if settlement not in ("deduct", "refund"): raise OpError("Règlement de l'avoir : déduit de la dette ou remboursé.")
    method = _text(data.get("refund_method"), "Le mode de remboursement", 30) if settlement == "refund" else ""
    if settlement == "refund" and not valid_method(c, method): raise OpError("Un remboursement demande un mode d'encaissement connu.")
    lines = []
    raw = data.get("lines") or []
    if not isinstance(raw, list) or len(raw) > 30: raise OpError("Marchandises rendues : 30 lignes au plus.")
    bought = {l["product_id"]: l for l in c.execute("SELECT * FROM purchase_lines WHERE purchase_id=? AND product_id IS NOT NULL", (p["id"],))}
    back = returned_qty(c, p["id"]); levels = stock_levels(c); asked: dict[str, int] = {}
    for i, ln in enumerate(raw, 1):
        pid = str(ln.get("product_id") or ""); q = _int(ln.get("qty"), f"Ligne {i} : quantité", 1, 10 ** 7)
        if pid not in bought: raise OpError(f"Ligne {i} : ce produit ne figure pas sur cet achat.")
        asked[pid] = asked.get(pid, 0) + q
        if asked[pid] + back.get(pid, 0) > bought[pid]["qty"]: raise OpError(f"Ligne {i} : on ne peut pas rendre plus que ce qui a été acheté ({bought[pid]['qty']}, dont {back.get(pid, 0)} déjà rendu).")
        if pid in levels and asked[pid] > levels[pid]: raise OpError(f"Ligne {i} : le stock ({levels[pid]}) ne permet pas de rendre {asked[pid]}.")
        lines.append((i, pid, bought[pid]["name"], q, bought[pid]["unit_cost"], bought[pid]["lot_id"]))
    amount = _int(data.get("amount") or sum(l[3] * l[4] for l in lines), "Le montant de l'avoir", 1, 10 ** 12)
    t = purchase_totals(c, p)
    if amount > t["net_total"]: return {"status": "invalid", "message": f"L'avoir dépasse le montant restant de l'achat ({t['net_total']})."}
    if settlement == "deduct" and amount > t["balance"]: return {"status": "invalid", "message": f"Un avoir « déduit de la dette » ne peut pas dépasser la dette restante ({t['balance']}). Choisissez « remboursé »."}
    if settlement == "refund" and amount > t["paid_total"]: return {"status": "invalid", "message": f"On ne peut se faire rembourser que ce qui a été payé ({t['paid_total']})."}
    number = f"AF-{next_counter(c, 'snote_no'):04d}"
    c.execute("INSERT INTO supplier_notes(id,number,purchase_id,supplier_id,note_date,amount,reason,settlement,refund_method,status,version,seq,created_at,updated_at,created_by,updated_by) VALUES(?,?,?,?,?,?,?,?,?,'active',1,?,?,?,?,?)",
              (nid, number, p["id"], p["supplier_id"], today().isoformat(), amount, reason, settlement, method, next_seq(c), now, now, user["id"], user["id"]))
    for ln in lines: c.execute("INSERT INTO supplier_note_lines(note_id,position,product_id,name,qty,unit_cost,lot_id) VALUES(?,?,?,?,?,?,?)", (nid, *ln))
    audit.record(c, actor, "snote.create", "supplier_note", nid, None, {"numéro": number, "achat": p["number"], "montant": amount, "règlement": "remboursé (" + method + ")" if settlement == "refund" else "déduit de la dette", "motif": reason,
                                                                       "marchandises rendues": [f"{l[3]} × {l[2]}" for l in lines]})
    return {"status": "ok", "supplier_note": view(c, c.execute("SELECT * FROM supplier_notes WHERE id=?", (nid,)).fetchone()), "purchase": purchase_view(c, c.execute("SELECT * FROM purchases WHERE id=?", (p["id"],)).fetchone())}


def _cancel(c, user, actor, data, role, now):
    from .stock_ops import purchase_view
    if not can(role, "purchases.cancel"): return _deny(c, actor, "snote.cancel", str(data.get("id") or ""), "droit manquant")
    n = c.execute("SELECT * FROM supplier_notes WHERE id=? AND status='active'", (str(data.get("id") or ""),)).fetchone()
    if n is None: return {"status": "invalid", "message": "Avoir introuvable ou déjà annulé."}
    if int(data.get("base_version") or 0) != n["version"]: return _conflict(c, actor, "supplier_note", n["id"], view(c, n), "annulation : avoir modifié entre-temps")
    period.ensure_open(c, n["note_date"])
    if c.execute("SELECT 1 FROM bank_matches WHERE item_type='supplierrefund' AND item_id=? LIMIT 1", (n["id"],)).fetchone(): raise OpError("Le remboursement est pointé avec un relevé : dépointez-le d'abord.")
    reason = _text(data.get("reason"), "Le motif", 200, True)
    if len(reason) < 3: raise OpError("Le motif d'annulation est obligatoire.")
    c.execute("UPDATE supplier_notes SET status='cancelled', cancel_reason=?, version=version+1, seq=?, updated_at=?, updated_by=? WHERE id=?", (reason, next_seq(c), now, user["id"], n["id"]))
    audit.record(c, actor, "snote.cancel", "supplier_note", n["id"], {"numéro": n["number"], "montant": n["amount"]}, {"statut": "annulé", "motif": reason})
    return {"status": "ok", "purchase": purchase_view(c, c.execute("SELECT * FROM purchases WHERE id=?", (n["purchase_id"],)).fetchone())}


HANDLERS = {"snote.create": _create, "snote.cancel": _cancel}
