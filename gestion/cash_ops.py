"""Dépenses, budgets, mouvements de caisse et clôture de caisse.

La caisse attendue n'est jamais saisie : elle se calcule.
    attendue = dernière caisse comptée + espèces encaissées (ventes, acomptes, paiements clients) − espèces sorties (dépenses, achats et règlements
               fournisseurs en espèces) ± mouvements (dépôt en banque, retrait, apport ou prélèvement du patron, remboursement client)
On compte les billets, on saisit le total : l'écart apparaît, doit être expliqué, et la journée est « clôturée ».
Seuls les paiements en espèces comptent dans la caisse ; Orange Money, MTN MoMo, virements et chèques n'y entrent pas.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import date, timedelta

from . import audit, period, security
from .ops import UUID_RE, OpError, _deny, next_seq
from .methods import valid_method
from .permissions import CASHIER_BACKDATE_DAYS, CASHIER_EXPENSE_LIMIT, METHODS, can
from .sales_ops import _conflict, _date, _int, _text, next_counter, today

# Catégories de dépenses : « compte » = suggestion du plan SYSCOHADA, À CONFIRMER avec un expert-comptable.
CATEGORIES = [
    {"key": "Loyer et charges locatives", "account": "622", "nature": "Fixe"}, {"key": "Électricité, eau, carburant", "account": "605", "nature": "Variable"},
    {"key": "Transport et livraison", "account": "612", "nature": "Variable"}, {"key": "Salaires et rémunérations", "account": "661", "nature": "Fixe"},
    {"key": "Charges sociales (CNSS)", "account": "664", "nature": "Fixe"}, {"key": "Entretien et réparations", "account": "624", "nature": "Variable"},
    {"key": "Assurances", "account": "625", "nature": "Fixe"}, {"key": "Publicité et marketing", "account": "627", "nature": "Variable"},
    {"key": "Téléphone et internet", "account": "628", "nature": "Fixe"}, {"key": "Frais bancaires", "account": "631", "nature": "Fixe"},
    {"key": "Honoraires (comptable, conseil)", "account": "632", "nature": "Fixe"}, {"key": "Impôts et taxes", "account": "641", "nature": "Fixe"},
    {"key": "Intérêts d'emprunt", "account": "671", "nature": "Fixe"}, {"key": "Fournitures et petits achats", "account": "604", "nature": "Variable"},
    {"key": "Autres charges", "account": "658", "nature": "Variable"},
    {"key": "Paie : salaires nets versés", "account": "422", "nature": "Fixe"},      # réservée au module Paie (solde le personnel dû, 422) : ne pas l'utiliser à la main
    {"key": "Paie : avances sur salaire", "account": "421", "nature": "Fixe"},       # réservée au module Paie (avance au personnel, 421, reprise sur un bulletin)
]
CAT = {c["key"]: c for c in CATEGORIES}
MOVE_KINDS = {"bank_deposit": -1, "bank_withdrawal": 1, "owner_in": 1, "owner_out": -1, "customer_refund": -1}
CASHIER_MOVES = {"bank_deposit", "customer_refund"}


# ---------------------------------------------------------------- vues
def categories(c: sqlite3.Connection) -> dict:
    """Catégories de dépenses : celles de base (fixes) + celles que l'entreprise a ajoutées."""
    out = {k: {**v, "custom": False, "active": True, "version": 0} for k, v in CAT.items()}
    for r in c.execute("SELECT * FROM expense_categories ORDER BY key"):
        out[r["key"]] = {"key": r["key"], "account": r["account"], "nature": r["nature"], "custom": True, "active": bool(r["active"]), "version": r["version"]}
    return out


def expense_view(e: sqlite3.Row, cats: dict | None = None) -> dict:
    d = {k: e[k] for k in ("id", "number", "expense_date", "category", "payee", "description", "amount", "method", "reference", "status", "cancel_reason", "version", "seq", "created_at", "created_by")}
    cats = cats or CAT; d["nature"] = cats.get(e["category"], {}).get("nature", "Variable"); d["account"] = cats.get(e["category"], {}).get("account", ""); return d


def move_view(m: sqlite3.Row) -> dict:
    return {k: m[k] for k in ("id", "move_date", "kind", "amount", "note", "account", "status", "cancel_reason", "version", "seq", "created_at")}


