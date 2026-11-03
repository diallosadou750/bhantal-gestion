"""Charges et produits constatés d'avance : étaler sur les mois concernés une dépense payée (ou une vente facturée) d'avance.

Exemple : une assurance de 1 200 000 payée en octobre pour douze mois. Elle ne doit peser sur le résultat d'octobre que pour sa part d'octobre : le reste est une
CHARGE CONSTATÉE D'AVANCE (compte 476, un actif : un service dont on a déjà payé la suite). Symétriquement, un abonnement facturé d'avance est un PRODUIT CONSTATÉ
D'AVANCE (compte 477, une dette : un service qu'on doit encore rendre).

Méthode (volontairement simple) : à la date de la dépense, on sort la totalité de la charge vers 476 ; chaque fin de mois écoulée, la part du mois (au prorata des jours
de la période couverte, arrondis exacts) retourne en charge. Les écritures sont déduites du registre, comme le reste de la comptabilité. La période couverte doit commencer
au plus tôt le 1er du mois de la dépense et finir après ce mois. Le prorata en jours est un choix de présentation à faire valider.
"""
from __future__ import annotations

import calendar
import re
import sqlite3
import uuid
from datetime import date, timedelta

from . import audit, period
from .cash_ops import categories as _expense_categories
from .ops import UUID_RE, OpError, _deny, next_seq
from .permissions import can
from .sales_ops import _conflict, _date, _int, _text, next_counter, today

KINDS = {"charge": {"label": "Charge constatée d'avance", "prefix": "CCA", "prefix_re": "6"}, "product": {"label": "Produit constaté d'avance", "prefix": "PCA", "prefix_re": "7"}}
MAX_DAYS = 366 * 5


