"""Rapprochement bancaire et mobile money : relevé de la banque (ou de l'opérateur) contre les opérations enregistrées dans l'application.

Principe : chaque ligne du relevé doit correspondre à une ou plusieurs opérations du livre (vente encaissée, paiement client, dépense, achat,
règlement fournisseur, dépôt ou retrait en banque). Ce qui est dans le livre mais pas encore à la banque (chèque émis non débité, dépôt en transit)
et ce qui est à la banque mais pas dans le livre (frais bancaires, virement inconnu) sont les « éléments de rapprochement ».
    écart = (solde du relevé − lignes du relevé non pointées) − (solde du livre − opérations du livre non pointées)
Un rapprochement est équilibré quand l'écart est nul. Les espèces ne sont pas concernées (voir Caisse).
"""
from __future__ import annotations

import hashlib
import sqlite3
import uuid
from datetime import date, timedelta

from . import audit
from .ops import UUID_RE, OpError, _deny, next_seq
from .permissions import can
from .sales_ops import _conflict, _date, _int, _text, today

ACCOUNTS = {
    "bank": {"label": "Banque", "methods": ["Virement bancaire", "Chèque"]},
    "orange": {"label": "Orange Money", "methods": ["Orange Money"]},
    "mtn": {"label": "MTN MoMo", "methods": ["MTN MoMo"]},
}
TOLERANCE_DAYS = 7


def accounts(c: sqlite3.Connection) -> dict:
    """Comptes de base + comptes ajoutés (payaccts), chacun avec son mode de paiement."""
    out = {k: {**v, "extra": False, "active": True, "kind": "mobile" if k != "bank" else "bank"} for k, v in ACCOUNTS.items()}
    for r in c.execute("SELECT * FROM pay_accounts ORDER BY created_at, key"):
        out[r["key"]] = {"label": r["label"], "methods": [r["label"]], "extra": True, "active": bool(r["active"]), "kind": r["kind"], "ledger": r["ledger"]}
    return out


def _account(c, v) -> str:
    if v not in accounts(c): raise OpError("Compte inconnu.")
    return v


def _opening(c, account):
    return c.execute("SELECT * FROM bank_accounts WHERE account=?", (account,)).fetchone()