def closing_view(x: sqlite3.Row) -> dict:
    return {k: x[k] for k in ("id", "close_date", "opening", "cash_in", "cash_out", "expected", "counted", "difference", "note", "status", "cancel_reason", "version", "seq", "created_at", "created_by")}


# ---------------------------------------------------------------- calcul de la caisse
def _sum(c, sql, args) -> int:
    return c.execute(sql, args).fetchone()[0] or 0


def flows(c: sqlite3.Connection, after: str | None, upto: str) -> dict:
    """Espèces entrées et sorties pour les dates dans l'intervalle ]after, upto] (after=None : depuis toujours)."""
    lo = after or "0000-00-00"
    sales = _sum(c, "SELECT SUM(paid_at_sale) FROM sales WHERE status='active' AND pay_method='Espèces' AND sale_date>? AND sale_date<=?", (lo, upto))
    pays = _sum(c, "SELECT SUM(p.amount) FROM payments p JOIN sales s ON s.id=p.sale_id WHERE p.status='active' AND s.status='active' AND p.method='Espèces' AND p.pay_date>? AND p.pay_date<=?", (lo, upto))
    exp = _sum(c, "SELECT SUM(amount) FROM expenses WHERE status='active' AND method='Espèces' AND expense_date>? AND expense_date<=?", (lo, upto))
    buy = _sum(c, "SELECT SUM(paid_at_purchase) FROM purchases WHERE status='active' AND pay_method='Espèces' AND purchase_date>? AND purchase_date<=?", (lo, upto))
    spay = _sum(c, "SELECT SUM(p.amount) FROM supplier_payments p JOIN purchases u ON u.id=p.purchase_id WHERE p.status='active' AND u.status='active' AND p.method='Espèces' AND p.pay_date>? AND p.pay_date<=?", (lo, upto))
    sr = _sum(c, "SELECT SUM(amount) FROM supplier_notes WHERE status='active' AND settlement='refund' AND refund_method='Espèces' AND note_date>? AND note_date<=?", (lo, upto))
    fa = _sum(c, "SELECT SUM(cost) FROM fixed_assets WHERE status<>'cancelled' AND origin='purchase' AND pay_mode='Espèces' AND acquisition_date>? AND acquisition_date<=?", (lo, upto))
    fs = _sum(c, "SELECT SUM(disposal_price) FROM fixed_assets WHERE status='disposed' AND disposal_method='Espèces' AND disposal_date>? AND disposal_date<=?", (lo, upto))
    mv = {k: _sum(c, "SELECT SUM(amount) FROM cash_movements WHERE status='active' AND kind=? AND move_date>? AND move_date<=?", (k, lo, upto)) for k in MOVE_KINDS}
    ins = {"sales": sales, "payments": pays, "bank_withdrawal": mv["bank_withdrawal"], "owner_in": mv["owner_in"], "asset_sales": fs, "supplier_refunds": sr}
    outs = {"expenses": exp, "purchases": buy, "supplier_payments": spay, "assets": fa, "bank_deposit": mv["bank_deposit"], "owner_out": mv["owner_out"], "customer_refund": mv["customer_refund"]}
    return {"in": ins, "out": outs, "in_total": sum(ins.values()), "out_total": sum(outs.values())}


def last_closing(c: sqlite3.Connection, before: str | None = None):
    if before:
        return c.execute("SELECT * FROM cash_closings WHERE status='active' AND close_date<? ORDER BY close_date DESC LIMIT 1", (before,)).fetchone()
    return c.execute("SELECT * FROM cash_closings WHERE status='active' ORDER BY close_date DESC LIMIT 1").fetchone()


def status(c: sqlite3.Connection) -> dict:
    last = last_closing(c)
    upto = today().isoformat()
    f = flows(c, last["close_date"] if last else None, upto)
    opening = last["counted"] if last else None
    return {"last_close_date": last["close_date"] if last else None, "opening": opening, "needs_opening": last is None, "to_date": upto, **f,
            "expected": (opening or 0) + f["in_total"] - f["out_total"]}


def closings_with_drift(c: sqlite3.Connection, since: int) -> list[dict]:
    out = []
    for x in c.execute("SELECT * FROM cash_closings WHERE seq>? ORDER BY close_date", (since,)):
        v = closing_view(x)
        if x["status"] == "active":
            prev = last_closing(c, x["close_date"])
            f = flows(c, prev["close_date"] if prev else None, x["close_date"])
            now_expected = x["opening"] + f["in_total"] - f["out_total"]
            v["drift"] = now_expected - x["expected"]                         # ≠ 0 : une opération datée de cette période a été ajoutée, annulée ou modifiée après la clôture
        out.append(v)
    return out


