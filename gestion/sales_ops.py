"""Catalogue, ventes, paiements, avoirs, réglages : règles métier appliquées par le serveur.

Règles clés (toutes vérifiées ici, jamais seulement dans l'interface) :
- une vente au comptant est soldée à la vente ; une vente à crédit exige une échéance et respecte la limite de crédit du client ;
- un paiement ne peut pas dépasser le reste à payer ; un paiement identique (même référence, même montant) n'est pas enregistré deux fois ;
- numéros de facture et d'avoir continus, attribués par le serveur, jamais réutilisés (une vente annulée garde son numéro) ;
- rien n'est supprimé : une vente, un paiement ou un avoir est annulé avec un motif, et reste visible dans le journal.
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import date, timedelta

from . import audit, period, security
from .audit import Actor
from .ops import UUID_RE, OpError, _deny, bump_epoch, client_view, importing, next_seq, visible_to
from .methods import all_methods, valid_method
from .permissions import BACKDATE_DAYS, MAX_DISCOUNT_PCT, METHODS, can

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SETTINGS_FIELDS = {"company_name": 80, "address": 160, "phone": 40, "tax_id": 40, "footer": 200, "doc_lang": 12}


def today() -> date:
    return security.now().date()


def _date(v, label: str, required: bool = True) -> str | None:
    if v in (None, ""):
        if required: raise OpError(f"{label} : date obligatoire.")
        return None
    if not isinstance(v, str) or not DATE_RE.match(v): raise OpError(f"{label} : date invalide (AAAA-MM-JJ).")
    try: date.fromisoformat(v)
    except ValueError: raise OpError(f"{label} : date invalide.")
    return v


def _int(v, label: str, lo: int, hi: int) -> int:
    try: n = int(v)
    except (TypeError, ValueError): raise OpError(f"{label} : nombre entier attendu.")
    if not (lo <= n <= hi): raise OpError(f"{label} : valeur hors limites ({lo} à {hi}).")
    return n


def _text(v, label: str, maxlen: int, required: bool = False) -> str:
    s = (v or "").strip() if isinstance(v, (str, type(None))) else str(v).strip()
    if required and not s: raise OpError(f"{label} est obligatoire.")
    if len(s) > maxlen: raise OpError(f"{label} : {maxlen} caractères maximum.")
    return s


def next_counter(c: sqlite3.Connection, key: str) -> int:
    c.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key=?", (key,))
    return int(c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()["value"])


# ---------------------------------------------------------------- montants d'une vente
def sale_totals(c: sqlite3.Connection, sale: sqlite3.Row) -> dict:
    pay = c.execute("SELECT COALESCE(SUM(amount),0) s FROM payments WHERE sale_id=? AND status='active'", (sale["id"],)).fetchone()["s"]
    notes = {r["settlement"]: r["s"] for r in c.execute("SELECT settlement, SUM(amount) s FROM credit_notes WHERE sale_id=? AND status='active' GROUP BY settlement", (sale["id"],))}
    notes_total = sum(notes.values()); refunds = notes.get("refund", 0)
    net = sale["total"] - notes_total
    paid = sale["paid_at_sale"] + pay - refunds
    return {"payments_total": pay, "credit_notes_total": notes_total, "refunds_total": refunds, "net_total": net, "paid_total": paid, "balance": 0 if sale["status"] == "cancelled" else net - paid}


def client_balance(c: sqlite3.Connection, client_id: str) -> int:
    return sum(sale_totals(c, s)["balance"] for s in c.execute("SELECT * FROM sales WHERE client_id=? AND status='active'", (client_id,)))


def sale_view(c: sqlite3.Connection, s: sqlite3.Row) -> dict:
    from . import returns
    back = returns.returned_by_position(c, s["id"])
    lines = [{"product_id": l["product_id"], "name": l["name"], "qty": l["qty"], "unit_price": l["unit_price"], "discount_pct": l["discount_pct"], "amount": l["amount"], "tax_rate": l["tax_rate"],
              "position": l["position"], "returned": back.get(l["position"], 0), "depot_id": l["depot_id"]}
             for l in c.execute("SELECT * FROM sale_lines WHERE sale_id=? ORDER BY position", (s["id"],))]
    t = sale_totals(c, s)
    seller = c.execute("SELECT full_name FROM users WHERE id=?", (s["seller_user_id"],)).fetchone() if s["seller_user_id"] else None
    d = {k: s[k] for k in ("id", "number", "sale_date", "client_id", "kind", "due_date", "seller_user_id", "total", "paid_at_sale", "pay_method", "note", "delivery_due", "delivered_at", "status", "cancel_reason", "version", "seq", "created_at")}
    d.update(t); d["lines"] = lines; d["seller_name"] = seller["full_name"] if seller else None
    return d


def payment_view(p: sqlite3.Row) -> dict:
    return {k: p[k] for k in ("id", "sale_id", "client_id", "pay_date", "amount", "method", "reference", "status", "cancel_reason", "version", "seq", "created_at")}


def note_view(n: sqlite3.Row, c: sqlite3.Connection | None = None) -> dict:
    d = {k: n[k] for k in ("id", "number", "sale_id", "client_id", "note_date", "amount", "reason", "settlement", "status", "version", "seq", "created_at")}
    if c is not None:
        from . import returns
        d["returns"] = returns.lines_of(c, n["id"])                                      # marchandise rendue en stock avec cet avoir
    return d


def product_view(p: sqlite3.Row) -> dict:
    d = {k: p[k] for k in ("id", "code", "name", "category", "unit", "price", "reorder_level", "version", "seq", "created_at")}; d["deleted"] = bool(p["deleted"]); d["track_stock"] = bool(p["track_stock"]); d["tax_rate"] = p["tax_rate"]; return d


def settings_get(c: sqlite3.Connection) -> dict:
    r = c.execute("SELECT value FROM meta WHERE key='company'").fetchone()
    d = json.loads(r["value"]) if r else {}
    out = {k: d.get(k, "") for k in SETTINGS_FIELDS}; out["tax_rate"] = int(d.get("tax_rate", 0))
    out["has_logo"] = c.execute("SELECT 1 FROM meta WHERE key='logo'").fetchone() is not None          # l'interface ne demande le logo que s'il existe
    out["has_signature"] = c.execute("SELECT 1 FROM meta WHERE key='signature'").fetchone() is not None
    return out


def sale_visible(c: sqlite3.Connection, user: sqlite3.Row, s: sqlite3.Row) -> bool:
    role = user["role"]
    if can(role, "sales.read_all"): return True
    if not can(role, "sales.read_own"): return False
    if s["seller_user_id"] == user["id"]: return True
    cl = c.execute("SELECT owner_user_id FROM clients WHERE id=?", (s["client_id"],)).fetchone()
    return bool(cl) and cl["owner_user_id"] == user["id"]


# ---------------------------------------------------------------- lecture / synchronisation
def pull(c: sqlite3.Connection, user: sqlite3.Row, since: int, epoch: int | None, db_id: str | None = None) -> dict:
    cur_epoch = int(c.execute("SELECT value FROM meta WHERE key='epoch'").fetchone()["value"])
    head = int(c.execute("SELECT value FROM meta WHERE key='seq'").fetchone()["value"])
    cur_db = c.execute("SELECT value FROM meta WHERE key='db_id'").fetchone()["value"]
    # synchronisation complète si : premier appel, droits modifiés, base remplacée/restaurée (autre identifiant) ou curseur en avance sur le serveur
    full = since == 0 or epoch != cur_epoch or db_id != cur_db or since > head
    if full: since = 0
    role = user["role"]
    out: dict = {"full": full, "cursor": head, "epoch": cur_epoch, "db_id": cur_db}
    out["clients"] = [client_view(r, role) for r in c.execute("SELECT * FROM clients WHERE seq>? ORDER BY seq", (since,)) if visible_to(role, user["id"], r)]
    out["products"] = [product_view(r) for r in c.execute("SELECT * FROM products WHERE seq>? ORDER BY seq", (since,))] if can(role, "products.read") else []
    sales, ids = [], set()
    visible_cache: dict[str, bool] = {}
    # une vente change aussi quand un paiement ou un avoir la touche : on renvoie les ventes dont une dépendance a bougé
    touched = {r["sale_id"] for r in c.execute("SELECT sale_id FROM payments WHERE seq>? UNION SELECT sale_id FROM credit_notes WHERE seq>?", (since, since))}
    for s in c.execute("SELECT * FROM sales WHERE seq>? OR id IN (%s) ORDER BY seq" % ",".join("?" * len(touched)) if touched else "SELECT * FROM sales WHERE seq>? ORDER BY seq",
                       (since, *touched) if touched else (since,)):
        ok = visible_cache.setdefault(s["id"], sale_visible(c, user, s))
        if ok: sales.append(sale_view(c, s)); ids.add(s["id"])
    out["sales"] = sales
    out["payments"] = [payment_view(p) for p in c.execute("SELECT * FROM payments WHERE seq>? ORDER BY seq", (since,))
                       if visible_cache.get(p["sale_id"]) or (p["sale_id"] not in visible_cache and sale_visible(c, user, c.execute("SELECT * FROM sales WHERE id=?", (p["sale_id"],)).fetchone()))]
    out["credit_notes"] = [note_view(n, c) for n in c.execute("SELECT * FROM credit_notes WHERE seq>? ORDER BY seq", (since,))
                           if visible_cache.get(n["sale_id"]) or (n["sale_id"] not in visible_cache and sale_visible(c, user, c.execute("SELECT * FROM sales WHERE id=?", (n["sale_id"],)).fetchone()))]
    out["settings"] = settings_get(c)
    out["methods"] = all_methods(c)
    out["salespeople"] = ([{"id": u["id"], "full_name": u["full_name"]} for u in c.execute("SELECT id, full_name FROM users WHERE active=1 AND role='sales' ORDER BY full_name")]
                          if can(role, "clients.write") else [])
    from . import stock_ops
    out.update(stock_ops.pull_extra(c, user, since))
    from . import cash_ops
    out.update(cash_ops.pull_extra(c, user, since))
    from . import bank_ops
    out.update(bank_ops.pull_extra(c, user, since))
    from . import accounting
    out.update(accounting.pull_extra(c, user, since))
    from . import deliveries, porders, quotes, recurring, supplier_notes, warehouses
    out.update(warehouses.pull_extra(c, user, since))
    out.update(supplier_notes.pull_extra(c, user, since))
    out.update(recurring.pull_extra(c, user, since))
    out.update(quotes.pull_extra(c, user, since)); out.update(porders.pull_extra(c, user, since))
    out.update(deliveries.pull_extra(c, user, since))
    return out


# ---------------------------------------------------------------- opérations
def _conflict(c, actor, entity, eid, row_view, note) -> dict:
    audit.record(c, actor, f"{entity}.conflict", entity, eid, note=note)
    return {"status": "conflict", "message": "Cet enregistrement a été modifié par quelqu'un d'autre.", "server": row_view}


def dispatch(c: sqlite3.Connection, user: sqlite3.Row, actor: Actor, op: dict) -> dict:
    t, data, role, now = op.get("type"), op.get("data") or {}, user["role"], security.iso()
    h = {"product.create": _product_create, "product.update": _product_update, "product.delete": _product_delete, "sale.create": _sale_create, "sale.deliver": _sale_deliver,
         "sale.cancel": _sale_cancel, "payment.create": _payment_create, "payment.cancel": _payment_cancel, "creditnote.create": _note_create, "settings.update": _settings_update}.get(t)
    if h is None:
        from . import accounting, assets, bank_ops, cash_ops, deferrals, deliveries, payaccts, porders, provisions, quotes, recurring, stock_ops, supplier_notes, transfers, warehouses
        h = stock_ops.HANDLERS.get(t) or cash_ops.HANDLERS.get(t) or bank_ops.HANDLERS.get(t) or accounting.HANDLERS.get(t) or assets.HANDLERS.get(t) or provisions.HANDLERS.get(t) or deferrals.HANDLERS.get(t) or quotes.HANDLERS.get(t) or porders.HANDLERS.get(t) or deliveries.HANDLERS.get(t) or payaccts.HANDLERS.get(t) or transfers.HANDLERS.get(t) or recurring.HANDLERS.get(t) or supplier_notes.HANDLERS.get(t) or warehouses.HANDLERS.get(t)
    if h is None: raise OpError("Type d'opération inconnu.")
    return h(c, user, actor, data, role, now)


# --- catalogue
def _product_fields(data: dict, partial: bool) -> dict:
    out = {}
    if "name" in data or not partial: out["name"] = _text(data.get("name"), "Le nom du produit", 120, True)
    if "category" in data: out["category"] = _text(data.get("category"), "La catégorie", 60)
    if "unit" in data: out["unit"] = _text(data.get("unit"), "L'unité", 30)
    if "price" in data or not partial: out["price"] = _int(data.get("price", 0), "Le prix", 0, 10 ** 10)
    if "reorder_level" in data: out["reorder_level"] = _int(data.get("reorder_level", 0), "Le seuil d'alerte", 0, 10 ** 7)
    if "track_stock" in data: out["track_stock"] = 1 if data.get("track_stock") else 0
    if "tax_rate" in data:                                                      # vide : taux de l'entreprise ; sinon taux propre au produit (0 = exonéré)
        v = data.get("tax_rate"); out["tax_rate"] = None if v in (None, "") else _int(v, "La TVA du produit", 0, 30)
    return out


def _product_create(c, user, actor, data, role, now):
    if not can(role, "products.write"): return _deny(c, actor, "product.create", None, "droit manquant")
    f = _product_fields(data, False)
    pid = str(data.get("id") or uuid.uuid4())
    if not UUID_RE.match(pid): raise OpError("Identifiant de produit invalide.")
    if c.execute("SELECT 1 FROM products WHERE id=?", (pid,)).fetchone(): raise OpError("Ce produit existe déjà.")
    code = _text(data.get("code"), "Le code", 20) or f"P{next_counter(c, 'product_no'):03d}"
    if c.execute("SELECT 1 FROM products WHERE code=?", (code,)).fetchone(): raise OpError("Ce code produit existe déjà.")
    c.execute("INSERT INTO products(id,code,name,category,unit,price,reorder_level,track_stock,tax_rate,version,seq,deleted,created_at,updated_at,created_by,updated_by) VALUES(?,?,?,?,?,?,?,?,?,1,?,0,?,?,?,?)",
              (pid, code, f["name"], f.get("category", ""), f.get("unit", ""), f["price"], f.get("reorder_level", 0), f.get("track_stock", 1), f.get("tax_rate"), next_seq(c), now, now, user["id"], user["id"]))
    r = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    audit.record(c, actor, "product.create", "product", pid, None, {"code": code, "name": r["name"], "price": r["price"]})
    return {"status": "ok", "product": product_view(r)}


def _product_update(c, user, actor, data, role, now):
    if not can(role, "products.write"): return _deny(c, actor, "product.update", None, "droit manquant")
    r = c.execute("SELECT * FROM products WHERE id=? AND deleted=0", (str(data.get("id") or ""),)).fetchone()
    if r is None: return {"status": "invalid", "message": "Produit introuvable."}
    f = _product_fields(data.get("changes") or {}, True)
    changed = {k: v for k, v in f.items() if r[k] != v}
    if int(data.get("base_version") or 0) != r["version"]:
        return _conflict(c, actor, "product", r["id"], product_view(r), f"version attendue {data.get('base_version')}, actuelle {r['version']}")
    if not changed: return {"status": "ok", "product": product_view(r), "unchanged": True}
    c.execute(f"UPDATE products SET {', '.join(k + '=?' for k in changed)}, version=version+1, seq=?, updated_at=?, updated_by=? WHERE id=?", (*changed.values(), next_seq(c), now, user["id"], r["id"]))
    audit.record(c, actor, "product.update", "product", r["id"], {k: r[k] for k in changed}, changed)
    return {"status": "ok", "product": product_view(c.execute("SELECT * FROM products WHERE id=?", (r["id"],)).fetchone())}


def _product_delete(c, user, actor, data, role, now):
    if not can(role, "products.write"): return _deny(c, actor, "product.delete", None, "droit manquant")
    r = c.execute("SELECT * FROM products WHERE id=? AND deleted=0", (str(data.get("id") or ""),)).fetchone()
    if r is None: return {"status": "invalid", "message": "Produit introuvable."}
    if int(data.get("base_version") or 0) != r["version"]:
        return _conflict(c, actor, "product", r["id"], product_view(r), "suppression : produit modifié entre-temps")
    c.execute("UPDATE products SET deleted=1, version=version+1, seq=?, updated_at=?, updated_by=? WHERE id=?", (next_seq(c), now, user["id"], r["id"]))
    audit.record(c, actor, "product.delete", "product", r["id"], {"code": r["code"], "name": r["name"]}, {"deleted": True}, note="suppression logique : les anciennes ventes gardent ce produit")
    return {"status": "ok"}


# --- ventes
def parse_lines(c, role, raw, subject: str = "Une vente"):
    """Valide et chiffre les lignes d'une vente ou d'un devis (prix du catalogue imposé, remise limitée selon le rôle) : (lignes, total)."""
    if not isinstance(raw, list) or not (1 <= len(raw) <= 30): raise OpError(f"{subject} comporte de 1 à 30 lignes.")
    max_disc = 100 if importing() else MAX_DISCOUNT_PCT.get(role, 100)
    lines, total = [], 0
    rate0 = settings_get(c)["tax_rate"]
    for i, ln in enumerate(raw, 1):
        if not isinstance(ln, dict): raise OpError(f"Ligne {i} invalide.")
        qty = _int(ln.get("qty"), f"Ligne {i} : quantité", 1, 10 ** 7)
        disc = _int(ln.get("discount_pct", 0), f"Ligne {i} : remise", 0, 100)
        if disc > max_disc: raise OpError(f"Ligne {i} : remise limitée à {max_disc} % pour votre rôle.")
        pid = ln.get("product_id") or None
        if pid:
            p = c.execute("SELECT * FROM products WHERE id=? AND deleted=0", (pid,)).fetchone()
            if p is None: raise OpError(f"Ligne {i} : produit introuvable.")
            name = p["name"]; tax = p["tax_rate"] if p["tax_rate"] is not None else rate0
            price = _int(ln.get("unit_price", p["price"]), f"Ligne {i} : prix", 0, 10 ** 10) if (can(role, "sales.override_price") or importing()) else p["price"]   # prix du catalogue imposé
        else:
            if not (can(role, "sales.free_line") or importing()): raise OpError(f"Ligne {i} : choisissez un produit du catalogue.")
            name = _text(ln.get("name"), f"Ligne {i} : désignation", 120, True)
            price = _int(ln.get("unit_price"), f"Ligne {i} : prix", 0, 10 ** 10)
            tax = rate0 if ln.get("tax_rate") in (None, "") else _int(ln["tax_rate"], f"Ligne {i} : TVA", 0, 30)
        amount = (qty * price * (100 - disc) + 50) // 100
        lines.append((i, pid, name, qty, price, disc, amount, tax)); total += amount
    return lines, total


def _sale_create(c, user, actor, data, role, now):
    if not can(role, "sales.create"): return _deny(c, actor, "sale.create", None, "droit manquant")
    kind = data.get("kind")
    if kind not in ("cash", "credit"): raise OpError("Type de vente : comptant ou crédit.")
    if kind == "credit" and not can(role, "sales.credit"): return _deny(c, actor, "sale.create", None, "ce rôle n'accorde pas de crédit")
    sid = str(data.get("id") or uuid.uuid4())
    if not UUID_RE.match(sid): raise OpError("Identifiant de vente invalide.")
    if c.execute("SELECT 1 FROM sales WHERE id=?", (sid,)).fetchone(): raise OpError("Cette vente existe déjà.")
    cl = c.execute("SELECT * FROM clients WHERE id=? AND deleted=0", (str(data.get("client_id") or ""),)).fetchone()
    if cl is None or not visible_to(role, user["id"], cl): raise OpError("Client introuvable.")
    sale_date = _date(data.get("sale_date") or today().isoformat(), "Date de la vente")
    d = date.fromisoformat(sale_date)
    if d > today() + timedelta(days=1): raise OpError("La date de la vente ne peut pas être dans le futur.")
    period.ensure_open(c, sale_date)
    if d < today() - timedelta(days=BACKDATE_DAYS) and not can(role, "sales.backdate") and not importing(): raise OpError(f"Antidater de plus de {BACKDATE_DAYS} jours demande l'accord du propriétaire ou du comptable.")
    lines, total = parse_lines(c, role, data.get("lines"))
    from . import warehouses
    deps = [warehouses.active_depot(c, ln.get("depot_id")) for ln in data["lines"]]            # dépôt de sortie de chaque ligne (principal par défaut)
    method = _text(data.get("pay_method"), "Mode de paiement", 30)
    if method and not valid_method(c, method): raise OpError("Mode de paiement inconnu.")
    due = _date(data.get("due_date"), "Échéance", required=False)
    override = bool(data.get("override_credit"))
    if kind == "cash":
        paid, due = total, None
        if total > 0 and not method: raise OpError("Une vente au comptant demande un mode de paiement.")
    else:
        if due is None: raise OpError("Une vente à crédit demande une date d'échéance.")
        if date.fromisoformat(due) < d: raise OpError("L'échéance ne peut pas précéder la vente.")
        paid = _int(data.get("paid_at_sale", 0), "Acompte", 0, total)
        if paid > 0 and not method: raise OpError("Un acompte demande un mode de paiement.")
        owed_after = client_balance(c, cl["id"]) + (total - paid)
        if total - paid > 0:
            limit = cl["credit_limit"]
            problem = "Aucune limite de crédit accordée à ce client." if limit <= 0 else (f"Limite de crédit dépassée : {owed_after} pour une limite de {limit}." if owed_after > limit else None)
            if importing(): problem = None                                  # historique repris tel quel : la limite ne s'applique pas aux ventes passées
            if problem and not (override and can(role, "sales.override_credit")):
                audit.record(c, actor, "sale.create.refused", "sale", sid, note=problem + f" (client {cl['code']}, vente de {total})")
                return {"status": "invalid", "message": problem + (" Demandez l'accord du propriétaire ou du comptable." if not can(role, "sales.override_credit") else " Cochez « Autoriser malgré la limite » pour passer outre.")}
            if problem: audit.record(c, actor, "sale.credit_override", "client", cl["id"], note=problem)
    number = f"FV-{next_counter(c, 'sale_no'):04d}"
    if importing() and re.fullmatch(r"FV-\d{4,8}", str(data.get("number") or "")):               # import : on garde le numéro de facture d'origine
        if c.execute("SELECT 1 FROM sales WHERE number=?", (data["number"],)).fetchone(): raise OpError(f"Le numéro {data['number']} existe déjà.")
        number = data["number"]
        c.execute("UPDATE meta SET value=? WHERE key='sale_no' AND CAST(value AS INTEGER) < ?", (str(int(number[3:])), int(number[3:])))
    delivery_due = _date(data.get("delivery_due"), "Livraison prévue", required=False)
    c.execute("""INSERT INTO sales(id,number,sale_date,client_id,kind,due_date,seller_user_id,total,paid_at_sale,pay_method,note,delivery_due,status,version,seq,created_at,updated_at,created_by,updated_by)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'active',1,?,?,?,?,?)""",
              (sid, number, sale_date, cl["id"], kind, due, user["id"], total, paid, method, _text(data.get("note"), "Note", 300), delivery_due, next_seq(c), now, now, user["id"], user["id"]))
    for ln, dep in zip(lines, deps):
        c.execute("INSERT INTO sale_lines(sale_id,position,product_id,name,qty,unit_price,discount_pct,amount,tax_rate,depot_id) VALUES(?,?,?,?,?,?,?,?,?,?)", (sid, *ln, dep))
    lot_warnings = warehouses.allocate_lots(c, sid, sale_date, lines)
    s = c.execute("SELECT * FROM sales WHERE id=?", (sid,)).fetchone()
    audit.record(c, actor, "sale.create", "sale", sid, None, {"number": number, "client": cl["code"], "kind": kind, "total": total, "paid_at_sale": paid, "due_date": due,
                                                               "lines": [f"{l[3]} × {l[2]}" for l in lines]})
    stock_warnings = []
    if not importing():                                                      # alerte seulement : une vente saisie hors connexion doit pouvoir arriver, et le stock réel peut être en avance sur les saisies
        from . import stock_ops
        levels = stock_ops.stock_levels(c)
        stock_warnings = [f"{l[2]} : stock insuffisant ({levels[l[1]]} après cette vente). Enregistrez l'entrée de stock ou faites un inventaire." for l in lines if l[1] and levels.get(l[1], 0) < 0]
        if stock_warnings: audit.record(c, actor, "sale.stock_warning", "sale", sid, note=" ; ".join(stock_warnings)[:300])
    return {"status": "ok", "sale": sale_view(c, s), "lot_warnings": lot_warnings, "stock_warnings": stock_warnings}


def _load_sale(c, user, sale_id):
    s = c.execute("SELECT * FROM sales WHERE id=?", (str(sale_id or ""),)).fetchone()
    if s is None or not sale_visible(c, user, s): return None
    return s


def _sale_deliver(c, user, actor, data, role, now):
    if not can(role, "sales.deliver"): return _deny(c, actor, "sale.deliver", None, "droit manquant")
    s = _load_sale(c, user, data.get("id"))
    if s is None or s["status"] != "active": return {"status": "invalid", "message": "Vente introuvable."}
    when = _date(data.get("delivered_at") or today().isoformat(), "Date de livraison")
    if int(data.get("base_version") or 0) != s["version"]:
        return _conflict(c, actor, "sale", s["id"], sale_view(c, s), f"version attendue {data.get('base_version')}, actuelle {s['version']}")
    from . import deliveries
    if sum(deliveries.remaining(c, s).values()) > 0:                         # « marquer livrée » = un bon de livraison pour tout ce qui reste à livrer
        deliveries.create_note(c, user, actor, s, when, None, "", "Livraison complète (vente marquée livrée)", now)
    else:
        c.execute("UPDATE sales SET delivered_at=?, version=version+1, seq=?, updated_at=?, updated_by=? WHERE id=?", (when, next_seq(c), now, user["id"], s["id"]))
    audit.record(c, actor, "sale.deliver", "sale", s["id"], {"delivered_at": s["delivered_at"]}, {"delivered_at": when}, note=s["number"])
    return {"status": "ok", "sale": sale_view(c, c.execute("SELECT * FROM sales WHERE id=?", (s["id"],)).fetchone())}


def _sale_cancel(c, user, actor, data, role, now):
    if not can(role, "sales.cancel"): return _deny(c, actor, "sale.cancel", str(data.get("id") or ""), "droit manquant")
    s = _load_sale(c, user, data.get("id"))
    if s is None or s["status"] != "active": return {"status": "invalid", "message": "Vente introuvable ou déjà annulée."}
    period.ensure_open(c, s["sale_date"])
    reason = _text(data.get("reason"), "Le motif", 200, True)
    if len(reason) < 3: raise OpError("Le motif d'annulation est obligatoire.")
    if int(data.get("base_version") or 0) != s["version"]:
        return _conflict(c, actor, "sale", s["id"], sale_view(c, s), "annulation : vente modifiée entre-temps")
    t = sale_totals(c, s)
    if t["payments_total"] or t["credit_notes_total"]:
        return {"status": "invalid", "message": "Cette vente a des paiements ou des avoirs : annulez-les d'abord (ou faites un avoir)."}
    c.execute("UPDATE sales SET status='cancelled', cancel_reason=?, version=version+1, seq=?, updated_at=?, updated_by=? WHERE id=?", (reason, next_seq(c), now, user["id"], s["id"]))
    note = s["number"] + (f" : {s['paid_at_sale']} encaissés à la vente, à rembourser" if s["paid_at_sale"] else "")
    audit.record(c, actor, "sale.cancel", "sale", s["id"], {"status": "active"}, {"status": "cancelled", "reason": reason}, note=note)
    return {"status": "ok", "sale": sale_view(c, c.execute("SELECT * FROM sales WHERE id=?", (s["id"],)).fetchone()), "refund_due": s["paid_at_sale"]}


# --- paiements
def _payment_create(c, user, actor, data, role, now):
    if not can(role, "payments.create"): return _deny(c, actor, "payment.create", None, "droit manquant")
    s = _load_sale(c, user, data.get("sale_id"))
    if s is None or s["status"] != "active": return {"status": "invalid", "message": "Vente introuvable."}
    pid = str(data.get("id") or uuid.uuid4())
    if not UUID_RE.match(pid): raise OpError("Identifiant de paiement invalide.")
    if c.execute("SELECT 1 FROM payments WHERE id=?", (pid,)).fetchone(): raise OpError("Ce paiement existe déjà.")
    amount = _int(data.get("amount"), "Le montant", 1, 10 ** 12)
    method = _text(data.get("method"), "Le mode de paiement", 30, True)
    if not valid_method(c, method): raise OpError("Mode de paiement inconnu.")
    ref = _text(data.get("reference"), "La référence", 60)
    pay_date = _date(data.get("pay_date") or today().isoformat(), "Date du paiement")
    if date.fromisoformat(pay_date) > today() + timedelta(days=1): raise OpError("La date du paiement ne peut pas être dans le futur.")
    period.ensure_open(c, pay_date)
    balance = sale_totals(c, s)["balance"]
    if amount > balance:
        return {"status": "invalid", "message": f"Le paiement dépasse le reste à payer ({balance})."}
    if ref and c.execute("SELECT 1 FROM payments WHERE sale_id=? AND reference=? AND method=? AND amount=? AND status='active'", (s["id"], ref, method, amount)).fetchone():
        return {"status": "invalid", "message": "Ce paiement (même référence, même montant) est déjà enregistré."}
    c.execute("INSERT INTO payments(id,sale_id,client_id,pay_date,amount,method,reference,status,version,seq,created_at,updated_at,created_by,updated_by) VALUES(?,?,?,?,?,?,?,'active',1,?,?,?,?,?)",
              (pid, s["id"], s["client_id"], pay_date, amount, method, ref, next_seq(c), now, now, user["id"], user["id"]))
    p = c.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
    audit.record(c, actor, "payment.create", "payment", pid, None, {"sale": s["number"], "amount": amount, "method": method, "reference": ref, "date": pay_date})
    return {"status": "ok", "payment": payment_view(p), "sale": sale_view(c, s)}


def _payment_cancel(c, user, actor, data, role, now):
    if not can(role, "payments.cancel"): return _deny(c, actor, "payment.cancel", str(data.get("id") or ""), "droit manquant")
    p = c.execute("SELECT * FROM payments WHERE id=?", (str(data.get("id") or ""),)).fetchone()
    s = c.execute("SELECT * FROM sales WHERE id=?", (p["sale_id"],)).fetchone() if p else None
    if p is None or p["status"] != "active" or not sale_visible(c, user, s): return {"status": "invalid", "message": "Paiement introuvable ou déjà annulé."}
    period.ensure_open(c, p["pay_date"])
    reason = _text(data.get("reason"), "Le motif", 200, True)
    if len(reason) < 3: raise OpError("Le motif d'annulation est obligatoire.")
    if int(data.get("base_version") or 0) != p["version"]:
        return _conflict(c, actor, "payment", p["id"], payment_view(p), "annulation : paiement modifié entre-temps")
    c.execute("UPDATE payments SET status='cancelled', cancel_reason=?, version=version+1, seq=?, updated_at=?, updated_by=? WHERE id=?", (reason, next_seq(c), now, user["id"], p["id"]))
    audit.record(c, actor, "payment.cancel", "payment", p["id"], {"status": "active", "amount": p["amount"]}, {"status": "cancelled", "reason": reason}, note=s["number"])
    return {"status": "ok", "payment": payment_view(c.execute("SELECT * FROM payments WHERE id=?", (p["id"],)).fetchone())}


# --- avoirs
def _note_create(c, user, actor, data, role, now):
    if not can(role, "creditnotes.create"): return _deny(c, actor, "creditnote.create", None, "droit manquant")
    s = _load_sale(c, user, data.get("sale_id"))
    if s is None or s["status"] != "active": return {"status": "invalid", "message": "Vente introuvable."}
    nid = str(data.get("id") or uuid.uuid4())
    if not UUID_RE.match(nid): raise OpError("Identifiant d'avoir invalide.")
    period.ensure_open(c, today().isoformat())
    if c.execute("SELECT 1 FROM credit_notes WHERE id=?", (nid,)).fetchone(): raise OpError("Cet avoir existe déjà.")
    amount = _int(data.get("amount"), "Le montant", 1, 10 ** 12)
    settlement = data.get("settlement")
    if settlement not in ("deduct", "refund"): raise OpError("Règlement de l'avoir : déduit du solde ou remboursé.")
    reason = _text(data.get("reason"), "Le motif", 200, True)
    t = sale_totals(c, s)
    if amount > t["net_total"]: return {"status": "invalid", "message": f"L'avoir dépasse le montant restant de la vente ({t['net_total']})."}
    if settlement == "deduct" and amount > t["balance"]: return {"status": "invalid", "message": f"Un avoir « déduit du solde » ne peut pas dépasser le reste à payer ({t['balance']}). Utilisez « remboursé »."}
    if settlement == "refund" and amount > t["paid_total"]: return {"status": "invalid", "message": f"On ne peut rembourser que ce qui a été payé ({t['paid_total']})."}
    from . import returns, warehouses
    plan = returns.plan(c, s, data.get("returns"), _int, warehouses.active_depot)          # marchandise rendue : tout est vérifié AVANT d'écrire quoi que ce soit
    number = f"AV-{next_counter(c, 'note_no'):04d}"
    c.execute("INSERT INTO credit_notes(id,number,sale_id,client_id,note_date,amount,reason,settlement,status,version,seq,created_at,updated_at,created_by,updated_by) VALUES(?,?,?,?,?,?,?,?, 'active',1,?,?,?,?,?)",
              (nid, number, s["id"], s["client_id"], today().isoformat(), amount, reason, settlement, next_seq(c), now, now, user["id"], user["id"]))
    returns.insert_lines(c, nid, plan)
    n = c.execute("SELECT * FROM credit_notes WHERE id=?", (nid,)).fetchone()
    after = {"number": number, "sale": s["number"], "amount": amount, "settlement": settlement, "reason": reason}
    if plan: after["marchandise rendue en stock"] = returns.summary(c, nid)
    audit.record(c, actor, "creditnote.create", "creditnote", nid, None, after)
    return {"status": "ok", "credit_note": note_view(n, c), "sale": sale_view(c, s)}


# --- réglages de l'entreprise (en-tête des factures)
def _settings_update(c, user, actor, data, role, now):
    if not can(role, "settings.manage"): return _deny(c, actor, "settings.update", None, "droit manquant")
    before = settings_get(c); new = dict(before)
    for k, n in SETTINGS_FIELDS.items():
        if k in data: new[k] = _text(data[k], k, n)
    if "doc_lang" in data:                                                      # langue des documents imprimés : vide ou « auto » = langue de l'appareil de la personne qui imprime
        from . import i18n
        if new["doc_lang"] not in ("", "auto") and new["doc_lang"] not in {l["code"] for l in i18n.languages()}: raise OpError("Langue des documents inconnue.")
    if "tax_rate" in data:
        new["tax_rate"] = _int(data["tax_rate"], "Taux de taxe", 0, 30)
        if new["tax_rate"] != before["tax_rate"] and period.locked_through(c):
            return {"status": "invalid", "message": "La comptabilité est verrouillée (ou un exercice est clôturé) : changer le taux de taxe modifierait les écritures déjà arrêtées."}
    diff_b = {k: before[k] for k in new if new[k] != before[k]}; diff_a = {k: new[k] for k in diff_b}
    if diff_a:
        c.execute("INSERT INTO meta(key,value) VALUES('company',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(new, ensure_ascii=False),))
        audit.record(c, actor, "settings.update", "settings", "company", diff_b, diff_a)
    return {"status": "ok", "settings": new}
