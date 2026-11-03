"""Dépôts, transferts de stock et lots avec dates de péremption.

Le stock total d'un produit reste DÉDUIT des mouvements (achats − ventes ± ajustements − retours), comme avant. Les dépôts n'ajoutent qu'une ventilation : chaque ligne d'achat
entre dans un dépôt, chaque ligne de vente en sort d'un, un transfert déplace une quantité d'un dépôt à l'autre (total inchangé). Il y a toujours le « Dépôt principal ».
Les lots : un achat peut porter un n° de lot et une date de péremption. À la vente, le stock sort en priorité du lot qui périme le plus tôt (premier péremption, premier sorti),
jamais d'un lot déjà périmé ; ce qui n'est pas couvert par un lot sort du stock sans lot. Le reste d'un lot = reçu − vendu − ajustements − retours au fournisseur.
Limite connue : les lots suivent le produit, pas le dépôt (un transfert déplace des quantités, pas des lots).
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import date, timedelta

from . import audit
from .ops import UUID_RE, OpError, _deny, next_seq
from .permissions import can
from .sales_ops import _conflict, _date, _int, _text, next_counter, today

MAIN = "main"
SOON_DAYS = 30


# ---------------------------------------------------------------- calculs
def stock_by_depot(c: sqlite3.Connection) -> dict[str, dict[str, int]]:
    """{produit: {dépôt: quantité}} pour les produits suivis en stock."""
    out: dict[str, dict[str, int]] = {}

    def add(sql, sign=1, args=()):
        for r in c.execute(sql, args):
            d = out.setdefault(r["pid"], {}); d[r["dep"]] = d.get(r["dep"], 0) + sign * r["q"]
    add("SELECT l.product_id pid, l.depot_id dep, SUM(l.qty) q FROM purchase_lines l JOIN purchases u ON u.id=l.purchase_id WHERE u.status='active' AND l.product_id IS NOT NULL GROUP BY l.product_id, l.depot_id")
    add("SELECT l.product_id pid, l.depot_id dep, SUM(l.qty) q FROM sale_lines l JOIN sales s ON s.id=l.sale_id WHERE s.status='active' AND l.product_id IS NOT NULL GROUP BY l.product_id, l.depot_id", -1)
    add("SELECT product_id pid, depot_id dep, SUM(qty) q FROM stock_adjustments WHERE status='active' GROUP BY product_id, depot_id")
    add("SELECT l.product_id pid, l.depot_id dep, SUM(l.qty) q FROM credit_note_lines l JOIN credit_notes n ON n.id=l.note_id JOIN sales s ON s.id=n.sale_id WHERE n.status='active' AND s.status='active' GROUP BY l.product_id, l.depot_id")      # marchandise rendue par les clients
    add("SELECT product_id pid, to_depot dep, SUM(qty) q FROM stock_transfers WHERE status='active' GROUP BY product_id, to_depot")
    add("SELECT product_id pid, from_depot dep, SUM(qty) q FROM stock_transfers WHERE status='active' GROUP BY product_id, from_depot", -1)
    add("""SELECT l.product_id pid, (SELECT MIN(pl.depot_id) FROM purchase_lines pl WHERE pl.purchase_id=n.purchase_id AND pl.product_id=l.product_id) dep, SUM(l.qty) q
           FROM supplier_note_lines l JOIN supplier_notes n ON n.id=l.note_id JOIN purchases u ON u.id=n.purchase_id
           WHERE n.status='active' AND u.status='active' AND l.product_id IS NOT NULL GROUP BY l.product_id, dep""", -1)
    tracked = {r["id"] for r in c.execute("SELECT id FROM products WHERE deleted=0 AND track_stock=1")}
    return {pid: {k: v for k, v in d.items() if v} for pid, d in out.items() if pid in tracked}


def depot_level(c: sqlite3.Connection, product_id: str, depot_id: str) -> int:
    return stock_by_depot(c).get(product_id, {}).get(depot_id, 0)


def lot_levels(c: sqlite3.Connection) -> list[dict]:
    t = today().isoformat(); soon = (today() + timedelta(days=SOON_DAYS)).isoformat(); out = []
    for l in c.execute("SELECT l.*, p.name pname FROM lots l JOIN products p ON p.id=l.product_id ORDER BY l.expiry_date IS NULL, l.expiry_date, l.lot_no"):
        recv = c.execute("SELECT COALESCE(SUM(pl.qty),0) q FROM purchase_lines pl JOIN purchases u ON u.id=pl.purchase_id WHERE pl.lot_id=? AND u.status='active'", (l["id"],)).fetchone()["q"]
        sold = c.execute("SELECT COALESCE(SUM(x.qty),0) q FROM sale_line_lots x JOIN sales s ON s.id=x.sale_id WHERE x.lot_id=? AND s.status='active'", (l["id"],)).fetchone()["q"]
        adj = c.execute("SELECT COALESCE(SUM(qty),0) q FROM stock_adjustments WHERE lot_id=? AND status='active'", (l["id"],)).fetchone()["q"]
        back = c.execute("SELECT COALESCE(SUM(x.qty),0) q FROM supplier_note_lines x JOIN supplier_notes n ON n.id=x.note_id JOIN purchases u ON u.id=n.purchase_id WHERE x.lot_id=? AND n.status='active' AND u.status='active'", (l["id"],)).fetchone()["q"]
        rendu = c.execute("SELECT COALESCE(SUM(x.qty),0) q FROM credit_note_lines x JOIN credit_notes n ON n.id=x.note_id JOIN sales s ON s.id=n.sale_id WHERE x.lot_id=? AND n.status='active' AND s.status='active'", (l["id"],)).fetchone()["q"]
        rem = recv - sold + adj - back + rendu
        status = "empty" if rem <= 0 else "expired" if l["expiry_date"] and l["expiry_date"] < t else "soon" if l["expiry_date"] and l["expiry_date"] <= soon else "ok"
        out.append({"id": l["id"], "product_id": l["product_id"], "product_name": l["pname"], "lot_no": l["lot_no"], "expiry_date": l["expiry_date"], "received": recv, "remaining": max(rem, 0), "status": status})
    return out


def depot_view(r) -> dict:
    return {"id": r["id"], "code": r["code"], "name": r["name"], "active": bool(r["active"]), "main": r["id"] == MAIN, "version": r["version"]}


def transfer_view(r) -> dict:
    return {k: r[k] for k in ("id", "number", "product_id", "from_depot", "to_depot", "qty", "transfer_date", "note", "status", "cancel_reason", "version", "seq", "created_at")}


def pull_extra(c: sqlite3.Connection, user: sqlite3.Row, since: int) -> dict:
    out = {"depots": [], "stock_depots": {}, "lots": [], "stock_transfers": []}
    if not can(user["role"], "stock.read"): return out
    out["depots"] = [depot_view(r) for r in c.execute("SELECT * FROM depots ORDER BY id<>'main', name")]
    out["stock_depots"] = stock_by_depot(c)
    out["lots"] = [l for l in lot_levels(c) if l["remaining"] > 0]
    out["stock_transfers"] = [transfer_view(r) for r in c.execute("SELECT * FROM stock_transfers ORDER BY seq")]
    return out


def active_depot(c: sqlite3.Connection, depot_id) -> str:
    d = str(depot_id or MAIN)
    r = c.execute("SELECT active FROM depots WHERE id=?", (d,)).fetchone()
    if r is None or not r["active"]: raise OpError("Dépôt inconnu ou désactivé.")
    return d


# ---------------------------------------------------------------- lots
def upsert_lot(c: sqlite3.Connection, product_id: str, lot_no: str, expiry: str | None, fallback_no: str, now: str) -> str | None:
    """Retrouve ou crée le lot d'une ligne d'achat. None si la ligne ne porte ni n° de lot ni date."""
    if not lot_no and not expiry: return None
    lot_no = lot_no or fallback_no
    r = c.execute("SELECT * FROM lots WHERE product_id=? AND lot_no=?", (product_id, lot_no)).fetchone()
    if r is not None:
        if (r["expiry_date"] or None) != (expiry or None): raise OpError(f"Le lot « {lot_no} » existe déjà avec une autre date de péremption ({r['expiry_date'] or 'aucune'}).")
        return r["id"]
    lid = str(uuid.uuid4())
    c.execute("INSERT INTO lots(id,product_id,lot_no,expiry_date,created_at) VALUES(?,?,?,?,?)", (lid, product_id, lot_no, expiry, now))
    return lid


def check_lot(c: sqlite3.Connection, product_id: str, lot_no: str, expiry: str | None, seen: dict) -> None:
    """Refuse AVANT toute écriture un lot incohérent (même n° avec une autre date de péremption)."""
    if not lot_no: return
    key = (product_id, lot_no)
    if key in seen and seen[key] != expiry: raise OpError(f"Le lot « {lot_no} » apparaît avec deux dates de péremption différentes.")
    seen[key] = expiry
    r = c.execute("SELECT expiry_date FROM lots WHERE product_id=? AND lot_no=?", (product_id, lot_no)).fetchone()
    if r is not None and (r["expiry_date"] or None) != (expiry or None): raise OpError(f"Le lot « {lot_no} » existe déjà avec une autre date de péremption ({r['expiry_date'] or 'aucune'}).")


def allocate_lots(c: sqlite3.Connection, sale_id: str, sale_date: str, lines: list[tuple]) -> list[str]:
    """Premier péremption, premier sorti : pour chaque ligne vendue, prélève sur les lots non périmés. Renvoie des avertissements."""
    warnings: list[str] = []
    levels = {l["id"]: l for l in lot_levels(c)}
    for ln in lines:
        position, pid, name, qty = ln[0], ln[1], ln[2], ln[3]
        if not pid: continue
        cands = sorted((l for l in levels.values() if l["product_id"] == pid and l["remaining"] > 0 and (not l["expiry_date"] or l["expiry_date"] >= sale_date)), key=lambda l: (l["expiry_date"] is None, l["expiry_date"] or "", l["lot_no"]))
        need = qty
        for l in cands:
            if need <= 0: break
            take = min(need, l["remaining"])
            c.execute("INSERT INTO sale_line_lots(sale_id,position,lot_id,qty) VALUES(?,?,?,?)", (sale_id, position, l["id"], take))
            l["remaining"] -= take; need -= take
        if need > 0 and any(l["product_id"] == pid for l in levels.values()):
            expired = sum(l["remaining"] for l in levels.values() if l["product_id"] == pid and l["expiry_date"] and l["expiry_date"] < sale_date)
            if expired: warnings.append(f"{name} : {expired} en stock sont périmés et n'ont pas été vendus comme lot ; {need} sorti(s) du stock sans lot.")
    return warnings


# ---------------------------------------------------------------- dépôts
def _depot_create(c, user, actor, data, role, now):
    if not can(role, "stock.adjust"): return _deny(c, actor, "depot.create", None, "droit manquant")
    name = _text(data.get("name"), "Le nom du dépôt", 60, True)
    if len(name) < 2: raise OpError("Le nom du dépôt est obligatoire.")
    if c.execute("SELECT 1 FROM depots WHERE name=? COLLATE NOCASE", (name,)).fetchone(): raise OpError("Un dépôt porte déjà ce nom.")
    did = str(data.get("id") or uuid.uuid4())
    if not UUID_RE.match(did): raise OpError("Identifiant de dépôt invalide.")
    code = f"D{next_counter(c, 'depot_no') + 1:02d}"                           # D01 est le dépôt principal
    c.execute("INSERT INTO depots(id,code,name,active,version,seq,created_at) VALUES(?,?,?,1,1,?,?)", (did, code, name, next_seq(c), now))
    audit.record(c, actor, "depot.create", "depot", did, None, {"code": code, "nom": name})
    return {"status": "ok", "depot": depot_view(c.execute("SELECT * FROM depots WHERE id=?", (did,)).fetchone())}


def _depot_update(c, user, actor, data, role, now):
    if not can(role, "stock.adjust"): return _deny(c, actor, "depot.update", str(data.get("id") or ""), "droit manquant")
    r = c.execute("SELECT * FROM depots WHERE id=?", (str(data.get("id") or ""),)).fetchone()
    if r is None: return {"status": "invalid", "message": "Dépôt introuvable."}
    if int(data.get("base_version") or 0) != r["version"]: return _conflict(c, actor, "depot", r["id"], depot_view(r), "dépôt modifié entre-temps")
    ch = data.get("changes") or {}; new = {}
    if "name" in ch:
        nm = _text(ch["name"], "Le nom du dépôt", 60, True)
        if len(nm) < 2 or c.execute("SELECT 1 FROM depots WHERE name=? COLLATE NOCASE AND id<>?", (nm, r["id"])).fetchone(): raise OpError("Nom invalide ou déjà utilisé.")
        new["name"] = nm
    if "active" in ch:
        act = 1 if ch["active"] else 0
        if not act:
            if r["id"] == MAIN: raise OpError("Le dépôt principal ne peut pas être désactivé.")
            left = {pid: d[r["id"]] for pid, d in stock_by_depot(c).items() if d.get(r["id"])}
            if left: raise OpError("Ce dépôt contient encore du stock : transférez-le d'abord.")
        new["active"] = act
    new = {k: v for k, v in new.items() if r[k] != v}
    if not new: return {"status": "ok", "depot": depot_view(r), "unchanged": True}
    c.execute(f"UPDATE depots SET {', '.join(k + '=?' for k in new)}, version=version+1, seq=? WHERE id=?", (*new.values(), next_seq(c), r["id"]))
    audit.record(c, actor, "depot.update", "depot", r["id"], {k: r[k] for k in new}, new, note=r["name"])
    return {"status": "ok", "depot": depot_view(c.execute("SELECT * FROM depots WHERE id=?", (r["id"],)).fetchone())}


# ---------------------------------------------------------------- transferts
def _transfer_create(c, user, actor, data, role, now):
    if not can(role, "stock.adjust"): return _deny(c, actor, "stransfer.create", None, "droit manquant")
    p = c.execute("SELECT * FROM products WHERE id=? AND deleted=0 AND track_stock=1", (str(data.get("product_id") or ""),)).fetchone()
    if p is None: return {"status": "invalid", "message": "Produit introuvable ou sans suivi de stock."}
    a, b = active_depot(c, data.get("from_depot")), active_depot(c, data.get("to_depot"))
    if a == b: raise OpError("Les deux dépôts doivent être différents.")
    qty = _int(data.get("qty"), "La quantité", 1, 10 ** 7)
    d = _date(data.get("transfer_date") or today().isoformat(), "La date")
    if date.fromisoformat(d) > today() + timedelta(days=1): raise OpError("La date ne peut pas être dans le futur.")
    have = depot_level(c, p["id"], a)
    if qty > have: return {"status": "invalid", "message": f"Le dépôt d'origine n'a que {have} de « {p['name']} »."}
    tid = str(data.get("id") or uuid.uuid4())
    if not UUID_RE.match(tid): raise OpError("Identifiant de transfert invalide.")
    if c.execute("SELECT 1 FROM stock_transfers WHERE id=?", (tid,)).fetchone(): raise OpError("Ce transfert existe déjà.")
    number = f"TS-{next_counter(c, 'stransfer_no'):04d}"
    c.execute("INSERT INTO stock_transfers(id,number,product_id,from_depot,to_depot,qty,transfer_date,note,status,version,seq,created_at,updated_at,created_by,updated_by) VALUES(?,?,?,?,?,?,?,?,'active',1,?,?,?,?,?)",
              (tid, number, p["id"], a, b, qty, d, _text(data.get("note"), "La note", 200), next_seq(c), now, now, user["id"], user["id"]))
    names = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM depots")}
    audit.record(c, actor, "stransfer.create", "stock_transfer", tid, None, {"numéro": number, "produit": p["name"], "quantité": qty, "de": names[a], "vers": names[b], "date": d})
    return {"status": "ok", "transfer": transfer_view(c.execute("SELECT * FROM stock_transfers WHERE id=?", (tid,)).fetchone())}


def _transfer_cancel(c, user, actor, data, role, now):
    if not can(role, "stock.adjust"): return _deny(c, actor, "stransfer.cancel", str(data.get("id") or ""), "droit manquant")
    r = c.execute("SELECT * FROM stock_transfers WHERE id=? AND status='active'", (str(data.get("id") or ""),)).fetchone()
    if r is None: return {"status": "invalid", "message": "Transfert introuvable ou déjà annulé."}
    if int(data.get("base_version") or 0) != r["version"]: return _conflict(c, actor, "stock_transfer", r["id"], transfer_view(r), "annulation : transfert modifié entre-temps")
    have = depot_level(c, r["product_id"], r["to_depot"])
    if r["qty"] > have: return {"status": "invalid", "message": f"Annulation impossible : le dépôt de destination n'a plus que {have} (une partie est déjà sortie)."}
    reason = _text(data.get("reason"), "Le motif", 200, True)
    if len(reason) < 3: raise OpError("Le motif d'annulation est obligatoire.")
    c.execute("UPDATE stock_transfers SET status='cancelled', cancel_reason=?, version=version+1, seq=?, updated_at=?, updated_by=? WHERE id=?", (reason, next_seq(c), now, user["id"], r["id"]))
    audit.record(c, actor, "stransfer.cancel", "stock_transfer", r["id"], {"numéro": r["number"], "quantité": r["qty"]}, {"statut": "annulé", "motif": reason})
    return {"status": "ok"}


HANDLERS = {"depot.create": _depot_create, "depot.update": _depot_update, "stransfer.create": _transfer_create, "stransfer.cancel": _transfer_cancel}
