"""Dépenses récurrentes (loyer, abonnement internet, salaire fixe…) : un modèle qui prépare la dépense du mois, sur demande.

Rien n'est créé tout seul en cachette : l'écran montre les échéances « à générer » et c'est une personne autorisée qui clique. Chaque échéance crée une VRAIE dépense
(numérotée DP-xxxx, avec ses écritures et son journal d'audit), datée du jour d'échéance du mois. Un mois déjà généré ne l'est jamais deux fois, même si la dépense
a été annulée ensuite. Modifier un modèle (nouveau loyer) ne change que les échéances futures.
"""
from __future__ import annotations

import calendar
import sqlite3
import uuid
from datetime import date, timedelta

from . import audit
from .cash_ops import categories as _expense_categories
from .methods import valid_method
from .ops import UUID_RE, OpError, _deny, next_seq
from .permissions import can
from .sales_ops import _conflict, _date, _int, _text, next_counter, today

MAX_BACKLOG = 24


def _due(y: int, m: int, day: int) -> date:
    return date(y, m, min(day, calendar.monthrange(y, m)[1]))


def pending(r, upto: date | None = None) -> list[str]:
    """Échéances (dates) pas encore générées, jusqu'à aujourd'hui."""
    upto = upto or today()
    if not r["active"]: return []
    start = date.fromisoformat(r["start_date"]); end = date.fromisoformat(r["end_date"]) if r["end_date"] else None
    out, y, m = [], start.year, start.month
    while (y, m) <= (upto.year, upto.month):
        d = _due(y, m, r["day"])
        ym = f"{y}-{m:02d}"
        if d >= start and d <= upto and (end is None or d <= end) and (not r["last_generated"] or ym > r["last_generated"]): out.append(d.isoformat())
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def view(r) -> dict:
    d = {k: r[k] for k in ("id", "number", "label", "category", "amount", "method", "payee", "day", "start_date", "end_date", "active", "last_generated", "note", "version", "seq", "created_at")}
    d["active"] = bool(r["active"]); d["pending"] = pending(r)
    return d


def pull_extra(c: sqlite3.Connection, user: sqlite3.Row, since: int) -> dict:
    if not can(user["role"], "recurring.manage"): return {"recurring": []}
    return {"recurring": [view(r) for r in c.execute("SELECT * FROM recurring_expenses ORDER BY number")]}


def _create(c, user, actor, data, role, now):
    if not can(role, "recurring.manage"): return _deny(c, actor, "recurring.create", None, "droit manquant")
    rid = str(data.get("id") or uuid.uuid4())
    if not UUID_RE.match(rid): raise OpError("Identifiant invalide.")
    if c.execute("SELECT 1 FROM recurring_expenses WHERE id=?", (rid,)).fetchone(): raise OpError("Ce modèle existe déjà.")
    label = _text(data.get("label"), "Le libellé", 120, True)
    if len(label) < 3: raise OpError("Le libellé est obligatoire.")
    cats = _expense_categories(c)
    if data.get("category") not in cats or not cats[data["category"]]["active"]: raise OpError("Catégorie de dépense inconnue ou désactivée.")
    amount = _int(data.get("amount"), "Le montant", 1, 10 ** 12)
    method = _text(data.get("method"), "Le mode de paiement", 30, True)
    if not valid_method(c, method): raise OpError("Mode de paiement inconnu.")
    payee = _text(data.get("payee"), "Le bénéficiaire", 120)
    day = _int(data.get("day"), "Le jour du mois", 1, 31)
    start = _date(data.get("start_date") or today().isoformat(), "Le début")
    end = _date(data.get("end_date"), "La fin", required=False)
    if end and end < start: raise OpError("La fin précède le début.")
    number = f"RC-{next_counter(c, 'recurring_no'):04d}"
    c.execute("INSERT INTO recurring_expenses(id,number,label,category,amount,method,payee,day,start_date,end_date,active,last_generated,note,version,seq,created_at,updated_at,created_by,updated_by) VALUES(?,?,?,?,?,?,?,?,?,?,1,NULL,?,1,?,?,?,?,?)",
              (rid, number, label, data["category"], amount, method, payee, day, start, end, _text(data.get("note"), "La note", 200), next_seq(c), now, now, user["id"], user["id"]))
    audit.record(c, actor, "recurring.create", "recurring", rid, None, {"numéro": number, "libellé": label, "montant": amount, "jour": day, "début": start, "fin": end})
    return {"status": "ok", "recurring": view(c.execute("SELECT * FROM recurring_expenses WHERE id=?", (rid,)).fetchone())}