# ---------------------------------------------------------------- le livre : opérations de l'application qui touchent ce compte
def book_items(c: sqlite3.Connection, account: str) -> list[dict]:
    methods = accounts(c)[account]["methods"]; ph = ",".join("?" * len(methods)); out = []

    def add(kind, rows, sign, label):
        for r in rows:
            out.append({"type": kind, "id": r["id"], "date": r["d"], "amount": sign * r["amt"], "ref": r["ref"] or "", "label": label(r)})
    add("sale", c.execute(f"SELECT s.id, s.sale_date d, s.paid_at_sale amt, '' ref, s.number, cl.name FROM sales s JOIN clients cl ON cl.id=s.client_id WHERE s.status='active' AND s.paid_at_sale>0 AND s.pay_method IN ({ph})", methods), 1,
        lambda r: f"Vente {r['number']} encaissée : {r['name']}")
    add("payment", c.execute(f"SELECT p.id, p.pay_date d, p.amount amt, p.reference ref, s.number, cl.name FROM payments p JOIN sales s ON s.id=p.sale_id JOIN clients cl ON cl.id=s.client_id WHERE p.status='active' AND s.status='active' AND p.method IN ({ph})", methods), 1,
        lambda r: f"Paiement client {r['name']} (facture {r['number']})")
    add("expense", c.execute(f"SELECT id, expense_date d, amount amt, reference ref, number, category, payee FROM expenses WHERE status='active' AND method IN ({ph})", methods), -1,
        lambda r: f"Dépense {r['number']} : {r['category']}{' · ' + r['payee'] if r['payee'] else ''}")
    add("purchase", c.execute(f"SELECT u.id, u.purchase_date d, u.paid_at_purchase amt, u.supplier_ref ref, u.number, sp.name FROM purchases u JOIN suppliers sp ON sp.id=u.supplier_id WHERE u.status='active' AND u.paid_at_purchase>0 AND u.pay_method IN ({ph})", methods), -1,
        lambda r: f"Achat {r['number']} payé : {r['name']}")
    add("supplierpay", c.execute(f"SELECT p.id, p.pay_date d, p.amount amt, p.reference ref, u.number, sp.name FROM supplier_payments p JOIN purchases u ON u.id=p.purchase_id JOIN suppliers sp ON sp.id=u.supplier_id WHERE p.status='active' AND u.status='active' AND p.method IN ({ph})", methods), -1,
        lambda r: f"Règlement fournisseur {r['name']} (achat {r['number']})")
    add("asset", c.execute(f"SELECT id, acquisition_date d, cost amt, '' ref, number, name FROM fixed_assets WHERE status<>'cancelled' AND origin='purchase' AND pay_mode IN ({ph})", methods), -1,
        lambda r: f"Immobilisation {r['number']} payée : {r['name']}")
    add("assetsale", c.execute(f"SELECT id, disposal_date d, disposal_price amt, '' ref, number, name FROM fixed_assets WHERE status='disposed' AND disposal_price>0 AND disposal_method IN ({ph})", methods), 1,
        lambda r: f"Cession {r['number']} encaissée : {r['name']}")
    if account != "orange" and account != "mtn":                      # dépôts et retraits d'espèces : sur le compte bancaire choisi (par défaut le compte « Banque »)
        add("move", c.execute("SELECT id, move_date d, amount amt, note ref, kind FROM cash_movements WHERE status='active' AND kind='bank_deposit' AND account=?", (account,)), 1, lambda r: f"Dépôt d'espèces en banque : {r['ref']}")
        add("move", c.execute("SELECT id, move_date d, amount amt, note ref, kind FROM cash_movements WHERE status='active' AND kind='bank_withdrawal' AND account=?", (account,)), -1, lambda r: f"Retrait en banque : {r['ref']}")
    add("supplierrefund", c.execute(f"SELECT id, note_date d, amount amt, '' ref, number, reason FROM supplier_notes WHERE status='active' AND settlement='refund' AND refund_method IN ({ph})", methods), 1,
        lambda r: f"Remboursement fournisseur (avoir {r['number']}) : {r['reason']}")
    from . import transfers
    out += transfers.book_items(c, account)
    op = _opening(c, account)
    if op: out = [i for i in out if i["date"] > op["opening_date"]]
    out.sort(key=lambda i: (i["date"], i["type"], i["id"]))
    return out


def _matched_map(c) -> dict[tuple, str]:
    return {(m["item_type"], m["item_id"]): m["line_id"] for m in c.execute("SELECT * FROM bank_matches")}


def line_view(c: sqlite3.Connection, ln: sqlite3.Row, items_by_key: dict | None = None) -> dict:
    d = {k: ln[k] for k in ("id", "account", "line_date", "label", "amount", "reference", "status", "note", "version", "seq", "created_at")}
    ms = c.execute("SELECT * FROM bank_matches WHERE line_id=?", (ln["id"],)).fetchall()
    d["matches"] = [{"type": m["item_type"], "id": m["item_id"], "amount": m["amount"], "label": (items_by_key or {}).get((m["item_type"], m["item_id"]), {}).get("label"), "active": (m["item_type"], m["item_id"]) in (items_by_key or {})} for m in ms]
    return d


def rec_view(r: sqlite3.Row) -> dict:
    return {k: r[k] for k in ("id", "account", "rec_date", "statement_balance", "book_balance", "book_only", "bank_only", "difference", "note", "version", "seq", "created_at")}


