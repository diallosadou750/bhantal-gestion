"""Virements internes entre deux comptes bancaires ou mobile money de l'entreprise (ex. banque → Orange Money, deuxième banque → première).

Un virement interne n'est ni une dépense ni une vente : c'est de l'argent qui change de compte. Il apparaît en sortie sur le compte d'origine et en entrée sur le compte de
destination (chacun à pointer avec son relevé), et passe en comptabilité de l'un à l'autre. Pour les espèces, voir les mouvements de caisse (dépôt et retrait).
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import date, timedelta

from . import audit, period
from .ops import UUID_RE, OpError, _deny, next_seq
from .permissions import can
from .sales_ops import _conflict, _date, _int, _text, next_counter, today


def view(r) -> dict:
    return {k: r[k] for k in ("id", "number", "from_account", "to_account", "amount", "transfer_date", "reference", "note", "status", "cancel_reason", "version", "seq", "created_at")}


def pull_extra(c: sqlite3.Connection, user: sqlite3.Row, since: int) -> dict:
    if not can(user["role"], "bank.read"): return {"transfers": []}
    return {"transfers": [view(r) for r in c.execute("SELECT * FROM transfers ORDER BY seq")]}


def ledger_of(mp: dict, key: str) -> str:
    return {"bank": mp["bank"], "orange": mp["orange"], "mtn": mp["mtn"]}.get(key) or mp["_extra_key"][key]


def entries(c: sqlite3.Connection, mp: dict) -> list[dict]:
    out = []
    for r in c.execute("SELECT * FROM transfers WHERE status='active' ORDER BY transfer_date, number"):
        try: a, b = ledger_of(mp, r["from_account"]), ledger_of(mp, r["to_account"])
        except KeyError: continue
        out.append({"key": f"transfer:{r['id']}", "date": r["transfer_date"], "journal": "BQ", "ref": r["number"], "label": f"Virement interne {r['number']}{' : ' + r['note'] if r['note'] else ''}",
                    "lines": [(b, r["amount"], 0), (a, 0, r["amount"])], "auto": True})
    return out


def book_items(c: sqlite3.Connection, account: str) -> list[dict]:
    out = []
    for r in c.execute("SELECT * FROM transfers WHERE status='active' AND (from_account=? OR to_account=?)", (account, account)):
        sign = -1 if r["from_account"] == account else 1
        out.append({"type": "transfer_out" if sign < 0 else "transfer_in", "id": r["id"], "date": r["transfer_date"], "amount": sign * r["amount"], "ref": r["reference"] or "",
                    "label": f"Virement interne {r['number']} {'vers' if sign < 0 else 'depuis'} {_label(c, r['to_account'] if sign < 0 else r['from_account'])}"})
    return out


def _label(c, key):
    from . import bank_ops
    return bank_ops.accounts(c).get(key, {"label": key})["label"]


def _create(c, user, actor, data, role, now):
    from . import bank_ops
    if not can(role, "bank.write"): return _deny(c, actor, "transfer.create", None, "droit manquant")
    tid = str(data.get("id") or uuid.uuid4())
    if not UUID_RE.match(tid): raise OpError("Identifiant de virement invalide.")
    if c.execute("SELECT 1 FROM transfers WHERE id=?", (tid,)).fetchone(): raise OpError("Ce virement existe déjà.")
    acs = bank_ops.accounts(c); a, b = str(data.get("from_account") or ""), str(data.get("to_account") or "")
    for k in (a, b):
        if k not in acs or not acs[k]["active"]: raise OpError("Choisissez deux comptes actifs.")
    if a == b: raise OpError("Les deux comptes doivent être différents.")
    amount = _int(data.get("amount"), "Le montant", 1, 10 ** 13)
    d = _date(data.get("transfer_date") or today().isoformat(), "La date")
    if date.fromisoformat(d) > today() + timedelta(days=1): raise OpError("La date ne peut pas être dans le futur.")
    period.ensure_open(c, d)
    for k in (a, b):                                                     # chaque compte doit avoir son solde d'ouverture, sinon le rapprochement serait faux
        op = c.execute("SELECT opening_date FROM bank_accounts WHERE account=?", (k,)).fetchone()
        if op and d <= op["opening_date"]: raise OpError(f"La date précède l'ouverture du compte « {acs[k]['label']} » ({op['opening_date']}).")
    number = f"VI-{next_counter(c, 'transfer_no'):04d}"
    c.execute("INSERT INTO transfers(id,number,from_account,to_account,amount,transfer_date,reference,note,status,version,seq,created_at,updated_at,created_by,updated_by) VALUES(?,?,?,?,?,?,?,?,'active',1,?,?,?,?,?)",
              (tid, number, a, b, amount, d, _text(data.get("reference"), "La référence", 60), _text(data.get("note"), "La note", 200), next_seq(c), now, now, user["id"], user["id"]))
    audit.record(c, actor, "transfer.create", "transfer", tid, None, {"numéro": number, "de": acs[a]["label"], "vers": acs[b]["label"], "montant": amount, "date": d})
    return {"status": "ok", "transfer": view(c.execute("SELECT * FROM transfers WHERE id=?", (tid,)).fetchone())}


def _cancel(c, user, actor, data, role, now):
    if not can(role, "bank.write"): return _deny(c, actor, "transfer.cancel", str(data.get("id") or ""), "droit manquant")
    r = c.execute("SELECT * FROM transfers WHERE id=? AND status='active'", (str(data.get("id") or ""),)).fetchone()
    if r is None: return {"status": "invalid", "message": "Virement introuvable ou déjà annulé."}
    if int(data.get("base_version") or 0) != r["version"]: return _conflict(c, actor, "transfer", r["id"], view(r), "annulation : virement modifié entre-temps")
    period.ensure_open(c, r["transfer_date"])
    matched = c.execute("SELECT 1 FROM bank_matches WHERE item_id=? AND item_type IN ('transfer_out','transfer_in') LIMIT 1", (r["id"],)).fetchone()
    if matched: raise OpError("Ce virement est pointé avec un relevé : dépointez d'abord les lignes concernées.")
    reason = _text(data.get("reason"), "Le motif", 200, True)
    if len(reason) < 3: raise OpError("Le motif d'annulation est obligatoire.")
    c.execute("UPDATE transfers SET status='cancelled', cancel_reason=?, version=version+1, seq=?, updated_at=?, updated_by=? WHERE id=?", (reason, next_seq(c), now, user["id"], r["id"]))
    audit.record(c, actor, "transfer.cancel", "transfer", r["id"], {"numéro": r["number"], "montant": r["amount"]}, {"statut": "annulé", "motif": reason})
    return {"status": "ok"}


HANDLERS = {"transfer.create": _create, "transfer.cancel": _cancel}