def _month_last(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def shares(row, upto: date) -> list[tuple[str, int]]:
    """Parts rendues au résultat à chaque fin de mois (ou à la date de fin), jusqu'à upto : (date, montant)."""
    start, end = date.fromisoformat(row["start_date"]), date.fromisoformat(row["end_date"])
    total_days = (end - start).days + 1
    out, prev, cur = [], 0, date(start.year, start.month, 1)
    while cur <= end:
        stop = min(_month_last(cur), end)
        if stop > upto: break
        cum = round(row["amount"] * ((stop - start).days + 1) / total_days)
        if cum - prev: out.append((stop.isoformat(), cum - prev))
        prev = cum
        cur = _month_last(cur) + timedelta(days=1)
    return out


def alive(c: sqlite3.Connection, r) -> bool:
    if r["status"] != "active": return False
    if r["expense_id"]:
        e = c.execute("SELECT status FROM expenses WHERE id=?", (r["expense_id"],)).fetchone()
        return bool(e) and e["status"] == "active"
    return True


def view(c: sqlite3.Connection, r) -> dict:
    d = {k: r[k] for k in ("id", "number", "kind", "label", "account", "amount", "book_date", "start_date", "end_date", "expense_id", "status", "cancel_reason", "note", "version", "seq", "created_at")}
    done = sum(a for _, a in shares(r, today()))
    d["kind_label"] = KINDS[r["kind"]]["label"]; d["recognized"] = done; d["remaining"] = r["amount"] - done
    d["orphan"] = r["status"] == "active" and not alive(c, r)                      # dépense liée annulée : la régularisation ne produit plus d'écriture
    d["counter_account"] = "476" if r["kind"] == "charge" else "477"
    e = c.execute("SELECT number FROM expenses WHERE id=?", (r["expense_id"],)).fetchone() if r["expense_id"] else None
    d["expense_number"] = e["number"] if e else None
    return d


def entries(c: sqlite3.Connection, mp: dict) -> list[dict]:
    out = []
    for r in c.execute("SELECT * FROM deferrals WHERE status='active' ORDER BY book_date, number"):
        if not alive(c, r): continue
        ch = r["kind"] == "charge"; acct, ctr = r["account"], mp["deferred_charges"] if ch else mp["deferred_products"]
        lab = f"{r['number']} : {r['label']}"
        out.append({"key": f"defer:{r['id']}", "date": r["book_date"], "journal": "OD", "ref": r["number"], "label": f"{'Charge' if ch else 'Produit'} constaté d'avance {lab}",
                    "lines": [(ctr, r["amount"], 0), (acct, 0, r["amount"])] if ch else [(acct, r["amount"], 0), (ctr, 0, r["amount"])], "auto": True})
        for dt, amt in shares(r, today()):
            out.append({"key": f"deferrel:{r['id']}:{dt}", "date": dt, "journal": "OD", "ref": f"ETAL-{dt[:7]}", "label": f"Part du mois, {lab}",
                        "lines": [(acct, amt, 0), (ctr, 0, amt)] if ch else [(ctr, amt, 0), (acct, 0, amt)], "auto": True})
    return out


def _create(c, user, actor, data, role, now):
    if not can(role, "accounting.write"): return _deny(c, actor, "deferral.create", None, "droit manquant")
    rid = str(data.get("id") or uuid.uuid4())
    if not UUID_RE.match(rid): raise OpError("Identifiant invalide.")
    if c.execute("SELECT 1 FROM deferrals WHERE id=?", (rid,)).fetchone(): raise OpError("Cette régularisation existe déjà.")
    kind = data.get("kind")
    if kind not in KINDS: raise OpError("Charge ou produit constaté d'avance ?")
    label = _text(data.get("label"), "Le libellé", 160, True)
    if len(label) < 3: raise OpError("Le libellé est obligatoire.")
    expense_id, account = None, str(data.get("account") or "")
    if kind == "charge" and data.get("expense_id"):
        e = c.execute("SELECT * FROM expenses WHERE id=? AND status='active'", (str(data["expense_id"]),)).fetchone()
        if e is None: raise OpError("Dépense introuvable ou annulée.")
        if c.execute("SELECT 1 FROM deferrals WHERE expense_id=? AND status='active'", (e["id"],)).fetchone(): raise OpError("Cette dépense est déjà étalée.")
        expense_id, book, account = e["id"], e["expense_date"], _expense_categories(c).get(e["category"], {"account": "658"})["account"]
        amount = _int(data.get("amount") or e["amount"], "Le montant à étaler", 1, e["amount"])
    else:
        book = _date(data.get("book_date") or today().isoformat(), "La date de comptabilisation")
        amount = _int(data.get("amount"), "Le montant à étaler", 1, 10 ** 13)
        if not re.fullmatch(KINDS[kind]["prefix_re"] + r"\d{2,7}", account): raise OpError("Compte de charge (classe 6)" if kind == "charge" else "Compte de produit (classe 7)")
        if date.fromisoformat(book) > today(): raise OpError("La date ne peut pas être dans le futur.")
        if kind == "charge" and account[:2] in ("69",): raise OpError("Une dotation aux provisions ne s'étale pas ici.")
    if not c.execute("SELECT 1 FROM accounts WHERE code=?", (account,)).fetchone(): raise OpError(f"Le compte {account} n'existe pas dans le plan comptable.")
    start, end = _date(data.get("start_date"), "Le début de la période couverte"), _date(data.get("end_date"), "La fin de la période couverte")
    sd, ed, bd = date.fromisoformat(start), date.fromisoformat(end), date.fromisoformat(book)
    if ed < sd: raise OpError("La fin de la période précède son début.")
    if (ed - sd).days + 1 > MAX_DAYS: raise OpError("Période trop longue (5 ans au plus).")
    if sd < bd.replace(day=1): raise OpError("La période couverte commence au plus tôt le 1er du mois de la dépense.")
    if ed <= _month_last(bd): raise OpError("Rien à étaler : la période se termine dans le mois de la dépense.")
    period.ensure_open(c, book)
    number = f"{KINDS[kind]['prefix']}-{next_counter(c, 'deferral_no'):04d}"
    c.execute("INSERT INTO deferrals(id,number,kind,label,account,amount,book_date,start_date,end_date,expense_id,status,note,version,seq,created_at,updated_at,created_by,updated_by) VALUES(?,?,?,?,?,?,?,?,?,?,'active',?,1,?,?,?,?,?)",
              (rid, number, kind, label, account, amount, book, start, end, expense_id, _text(data.get("note"), "La note", 200), next_seq(c), now, now, user["id"], user["id"]))
    audit.record(c, actor, "deferral.create", "deferral", rid, None, {"numéro": number, "nature": KINDS[kind]["label"], "libellé": label, "montant": amount, "période": f"{start} au {end}", "compte": account, "dépense": expense_id})
    return {"status": "ok", "deferral": view(c, c.execute("SELECT * FROM deferrals WHERE id=?", (rid,)).fetchone())}


def _cancel(c, user, actor, data, role, now):
    if not can(role, "accounting.write"): return _deny(c, actor, "deferral.cancel", str(data.get("id") or ""), "droit manquant")
    r = c.execute("SELECT * FROM deferrals WHERE id=? AND status='active'", (str(data.get("id") or ""),)).fetchone()
    if r is None: return {"status": "invalid", "message": "Régularisation introuvable ou déjà annulée."}
    if int(data.get("base_version") or 0) != r["version"]: return _conflict(c, actor, "deferral", r["id"], view(c, r), "annulation : régularisation modifiée entre-temps")
    period.ensure_open(c, r["book_date"])
    reason = _text(data.get("reason"), "Le motif", 200, True)
    if len(reason) < 3: raise OpError("Le motif d'annulation est obligatoire.")
    c.execute("UPDATE deferrals SET status='cancelled', cancel_reason=?, version=version+1, seq=?, updated_at=?, updated_by=? WHERE id=?", (reason, next_seq(c), now, user["id"], r["id"]))
    audit.record(c, actor, "deferral.cancel", "deferral", r["id"], {"numéro": r["number"], "montant": r["amount"]}, {"statut": "annulée", "motif": reason})
    return {"status": "ok"}


HANDLERS = {"deferral.create": _create, "deferral.cancel": _cancel}
