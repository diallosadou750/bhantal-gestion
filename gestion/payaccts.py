"""Comptes bancaires et mobile money supplémentaires (deuxième banque, autre opérateur…).

Chaque compte ajouté devient un MODE DE PAIEMENT portant son nom (ex. « Virement UBA ») : le choisir sur un paiement, une dépense, un achat… range l'opération sur ce compte,
dans son propre rapprochement bancaire et dans son propre compte comptable (521xx créé automatiquement, jamais supprimé). Un compte désactivé n'apparaît plus dans les choix
mais son historique reste. Les comptes de base (Banque, Orange Money, MTN MoMo) existent toujours.
"""
from __future__ import annotations

import sqlite3

from . import audit
from .methods import extra_labels
from .ops import OpError, _deny, next_seq
from .permissions import METHODS, can
from .sales_ops import _conflict, _text, next_counter

RESERVED = {m.lower() for m in METHODS} | {"owner", "credit", "autre", "espèces", "banque"}


def view(r) -> dict:
    return {"key": r["key"], "label": r["label"], "kind": r["kind"], "ledger": r["ledger"], "active": bool(r["active"]), "version": r["version"], "created_at": r["created_at"]}


def pull_extra(c: sqlite3.Connection, user: sqlite3.Row, since: int) -> dict:
    if not can(user["role"], "bank.read"): return {"pay_accounts": []}
    return {"pay_accounts": [view(r) for r in c.execute("SELECT * FROM pay_accounts ORDER BY created_at, key")]}


def _create(c, user, actor, data, role, now):
    if not can(role, "bank.write"): return _deny(c, actor, "payacct.create", None, "droit manquant")
    label = _text(data.get("label"), "Le nom du compte", 40, True)
    if len(label) < 3: raise OpError("Le nom du compte doit faire au moins 3 caractères (ex. « Virement UBA »).")
    if label.lower() in RESERVED or label.lower() in {x.lower() for x in extra_labels(c, False)}: raise OpError("Ce nom est déjà utilisé : choisissez-en un autre.")
    kind = data.get("kind")
    if kind not in ("bank", "mobile"): raise OpError("Banque ou mobile money ?")
    n = next_counter(c, "payacct_no")
    key, code = f"acct{n}", f"521{n + 2:02d}"
    while c.execute("SELECT 1 FROM accounts WHERE code=?", (code,)).fetchone(): code = str(int(code) + 1)
    c.execute("INSERT INTO accounts(code,name,custom,created_at) VALUES(?,?,1,?)", (code, f"{'Banque' if kind == 'bank' else 'Mobile money'} : {label}", now))
    c.execute("INSERT INTO pay_accounts(key,label,kind,ledger,active,version,seq,created_at,created_by) VALUES(?,?,?,?,1,1,?,?,?)", (key, label, kind, code, next_seq(c), now, user["id"]))
    audit.record(c, actor, "payacct.create", "pay_account", key, None, {"nom": label, "type": "banque" if kind == "bank" else "mobile money", "compte comptable": code})
    return {"status": "ok", "pay_account": view(c.execute("SELECT * FROM pay_accounts WHERE key=?", (key,)).fetchone())}


def _set_active(c, user, actor, data, role, now):
    if not can(role, "bank.write"): return _deny(c, actor, "payacct.set_active", str(data.get("key") or ""), "droit manquant")
    r = c.execute("SELECT * FROM pay_accounts WHERE key=?", (str(data.get("key") or ""),)).fetchone()
    if r is None: return {"status": "invalid", "message": "Compte introuvable."}
    if int(data.get("base_version") or 0) != r["version"]: return _conflict(c, actor, "pay_account", r["key"], view(r), "compte modifié entre-temps")
    active = 1 if data.get("active") else 0
    if active == r["active"]: return {"status": "ok", "pay_account": view(r), "unchanged": True}
    c.execute("UPDATE pay_accounts SET active=?, version=version+1, seq=? WHERE key=?", (active, next_seq(c), r["key"]))
    audit.record(c, actor, "payacct.set_active", "pay_account", r["key"], {"actif": bool(r["active"])}, {"actif": bool(active)}, note=r["label"])
    return {"status": "ok", "pay_account": view(c.execute("SELECT * FROM pay_accounts WHERE key=?", (r["key"],)).fetchone())}


HANDLERS = {"payacct.create": _create, "payacct.set_active": _set_active}