def compute(c: sqlite3.Connection, account: str, upto: str, statement_balance: int | None = None) -> dict:
    op = _opening(c, account)
    items = book_items(c, account); matched = _matched_map(c)
    line_date = {r["id"]: r["line_date"] for r in c.execute("SELECT id, line_date FROM bank_lines WHERE account=?", (account,))}
    inc, book_only = [], 0
    for i in items:
        m_line = matched.get((i["type"], i["id"]))
        in_scope = i["date"] <= upto or (m_line is not None and line_date.get(m_line, "9999") <= upto)
        if not in_scope: continue
        inc.append(i)
        if m_line is None or line_date.get(m_line, "9999") > upto: book_only += i["amount"]
    book_balance = (op["opening_balance"] if op else 0) + sum(i["amount"] for i in inc)
    bank_only = 0; open_lines = 0
    for r in c.execute("SELECT * FROM bank_lines WHERE account=? AND status IN ('open','flagged') AND line_date<=?" + (" AND line_date>?" if op else ""), (account, upto, *( [op["opening_date"]] if op else []))):
        bank_only += r["amount"]; open_lines += 1
    out = {"account": account, "upto": upto, "book_balance": book_balance, "book_only": book_only, "bank_only": bank_only, "open_lines": open_lines, "opening": op["opening_balance"] if op else None}
    if statement_balance is not None:
        out["statement_balance"] = statement_balance
        out["difference"] = (statement_balance - bank_only) - (book_balance - book_only)
    return out


def pull_extra(c: sqlite3.Connection, user: sqlite3.Row, since: int) -> dict:
    from . import payaccts, transfers
    out = {**payaccts.pull_extra(c, user, since), **transfers.pull_extra(c, user, since), "bank_accounts": {}, "bank_lines": [], "bank_items": {}, "bank_reconciliations": [], "bank_status": {}, "bank_meta": {k: {"label": v["label"], "methods": v["methods"], "extra": v["extra"], "active": v["active"], "kind": v["kind"]} for k, v in accounts(c).items()}}
    if not can(user["role"], "bank.read"):
        out["bank_meta"] = {}; return out
    matched = _matched_map(c)
    for a in accounts(c):
        op = _opening(c, a)
        out["bank_accounts"][a] = {"opening_balance": op["opening_balance"], "opening_date": op["opening_date"]} if op else None
        items = book_items(c, a); by_key = {(i["type"], i["id"]): i for i in items}
        out["bank_items"][a] = [i for i in items if (i["type"], i["id"]) not in matched]
        out["bank_status"][a] = compute(c, a, today().isoformat())
        out["bank_status"][a]["unmatched_items"] = len(out["bank_items"][a])
        for ln in c.execute("SELECT * FROM bank_lines WHERE account=? AND seq>? ORDER BY line_date, seq", (a, since)):
            out["bank_lines"].append(line_view(c, ln, by_key))
    # une ligne pointée dont une opération a été annulée depuis doit être signalée même si la ligne elle-même n'a pas bougé
    out["bank_reconciliations"] = [rec_view(r) for r in c.execute("SELECT * FROM bank_reconciliations WHERE seq>? ORDER BY rec_date, seq", (since,))]
    stale = []
    for m in c.execute("SELECT DISTINCT line_id FROM bank_matches"):
        ln = c.execute("SELECT * FROM bank_lines WHERE id=?", (m["line_id"],)).fetchone()
        if ln["seq"] > since: continue
        by_key = {(i["type"], i["id"]): i for i in book_items(c, ln["account"])}
        v = line_view(c, ln, by_key)
        if any(not x["active"] for x in v["matches"]): out["bank_lines"].append(v)
    return out


# ---------------------------------------------------------------- opérations
def _last_validated(c, account):
    r = c.execute("SELECT MAX(rec_date) d FROM bank_reconciliations WHERE account=? AND difference=0", (account,)).fetchone()
    return r["d"]


def _bank_opening(c, user, actor, data, role, now):
    if not can(role, "bank.write"): return _deny(c, actor, "bank.opening", None, "droit manquant")
    acc = _account(c, data.get("account")); bal = _int(data.get("balance"), "Le solde d'ouverture", -10 ** 13, 10 ** 13)
    d = _date(data.get("date"), "Date du solde d'ouverture")
    if date.fromisoformat(d) > today(): raise OpError("La date d'ouverture ne peut pas être dans le futur.")
    if c.execute("SELECT 1 FROM bank_reconciliations WHERE account=?", (acc,)).fetchone(): return {"status": "invalid", "message": "Un rapprochement existe déjà : le solde d'ouverture ne peut plus changer."}
    before = _opening(c, acc)
    c.execute("INSERT INTO bank_accounts(account,opening_balance,opening_date,seq,updated_at,updated_by) VALUES(?,?,?,?,?,?) ON CONFLICT(account) DO UPDATE SET opening_balance=excluded.opening_balance, opening_date=excluded.opening_date, seq=excluded.seq, updated_at=excluded.updated_at, updated_by=excluded.updated_by",
              (acc, bal, d, next_seq(c), now, user["id"]))
    audit.record(c, actor, "bank.opening", "bank", acc, {"solde": before["opening_balance"], "date": before["opening_date"]} if before else None, {"solde": bal, "date": d}, note=accounts(c)[acc]["label"])
    return {"status": "ok"}