def _update(c, user, actor, data, role, now):
    if not can(role, "recurring.manage"): return _deny(c, actor, "recurring.update", str(data.get("id") or ""), "droit manquant")
    r = c.execute("SELECT * FROM recurring_expenses WHERE id=?", (str(data.get("id") or ""),)).fetchone()
    if r is None: return {"status": "invalid", "message": "Modèle introuvable."}
    if int(data.get("base_version") or 0) != r["version"]: return _conflict(c, actor, "recurring", r["id"], view(r), "modèle modifié entre-temps")
    ch = data.get("changes") or {}; new = {}
    if "label" in ch: new["label"] = _text(ch["label"], "Le libellé", 120, True)
    if "amount" in ch: new["amount"] = _int(ch["amount"], "Le montant", 1, 10 ** 12)
    if "payee" in ch: new["payee"] = _text(ch["payee"], "Le bénéficiaire", 120)
    if "day" in ch: new["day"] = _int(ch["day"], "Le jour du mois", 1, 31)
    if "method" in ch:
        if not valid_method(c, ch["method"]): raise OpError("Mode de paiement inconnu.")
        new["method"] = ch["method"]
    if "end_date" in ch:
        new["end_date"] = _date(ch["end_date"] or None, "La fin", required=False)
        if new["end_date"] and new["end_date"] < r["start_date"]: raise OpError("La fin précède le début.")
    if "active" in ch: new["active"] = 1 if ch["active"] else 0
    new = {k: v for k, v in new.items() if r[k] != v}
    if not new: return {"status": "ok", "recurring": view(r), "unchanged": True}
    c.execute(f"UPDATE recurring_expenses SET {', '.join(k + '=?' for k in new)}, version=version+1, seq=?, updated_at=?, updated_by=? WHERE id=?", (*new.values(), next_seq(c), now, user["id"], r["id"]))
    audit.record(c, actor, "recurring.update", "recurring", r["id"], {k: r[k] for k in new}, new, note=r["number"])
    return {"status": "ok", "recurring": view(c.execute("SELECT * FROM recurring_expenses WHERE id=?", (r["id"],)).fetchone())}


def _generate(c, user, actor, data, role, now):
    from .cash_ops import _expense_create
    if not can(role, "recurring.manage"): return _deny(c, actor, "recurring.generate", None, "droit manquant")
    only = str(data.get("id") or "")
    rows = [r for r in c.execute("SELECT * FROM recurring_expenses WHERE active=1 ORDER BY number") if not only or r["id"] == only]
    created, blocked = [], []
    for r in rows:
        for d in pending(r)[:MAX_BACKLOG]:
            res = None
            try:
                res = _expense_create(c, user, actor, {"category": r["category"], "amount": r["amount"], "method": r["method"], "payee": r["payee"], "expense_date": d,
                                                       "description": f"{r['label']} ({r['number']}, échéance du {d})"}, role, now)
            except OpError as e:
                blocked.append({"number": r["number"], "date": d, "message": e.message}); break
            if res.get("status") != "ok": blocked.append({"number": r["number"], "date": d, "message": res.get("message", "refusé")}); break
            c.execute("UPDATE recurring_expenses SET last_generated=?, version=version+1, seq=?, updated_at=?, updated_by=? WHERE id=?", (d[:7], next_seq(c), now, user["id"], r["id"]))
            created.append({"number": r["number"], "date": d, "expense": res["expense"]["number"], "amount": r["amount"]})
    if created or blocked:
        audit.record(c, actor, "recurring.generate", "recurring", only or None, None, {"dépenses créées": len(created), "bloquées": len(blocked)}, note="; ".join(f"{x['expense']} ({x['number']} {x['date']})" for x in created[:20]) or None)
    return {"status": "ok", "created": created, "blocked": blocked}


HANDLERS = {"recurring.create": _create, "recurring.update": _update, "recurring.generate": _generate}
