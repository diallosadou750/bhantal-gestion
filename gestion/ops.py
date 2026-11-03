"""Opérations métier (clients) : un seul chemin de code pour l'API directe et pour la synchronisation hors connexion.

Chaque opération porte un identifiant unique (op_id) : la rejouer ne la fait pas deux fois.
Les modifications utilisent un numéro de version : si quelqu'un d'autre a modifié la fiche entre-temps, il y a conflit
(rien n'est écrasé en silence) et la tentative est journalisée.
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import re
import sqlite3
import uuid

from . import audit, security
from .audit import Actor
from .permissions import HIDDEN_CLIENT_FIELDS, can

CLIENT_FIELDS = ("name", "kind", "phone", "zone", "credit_limit", "note", "owner_user_id")
UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")


_IMPORTING: contextvars.ContextVar[bool] = contextvars.ContextVar("gestion_importing", default=False)


def importing() -> bool:
    """Vrai seulement pendant un import d'historique lancé en ligne de commande par le propriétaire (jamais depuis l'API)."""
    return _IMPORTING.get()


@contextlib.contextmanager
def import_mode():
    token = _IMPORTING.set(True)
    try:
        yield
    finally:
        _IMPORTING.reset(token)


class OpError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def next_seq(c: sqlite3.Connection) -> int:
    c.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='seq'")
    return int(c.execute("SELECT value FROM meta WHERE key='seq'").fetchone()["value"])


def client_view(row: sqlite3.Row, role: str) -> dict:
    hidden = HIDDEN_CLIENT_FIELDS.get(role, set())
    d = {k: row[k] for k in ("id", "code", "name", "kind", "phone", "zone", "owner_user_id", "credit_limit", "note", "version", "seq", "created_at", "updated_at")}
    d["deleted"] = bool(row["deleted"])
    for k in hidden:
        d.pop(k, None)
    return d


def _clean(data: dict, partial: bool) -> dict:
    out: dict = {}
    for k in CLIENT_FIELDS:
        if k not in data:
            continue
        v = data[k]
        if k == "name":
            v = (v or "").strip()
            if not (1 <= len(v) <= 120): raise OpError("Le nom du client est obligatoire (120 caractères maximum).")
        elif k in ("kind", "zone"):
            v = (v or "").strip()
            if len(v) > 60: raise OpError("Texte trop long (60 caractères maximum).")
        elif k == "phone":
            v = re.sub(r"[^\d+]", "", v or "")
            if len(v) > 20: raise OpError("Numéro de téléphone trop long.")
        elif k == "credit_limit":
            try: v = int(v or 0)
            except (TypeError, ValueError): raise OpError("La limite de crédit doit être un nombre entier.")
            if not (0 <= v <= 10 ** 12): raise OpError("Limite de crédit invalide.")
        elif k == "note":
            v = (v or "").strip()
            if len(v) > 500: raise OpError("Note trop longue (500 caractères maximum).")
        elif k == "owner_user_id":
            if v in (None, ""): v = None
            else:
                try: v = int(v)
                except (TypeError, ValueError): raise OpError("Commercial invalide.")
        out[k] = v
    if not partial and "name" not in out:
        raise OpError("Le nom du client est obligatoire.")
    return out


def _next_code(c: sqlite3.Connection) -> str:
    n = c.execute("SELECT COALESCE(MAX(CAST(SUBSTR(code,2) AS INTEGER)),0) n FROM clients").fetchone()["n"]
    return f"C{n + 1:03d}"


def _full(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in ("code", "name", "kind", "phone", "zone", "owner_user_id", "credit_limit", "note", "deleted")}


def visible_to(role: str, user_id: int, row: sqlite3.Row) -> bool:
    if can(role, "clients.read_all"):
        return True
    return can(role, "clients.read_own") and row["owner_user_id"] == user_id


def apply_op(c: sqlite3.Connection, user: sqlite3.Row, actor: Actor, op: dict) -> dict:
    """À appeler dans une transaction. Renvoie toujours un dict {status: ok|conflict|denied|invalid, ...}."""
    op_id = str(op.get("op_id") or "")
    if not (8 <= len(op_id) <= 64):
        return {"status": "invalid", "message": "Identifiant d'opération manquant."}
    done = c.execute("SELECT result_json FROM processed_ops WHERE op_id=?", (op_id,)).fetchone()
    if done:
        return {**json.loads(done["result_json"]), "replayed": True}
    try:
        res = _dispatch(c, user, actor, op)
    except OpError as e:
        return {"status": "invalid", "message": e.message}          # non mémorisé : l'utilisateur peut corriger et renvoyer
    res["op_id"] = op_id
    c.execute("INSERT INTO processed_ops(op_id,user_id,ts,result_json) VALUES(?,?,?,?)", (op_id, user["id"], security.iso(), json.dumps(res, ensure_ascii=False)))
    return res


def _deny(c, actor, action, entity_id, why) -> dict:
    audit.record(c, actor, action + ".denied", "client", entity_id, note=why)
    return {"status": "denied", "message": "Vous n'avez pas le droit de faire cette action."}


def bump_epoch(c: sqlite3.Connection) -> None:
    """Change les droits de visibilité : les appareils refont une synchronisation complète."""
    c.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='epoch'")


def _dispatch(c, user, actor, op) -> dict:
    t, role = op.get("type"), user["role"]
    data = op.get("data") or {}
    now = security.iso()
    if isinstance(t, str) and t.split(".")[0] in ("product", "sale", "payment", "creditnote", "settings", "supplier", "purchase", "supplierpay", "stock", "expense", "cash", "budget", "bank", "journal", "account", "accounting", "fiscal", "asset", "provision", "deferral", "quote", "porder", "delivery", "payacct", "transfer", "recurring", "snote", "depot", "stransfer", "expcat"):
        from . import sales_ops
        return sales_ops.dispatch(c, user, actor, op)
    if t == "client.create":
        if not (can(role, "clients.create") or can(role, "clients.write")):
            return _deny(c, actor, "client.create", None, "rôle sans droit de création")
        f = _clean(data, partial=False)
        if not can(role, "clients.write"):
            f["owner_user_id"] = user["id"]                      # un commercial ne crée que pour lui-même
        cid = str(data.get("id") or uuid.uuid4())
        if not UUID_RE.match(cid): raise OpError("Identifiant de client invalide.")
        if c.execute("SELECT 1 FROM clients WHERE id=?", (cid,)).fetchone(): raise OpError("Ce client existe déjà.")
        if f.get("owner_user_id") is not None and not c.execute("SELECT 1 FROM users WHERE id=? AND active=1", (f["owner_user_id"],)).fetchone():
            raise OpError("Commercial inconnu ou désactivé.")
        seq = next_seq(c)
        row = {"credit_limit": 0, "kind": "", "phone": "", "zone": "", "note": "", "owner_user_id": None, **f}
        code = _next_code(c)
        if importing() and re.fullmatch(r"C\d{3,6}", str(data.get("code") or "")):          # import : on garde les codes clients d'origine
            if c.execute("SELECT 1 FROM clients WHERE code=?", (data["code"],)).fetchone(): raise OpError(f"Le code client {data['code']} existe déjà.")
            code = data["code"]
        c.execute("""INSERT INTO clients(id,code,name,kind,phone,zone,owner_user_id,credit_limit,note,version,seq,deleted,created_at,updated_at,created_by,updated_by)
                     VALUES(?,?,?,?,?,?,?,?,?,1,?,0,?,?,?,?)""",
                  (cid, code, row["name"], row["kind"], row["phone"], row["zone"], row["owner_user_id"], row["credit_limit"], row["note"], seq, now, now, user["id"], user["id"]))
        r = c.execute("SELECT * FROM clients WHERE id=?", (cid,)).fetchone()
        audit.record(c, actor, "client.create", "client", cid, None, _full(r))
        return {"status": "ok", "client": client_view(r, role)}

    cid = str(data.get("id") or "")
    r = c.execute("SELECT * FROM clients WHERE id=?", (cid,)).fetchone()
    if r is None or r["deleted"] and t != "client.delete":
        return {"status": "invalid", "message": "Client introuvable."}

    if t == "client.update":
        own = can(role, "clients.write_own") and r["owner_user_id"] == user["id"]
        if not (can(role, "clients.write") or own):
            return _deny(c, actor, "client.update", cid, "fiche non modifiable par ce rôle")
        f = _clean(data.get("changes") or {}, partial=True)
        if not can(role, "clients.write"): f.pop("owner_user_id", None)
        changed = {k: v for k, v in f.items() if r[k] != v}
        if int(data.get("base_version") or 0) != r["version"]:
            audit.record(c, actor, "client.conflict", "client", cid, {k: r[k] for k in changed}, changed, note=f"version attendue {data.get('base_version')}, version actuelle {r['version']}")
            return {"status": "conflict", "message": "Cette fiche a été modifiée par quelqu'un d'autre.", "server": client_view(r, role)}
        if not changed:
            return {"status": "ok", "client": client_view(r, role), "unchanged": True}
        if "owner_user_id" in changed and changed["owner_user_id"] is not None and not c.execute("SELECT 1 FROM users WHERE id=? AND active=1", (changed["owner_user_id"],)).fetchone():
            raise OpError("Commercial inconnu ou désactivé.")
        sets = ", ".join(f"{k}=?" for k in changed)
        c.execute(f"UPDATE clients SET {sets}, version=version+1, seq=?, updated_at=?, updated_by=? WHERE id=?", (*changed.values(), next_seq(c), now, user["id"], cid))
        r2 = c.execute("SELECT * FROM clients WHERE id=?", (cid,)).fetchone()
        if "owner_user_id" in changed: bump_epoch(c)
        audit.record(c, actor, "client.update", "client", cid, {k: r[k] for k in changed}, changed)
        return {"status": "ok", "client": client_view(r2, role)}

    if t == "client.delete":
        if not can(role, "clients.delete"):
            return _deny(c, actor, "client.delete", cid, "rôle sans droit de suppression")
        if r["deleted"]:
            return {"status": "ok", "unchanged": True}
        if int(data.get("base_version") or 0) != r["version"]:
            audit.record(c, actor, "client.conflict", "client", cid, note="suppression refusée : fiche modifiée entre-temps")
            return {"status": "conflict", "message": "Cette fiche a été modifiée par quelqu'un d'autre.", "server": client_view(r, role)}
        c.execute("UPDATE clients SET deleted=1, version=version+1, seq=?, updated_at=?, updated_by=? WHERE id=?", (next_seq(c), now, user["id"], cid))
        audit.record(c, actor, "client.delete", "client", cid, _full(r), {"deleted": True}, note="suppression logique : la fiche reste conservée")
        return {"status": "ok"}

    raise OpError("Type d'opération inconnu.")


def list_clients(c: sqlite3.Connection, user: sqlite3.Row, since: int = 0) -> tuple[list[dict], int]:
    """Fiches visibles par l'utilisateur modifiées après le curseur `since` (suppressions comprises), et le nouveau curseur."""
    role = user["role"]
    rows = c.execute("SELECT * FROM clients WHERE seq>? ORDER BY seq", (since,)).fetchall()
    out = [client_view(r, role) for r in rows if visible_to(role, user["id"], r)]
    head = int(c.execute("SELECT value FROM meta WHERE key='seq'").fetchone()["value"])
    return out, head