def _fingerprint(account, d, amount, label, ref, k) -> str:
    return hashlib.sha1(f"{account}|{d}|{amount}|{label.strip().lower()}|{ref.strip().lower()}|{k}".encode("utf-8")).hexdigest()


def _bank_import(c, user, actor, data, role, now):
    if not can(role, "bank.write"): return _deny(c, actor, "bank.import", None, "droit manquant")
    acc = _account(c, data.get("account"))
    raw = data.get("lines")
    if not isinstance(raw, list) or not (1 <= len(raw) <= 2000): raise OpError("Un import comporte de 1 à 2000 lignes.")
    seen, created, skipped, errors = {}, 0, 0, []
    op = _opening(c, acc)
    for i, ln in enumerate(raw, 1):
        try:
            if not isinstance(ln, dict): raise OpError("ligne invalide")
            d = _date(ln.get("date"), "date"); amount = _int(ln.get("amount"), "montant", -10 ** 13, 10 ** 13)
            if amount == 0: raise OpError("montant nul")
            label, ref = _text(ln.get("label"), "libellé", 200), _text(ln.get("reference"), "référence", 60)
            if date.fromisoformat(d) > today() + timedelta(days=1): raise OpError("date dans le futur")
        except OpError as e:
            errors.append(f"Ligne {i} : {e.message}"); continue
        key = (d, amount, label.strip().lower(), ref.strip().lower())
        k = seen.get(key, 0); seen[key] = k + 1                                           # deux lignes identiques dans le même fichier restent deux lignes
        fp = _fingerprint(acc, d, amount, label, ref, k)
        if c.execute("SELECT 1 FROM bank_lines WHERE fingerprint=?", (fp,)).fetchone(): skipped += 1; continue
        c.execute("INSERT INTO bank_lines(id,account,line_date,label,amount,reference,fingerprint,status,version,seq,created_at,updated_at,created_by,updated_by) VALUES(?,?,?,?,?,?,?,'open',1,?,?,?,?,?)",
                  (str(uuid.uuid4()), acc, d, label, amount, ref, fp, next_seq(c), now, now, user["id"], user["id"])); created += 1
    if errors and created == 0 and skipped == 0: return {"status": "invalid", "message": "Aucune ligne valide. " + " ".join(errors[:5])}
    warn = "Avant le solde d'ouverture : ces lignes seront ignorées dans le rapprochement." if op and any(r["line_date"] <= op["opening_date"] for r in c.execute("SELECT line_date FROM bank_lines WHERE account=? ORDER BY seq DESC LIMIT ?", (acc, max(created, 1)))) else None
    audit.record(c, actor, "bank.import", "bank", acc, None, {"lignes créées": created, "déjà présentes": skipped, "refusées": len(errors)}, note=accounts(c)[acc]["label"] + (" · " + warn if warn else ""))
    return {"status": "ok", "created": created, "skipped": skipped, "errors": errors[:20], "warning": warn}


def _load_line(c, data, key="line_id"):
    ln = c.execute("SELECT * FROM bank_lines WHERE id=?", (str(data.get(key) or ""),)).fetchone()
    return ln


def _link(c, ln, items, now, user):
    for it in items:
        c.execute("INSERT INTO bank_matches(line_id,item_type,item_id,amount) VALUES(?,?,?,?)", (ln["id"], it["type"], it["id"], it["amount"]))
    c.execute("UPDATE bank_lines SET status='matched', version=version+1, seq=?, updated_at=?, updated_by=? WHERE id=?", (next_seq(c), now, user["id"], ln["id"]))


