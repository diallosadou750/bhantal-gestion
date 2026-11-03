"""Bons de livraison : ce qui a réellement été remis au client, en une ou plusieurs fois, avec le nom de celui qui a reçu.

Un bon de livraison ne touche ni le stock (déjà sorti à la vente) ni la comptabilité : c'est la preuve de remise de la marchandise. Les quantités livrées d'une ligne de vente ne
peuvent jamais dépasser les quantités vendues. Quand tout est livré, la vente est marquée « livrée » automatiquement ; si on annule un bon, elle ne l'est plus.
Une vente marquée livrée avant l'existence des bons (sans aucun bon, même annulé) est considérée comme entièrement livrée.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import date, timedelta

from . import audit
from .ops import UUID_RE, OpError, _deny, next_seq
from .permissions import can
from .sales_ops import _conflict, _date, _int, _text, next_counter, sale_visible, today


def remaining(c: sqlite3.Connection, sale) -> dict[int, int]:
    """{position de la ligne de vente: quantité restant à livrer}."""
    lines = {l["position"]: l["qty"] for l in c.execute("SELECT position, qty FROM sale_lines WHERE sale_id=?", (sale["id"],))}
    notes = c.execute("SELECT 1 FROM delivery_notes WHERE sale_id=? LIMIT 1", (sale["id"],)).fetchone()
    if sale["delivered_at"] and not notes: return {p: 0 for p in lines}               # livrée avant la mise en place des bons
    done = {r["sale_position"]: r["q"] for r in c.execute("SELECT l.sale_position, SUM(l.qty) q FROM delivery_lines l JOIN delivery_notes n ON n.id=l.note_id WHERE n.sale_id=? AND n.status='active' GROUP BY l.sale_position", (sale["id"],))}
    return {p: max(0, q - done.get(p, 0)) for p, q in lines.items()}


def view(c: sqlite3.Connection, n) -> dict:
    d = {k: n[k] for k in ("id", "number", "sale_id", "delivery_date", "recipient", "note", "status", "cancel_reason", "version", "seq", "created_at")}
    d["lines"] = [{"sale_position": l["sale_position"], "product_id": l["product_id"], "name": l["name"], "qty": l["qty"]} for l in c.execute("SELECT * FROM delivery_lines WHERE note_id=? ORDER BY position", (n["id"],))]
    return d


def pull_extra(c: sqlite3.Connection, user: sqlite3.Row, since: int) -> dict:
    out, cache = [], {}
    for n in c.execute("SELECT * FROM delivery_notes ORDER BY seq"):
        if n["sale_id"] not in cache:
            s = c.execute("SELECT * FROM sales WHERE id=?", (n["sale_id"],)).fetchone()
            cache[n["sale_id"]] = bool(s) and sale_visible(c, user, s)
        if cache[n["sale_id"]]: out.append(view(c, n))
    return {"delivery_notes": out}


def _sync_sale(c, user, sale, now) -> None:
    """Met à jour « livrée le » de la vente d'après les bons actifs."""
    left = sum(remaining(c, sale).values())
    last = c.execute("SELECT MAX(delivery_date) d FROM delivery_notes WHERE sale_id=? AND status='active'", (sale["id"],)).fetchone()["d"]
    want = last if left == 0 and last else (sale["delivered_at"] if left == 0 else None)
    if want != sale["delivered_at"]:
        c.execute("UPDATE sales SET delivered_at=?, version=version+1, seq=?, updated_at=?, updated_by=? WHERE id=?", (want, next_seq(c), now, user["id"], sale["id"]))


def create_note(c, user, actor, sale, d: str, picks: dict[int, int] | None, recipient: str, note: str, now, nid: str | None = None) -> dict:
    rem = remaining(c, sale)
    lines = {l["position"]: l for l in c.execute("SELECT * FROM sale_lines WHERE sale_id=?", (sale["id"],))}
    picks = {p: q for p, q in (picks if picks is not None else rem).items() if q}
    if not picks: raise OpError("Rien à livrer : tout est déjà livré.")
    for p, q in picks.items():
        if p not in lines: raise OpError("Ligne de vente inconnue.")
        if q > rem[p]: raise OpError(f"Ligne « {lines[p]['name']} » : il ne reste que {rem[p]} à livrer.")
    nid = nid or str(uuid.uuid4())
    number = f"BL-{next_counter(c, 'delivery_no'):04d}"
    c.execute("INSERT INTO delivery_notes(id,number,sale_id,delivery_date,recipient,note,status,version,seq,created_at,updated_at,created_by,updated_by) VALUES(?,?,?,?,?,?,'active',1,?,?,?,?,?)",
              (nid, number, sale["id"], d, recipient, note, next_seq(c), now, now, user["id"], user["id"]))
    for i, (p, q) in enumerate(sorted(picks.items()), 1):
        c.execute("INSERT INTO delivery_lines(note_id,position,sale_position,product_id,name,qty) VALUES(?,?,?,?,?,?)", (nid, i, p, lines[p]["product_id"], lines[p]["name"], q))
    audit.record(c, actor, "delivery.create", "delivery", nid, None, {"numéro": number, "vente": sale["number"], "date": d, "reçu par": recipient, "lignes": [f"{q} × {lines[p]['name']}" for p, q in sorted(picks.items())]})
    s2 = c.execute("SELECT * FROM sales WHERE id=?", (sale["id"],)).fetchone()
    _sync_sale(c, user, s2, now)
    return {"id": nid, "number": number}


