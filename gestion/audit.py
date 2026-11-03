"""Journal d'audit : qui, quand, quoi, avant, après, depuis quel appareil. Chaîne de hachage contre les altérations."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass

from . import security

GENESIS = "0" * 64


@dataclass
class Actor:
    user_id: int | None
    username: str | None
    role: str | None
    device: str = ""
    ip: str = ""


def _canon(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(prev: str, fields: dict) -> str:
    return hashlib.sha256((prev + _canon(fields)).encode("utf-8")).hexdigest()


def _fields(ts, actor_user_id, username, role, action, entity, entity_id, before, after, device, ip, note) -> dict:
    return {"ts": ts, "user_id": actor_user_id, "username": username, "role": role, "action": action, "entity": entity,
            "entity_id": entity_id, "before": before, "after": after, "device": device, "ip": ip, "note": note}


def record(conn: sqlite3.Connection, actor: Actor, action: str, entity: str | None = None, entity_id: str | None = None,
           before=None, after=None, note: str | None = None) -> int:
    """À appeler DANS la transaction du changement : les deux réussissent ou échouent ensemble."""
    row = conn.execute("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    prev = row["hash"] if row else GENESIS
    ts = security.iso()
    f = _fields(ts, actor.user_id, actor.username, actor.role, action, entity, entity_id, before, after, actor.device, actor.ip, note)
    h = _digest(prev, f)
    cur = conn.execute(
        "INSERT INTO audit_log(ts,user_id,username,role,action,entity,entity_id,before_json,after_json,device,ip,note,prev_hash,hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (ts, actor.user_id, actor.username, actor.role, action, entity, entity_id,
         None if before is None else _canon(before), None if after is None else _canon(after), actor.device, actor.ip, note, prev, h))
    return cur.lastrowid


SEAL_PUB, SEALS = "audit_seal_pub", "audit_seals"


def seal_state(conn: sqlite3.Connection) -> dict:
    """Clé publique enregistrée (la clé privée n'est JAMAIS sur le serveur) et liste des sceaux."""
    pub = conn.execute("SELECT value FROM meta WHERE key=?", (SEAL_PUB,)).fetchone()
    seals = conn.execute("SELECT value FROM meta WHERE key=?", (SEALS,)).fetchone()
    return {"public_key": pub["value"] if pub else None, "seals": json.loads(seals["value"]) if seals else []}


def _meta_set(conn, key: str, value) -> None:
    conn.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value if isinstance(value, str) else json.dumps(value)))


def seal_checkpoint(conn: sqlite3.Connection) -> dict | None:
    """Dernière entrée du journal : c'est elle (identifiant + empreinte) que le propriétaire signe hors du serveur."""
    r = conn.execute("SELECT id, ts, hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    return None if r is None else {"id": r["id"], "hash": r["hash"], "ts": r["ts"]}


def seal_check(conn: sqlite3.Connection) -> dict:
    """Chaque sceau doit avoir une signature valide pour la clé enregistrée ET la même empreinte que l'entrée correspondante du journal."""
    from . import signature
    st = seal_state(conn); bad = []
    for s in st["seals"]:
        r = conn.execute("SELECT hash FROM audit_log WHERE id=?", (s["id"],)).fetchone()
        if not signature.seal_ok(s.get("public_key") or st["public_key"] or "", s["id"], s["hash"], s["sig"]) or r is None or r["hash"] != s["hash"]: bad.append(s["id"])
    last = st["seals"][-1] if st["seals"] else None
    head = conn.execute("SELECT MAX(id) AS m FROM audit_log").fetchone()["m"] or 0
    return {"public_key": st["public_key"], "fingerprint": signature.fingerprint(st["public_key"]) if st["public_key"] else None, "count": len(st["seals"]), "ok": not bad, "broken": bad,
            "last": last, "entries_since_last": head - (last["id"] if last else 0)}


def seal_add(conn: sqlite3.Connection, entry_id: int, entry_hash: str, sig_hex: str, signed_at: str) -> dict:
    from . import signature
    st = seal_state(conn)
    if not st["public_key"]: raise ValueError("Enregistrez d'abord la clé publique de signature.")
    r = conn.execute("SELECT hash FROM audit_log WHERE id=?", (entry_id,)).fetchone()
    if r is None or r["hash"] != entry_hash: raise ValueError("Ce point ne correspond à aucune entrée du journal (le journal a changé, ou la copie est fausse).")
    if st["seals"] and entry_id <= st["seals"][-1]["id"]: raise ValueError("Ce point est plus ancien que le dernier sceau : signez un point plus récent.")
    if not signature.seal_ok(st["public_key"], entry_id, entry_hash, sig_hex): raise ValueError("Signature invalide : elle ne correspond pas à la clé publique enregistrée.")
    seal = {"id": entry_id, "hash": entry_hash, "sig": sig_hex.lower(), "signed_at": signed_at, "public_key": st["public_key"]}
    _meta_set(conn, SEALS, st["seals"] + [seal]); return seal


def verify(conn: sqlite3.Connection) -> dict:
    """Recalcule toute la chaîne. Une ligne modifiée, retirée ou insérée au milieu casse la vérification."""
    prev = GENESIS
    n = 0
    for r in conn.execute("SELECT * FROM audit_log ORDER BY id"):
        f = _fields(r["ts"], r["user_id"], r["username"], r["role"], r["action"], r["entity"], r["entity_id"],
                    None if r["before_json"] is None else json.loads(r["before_json"]),
                    None if r["after_json"] is None else json.loads(r["after_json"]), r["device"], r["ip"], r["note"])
        if r["prev_hash"] != prev or _digest(prev, f) != r["hash"]:
            return {"ok": False, "entries": n, "broken_at": r["id"], "head": prev, "seals": seal_check(conn)}
        prev = r["hash"]
        n += 1
    sc = seal_check(conn)
    return {"ok": sc["ok"], "chain_ok": True, "entries": n, "broken_at": None, "head": prev, "seals": sc}