def _bank_match(c, user, actor, data, role, now):
    if not can(role, "bank.write"): return _deny(c, actor, "bank.match", None, "droit manquant")
    ln = _load_line(c, data)
    if ln is None or ln["status"] in ("void", "matched"): return {"status": "invalid", "message": "Ligne introuvable ou déjà pointée."}
    if int(data.get("base_version") or 0) != ln["version"]: return _conflict(c, actor, "bank_line", ln["id"], line_view(c, ln), "pointage : ligne modifiée entre-temps")
    refs = data.get("items")
    if not isinstance(refs, list) or not (1 <= len(refs) <= 50): raise OpError("Choisissez de 1 à 50 opérations du livre.")
    avail = {(i["type"], i["id"]): i for i in book_items(c, ln["account"])}; matched = _matched_map(c); chosen, seen = [], set()
    for r in refs:
        key = (str((r or {}).get("type")), str((r or {}).get("id")))
        if key in seen: raise OpError("Opération choisie deux fois.")
        seen.add(key)
        if key in matched: return {"status": "invalid", "message": "Une des opérations est déjà pointée avec une autre ligne."}
        if key not in avail: return {"status": "invalid", "message": "Une des opérations n'existe plus, est annulée ou n'appartient pas à ce compte."}
        chosen.append(avail[key])
    total = sum(i["amount"] for i in chosen)
    if total != ln["amount"]: return {"status": "invalid", "message": f"Les opérations choisies totalisent {total} alors que la ligne du relevé est de {ln['amount']} : écart de {ln['amount'] - total}."}
    _link(c, ln, chosen, now, user)
    audit.record(c, actor, "bank.match", "bank_line", ln["id"], None, {"ligne": f"{ln['line_date']} {ln['label']} {ln['amount']}", "opérations": [i["label"] for i in chosen]}, note=accounts(c)[ln["account"]]["label"])
    return {"status": "ok", "line": line_view(c, c.execute("SELECT * FROM bank_lines WHERE id=?", (ln["id"],)).fetchone(), avail)}


def _bank_unmatch(c, user, actor, data, role, now):
    if not can(role, "bank.write"): return _deny(c, actor, "bank.unmatch", None, "droit manquant")
    ln = _load_line(c, data)
    if ln is None or ln["status"] not in ("matched", "flagged"): return {"status": "invalid", "message": "Cette ligne n'est ni pointée ni signalée."}
    reason = _text(data.get("reason"), "Le motif", 200, True)
    if len(reason) < 3: raise OpError("Le motif est obligatoire.")
    if int(data.get("base_version") or 0) != ln["version"]: return _conflict(c, actor, "bank_line", ln["id"], line_view(c, ln), "dépointage : ligne modifiée entre-temps")
    lv = _last_validated(c, ln["account"])
    if lv and ln["line_date"] <= lv and role != "owner": return {"status": "invalid", "message": f"Ce compte est rapproché (équilibré) jusqu'au {lv} : seul le propriétaire peut revenir sur ce pointage."}
    before = [m["item_type"] + ":" + m["item_id"][:8] for m in c.execute("SELECT * FROM bank_matches WHERE line_id=?", (ln["id"],))]
    c.execute("DELETE FROM bank_matches WHERE line_id=?", (ln["id"],))
    c.execute("UPDATE bank_lines SET status='open', note='', version=version+1, seq=?, updated_at=?, updated_by=? WHERE id=?", (next_seq(c), now, user["id"], ln["id"]))
    audit.record(c, actor, "bank.unmatch", "bank_line", ln["id"], {"statut": ln["status"], "opérations": before}, {"statut": "open", "motif": reason}, note=f"{ln['line_date']} {ln['label']} {ln['amount']}")
    return {"status": "ok"}


