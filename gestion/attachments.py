"""Pièces jointes : la photo du reçu d'une dépense, la facture PDF d'un fournisseur, le bon signé d'une livraison…

Stockées DANS la base (donc sauvegardées, restaurées et exportées avec elle, sans dossier de fichiers à garder synchronisé). Formats acceptés : JPEG, PNG et PDF, reconnus à leur
contenu et non à leur nom (jamais de SVG ni de HTML, qui peuvent contenir du script). 5 Mo au plus par fichier, 10 fichiers au plus par document. L'application mobile réduit les
photos avant l'envoi. Qui peut ajouter ou voir une pièce : exactement ceux qui peuvent écrire ou lire le document auquel elle est jointe. Retirer une pièce ne l'efface pas de la
base (elle devient inaccessible et le journal d'audit garde la trace) : un justificatif comptable ne disparaît pas sans laisser de trace.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import uuid

from . import audit
from .ops import OpError, visible_to
from .permissions import can
from .sales_ops import _text, sale_visible

MAX_BYTES = 5 * 1024 * 1024
MAX_PER_ENTITY = 10
ENTITIES = {"expense", "purchase", "supplier_note", "sale", "payment", "product", "asset", "delivery", "quote", "transfer"}
_NAME_BAD = re.compile(r"[\x00-\x1f/\\:*?\"<>|]")


def kind_of(b: bytes) -> str | None:
    if b[:8] == b"\x89PNG\r\n\x1a\n": return "image/png"
    if b[:3] == b"\xff\xd8\xff": return "image/jpeg"
    if b[:5] == b"%PDF-": return "application/pdf"
    return None


def clean_name(name: str, mime: str) -> str:
    ext = {"image/png": ".png", "image/jpeg": ".jpg", "application/pdf": ".pdf"}[mime]
    base = _NAME_BAD.sub("_", (name or "").strip())[:80].strip(". ") or "piece"
    return base if base.lower().endswith(ext) or (mime == "image/jpeg" and base.lower().endswith(".jpeg")) else base + ext


def access(c: sqlite3.Connection, user: sqlite3.Row, entity: str, eid: str, write: bool) -> bool:
    """Le document existe-t-il et l'utilisateur peut-il le lire (ou le modifier, si write) ?"""
    if entity not in ENTITIES: return False
    role, uid = user["role"], user["id"]

    def sale_of(sale_id):
        s = c.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
        return s is not None and sale_visible(c, user, s)
    if entity == "expense":
        e = c.execute("SELECT * FROM expenses WHERE id=?", (eid,)).fetchone()
        if e is None: return False
        if write: return can(role, "expenses.create") and (role != "cashier" or e["created_by"] == uid)
        return can(role, "expenses.read_all") or (can(role, "expenses.read_own") and e["created_by"] == uid)
    if entity in ("purchase", "supplier_note"):
        tbl = "purchases" if entity == "purchase" else "supplier_notes"
        return c.execute(f"SELECT 1 FROM {tbl} WHERE id=?", (eid,)).fetchone() is not None and can(role, "purchases.create" if write else "purchases.read")
    if entity == "sale": return sale_of(eid) and (can(role, "sales.create") if write else True)
    if entity == "payment":
        p = c.execute("SELECT sale_id FROM payments WHERE id=?", (eid,)).fetchone()
        return p is not None and sale_of(p["sale_id"]) and (can(role, "payments.create") if write else True)
    if entity == "product":
        return c.execute("SELECT 1 FROM products WHERE id=?", (eid,)).fetchone() is not None and can(role, "products.write" if write else "products.read")
    if entity == "delivery":
        d = c.execute("SELECT sale_id FROM delivery_notes WHERE id=?", (eid,)).fetchone()
        return d is not None and sale_of(d["sale_id"]) and (can(role, "sales.deliver") if write else True)
    if entity == "quote":
        q = c.execute("SELECT client_id FROM quotes WHERE id=?", (eid,)).fetchone()
        cl = c.execute("SELECT * FROM clients WHERE id=?", (q["client_id"],)).fetchone() if q else None
        return cl is not None and visible_to(role, uid, cl) and (can(role, "sales.create") if write else (can(role, "sales.read_all") or can(role, "sales.read_own")))
    if entity == "asset":
        return c.execute("SELECT 1 FROM fixed_assets WHERE id=?", (eid,)).fetchone() is not None and can(role, "accounting.write" if write else "accounting.read")
    if entity == "transfer":
        return c.execute("SELECT 1 FROM transfers WHERE id=?", (eid,)).fetchone() is not None and can(role, "bank.write" if write else "bank.read")
    return False


def meta(r) -> dict:
    return {"id": r["id"], "entity": r["entity"], "entity_id": r["entity_id"], "name": r["name"], "mime": r["mime"], "size": r["size"], "created_at": r["created_at"], "uploaded_by": r["uploaded_by_name"]}


def list_for(c: sqlite3.Connection, entity: str, eid: str) -> list[dict]:
    return [meta(r) for r in c.execute("SELECT * FROM attachments WHERE entity=? AND entity_id=? AND status='active' ORDER BY created_at", (entity, eid))]


def add(c: sqlite3.Connection, user: sqlite3.Row, actor, entity: str, eid: str, name: str, data: bytes, now: str) -> dict:
    if not data: raise OpError("Fichier vide.")
    if len(data) > MAX_BYTES: raise OpError("Fichier trop lourd (5 Mo au plus).")
    mime = kind_of(data)
    if mime is None: raise OpError("Format accepté : JPEG, PNG ou PDF.")
    if c.execute("SELECT COUNT(*) n FROM attachments WHERE entity=? AND entity_id=? AND status='active'", (entity, eid)).fetchone()["n"] >= MAX_PER_ENTITY: raise OpError(f"{MAX_PER_ENTITY} pièces au plus par document.")
    digest = hashlib.sha256(data).hexdigest()
    if c.execute("SELECT 1 FROM attachments WHERE entity=? AND entity_id=? AND sha256=? AND status='active'", (entity, eid, digest)).fetchone(): raise OpError("Cette pièce est déjà jointe à ce document.")
    aid = str(uuid.uuid4()); fname = clean_name(name, mime)
    c.execute("INSERT INTO attachments(id,entity,entity_id,name,mime,size,sha256,data,status,created_at,uploaded_by,uploaded_by_name) VALUES(?,?,?,?,?,?,?,?,'active',?,?,?)",
              (aid, entity, eid, fname, mime, len(data), digest, data, now, user["id"], user["full_name"]))
    audit.record(c, actor, "attachment.added", entity, eid, None, {"fichier": fname, "type": mime, "taille (octets)": len(data), "empreinte": digest[:16]})
    return meta(c.execute("SELECT * FROM attachments WHERE id=?", (aid,)).fetchone())


def remove(c: sqlite3.Connection, user: sqlite3.Row, actor, aid: str, reason: str) -> None:
    r = c.execute("SELECT * FROM attachments WHERE id=? AND status='active'", (aid,)).fetchone()
    if r is None or not access(c, user, r["entity"], r["entity_id"], True): raise OpError("Pièce introuvable.")
    reason = _text(reason, "Le motif", 200, True)
    if len(reason) < 3: raise OpError("Le motif du retrait est obligatoire.")
    c.execute("UPDATE attachments SET status='removed', removed_reason=?, removed_by=? WHERE id=?", (reason, user["id"], aid))
    audit.record(c, actor, "attachment.removed", r["entity"], r["entity_id"], {"fichier": r["name"], "empreinte": r["sha256"][:16]}, {"statut": "retirée", "motif": reason})