# ---------------------------------------------------------------- budgets et rentabilité
def budgets_get(c: sqlite3.Connection) -> dict:
    import json
    r = c.execute("SELECT value FROM meta WHERE key='budgets'").fetchone()
    return json.loads(r["value"]) if r else {}


def rentability(c: sqlite3.Connection) -> dict:
    """Seuil de rentabilité du mois : charges fixes ÷ taux de marge. Le taux de marge vient du coût moyen d'achat (estimation)."""
    month = today().isoformat()[:7]
    sales = _sum(c, "SELECT SUM(total) FROM sales WHERE status='active' AND sale_date LIKE ?", (month + "%",))
    cost = 0
    for r in c.execute("""SELECT l.product_id pid, SUM(l.qty) q FROM sale_lines l JOIN sales s ON s.id=l.sale_id WHERE s.status='active' AND s.sale_date LIKE ? AND l.product_id IS NOT NULL GROUP BY l.product_id""", (month + "%",)):
        a = c.execute("SELECT SUM(l.amount) amt, SUM(l.qty) q FROM purchase_lines l JOIN purchases u ON u.id=l.purchase_id WHERE u.status='active' AND l.product_id=?", (r["pid"],)).fetchone()
        if a and a["q"]: cost += round(r["q"] * a["amt"] / a["q"])
    fixed = variable = 0; cats = categories(c)
    for e in c.execute("SELECT category, SUM(amount) s FROM expenses WHERE status='active' AND expense_date LIKE ? GROUP BY category", (month + "%",)):
        if cats.get(e["category"], {}).get("nature") == "Fixe": fixed += e["s"]
        else: variable += e["s"]
    margin = (sales - cost) / sales if sales else None
    breakeven = round(fixed / margin) if margin and margin > 0 else None
    return {"month": month, "sales": sales, "estimated_cost": cost, "margin_rate": None if margin is None else round(margin, 4), "fixed": fixed, "variable": variable,
            "breakeven": breakeven, "reached": None if not breakeven else round(sales / breakeven, 3), "result": (sales - cost - fixed - variable)}


def pull_extra(c: sqlite3.Connection, user: sqlite3.Row, since: int) -> dict:
    role, uid = user["role"], user["id"]
    out = {"expenses": [], "cash_movements": [], "cash_closings": [], "cash": None, "budgets": {}, "expense_categories": list(categories(c).values()), "rentability": None}
    if can(role, "expenses.read_all"):
        cats = categories(c); out["expenses"] = [expense_view(e, cats) for e in c.execute("SELECT * FROM expenses WHERE seq>? ORDER BY seq", (since,))]
    elif can(role, "expenses.read_own"):
        cats = categories(c); out["expenses"] = [expense_view(e, cats) for e in c.execute("SELECT * FROM expenses WHERE seq>? AND created_by=? ORDER BY seq", (since, uid))]
    if can(role, "cash.read"):
        out["cash_movements"] = [move_view(m) for m in c.execute("SELECT * FROM cash_movements WHERE seq>? ORDER BY seq", (since,))]
        out["cash_closings"] = closings_with_drift(c, since)
        out["cash"] = status(c)
    if can(role, "expenses.read_all"):
        out["budgets"] = budgets_get(c)
    if can(role, "reports.read"):
        out["rentability"] = rentability(c)
    return out


# ---------------------------------------------------------------- opérations
def _closed_through(c) -> str | None:
    last = last_closing(c)
    return last["close_date"] if last else None