def _bank_flag(c, user, actor, data, role, now):
    if not can(role, "bank.write"): return _deny(c, actor, "bank.flag", None, "droit manquant")
    ln = _load_line(c, data)
    if ln is None or ln["status"] != "open": return {"status": "invalid", "message": "Ligne introuvable ou déjà traitée."}
    note = _text(data.get("note"), "La note", 200, True)
    if len(note) < 3: raise OpError("Expliquez pourquoi cette ligne est à justifier.")
    if int(data.get("base_version") or 0) != ln["version"]: return _conflict(c, actor, "bank_line", ln["id"], line_view(c, ln), "signalement : ligne modifiée entre-temps")
    c.execute("UPDATE bank_lines SET status='flagged', note=?, version=version+1, seq=?, updated_at=?, updated_by=? WHERE id=?", (note, next_seq(c), now, user["id"], ln["id"]))
    audit.record(c, actor, "bank.flag", "bank_line", ln["id"], {"statut": "open"}, {"statut": "à justifier", "note": note}, note=f"{ln['line_date']} {ln['label']} {ln['amount']}")
    return {"status": "ok"}


def _bank_void(c, user, actor, data, role, now):
    if not can(role, "bank.write"): return _deny(c, actor, "bank.void", None, "droit manquant")
    ln = _load_line(c, data)
    if ln is None or ln["status"] not in ("open", "flagged"): return {"status": "invalid", "message": "Seule une ligne non pointée peut être retirée."}
    reason = _text(data.get("reason"), "Le motif", 200, True)
    if len(reason) < 3: raise OpError("Le motif est obligatoire.")
    if int(data.get("base_version") or 0) != ln["version"]: return _conflict(c, actor, "bank_line", ln["id"], line_view(c, ln), "retrait : ligne modifiée entre-temps")
    c.execute("UPDATE bank_lines SET status='void', note=?, version=version+1, seq=?, updated_at=?, updated_by=? WHERE id=?", (reason, next_seq(c), now, user["id"], ln["id"]))
    audit.record(c, actor, "bank.void", "bank_line", ln["id"], {"statut": ln["status"]}, {"statut": "retirée", "motif": reason}, note=f"{ln['line_date']} {ln['label']} {ln['amount']}")
    return {"status": "ok"}


def _bank_create_expense(c, user, actor, data, role, now):
    if not (can(role, "bank.write") and can(role, "expenses.create")): return _deny(c, actor, "bank.create_expense", None, "droit manquant")
    ln = _load_line(c, data)
    if ln is None or ln["status"] not in ("open", "flagged"): return {"status": "invalid", "message": "Ligne introuvable ou déjà pointée."}
    if ln["amount"] >= 0: return {"status": "invalid", "message": "Seule une ligne de débit (sortie d'argent) peut devenir une dépense."}
    if int(data.get("base_version") or 0) != ln["version"]: return _conflict(c, actor, "bank_line", ln["id"], line_view(c, ln), "création de dépense : ligne modifiée entre-temps")
    from .cash_ops import _expense_create
    method = accounts(c)[ln["account"]]["methods"][0]
    res = _expense_create(c, user, actor, {"id": str(uuid.uuid4()), "expense_date": ln["line_date"], "category": data.get("category"), "payee": ln["label"][:120], "description": _text(data.get("description"), "La description", 200) or "Constatée sur le relevé",
                                           "amount": -ln["amount"], "method": method, "reference": ln["reference"]}, role, now)
    if res["status"] != "ok": return res
    exp = res["expense"]
    _link(c, ln, [{"type": "expense", "id": exp["id"], "amount": ln["amount"]}], now, user)
    audit.record(c, actor, "bank.match", "bank_line", ln["id"], None, {"ligne": f"{ln['line_date']} {ln['label']} {ln['amount']}", "opérations": [f"Dépense {exp['number']} créée depuis le relevé"]}, note=accounts(c)[ln["account"]]["label"])
    return {"status": "ok", "expense": exp}


