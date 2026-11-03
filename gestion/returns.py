"""Retours en stock liés aux avoirs clients.

Un avoir peut s'accompagner de marchandise rendue par le client : elle REMONTE dans le stock du dépôt choisi (par défaut le dépôt d'où elle était partie) et, si la vente avait
prélevé sur des lots (premier périmé, premier sorti), elle retourne dans ces mêmes lots, dans l'ordre du prélèvement. Rien n'est stocké en double : les quantités rendues sont
lues avec les achats, les ventes et les ajustements pour calculer le stock (voir stock_ops, warehouses, accounting.stock_value).
Règles : on ne rend jamais plus que ce qui a été vendu sur la ligne (en tenant compte des retours déjà faits) ; un article libre ou un produit non suivi en stock n'a pas de stock
à remettre ; tout est vérifié AVANT la moindre écriture.
"""
from __future__ import annotations

import sqlite3

from .ops import OpError


def returned_by_position(c: sqlite3.Connection, sale_id: str) -> dict[int, int]:
    return {r["pos"]: r["q"] for r in c.execute(
        "SELECT l.sale_position pos, SUM(l.qty) q FROM credit_note_lines l JOIN credit_notes n ON n.id=l.note_id WHERE n.sale_id=? AND n.status='active' GROUP BY l.sale_position", (sale_id,))}


def plan(c: sqlite3.Connection, sale: sqlite3.Row, returns, int_fn, active_depot) -> list[dict]:
    """Vérifie la demande et renvoie les lignes à enregistrer (une par lot touché). Lève OpError sans rien écrire."""
    if not returns: return []
    if not isinstance(returns, list) or len(returns) > 30: raise OpError("Marchandise rendue : de 1 à 30 lignes.")
    lines = {l["position"]: l for l in c.execute("SELECT * FROM sale_lines WHERE sale_id=? ORDER BY position", (sale["id"],))}      # clé : numéro de ligne de la vente (à partir de 1)
    already = returned_by_position(c, sale["id"]); asked: dict[int, int] = {}
    lot_back = {(r["pos"], r["lot"]): r["q"] for r in c.execute(
        "SELECT l.sale_position pos, l.lot_id lot, SUM(l.qty) q FROM credit_note_lines l JOIN credit_notes n ON n.id=l.note_id WHERE n.sale_id=? AND n.status='active' AND l.lot_id IS NOT NULL GROUP BY l.sale_position, l.lot_id", (sale["id"],))}
    rows: list[dict] = []
    for r in returns:
        if not isinstance(r, dict): raise OpError("Marchandise rendue : ligne illisible.")
        pos = int_fn(r.get("position"), "La ligne rendue", 1, max(lines, default=1))
        qty = int_fn(r.get("qty"), "La quantité rendue", 1, 10 ** 9)
        ln = lines.get(pos)
        if ln is None: raise OpError("Cette vente n'a pas cette ligne.")
        if not ln["product_id"]: raise OpError(f"« {ln['name']} » est un article libre : il n'est pas suivi en stock, rien à remettre.")
        prod = c.execute("SELECT name, track_stock FROM products WHERE id=?", (ln["product_id"],)).fetchone()
        if prod is None or not prod["track_stock"]: raise OpError(f"« {ln['name']} » n'est pas suivi en stock : rien à remettre.")
        asked[pos] = asked.get(pos, 0) + qty
        if already.get(pos, 0) + asked[pos] > ln["qty"]:
            raise OpError(f"On ne peut pas rendre plus que ce qui a été vendu : {ln['qty']} × « {ln['name']} » vendus, dont {already.get(pos, 0)} déjà rendus.")
        depot = active_depot(c, r.get("depot_id") or ln["depot_id"])
        need = qty
        for a in c.execute("SELECT a.lot_id, a.qty FROM sale_line_lots a JOIN lots x ON x.id=a.lot_id WHERE a.sale_id=? AND a.position=? ORDER BY x.expiry_date IS NULL, x.expiry_date, x.lot_no", (sale["id"], pos)):   # retour dans les lots d'où la marchandise est partie, dans l'ordre du prélèvement (premier périmé, premier sorti)
            if need <= 0: break
            dispo = a["qty"] - lot_back.get((pos, a["lot_id"]), 0)
            take = min(need, dispo)
            if take > 0:
                rows.append({"position": pos, "product_id": ln["product_id"], "name": ln["name"], "qty": take, "depot_id": depot, "lot_id": a["lot_id"]})
                lot_back[(pos, a["lot_id"])] = lot_back.get((pos, a["lot_id"]), 0) + take; need -= take
        if need > 0: rows.append({"position": pos, "product_id": ln["product_id"], "name": ln["name"], "qty": need, "depot_id": depot, "lot_id": None})
    return rows


def insert_lines(c: sqlite3.Connection, note_id: str, rows: list[dict]) -> None:
    for i, r in enumerate(rows):
        c.execute("INSERT INTO credit_note_lines(note_id,position,sale_position,product_id,name,qty,depot_id,lot_id) VALUES(?,?,?,?,?,?,?,?)",
                  (note_id, i, r["position"], r["product_id"], r["name"], r["qty"], r["depot_id"], r["lot_id"]))


def lines_of(c: sqlite3.Connection, note_id: str) -> list[dict]:
    return [{"position": r["sale_position"], "product_id": r["product_id"], "name": r["name"], "qty": r["qty"], "depot_id": r["depot_id"], "lot_no": r["lot_no"]}
            for r in c.execute("SELECT l.*, x.lot_no lot_no FROM credit_note_lines l LEFT JOIN lots x ON x.id=l.lot_id WHERE l.note_id=? ORDER BY l.position", (note_id,))]


def summary(c: sqlite3.Connection, note_id: str) -> str:
    """« 3 × Ciment 50 kg, 1 × Riz » ou chaîne vide."""
    merged: dict[str, int] = {}
    for x in lines_of(c, note_id): merged[x["name"]] = merged.get(x["name"], 0) + x["qty"]
    return ", ".join(f"{q} × {n}" for n, q in merged.items())