def _expense_create(c, user, actor, data, role, now):
    if not can(role, "expenses.create"): return _deny(c, actor, "expense.create", None, "droit manquant")
    eid = str(data.get("id") or uuid.uuid4())
    if not UUID_RE.match(eid): raise OpError("Identifiant de dépense invalide.")
    if c.execute("SELECT 1 FROM expenses WHERE id=?", (eid,)).fetchone(): raise OpError("Cette dépense existe déjà.")
    cat = data.get("category")
    cats = categories(c)
    if cat not in cats or not cats[cat]["active"]: raise OpError("Catégorie de dépense inconnue ou désactivée.")
    amount = _int(data.get("amount"), "Le montant", 1, 10 ** 12)
    method = _text(data.get("method"), "Le mode de paiement", 30, True)
    if not valid_method(c, method): raise OpError("Mode de paiement inconnu.")
    payee, desc, ref = _text(data.get("payee"), "Le bénéficiaire", 120), _text(data.get("description"), "La description", 200), _text(data.get("reference"), "La référence", 60)
    if not payee and not desc: raise OpError("Indiquez le bénéficiaire ou une description.")
    edate = _date(data.get("expense_date") or today().isoformat(), "Date de la dépense")
    d = date.fromisoformat(edate)
    if d > today() + timedelta(days=1): raise OpError("La date de la dépense ne peut pas être dans le futur.")
    period.ensure_open(c, edate)
    if role == "cashier":
        if amount > CASHIER_EXPENSE_LIMIT: return {"status": "invalid", "message": f"Au-delà de {CASHIER_EXPENSE_LIMIT} par dépense, c'est le comptable ou le propriétaire qui l'enregistre."}
        if d < today() - timedelta(days=CASHIER_BACKDATE_DAYS): raise OpError(f"Un caissier ne peut pas antidater de plus de {CASHIER_BACKDATE_DAYS} jours.")
        closed = _closed_through(c)
        if closed and edate <= closed: raise OpError(f"La caisse est clôturée jusqu'au {closed} : demandez au comptable d'enregistrer cette dépense.")
    closed = _closed_through(c)
    num = f"DP-{next_counter(c, 'expense_no'):04d}"
    c.execute("INSERT INTO expenses(id,number,expense_date,category,payee,description,amount,method,reference,status,version,seq,created_at,updated_at,created_by,updated_by) VALUES(?,?,?,?,?,?,?,?,?,'active',1,?,?,?,?,?)",
              (eid, num, edate, cat, payee, desc, amount, method, ref, next_seq(c), now, now, user["id"], user["id"]))
    e = c.execute("SELECT * FROM expenses WHERE id=?", (eid,)).fetchone()
    note = f"ATTENTION : période de caisse déjà clôturée (jusqu'au {closed})" if closed and edate <= closed and method == "Espèces" else None
    audit.record(c, actor, "expense.create", "expense", eid, None, {"number": num, "category": cat, "amount": amount, "method": method, "payee": payee, "description": desc, "date": edate}, note=note)
    return {"status": "ok", "expense": expense_view(e, cats), "closed_period_warning": bool(note)}


def _expense_cancel(c, user, actor, data, role, now):
    if not can(role, "expenses.cancel"): return _deny(c, actor, "expense.cancel", str(data.get("id") or ""), "droit manquant")
    e = c.execute("SELECT * FROM expenses WHERE id=? AND status='active'", (str(data.get("id") or ""),)).fetchone()
    if e is None: return {"status": "invalid", "message": "Dépense introuvable ou déjà annulée."}
    period.ensure_open(c, e["expense_date"])
    reason = _text(data.get("reason"), "Le motif", 200, True)
    if len(reason) < 3: raise OpError("Le motif d'annulation est obligatoire.")
    from .payroll import check_advance_expense_cancel
    check_advance_expense_cancel(c, e["id"])
    if int(data.get("base_version") or 0) != e["version"]:
        return _conflict(c, actor, "expense", e["id"], expense_view(e, categories(c)), "annulation : dépense modifiée entre-temps")
    c.execute("UPDATE expenses SET status='cancelled', cancel_reason=?, version=version+1, seq=?, updated_at=?, updated_by=? WHERE id=?", (reason, next_seq(c), now, user["id"], e["id"]))
    closed = _closed_through(c)
    note = e["number"] + (f" : ATTENTION, période de caisse clôturée jusqu'au {closed}" if closed and e["expense_date"] <= closed and e["method"] == "Espèces" else "")
    audit.record(c, actor, "expense.cancel", "expense", e["id"], {"status": "active", "amount": e["amount"]}, {"status": "cancelled", "reason": reason}, note=note)
    return {"status": "ok", "expense": expense_view(c.execute("SELECT * FROM expenses WHERE id=?", (e["id"],)).fetchone(), categories(c))}