def _bank_automatch(c, user, actor, data, role, now):
    if not can(role, "bank.write"): return _deny(c, actor, "bank.automatch", None, "droit manquant")
    acc = _account(c, data.get("account"))
    matched = _matched_map(c); items = [i for i in book_items(c, acc) if (i["type"], i["id"]) not in matched]
    pairs, ambiguous = [], 0
    for ln in c.execute("SELECT * FROM bank_lines WHERE account=? AND status='open' ORDER BY line_date, seq", (acc,)):
        cand = [i for i in items if i["amount"] == ln["amount"] and abs((date.fromisoformat(i["date"]) - date.fromisoformat(ln["line_date"])).days) <= TOLERANCE_DAYS]
        ref = ln["reference"].strip().lower()
        by_ref = [i for i in cand if ref and (i["ref"].strip().lower() == ref or ref in i["label"].lower())] if ref else []
        pick = by_ref[0] if len(by_ref) == 1 else (cand[0] if len(cand) == 1 and not by_ref else None)
        if pick is None:
            if cand: ambiguous += 1
            continue
        _link(c, ln, [pick], now, user); items.remove(pick); pairs.append((ln, pick))
    audit.record(c, actor, "bank.automatch", "bank", acc, None, {"lignes pointées": len(pairs), "à voir à la main (plusieurs candidats)": ambiguous, "détail": [f"{ln['line_date']} {ln['amount']} ↔ {i['label']}" for ln, i in pairs[:30]]}, note=accounts(c)[acc]["label"])
    return {"status": "ok", "matched": len(pairs), "ambiguous": ambiguous}


def _bank_reconcile(c, user, actor, data, role, now):
    if not can(role, "bank.write"): return _deny(c, actor, "bank.reconcile", None, "droit manquant")
    acc = _account(c, data.get("account"))
    if _opening(c, acc) is None: return {"status": "invalid", "message": "Fixez d'abord le solde d'ouverture de ce compte."}
    rid = str(data.get("id") or uuid.uuid4())
    if not UUID_RE.match(rid): raise OpError("Identifiant de rapprochement invalide.")
    if c.execute("SELECT 1 FROM bank_reconciliations WHERE id=?", (rid,)).fetchone(): raise OpError("Ce rapprochement existe déjà.")
    rdate = _date(data.get("date"), "Date du relevé")
    if date.fromisoformat(rdate) > today(): raise OpError("La date du relevé ne peut pas être dans le futur.")
    bal = _int(data.get("statement_balance"), "Le solde du relevé", -10 ** 13, 10 ** 13)
    last = c.execute("SELECT MAX(rec_date) d FROM bank_reconciliations WHERE account=?", (acc,)).fetchone()["d"]
    if last and rdate < last: return {"status": "invalid", "message": f"Un rapprochement existe déjà au {last} : choisissez une date égale ou postérieure."}
    r = compute(c, acc, rdate, bal); note = _text(data.get("note"), "La note", 200)
    if r["difference"] != 0 and len(note) < 3:
        return {"status": "invalid", "message": f"Écart de {r['difference']} : le rapprochement n'est pas équilibré. Cherchez l'opération manquante, ou expliquez l'écart dans la note.", "computation": r}
    c.execute("INSERT INTO bank_reconciliations(id,account,rec_date,statement_balance,book_balance,book_only,bank_only,difference,note,version,seq,created_at,created_by) VALUES(?,?,?,?,?,?,?,?,?,1,?,?,?)",
              (rid, acc, rdate, bal, r["book_balance"], r["book_only"], r["bank_only"], r["difference"], note, next_seq(c), now, user["id"]))
    x = c.execute("SELECT * FROM bank_reconciliations WHERE id=?", (rid,)).fetchone()
    audit.record(c, actor, "bank.reconcile", "bank", rid, None, {"compte": accounts(c)[acc]["label"], "date": rdate, "solde du relevé": bal, "solde du livre": r["book_balance"], "écart": r["difference"]}, note=("ÉCART : " + note) if r["difference"] else "équilibré")
    return {"status": "ok", "reconciliation": rec_view(x), "computation": r}


HANDLERS = {"bank.opening": _bank_opening, "bank.import": _bank_import, "bank.match": _bank_match, "bank.unmatch": _bank_unmatch, "bank.flag": _bank_flag, "bank.void": _bank_void,
            "bank.create_expense": _bank_create_expense, "bank.automatch": _bank_automatch, "bank.reconcile": _bank_reconcile}