def _create(c, user, actor, data, role, now):
    from .sales_ops import _load_sale, sale_view
    if not can(role, "sales.deliver"): return _deny(c, actor, "delivery.create", None, "droit manquant")
    nid = str(data.get("id") or uuid.uuid4())
    if not UUID_RE.match(nid): raise OpError("Identifiant de bon invalide.")
    if c.execute("SELECT 1 FROM delivery_notes WHERE id=?", (nid,)).fetchone(): raise OpError("Ce bon de livraison existe déjà.")
    s = _load_sale(c, user, data.get("sale_id"))
    if s is None or s["status"] != "active": return {"status": "invalid", "message": "Vente introuvable ou annulée."}
    d = _date(data.get("delivery_date") or today().isoformat(), "Date de livraison")
    if date.fromisoformat(d) > today() + timedelta(days=1): raise OpError("La date de livraison ne peut pas être dans le futur.")
    if d < s["sale_date"]: raise OpError("La livraison ne peut pas précéder la vente.")
    raw = data.get("lines")
    picks = None
    if raw is not None:
        if not isinstance(raw, list): raise OpError("Lignes invalides.")
        picks = {}
        for r in raw:
            p = _int(r.get("sale_position") if isinstance(r, dict) else None, "Ligne de vente", 1, 1000)
            picks[p] = picks.get(p, 0) + _int(r.get("qty"), "Quantité livrée", 0, 10 ** 7)
    made = create_note(c, user, actor, s, d, picks, _text(data.get("recipient"), "Le nom de celui qui reçoit", 80), _text(data.get("note"), "La note", 200), now, nid)
    n = c.execute("SELECT * FROM delivery_notes WHERE id=?", (nid,)).fetchone()
    return {"status": "ok", "delivery": view(c, n), "sale": sale_view(c, c.execute("SELECT * FROM sales WHERE id=?", (s["id"],)).fetchone())}


def _cancel(c, user, actor, data, role, now):
    from .sales_ops import sale_view
    if not can(role, "sales.deliver"): return _deny(c, actor, "delivery.cancel", str(data.get("id") or ""), "droit manquant")
    n = c.execute("SELECT * FROM delivery_notes WHERE id=? AND status='active'", (str(data.get("id") or ""),)).fetchone()
    s = c.execute("SELECT * FROM sales WHERE id=?", (n["sale_id"],)).fetchone() if n else None
    if n is None or not sale_visible(c, user, s): return {"status": "invalid", "message": "Bon de livraison introuvable ou déjà annulé."}
    if int(data.get("base_version") or 0) != n["version"]: return _conflict(c, actor, "delivery", n["id"], view(c, n), "annulation : bon modifié entre-temps")
    reason = _text(data.get("reason"), "Le motif", 200, True)
    if len(reason) < 3: raise OpError("Le motif d'annulation est obligatoire.")
    c.execute("UPDATE delivery_notes SET status='cancelled', cancel_reason=?, version=version+1, seq=?, updated_at=?, updated_by=? WHERE id=?", (reason, next_seq(c), now, user["id"], n["id"]))
    _sync_sale(c, user, c.execute("SELECT * FROM sales WHERE id=?", (s["id"],)).fetchone(), now)
    audit.record(c, actor, "delivery.cancel", "delivery", n["id"], {"numéro": n["number"], "statut": "actif"}, {"statut": "annulé", "motif": reason}, note=s["number"])
    return {"status": "ok", "sale": sale_view(c, c.execute("SELECT * FROM sales WHERE id=?", (s["id"],)).fetchone())}


HANDLERS = {"delivery.create": _create, "delivery.cancel": _cancel}