def _expcat_create(c, user, actor, data, role, now):
    import re
    from . import period
    if not can(role, "budgets.set"): return _deny(c, actor, "expcat.create", None, "droit manquant")
    key = _text(data.get("key"), "Le nom de la catégorie", 50, True)
    if len(key) < 3: raise OpError("Le nom de la catégorie doit faire au moins 3 caractères.")
    if key.lower() in {k.lower() for k in categories(c)}: raise OpError("Une catégorie porte déjà ce nom.")
    account = str(data.get("account") or "")
    if not re.fullmatch(r"6[0-7]\d{1,6}", account): raise OpError("Compte de charge : un compte de la classe 6 (hors dotations et provisions).")
    if not c.execute("SELECT 1 FROM accounts WHERE code=?", (account,)).fetchone(): raise OpError(f"Le compte {account} n'existe pas dans le plan comptable (ajoutez-le d'abord dans Compta > Réglages).")
    nature = data.get("nature")
    if nature not in ("Fixe", "Variable"): raise OpError("Nature : fixe ou variable.")
    c.execute("INSERT INTO expense_categories(key,account,nature,active,version,seq,created_at) VALUES(?,?,?,1,1,?,?)", (key, account, nature, next_seq(c), now))
    audit.record(c, actor, "expcat.create", "expense_category", key, None, {"nom": key, "compte": account, "nature": nature})
    return {"status": "ok", "category": categories(c)[key]}


def _expcat_update(c, user, actor, data, role, now):
    from . import period
    if not can(role, "budgets.set"): return _deny(c, actor, "expcat.update", str(data.get("key") or ""), "droit manquant")
    r = c.execute("SELECT * FROM expense_categories WHERE key=?", (str(data.get("key") or ""),)).fetchone()
    if r is None: return {"status": "invalid", "message": "Catégorie personnalisée introuvable (les catégories de base ne se modifient pas)."}
    if int(data.get("base_version") or 0) != r["version"]: return _conflict(c, actor, "expense_category", r["key"], categories(c)[r["key"]], "catégorie modifiée entre-temps")
    ch = data.get("changes") or {}; new = {}
    if "nature" in ch:
        if ch["nature"] not in ("Fixe", "Variable"): raise OpError("Nature : fixe ou variable.")
        new["nature"] = ch["nature"]
    if "active" in ch: new["active"] = 1 if ch["active"] else 0
    if "account" in ch:
        import re
        acc = str(ch["account"] or "")
        if not re.fullmatch(r"6[0-7]\d{1,6}", acc) or not c.execute("SELECT 1 FROM accounts WHERE code=?", (acc,)).fetchone(): raise OpError("Compte de charge inconnu (classe 6, hors dotations et provisions).")
        lock = period.locked_through(c)
        if acc != r["account"] and lock and c.execute("SELECT 1 FROM expenses WHERE category=? AND status='active' AND expense_date<=? LIMIT 1", (r["key"], lock)).fetchone():
            raise OpError(f"Des dépenses de cette catégorie sont dans une période verrouillée (jusqu'au {lock}) : changer son compte comptable modifierait des écritures déjà arrêtées.")
        new["account"] = acc
    new = {k: v for k, v in new.items() if r[k] != v}
    if not new: return {"status": "ok", "category": categories(c)[r["key"]], "unchanged": True}
    c.execute(f"UPDATE expense_categories SET {', '.join(k + '=?' for k in new)}, version=version+1, seq=? WHERE key=?", (*new.values(), next_seq(c), r["key"]))
    audit.record(c, actor, "expcat.update", "expense_category", r["key"], {k: r[k] for k in new}, new, note="le compte d'une catégorie s'applique à toutes ses dépenses, passées comprises" if "account" in new else None)
    return {"status": "ok", "category": categories(c)[r["key"]]}


def _budget_set(c, user, actor, data, role, now):
    import json
    if not can(role, "budgets.set"): return _deny(c, actor, "budget.set", None, "droit manquant")
    cat = data.get("category")
    if cat not in categories(c): raise OpError("Catégorie inconnue.")
    amount = _int(data.get("amount", 0), "Le budget mensuel", 0, 10 ** 12)
    b = budgets_get(c); before = b.get(cat, 0)
    if amount == before: return {"status": "ok", "budgets": b, "unchanged": True}
    b[cat] = amount
    c.execute("INSERT INTO meta(key,value) VALUES('budgets',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(b, ensure_ascii=False),))
    audit.record(c, actor, "budget.set", "budget", cat, {"mensuel": before}, {"mensuel": amount})
    return {"status": "ok", "budgets": b}


def _cash_move(c, user, actor, data, role, now):
    if not can(role, "cash.move"): return _deny(c, actor, "cash.move", None, "droit manquant")
    kind = data.get("kind")
    if kind not in MOVE_KINDS: raise OpError("Type de mouvement inconnu.")
    if role == "cashier" and kind not in CASHIER_MOVES: return _deny(c, actor, "cash.move", None, f"un caissier ne peut pas faire « {kind} »")
    mid = str(data.get("id") or uuid.uuid4())
    if not UUID_RE.match(mid): raise OpError("Identifiant de mouvement invalide.")
    if c.execute("SELECT 1 FROM cash_movements WHERE id=?", (mid,)).fetchone(): raise OpError("Ce mouvement existe déjà.")
    account = "bank"
    if kind in ("bank_deposit", "bank_withdrawal") and data.get("account"):
        from . import bank_ops
        acs = bank_ops.accounts(c); account = str(data["account"])
        if account not in acs or acs[account]["kind"] != "bank" or not acs[account]["active"]: raise OpError("Choisissez un compte bancaire actif.")
    amount = _int(data.get("amount"), "Le montant", 1, 10 ** 12)
    note = _text(data.get("note"), "La note", 200)
    if len(note) < 3: raise OpError("Expliquez le mouvement dans la note (n° de bordereau, nom du client…).")
    mdate = _date(data.get("move_date") or today().isoformat(), "Date du mouvement")
    d = date.fromisoformat(mdate)
    if d > today() + timedelta(days=1): raise OpError("La date du mouvement ne peut pas être dans le futur.")
    period.ensure_open(c, mdate)
    closed = _closed_through(c)
    if role == "cashier":
        if d < today() - timedelta(days=CASHIER_BACKDATE_DAYS): raise OpError(f"Un caissier ne peut pas antidater de plus de {CASHIER_BACKDATE_DAYS} jours.")
        if closed and mdate <= closed: raise OpError(f"La caisse est clôturée jusqu'au {closed}.")
    if MOVE_KINDS[kind] < 0:
        st = status(c)
        if amount > st["expected"] and not st["needs_opening"]:
            return {"status": "invalid", "message": f"La caisse n'a que {st['expected']} en espèces : on ne peut pas en sortir {amount}."}
    c.execute("INSERT INTO cash_movements(id,move_date,kind,amount,note,account,status,version,seq,created_at,updated_at,created_by,updated_by) VALUES(?,?,?,?,?,?,'active',1,?,?,?,?,?)",
              (mid, mdate, kind, amount, note, account, next_seq(c), now, now, user["id"], user["id"]))
    m = c.execute("SELECT * FROM cash_movements WHERE id=?", (mid,)).fetchone()
    audit.record(c, actor, "cash.move", "cash_movement", mid, None, {"kind": kind, "amount": amount, "date": mdate, "note": note, "compte": account},
                 note=f"ATTENTION : période de caisse déjà clôturée (jusqu'au {closed})" if closed and mdate <= closed else None)
    return {"status": "ok", "movement": move_view(m)}


def _cash_move_cancel(c, user, actor, data, role, now):
    if not can(role, "cash.cancel"): return _deny(c, actor, "cash.move_cancel", str(data.get("id") or ""), "droit manquant")
    m = c.execute("SELECT * FROM cash_movements WHERE id=? AND status='active'", (str(data.get("id") or ""),)).fetchone()
    if m is None: return {"status": "invalid", "message": "Mouvement introuvable ou déjà annulé."}
    period.ensure_open(c, m["move_date"])
    reason = _text(data.get("reason"), "Le motif", 200, True)
    if len(reason) < 3: raise OpError("Le motif d'annulation est obligatoire.")
    if int(data.get("base_version") or 0) != m["version"]:
        return _conflict(c, actor, "cash_movement", m["id"], move_view(m), "annulation : mouvement modifié entre-temps")
    c.execute("UPDATE cash_movements SET status='cancelled', cancel_reason=?, version=version+1, seq=?, updated_at=?, updated_by=? WHERE id=?", (reason, next_seq(c), now, user["id"], m["id"]))
    audit.record(c, actor, "cash.move_cancel", "cash_movement", m["id"], {"status": "active", "amount": m["amount"]}, {"status": "cancelled", "reason": reason})
    return {"status": "ok"}


def _cash_close(c, user, actor, data, role, now):
    if not can(role, "cash.close"): return _deny(c, actor, "cash.close", None, "droit manquant")
    cid = str(data.get("id") or uuid.uuid4())
    if not UUID_RE.match(cid): raise OpError("Identifiant de clôture invalide.")
    if c.execute("SELECT 1 FROM cash_closings WHERE id=?", (cid,)).fetchone(): raise OpError("Cette clôture existe déjà.")
    cdate = _date(data.get("close_date") or today().isoformat(), "Date de clôture")
    if date.fromisoformat(cdate) > today(): raise OpError("On ne clôture pas une journée à venir.")
    last = last_closing(c)
    if last and cdate <= last["close_date"]: return {"status": "invalid", "message": f"La caisse est déjà clôturée au {last['close_date']}."}
    counted = _int(data.get("counted"), "Les espèces comptées", 0, 10 ** 13)
    if last: opening = last["counted"]
    else:
        if data.get("opening") in (None, ""): return {"status": "invalid", "message": "Première clôture : indiquez les espèces qui étaient en caisse au départ (solde d'ouverture)."}
        opening = _int(data.get("opening"), "Le solde d'ouverture", 0, 10 ** 13)
    f = flows(c, last["close_date"] if last else None, cdate)
    expected = opening + f["in_total"] - f["out_total"]
    diff = counted - expected
    note = _text(data.get("note"), "La note", 200)
    if diff != 0 and len(note) < 3: return {"status": "invalid", "message": f"Écart de {diff} entre la caisse attendue ({expected}) et le comptage ({counted}) : expliquez-le dans la note."}
    c.execute("INSERT INTO cash_closings(id,close_date,opening,cash_in,cash_out,expected,counted,difference,note,status,version,seq,created_at,updated_at,created_by,updated_by) VALUES(?,?,?,?,?,?,?,?,?,'active',1,?,?,?,?,?)",
              (cid, cdate, opening, f["in_total"], f["out_total"], expected, counted, diff, note, next_seq(c), now, now, user["id"], user["id"]))
    x = c.execute("SELECT * FROM cash_closings WHERE id=?", (cid,)).fetchone()
    audit.record(c, actor, "cash.close", "cash_closing", cid, None, {"date": cdate, "attendu": expected, "compté": counted, "écart": diff}, note=("ÉCART : " + note) if diff else None)
    return {"status": "ok", "closing": closing_view(x), "difference": diff}


def _cash_cancel_close(c, user, actor, data, role, now):
    if not can(role, "cash.reopen"): return _deny(c, actor, "cash.reopen", str(data.get("id") or ""), "réservé au propriétaire")
    x = c.execute("SELECT * FROM cash_closings WHERE id=? AND status='active'", (str(data.get("id") or ""),)).fetchone()
    if x is None: return {"status": "invalid", "message": "Clôture introuvable ou déjà annulée."}
    if last_closing(c)["id"] != x["id"]: return {"status": "invalid", "message": "Seule la dernière clôture peut être rouverte."}
    reason = _text(data.get("reason"), "Le motif", 200, True)
    if len(reason) < 3: raise OpError("Le motif est obligatoire.")
    if int(data.get("base_version") or 0) != x["version"]:
        return _conflict(c, actor, "cash_closing", x["id"], closing_view(x), "réouverture : clôture modifiée entre-temps")
    c.execute("UPDATE cash_closings SET status='cancelled', cancel_reason=?, version=version+1, seq=?, updated_at=?, updated_by=? WHERE id=?", (reason, next_seq(c), now, user["id"], x["id"]))
    audit.record(c, actor, "cash.reopen", "cash_closing", x["id"], {"status": "active", "date": x["close_date"], "compté": x["counted"]}, {"status": "cancelled", "reason": reason})
    return {"status": "ok"}


HANDLERS = {"expcat.create": _expcat_create, "expcat.update": _expcat_update, "expense.create": _expense_create, "expense.cancel": _expense_cancel, "budget.set": _budget_set, "cash.move": _cash_move, "cash.move_cancel": _cash_move_cancel,
            "cash.close": _cash_close, "cash.cancel_close": _cash_cancel_close}
