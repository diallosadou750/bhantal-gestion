"""Comptabilité en partie double déduite des opérations de l'application, avec écritures manuelles, balance, résultat, bilan et TVA.

AVERTISSEMENT : le plan de comptes, les règles d'imputation et les états produits sont un OUTIL DE TRAVAIL inspiré du plan SYSCOHADA, établi sans accès
au texte officiel complet. Ils ne remplacent ni un expert-comptable ni les états financiers officiels. Les comptes et taux par défaut sont à faire
valider par un professionnel inscrit avant tout usage déclaratif.

Principe : les documents (ventes, achats, paiements, dépenses, mouvements de caisse) sont la source unique ; chaque document actif produit ses écritures
(les documents annulés n'en produisent plus ; leur trace reste dans le journal d'audit). Les corrections se font par écritures manuelles (OD).
Les stocks suivent la méthode de l'inventaire intermittent : achats en classe 6, puis variation de stock calculée à la date du rapport,
valorisée au coût moyen d'achat (estimation).
"""
from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import uuid
from datetime import date, timedelta

from . import audit, security
from .cash_ops import categories as _expense_categories
from .ops import UUID_RE, OpError, _deny, next_seq
from .permissions import can
from .sales_ops import _conflict, _date, _int, _text, next_counter, settings_get, today

PLAN = [
    ("101", "Capital social"), ("106", "Réserves"), ("121", "Report à nouveau créditeur"), ("129", "Report à nouveau débiteur"), ("131", "Résultat net : bénéfice"), ("139", "Résultat net : perte"),
    ("162", "Emprunts auprès des établissements de crédit"), ("165", "Dépôts et cautionnements reçus"), ("168", "Autres emprunts et dettes"),
    ("211", "Frais de développement"), ("213", "Logiciels"), ("221", "Terrains agricoles et forestiers"), ("231", "Bâtiments industriels, agricoles, commerciaux"), ("241", "Matériel et outillage industriel et commercial"),
    ("244", "Matériel et mobilier de bureau"), ("245", "Matériel de transport"), ("248", "Autres matériels"), ("281", "Amortissements des immobilisations incorporelles"), ("283", "Amortissements des bâtiments"),
    ("284", "Amortissements du matériel"), ("311", "Marchandises"), ("321", "Matières premières et fournitures liées"), ("331", "Matières consommables"), ("361", "Produits finis"), ("391", "Dépréciation des stocks de marchandises"),
    ("401", "Fournisseurs, dettes en compte"), ("409", "Fournisseurs débiteurs (avances et acomptes versés)"), ("411", "Clients"), ("419", "Clients créditeurs (avances reçues)"), ("421", "Personnel : avances et acomptes"),
    ("422", "Personnel : rémunérations dues"), ("431", "Sécurité sociale (CNSS)"), ("4411", "État, impôt sur les bénéfices"), ("4431", "TVA facturée sur ventes"), ("4441", "État, TVA due"), ("4452", "TVA récupérable sur achats"),
    ("4471", "Impôts et taxes retenus à la source (ITS)"), ("462", "Associés : comptes courants"), ("471", "Débiteurs et créditeurs divers"), ("476", "Charges constatées d'avance"), ("477", "Produits constatés d'avance"),
    ("521", "Banques"), ("5211", "Banques : Orange Money (sous-compte, à valider)"), ("5212", "Banques : MTN MoMo (sous-compte, à valider)"), ("531", "Chèques postaux"), ("571", "Caisse"), ("585", "Virements de fonds"),
    ("601", "Achats de marchandises"), ("602", "Achats de matières premières"), ("603", "Variations de stocks"), ("604", "Achats stockés de matières et fournitures consommables"), ("605", "Autres achats (eau, électricité, carburant)"),
    ("611", "Transports sur achats"), ("612", "Transports sur ventes"), ("622", "Locations et charges locatives"), ("624", "Entretien, réparations et maintenance"), ("625", "Primes d'assurance"),
    ("626", "Études, recherches et documentation"), ("627", "Publicité, publications, relations publiques"), ("628", "Frais de télécommunications"), ("631", "Frais bancaires"), ("632", "Rémunérations d'intermédiaires et de conseils"),
    ("633", "Frais de formation du personnel"), ("638", "Autres charges externes"), ("641", "Impôts et taxes directs"), ("646", "Droits d'enregistrement"), ("658", "Charges diverses"), ("661", "Rémunérations directes versées au personnel"),
    ("664", "Charges sociales"), ("671", "Intérêts des emprunts"), ("676", "Pertes de change"), ("681", "Dotations aux amortissements d'exploitation"), ("691", "Dotations aux provisions d'exploitation"),
    ("701", "Ventes de marchandises"), ("702", "Ventes de produits finis"), ("706", "Services vendus"), ("707", "Produits accessoires"), ("711", "Subventions d'exploitation"), ("771", "Intérêts de prêts et créances"),
    ("776", "Gains de change"), ("781", "Transferts de charges d'exploitation"), ("791", "Reprises de provisions d'exploitation"),
    ("811", "Valeurs comptables des cessions d'immobilisations incorporelles"), ("821", "Produits des cessions d'immobilisations"), ("831", "Charges HAO constatées"), ("841", "Produits HAO constatés"), ("871", "Participation des travailleurs"), ("891", "Impôts sur le résultat"),
]
PLAN += [("191", "Provisions pour litiges"), ("198", "Autres provisions pour risques et charges"), ("491", "Dépréciations des comptes clients")]
DEFAULT_MAP = {"cash": "571", "bank": "521", "orange": "5211", "mtn": "5212", "other": "471", "customers": "411", "suppliers": "401", "sales": "701", "purchases": "601", "stock": "311", "stock_variation": "603",
               "vat_collected": "4431", "vat_deductible": "4452", "associates": "462", "opening_equity": "101", "depreciation_expense": "681", "disposal_cost": "811", "disposal_product": "821", "provision_expense": "691", "provision_reversal": "791", "deferred_charges": "476", "deferred_products": "477"}
MAP_LABELS = {"cash": "Caisse (espèces)", "bank": "Banque (virements, chèques)", "orange": "Orange Money", "mtn": "MTN MoMo", "other": "Autre mode de paiement", "customers": "Clients", "suppliers": "Fournisseurs",
              "sales": "Ventes de marchandises", "purchases": "Achats de marchandises", "stock": "Stock de marchandises", "stock_variation": "Variation de stocks", "vat_collected": "TVA collectée (sur ventes)",
              "vat_deductible": "TVA récupérable (sur achats)", "associates": "Comptes courants d'associés (apports, prélèvements)", "opening_equity": "Contrepartie des soldes d'ouverture", "depreciation_expense": "Dotations aux amortissements", "disposal_cost": "Valeur comptable des immobilisations cédées", "disposal_product": "Produits des cessions d'immobilisations", "provision_expense": "Dotations aux provisions", "provision_reversal": "Reprises de provisions", "deferred_charges": "Charges constatées d'avance", "deferred_products": "Produits constatés d'avance"}
JOURNALS = {"VE": "Ventes", "AC": "Achats et charges", "BQ": "Banque et mobile money", "CA": "Caisse", "OD": "Opérations diverses", "CL": "Clôture d'exercice"}


def is_product(k: str) -> bool:
    return k[0] == "7" or (k[0] == "8" and len(k) > 1 and int(k[1]) % 2 == 0)


def is_charge(k: str) -> bool:
    return k[0] == "6" or (k[0] == "8" and len(k) > 1 and int(k[1]) % 2 == 1)


def seed_plan(conn: sqlite3.Connection) -> None:
    now = security.iso()
    conn.executemany("INSERT OR IGNORE INTO accounts(code,name,custom,created_at) VALUES(?,?,0,?)", [(c, n, now) for c, n in PLAN])


def get_settings(c: sqlite3.Connection) -> dict:
    r = c.execute("SELECT value FROM meta WHERE key='acct_settings'").fetchone()
    d = json.loads(r["value"]) if r else {}
    return {"map": {**DEFAULT_MAP, **d.get("map", {})}, "purchase_tax_rate": int(d.get("purchase_tax_rate", 0)), "sales_tax_rate": settings_get(c)["tax_rate"]}


def split_tax(total: int, rate: int) -> tuple[int, int]:
    if rate <= 0: return total, 0
    ht = round(total * 100 / (100 + rate)); return ht, total - ht


def vat_split_rows(rows, default: int):
    """rows : (montant TTC, taux ou None) → (hors taxe, TVA, {taux: (hors taxe, TVA)}). Le calcul se fait taux par taux, le reste d'arrondi ne se perd jamais."""
    groups: dict[int, int] = {}
    for amount, rate in rows: groups[default if rate is None else rate] = groups.get(default if rate is None else rate, 0) + amount
    ht = tva = 0; br: dict[int, tuple[int, int]] = {}
    for rate, amt in sorted(groups.items()):
        h_, t_ = split_tax(amt, rate); ht += h_; tva += t_; br[rate] = (h_, t_)
    return ht, tva, br


def _pay_account(mp: dict, method: str) -> tuple[str, str]:
    """(compte, journal) selon le mode de paiement."""
    acct = {"Espèces": mp["cash"], "Virement bancaire": mp["bank"], "Chèque": mp["bank"], "Orange Money": mp["orange"], "MTN MoMo": mp["mtn"]}.get(method) or mp.get("_extra", {}).get(method, mp["other"])
    return acct, "CA" if method == "Espèces" else "BQ"


# ---------------------------------------------------------------- écritures automatiques
def auto_entries(c: sqlite3.Connection) -> list[dict]:
    st = get_settings(c); rs, rp = st["sales_tax_rate"], st["purchase_tax_rate"]; out: list[dict] = []
    mp = {**st["map"], "_extra": {r["label"]: r["ledger"] for r in c.execute("SELECT label, ledger FROM pay_accounts")}, "_extra_key": {r["key"]: r["ledger"] for r in c.execute("SELECT key, ledger FROM pay_accounts")}}

    def add(key, d, journal, ref, label, lines):
        lines = [(a, int(dr), int(cr)) for a, dr, cr in lines if dr or cr]
        if lines: out.append({"key": key, "date": d, "journal": journal, "ref": ref, "label": label, "lines": lines, "auto": True, "n": len(out)})
    from collections import defaultdict
    slines, plines = defaultdict(list), defaultdict(list)
    for r in c.execute("SELECT sale_id, amount, tax_rate FROM sale_lines"): slines[r["sale_id"]].append((r["amount"], r["tax_rate"]))
    for r in c.execute("SELECT purchase_id, amount, tax_rate FROM purchase_lines"): plines[r["purchase_id"]].append((r["amount"], r["tax_rate"]))

    def share(rows, default, total, amount):                       # part de TVA d'une pièce qui porte sur une partie du document (avoir) : au prorata de la TVA du document
        ht0, tva0, _ = vat_split_rows(rows, default)
        t = round(amount * tva0 / total) if total else 0
        return amount - t, t
    for s in c.execute("SELECT s.*, cl.name cname FROM sales s JOIN clients cl ON cl.id=s.client_id WHERE s.status='active'"):
        ht, tva, _ = vat_split_rows(slines[s["id"]], rs)
        if ht + tva != s["total"]: ht, tva = split_tax(s["total"], rs)
        add(f"sale:{s['id']}", s["sale_date"], "VE", s["number"], f"Vente {s['number']} : {s['cname']}", [(mp["customers"], s["total"], 0), (mp["sales"], 0, ht), (mp["vat_collected"], 0, tva)])
        if s["paid_at_sale"] > 0:
            a, j = _pay_account(mp, s["pay_method"])
            add(f"salepay:{s['id']}", s["sale_date"], j, s["number"], f"Encaissement à la vente {s['number']}", [(a, s["paid_at_sale"], 0), (mp["customers"], 0, s["paid_at_sale"])])
    for p in c.execute("SELECT p.*, s.number, cl.name cname FROM payments p JOIN sales s ON s.id=p.sale_id JOIN clients cl ON cl.id=s.client_id WHERE p.status='active' AND s.status='active'"):
        a, j = _pay_account(mp, p["method"])
        add(f"payment:{p['id']}", p["pay_date"], j, p["reference"] or p["number"], f"Règlement {p['cname']} (facture {p['number']})", [(a, p["amount"], 0), (mp["customers"], 0, p["amount"])])
    for n in c.execute("SELECT n.*, s.number snum, s.total stotal FROM credit_notes n JOIN sales s ON s.id=n.sale_id WHERE n.status='active' AND s.status='active'"):
        ht, tva = share(slines[n["sale_id"]], rs, n["stotal"], n["amount"])
        add(f"note:{n['id']}", n["note_date"], "VE", n["number"], f"Avoir {n['number']} sur {n['snum']} : {n['reason']}", [(mp["sales"], ht, 0), (mp["vat_collected"], tva, 0), (mp["customers"], 0, n["amount"])])
    for u in c.execute("SELECT u.*, sp.name sname FROM purchases u JOIN suppliers sp ON sp.id=u.supplier_id WHERE u.status='active'"):
        ht, tva, _ = vat_split_rows(plines[u["id"]], rp)
        if ht + tva != u["total"]: ht, tva = split_tax(u["total"], rp)
        add(f"purchase:{u['id']}", u["purchase_date"], "AC", u["number"], f"Achat {u['number']} : {u['sname']}", [(mp["purchases"], ht, 0), (mp["vat_deductible"], tva, 0), (mp["suppliers"], 0, u["total"])])
        if u["paid_at_purchase"] > 0:
            a, j = _pay_account(mp, u["pay_method"])
            add(f"purchasepay:{u['id']}", u["purchase_date"], j, u["number"], f"Paiement à l'achat {u['number']}", [(mp["suppliers"], u["paid_at_purchase"], 0), (a, 0, u["paid_at_purchase"])])
    for p in c.execute("SELECT p.*, u.number, sp.name sname FROM supplier_payments p JOIN purchases u ON u.id=p.purchase_id JOIN suppliers sp ON sp.id=u.supplier_id WHERE p.status='active' AND u.status='active'"):
        a, j = _pay_account(mp, p["method"])
        add(f"spay:{p['id']}", p["pay_date"], j, p["reference"] or p["number"], f"Règlement fournisseur {p['sname']} (achat {p['number']})", [(mp["suppliers"], p["amount"], 0), (a, 0, p["amount"])])
    ecats = _expense_categories(c)
    for e in c.execute("SELECT * FROM expenses WHERE status='active'"):
        a, j = _pay_account(mp, e["method"])
        add(f"expense:{e['id']}", e["expense_date"], j, e["number"], f"Dépense {e['number']} : {e['category']}{' · ' + e['payee'] if e['payee'] else ''}", [(ecats.get(e["category"], {"account": "658"})["account"], e["amount"], 0), (a, 0, e["amount"])])
    from . import assets as _assets, deferrals as _def, payroll as _payroll, provisions as _prov, supplier_notes as _sn, transfers as _tr
    for e in _assets.entries(c, mp, lambda m: _pay_account(mp, m)) + _prov.entries(c, mp) + _payroll.entries(c) + _def.entries(c, mp) + _tr.entries(c, mp) + _sn.entries(c, mp, lambda pid, amt: share(plines[pid], rp, c.execute('SELECT total FROM purchases WHERE id=?', (pid,)).fetchone()['total'], amt), lambda m: _pay_account(mp, m)):
        out.append({**e, "n": len(out)})
    cash = mp["cash"]
    for m in c.execute("SELECT * FROM cash_movements WHERE status='active'"):
        k, amt, lab = m["kind"], m["amount"], m["note"]
        bk = mp["_extra_key"].get(m["account"], mp["bank"])
        lines = {"bank_deposit": [(bk, amt, 0), (cash, 0, amt)], "bank_withdrawal": [(cash, amt, 0), (bk, 0, amt)], "owner_in": [(cash, amt, 0), (mp["associates"], 0, amt)],
                 "owner_out": [(mp["associates"], amt, 0), (cash, 0, amt)], "customer_refund": [(mp["customers"], amt, 0), (cash, 0, amt)]}[k]
        add(f"move:{m['id']}", m["move_date"], "CA", k, {"bank_deposit": "Dépôt d'espèces en banque", "bank_withdrawal": "Retrait en banque", "owner_in": "Apport du patron", "owner_out": "Prélèvement du patron", "customer_refund": "Remboursement client"}[k] + " : " + lab, lines)
    # soldes d'ouverture repris des modules Banque et Caisse (contrepartie : compte d'ouverture, à ventiler par un professionnel)
    for r in c.execute("SELECT * FROM bank_accounts"):
        a = {"bank": mp["bank"], "orange": mp["orange"], "mtn": mp["mtn"]}.get(r["account"]) or mp["_extra_key"][r["account"]]
        bal = r["opening_balance"]
        add(f"opening:{r['account']}", r["opening_date"], "OD", "OUV", f"Solde d'ouverture {r['account']} (à ventiler)", [(a, max(bal, 0), max(-bal, 0)), (mp["opening_equity"], max(-bal, 0), max(bal, 0))])
    first = c.execute("SELECT * FROM cash_closings WHERE status='active' ORDER BY close_date LIMIT 1").fetchone()
    if first and first["opening"]:
        starts = [r[0] for r in (c.execute("SELECT MIN(sale_date) FROM sales").fetchone(), c.execute("SELECT MIN(expense_date) FROM expenses").fetchone(), c.execute("SELECT MIN(move_date) FROM cash_movements").fetchone()) if r[0]]
        d0 = (date.fromisoformat(min(starts + [first["close_date"]])) - timedelta(days=1)).isoformat()
        add("opening:cash", d0, "OD", "OUV", "Solde d'ouverture de la caisse (à ventiler)", [(cash, first["opening"], 0), (mp["opening_equity"], 0, first["opening"])])
    for fy in c.execute("SELECT * FROM fiscal_years WHERE status='closed' ORDER BY year"):         # écritures de clôture figées au moment de la clôture
        for no in sorted({r["entry_no"] for r in c.execute("SELECT entry_no FROM fiscal_lines WHERE year=?", (fy["year"],))}):
            ls = c.execute("SELECT * FROM fiscal_lines WHERE year=? AND entry_no=? ORDER BY id", (fy["year"], no)).fetchall()
            out.append({"key": f"closing:{fy['year']}:{no}", "date": fy["end_date"], "journal": "CL", "ref": f"CLÔT-{fy['year']}", "label": ls[0]["label"], "lines": [(l["account"], l["debit"], l["credit"]) for l in ls], "auto": True, "n": 10 ** 6 + fy["year"] * 10 + no})
    for e in c.execute("SELECT * FROM journal_entries WHERE status='active'"):
        lines = [(l["account"], l["debit"], l["credit"]) for l in c.execute("SELECT * FROM journal_lines WHERE entry_id=? ORDER BY position", (e["id"],))]
        out.append({"key": f"od:{e['id']}", "date": e["entry_date"], "journal": "OD", "ref": e["number"], "label": e["label"], "lines": lines, "auto": False, "entry_id": e["id"], "n": len(out)})
    out.sort(key=lambda x: (x["date"], x["n"]))
    return out


def stock_value(c: sqlite3.Connection, d: str | None) -> int:
    """Valeur du stock à la fin du jour d (None : avant tout), au coût moyen d'achat des achats jusqu'à d. Un stock négatif ou sans coût compte pour zéro."""
    if d is None: return 0
    buy = {r["pid"]: (r["q"], r["a"]) for r in c.execute("SELECT l.product_id pid, SUM(l.qty) q, SUM(l.amount) a FROM purchase_lines l JOIN purchases u ON u.id=l.purchase_id WHERE u.status='active' AND u.purchase_date<=? GROUP BY l.product_id", (d,))}
    sold = {r["pid"]: r["q"] for r in c.execute("SELECT l.product_id pid, SUM(l.qty) q FROM sale_lines l JOIN sales s ON s.id=l.sale_id WHERE s.status='active' AND s.sale_date<=? AND l.product_id IS NOT NULL GROUP BY l.product_id", (d,))}
    adj = {r["pid"]: r["q"] for r in c.execute("SELECT product_id pid, SUM(qty) q FROM stock_adjustments WHERE status='active' AND adj_date<=? GROUP BY product_id", (d,))}
    for r in c.execute("SELECT l.product_id pid, SUM(l.qty) q FROM credit_note_lines l JOIN credit_notes n ON n.id=l.note_id JOIN sales s ON s.id=n.sale_id WHERE n.status='active' AND s.status='active' AND n.note_date<=? GROUP BY l.product_id", (d,)):
        adj[r["pid"]] = adj.get(r["pid"], 0) + r["q"]                         # marchandise rendue par les clients : elle remonte dans le stock
    tracked = {r["id"] for r in c.execute("SELECT id FROM products WHERE track_stock=1")}
    total = 0
    for pid, (q, a) in buy.items():
        if pid not in tracked or not q: continue
        qty = q - sold.get(pid, 0) + adj.get(pid, 0)
        if qty > 0: total += round(qty * a / q)
    return total


def _day_before(d: str | None) -> str | None:
    return (date.fromisoformat(d) - timedelta(days=1)).isoformat() if d else None


def stock_entry(c: sqlite3.Connection, a: str | None, b: str) -> dict | None:
    """Écriture d'inventaire calculée : variation de la valeur du stock entre la veille de a et b (méthode de l'inventaire intermittent)."""
    mp = get_settings(c)["map"]
    delta = stock_value(c, b) - stock_value(c, _day_before(a))
    if delta == 0: return None
    lines = [(mp["stock"], delta, 0), (mp["stock_variation"], 0, delta)] if delta > 0 else [(mp["stock_variation"], -delta, 0), (mp["stock"], 0, -delta)]
    return {"key": "inventory", "date": b, "journal": "OD", "ref": "INV", "label": "Variation de stock (inventaire intermittent, valorisé au coût moyen d'achat : estimation)", "lines": lines, "auto": True, "virtual": True}


def entries_between(c: sqlite3.Connection, a: str | None, b: str | None, allentries=None) -> list[dict]:
    allentries = allentries if allentries is not None else auto_entries(c)
    sel = [e for e in allentries if (a is None or e["date"] >= a) and (b is None or e["date"] <= b)]
    if b:
        v = stock_entry(c, a, b)
        if v: sel.append(v)
    return sel


def account_names(c: sqlite3.Connection) -> dict:
    return {r["code"]: r["name"] for r in c.execute("SELECT code, name FROM accounts")}


def _sum_lines(entries) -> dict:
    t: dict[str, list[int]] = {}
    for e in entries:
        for a, dr, cr in e["lines"]:
            x = t.setdefault(a, [0, 0]); x[0] += dr; x[1] += cr
    return t


def trial_balance(c: sqlite3.Connection, a: str | None, b: str) -> dict:
    allen = auto_entries(c); names = account_names(c)
    before = _sum_lines(entries_between(c, None, _day_before(a), allen) if a else [])
    mov = _sum_lines(entries_between(c, a, b, allen))
    rows, tot = [], {"od": 0, "oc": 0, "md": 0, "mc": 0, "cd": 0, "cc": 0}
    for code in sorted(set(before) | set(mov)):
        od, oc = before.get(code, [0, 0]); md, mc = mov.get(code, [0, 0])
        ob = od - oc; cb = ob + md - mc
        r = {"account": code, "name": names.get(code, "(compte inconnu)"), "open_debit": max(ob, 0), "open_credit": max(-ob, 0), "debit": md, "credit": mc, "close_debit": max(cb, 0), "close_credit": max(-cb, 0)}
        rows.append(r)
        for k, v in (("od", r["open_debit"]), ("oc", r["open_credit"]), ("md", md), ("mc", mc), ("cd", r["close_debit"]), ("cc", r["close_credit"])): tot[k] += v
    return {"from": a, "to": b, "rows": rows, "totals": tot, "balanced": tot["md"] == tot["mc"] and tot["cd"] == tot["cc"] and tot["od"] == tot["oc"]}


def ledger(c: sqlite3.Connection, account: str, a: str | None, b: str) -> dict:
    allen = auto_entries(c); names = account_names(c)
    prev = _sum_lines(entries_between(c, None, _day_before(a), allen) if a else []).get(account, [0, 0]); ob = prev[0] - prev[1]
    rows, run = [], ob
    for e in entries_between(c, a, b, allen):
        for acc, dr, cr in e["lines"]:
            if acc == account:
                run += dr - cr; rows.append({"date": e["date"], "journal": e["journal"], "ref": e["ref"], "label": e["label"], "debit": dr, "credit": cr, "balance": run})
    rows.sort(key=lambda r: r["date"])
    run = ob
    for r in rows: run += r["debit"] - r["credit"]; r["balance"] = run
    return {"account": account, "name": names.get(account, "(compte inconnu)"), "from": a, "to": b, "opening": ob, "rows": rows, "closing": run}


def journal(c: sqlite3.Connection, a: str | None, b: str, code: str | None) -> dict:
    names = account_names(c)
    es = [e for e in entries_between(c, a, b) if not code or e["journal"] == code]
    es.sort(key=lambda e: (e["date"], e.get("n", 10 ** 9)))
    return {"from": a, "to": b, "journal": code, "entries": [{"date": e["date"], "journal": e["journal"], "ref": e["ref"], "label": e["label"], "auto": e["auto"], "entry_id": e.get("entry_id"),
                                                                "lines": [{"account": x, "name": names.get(x, ""), "debit": dr, "credit": cr} for x, dr, cr in e["lines"]]} for e in es]}


def statements(c: sqlite3.Connection, a: str | None, b: str) -> dict:
    allen = auto_entries(c); names = account_names(c)
    mov = _sum_lines([e for e in entries_between(c, a, b, allen) if e["journal"] != "CL"])                # le résultat d'un exercice se lit AVANT ses écritures de clôture
    prod = sorted(((k, v[1] - v[0]) for k, v in mov.items() if is_product(k)), key=lambda x: x[0]); chg = sorted(((k, v[0] - v[1]) for k, v in mov.items() if is_charge(k)), key=lambda x: x[0])
    pt, ct = sum(v for _, v in prod), sum(v for _, v in chg); sales = sum(v for k, v in prod if k.startswith("70"))
    purchases = sum(v for k, v in chg if k.startswith("60"))
    result = {"products": [{"account": k, "name": names.get(k, ""), "amount": v} for k, v in prod], "charges": [{"account": k, "name": names.get(k, ""), "amount": v} for k, v in chg],
              "products_total": pt, "charges_total": ct, "result": pt - ct, "sales": sales, "purchases_and_stock_variation": purchases, "gross_margin": sales - purchases}
    cum = _sum_lines(entries_between(c, None, b, allen))                      # bilan : situation cumulée depuis l'ouverture des livres
    res_cum = sum(v[1] - v[0] for k, v in cum.items() if is_product(k)) - sum(v[0] - v[1] for k, v in cum.items() if is_charge(k))
    assets, liabs = [], []
    for k in sorted(k for k in cum if k[0] in "12345"):
        bal = cum[k][0] - cum[k][1]
        if bal > 0 or (bal < 0 and k[:2] in ("28", "29", "39", "49", "59")):         # amortissements et dépréciations : déduits de l'actif (montant négatif)
            assets.append({"account": k, "name": names.get(k, ""), "amount": bal})
        elif bal < 0: liabs.append({"account": k, "name": names.get(k, ""), "amount": -bal})
    at, lt = sum(x["amount"] for x in assets), sum(x["amount"] for x in liabs)
    if res_cum >= 0: liabs.append({"account": "130", "name": "Résultat des exercices non clôturés (bénéfice)", "amount": res_cum}); lt += res_cum
    else: assets.append({"account": "139", "name": "Résultat des exercices non clôturés (perte)", "amount": -res_cum}); at += -res_cum
    bilan = {"assets": assets, "liabilities": liabs, "assets_total": at, "liabilities_total": lt, "balanced": at == lt, "as_of": b, "note": "Situation cumulée depuis l'ouverture des livres : les exercices clôturés sont reportés dans les comptes de capitaux (résultat en instance d'affectation ou report à nouveau)."}
    return {"from": a, "to": b, "income": result, "balance_sheet": bilan, "vat": vat_report(c, a, b, allen)}


def _rate_breakdown(c, doc, lines, key, date_col, a, b, default) -> list[dict]:
    """Base hors taxe et TVA par taux sur les documents actifs de la période (avoirs non déduits : repère pour préparer la déclaration)."""
    groups: dict[int, int] = {}
    for r in c.execute(f"SELECT l.amount, l.tax_rate FROM {lines} l JOIN {doc} d ON d.id=l.{key} WHERE d.status='active' AND d.{date_col}<=?" + (f" AND d.{date_col}>=?" if a else ""), (b, a) if a else (b,)):
        rate = default if r["tax_rate"] is None else r["tax_rate"]; groups[rate] = groups.get(rate, 0) + r["amount"]
    out = []
    for rate, amt in sorted(groups.items()):
        ht, tva = split_tax(amt, rate); out.append({"rate": rate, "total": amt, "base": ht, "tax": tva})
    return out


def vat_report(c: sqlite3.Connection, a: str | None, b: str, allen=None) -> dict:
    st = get_settings(c); mp = st["map"]; allen = allen if allen is not None else auto_entries(c)
    months: dict[str, list[int]] = {}
    for e in allen:
        if (a and e["date"] < a) or e["date"] > b: continue
        for acc, dr, cr in e["lines"]:
            if acc in (mp["vat_collected"], mp["vat_deductible"]):
                m = months.setdefault(e["date"][:7], [0, 0])
                if acc == mp["vat_collected"]: m[0] += cr - dr
                else: m[1] += dr - cr
    rows = [{"month": k, "collected": v[0], "deductible": v[1], "due": v[0] - v[1]} for k, v in sorted(months.items())]
    by_rate = {"sales": _rate_breakdown(c, "sales", "sale_lines", "sale_id", "sale_date", a, b, st["sales_tax_rate"]), "purchases": _rate_breakdown(c, "purchases", "purchase_lines", "purchase_id", "purchase_date", a, b, st["purchase_tax_rate"])}
    return {"by_rate": by_rate, "rows": rows, "collected": sum(r["collected"] for r in rows), "deductible": sum(r["deductible"] for r in rows), "due": sum(r["due"] for r in rows),
            "sales_rate": st["sales_tax_rate"], "purchase_rate": st["purchase_tax_rate"],
            "note": "Taux saisis par vous, à confirmer auprès de l'administration fiscale. Cet état aide à préparer la déclaration ; il ne la remplace pas."}


def export_csv(kind: str, data: dict) -> str:
    buf = io.StringIO(); w = csv.writer(buf, delimiter=";")

    def safe(v):
        s = "" if v is None else str(v)
        return "'" + s if s[:1] in ("=", "+", "-", "@") and not re.fullmatch(r"-?\d+", s) else s
    if kind == "journal":
        w.writerow(["date", "journal", "pièce", "libellé", "compte", "intitulé", "débit", "crédit"])
        for e in data["entries"]:
            for ln in e["lines"]: w.writerow([e["date"], e["journal"], safe(e["ref"]), safe(e["label"]), ln["account"], safe(ln["name"]), ln["debit"], ln["credit"]])
    elif kind == "balance":
        w.writerow(["compte", "intitulé", "ouverture débit", "ouverture crédit", "mouvements débit", "mouvements crédit", "solde débit", "solde crédit"])
        for r in data["rows"]: w.writerow([r["account"], safe(r["name"]), r["open_debit"], r["open_credit"], r["debit"], r["credit"], r["close_debit"], r["close_credit"]])
    elif kind == "ledger":
        w.writerow(["date", "journal", "pièce", "libellé", "débit", "crédit", "solde"])
        for r in data["rows"]: w.writerow([r["date"], r["journal"], safe(r["ref"]), safe(r["label"]), r["debit"], r["credit"], r["balance"]])
    return "﻿" + buf.getvalue()


def pull_extra(c: sqlite3.Connection, user: sqlite3.Row, since: int) -> dict:
    from . import period
    if not can(user["role"], "accounting.read"): return {"accounting": None, "journal_entries": [], "assets": [], "provisions": [], "provision_suggestions": [], "deferrals": []}
    st = get_settings(c)
    entries = []
    from . import assets as _assets, deferrals as _def, provisions as _prov
    for e in c.execute("SELECT * FROM journal_entries WHERE seq>? ORDER BY seq", (since,)):
        entries.append({"id": e["id"], "number": e["number"], "entry_date": e["entry_date"], "label": e["label"], "status": e["status"], "cancel_reason": e["cancel_reason"], "version": e["version"], "seq": e["seq"],
                        "lines": [{"account": l["account"], "debit": l["debit"], "credit": l["credit"], "memo": l["memo"]} for l in c.execute("SELECT * FROM journal_lines WHERE entry_id=? ORDER BY position", (e["id"],))]})
    return {"accounting": {"accounts": [{"code": r["code"], "name": r["name"], "custom": bool(r["custom"])} for r in c.execute("SELECT * FROM accounts ORDER BY code")], "map": st["map"], "map_labels": MAP_LABELS,
                           "purchase_tax_rate": st["purchase_tax_rate"], "sales_tax_rate": st["sales_tax_rate"], "locked_through": period.locked_through(c), "journals": JOURNALS,
                           "fiscal_years": [{"year": r["year"], "start": r["start_date"], "end": r["end_date"], "result": r["result"], "allocation": r["allocation"], "closed_at": r["closed_at"]} for r in c.execute("SELECT * FROM fiscal_years ORDER BY year")],
                           "next_to_close": next_to_close(c), "first_year": first_year(c), "asset_categories": _assets.CATEGORIES, "provision_kinds": _prov.KINDS}, "journal_entries": entries,
            "assets": [_assets.view(r) for r in c.execute("SELECT * FROM fixed_assets ORDER BY acquisition_date, number")],
            "provisions": [_prov.view(c, r) for r in c.execute("SELECT * FROM provisions ORDER BY prov_date, number")], "provision_suggestions": _prov.suggestions(c),
            "deferrals": [_def.view(c, r) for r in c.execute("SELECT * FROM deferrals ORDER BY book_date, number")]}


# ---------------------------------------------------------------- opérations
CODE_RE = re.compile(r"^[1-8]\d{2,7}$")


def _account_create(c, user, actor, data, role, now):
    if not can(role, "accounting.write"): return _deny(c, actor, "account.create", None, "droit manquant")
    code, name = str(data.get("code") or "").strip(), _text(data.get("name"), "L'intitulé", 120, True)
    if not CODE_RE.match(code): raise OpError("Code de compte : 3 à 8 chiffres, commençant par 1 à 8.")
    if c.execute("SELECT 1 FROM accounts WHERE code=?", (code,)).fetchone(): return {"status": "invalid", "message": "Ce compte existe déjà."}
    c.execute("INSERT INTO accounts(code,name,custom,created_at) VALUES(?,?,1,?)", (code, name, now))
    audit.record(c, actor, "account.create", "account", code, None, {"code": code, "name": name}); return {"status": "ok"}


def _journal_create(c, user, actor, data, role, now):
    from . import period
    if not can(role, "accounting.write"): return _deny(c, actor, "journal.create", None, "droit manquant")
    eid = str(data.get("id") or uuid.uuid4())
    if not UUID_RE.match(eid): raise OpError("Identifiant d'écriture invalide.")
    if c.execute("SELECT 1 FROM journal_entries WHERE id=?", (eid,)).fetchone(): raise OpError("Cette écriture existe déjà.")
    d = _date(data.get("entry_date") or today().isoformat(), "Date de l'écriture")
    if date.fromisoformat(d) > today() + timedelta(days=1): raise OpError("La date de l'écriture ne peut pas être dans le futur.")
    period.ensure_open(c, d)
    label = _text(data.get("label"), "Le libellé", 200, True)
    raw = data.get("lines")
    if not isinstance(raw, list) or not (2 <= len(raw) <= 40): raise OpError("Une écriture comporte de 2 à 40 lignes.")
    known = {r["code"] for r in c.execute("SELECT code FROM accounts")}; lines, td, tc = [], 0, 0
    for i, ln in enumerate(raw, 1):
        if not isinstance(ln, dict): raise OpError(f"Ligne {i} invalide.")
        acc = str(ln.get("account") or "")
        if acc not in known: raise OpError(f"Ligne {i} : compte {acc or '(vide)'} inconnu.")
        dr, cr = _int(ln.get("debit") or 0, f"Ligne {i} : débit", 0, 10 ** 13), _int(ln.get("credit") or 0, f"Ligne {i} : crédit", 0, 10 ** 13)
        if (dr > 0) == (cr > 0): raise OpError(f"Ligne {i} : renseignez soit un débit, soit un crédit (pas les deux, pas aucun).")
        lines.append((i, acc, dr, cr, _text(ln.get("memo"), "Le commentaire", 120))); td += dr; tc += cr
    if td != tc: return {"status": "invalid", "message": f"Écriture déséquilibrée : débit {td}, crédit {tc} (écart {td - tc})."}
    number = f"OD-{next_counter(c, 'od_no'):04d}"
    c.execute("INSERT INTO journal_entries(id,number,entry_date,label,status,version,seq,created_at,updated_at,created_by,updated_by) VALUES(?,?,?,?, 'active',1,?,?,?,?,?)", (eid, number, d, label, next_seq(c), now, now, user["id"], user["id"]))
    for ln in lines: c.execute("INSERT INTO journal_lines(entry_id,position,account,debit,credit,memo) VALUES(?,?,?,?,?,?)", (eid, *ln))
    audit.record(c, actor, "journal.create", "journal_entry", eid, None, {"numéro": number, "date": d, "libellé": label, "total": td, "lignes": [f"{x[1]} D{x[2]} C{x[3]}" for x in lines]})
    return {"status": "ok", "number": number, "id": eid}


def _journal_cancel(c, user, actor, data, role, now):
    from . import period
    if not can(role, "accounting.write"): return _deny(c, actor, "journal.cancel", str(data.get("id") or ""), "droit manquant")
    e = c.execute("SELECT * FROM journal_entries WHERE id=? AND status='active'", (str(data.get("id") or ""),)).fetchone()
    if e is None: return {"status": "invalid", "message": "Écriture introuvable ou déjà annulée."}
    period.ensure_open(c, e["entry_date"])
    reason = _text(data.get("reason"), "Le motif", 200, True)
    if len(reason) < 3: raise OpError("Le motif d'annulation est obligatoire.")
    if int(data.get("base_version") or 0) != e["version"]: return _conflict(c, actor, "journal_entry", e["id"], {"number": e["number"], "version": e["version"]}, "annulation : écriture modifiée entre-temps")
    c.execute("UPDATE journal_entries SET status='cancelled', cancel_reason=?, version=version+1, seq=?, updated_at=?, updated_by=? WHERE id=?", (reason, next_seq(c), now, user["id"], e["id"]))
    audit.record(c, actor, "journal.cancel", "journal_entry", e["id"], {"statut": "active", "numéro": e["number"]}, {"statut": "annulée", "motif": reason}); return {"status": "ok"}


def _settings_update(c, user, actor, data, role, now):
    if not can(role, "accounting.write"): return _deny(c, actor, "accounting.settings", None, "droit manquant")
    cur = get_settings(c); known = {r["code"] for r in c.execute("SELECT code FROM accounts")}
    new_map = dict(cur["map"])
    for k, v in (data.get("map") or {}).items():
        if k not in DEFAULT_MAP: raise OpError(f"Réglage inconnu : {k}.")
        if str(v) not in known: raise OpError(f"Le compte {v} n'existe pas dans le plan.")
        new_map[k] = str(v)
    prate = _int(data.get("purchase_tax_rate", cur["purchase_tax_rate"]), "Taux de TVA sur achats", 0, 30)
    from . import period
    if (new_map != cur["map"] or prate != cur["purchase_tax_rate"]) and period.locked_through(c): return {"status": "invalid", "message": "La comptabilité est verrouillée : ces réglages ne peuvent plus changer."}
    before = {"comptes": {k: cur["map"][k] for k in new_map if new_map[k] != cur["map"][k]}, **({"tva achats": cur["purchase_tax_rate"]} if prate != cur["purchase_tax_rate"] else {})}
    after = {"comptes": {k: new_map[k] for k in new_map if new_map[k] != cur["map"][k]}, **({"tva achats": prate} if prate != cur["purchase_tax_rate"] else {})}
    if before == after: return {"status": "ok", "unchanged": True}
    c.execute("INSERT INTO meta(key,value) VALUES('acct_settings',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps({"map": new_map, "purchase_tax_rate": prate}, ensure_ascii=False),))
    audit.record(c, actor, "accounting.settings", "accounting", "settings", before, after); return {"status": "ok"}


def _lock(c, user, actor, data, role, now):
    from . import period
    if not can(role, "accounting.lock"): return _deny(c, actor, "accounting.lock", None, "réservé au propriétaire")
    new = _date(data.get("through"), "Date de verrouillage", required=False)
    cur = period.locked_through(c)
    fy = c.execute("SELECT MAX(end_date) d FROM fiscal_years WHERE status='closed'").fetchone()["d"]
    if fy and (not new or new < fy): return {"status": "invalid", "message": f"L'exercice clôturé au {fy} impose ce verrou : rouvrez d'abord l'exercice (Compta, Clôture)."}
    if new and date.fromisoformat(new) > today(): raise OpError("On ne verrouille pas une période à venir.")
    if new and cur and new < cur and len(_text(data.get("reason"), "Le motif", 200)) < 3: raise OpError("Réduire le verrou exige un motif.")
    if not new and cur and len(_text(data.get("reason"), "Le motif", 200)) < 3: raise OpError("Lever le verrou exige un motif.")
    if new == cur: return {"status": "ok", "unchanged": True}
    c.execute("INSERT INTO meta(key,value) VALUES('acct_lock',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (new or "",))
    audit.record(c, actor, "accounting.lock", "accounting", "lock", {"verrouillé jusqu'au": cur}, {"verrouillé jusqu'au": new}, note=_text(data.get("reason"), "Le motif", 200) or None)
    return {"status": "ok", "locked_through": new}


# ---------------------------------------------------------------- clôture d'exercice
def year_bounds(y: int) -> tuple[str, str]:
    return f"{y}-01-01", f"{y}-12-31"


def first_year(c: sqlite3.Connection) -> int | None:
    ds = [e["date"] for e in auto_entries(c) if e["journal"] != "CL"]
    return int(min(ds)[:4]) if ds else None


def closed_years(c: sqlite3.Connection) -> list[int]:
    return [r["year"] for r in c.execute("SELECT year FROM fiscal_years WHERE status='closed' ORDER BY year")]


def next_to_close(c: sqlite3.Connection) -> int | None:
    fy = first_year(c)
    if fy is None: return None
    done = set(closed_years(c)); y = fy
    while y in done: y += 1
    return y if date.fromisoformat(year_bounds(y)[1]) < today() else None


def closing_preview(c: sqlite3.Connection, year: int) -> dict:
    a, b = year_bounds(year); errors, warnings = [], []
    st = statements(c, a, b); inc, bs = st["income"], st["balance_sheet"]
    tb = trial_balance(c, a, b)
    if not tb["balanced"]: errors.append("La balance n'est pas équilibrée : il y a une anomalie à corriger avant de clôturer.")
    if not bs["balanced"]: errors.append("Le bilan n'est pas équilibré : il y a une anomalie à corriger avant de clôturer.")
    last_cash = c.execute("SELECT MAX(close_date) d FROM cash_closings WHERE status='active'").fetchone()["d"]
    had_cash = c.execute("SELECT 1 FROM sales WHERE sale_date BETWEEN ? AND ? AND pay_method='Espèces' LIMIT 1", (a, b)).fetchone() or c.execute("SELECT 1 FROM expenses WHERE expense_date BETWEEN ? AND ? AND method='Espèces' LIMIT 1", (a, b)).fetchone()
    if had_cash and (not last_cash or last_cash < b): warnings.append(f"La caisse n'est pas clôturée jusqu'au {b} (dernière clôture : {last_cash or 'aucune'}).")
    for acc, label in (("bank", "Banque"), ("orange", "Orange Money"), ("mtn", "MTN MoMo"), *[(r["key"], r["label"]) for r in c.execute("SELECT key, label FROM pay_accounts")]):
        if c.execute("SELECT 1 FROM bank_accounts WHERE account=? AND opening_date<=?", (acc, b)).fetchone():            # compte déjà ouvert à la fin de l'exercice
            last = c.execute("SELECT MAX(rec_date) d FROM bank_reconciliations WHERE account=? AND difference=0", (acc,)).fetchone()["d"]
            if not last or last < b: warnings.append(f"{label} : pas de rapprochement équilibré jusqu'au {b} (dernier : {last or 'aucun'}).")
            n = c.execute("SELECT COUNT(*) n FROM bank_lines WHERE account=? AND status IN ('open','flagged') AND line_date<=?", (acc, b)).fetchone()["n"]
            if n: warnings.append(f"{label} : {n} ligne(s) de relevé non pointée(s) à la fin de l'exercice.")
    from .stock_ops import stock_levels
    neg = sum(1 for q in stock_levels(c).values() if q < 0)
    if neg: warnings.append(f"{neg} produit(s) en stock négatif : achats probablement non saisis, donc stock et résultat sous-estimés.")
    warnings.append("Faites valider le plan de comptes, les comptes d'imputation, les taux de TVA et la valorisation du stock par votre expert-comptable avant de clôturer pour de bon.")
    done = set(closed_years(c)); fy = first_year(c)
    reasons = []
    if year in done: reasons.append("Cet exercice est déjà clôturé.")
    if date.fromisoformat(b) >= today(): reasons.append(f"L'exercice n'est pas terminé (fin le {b}).")
    if fy is not None and year < fy: reasons.append("Aucune activité avant cette date.")
    if fy is not None and any(y not in done for y in range(fy, year)): reasons.append(f"Clôturez d'abord les exercices précédents (à partir de {fy}).")
    return {"year": year, "from": a, "to": b, "result": inc["result"], "sales": inc["sales"], "products_total": inc["products_total"], "charges_total": inc["charges_total"], "assets_total": bs["assets_total"],
            "errors": errors, "warnings": warnings, "blocked": reasons + errors, "can_close": not reasons and not errors}


def _fiscal_close(c, user, actor, data, role, now):
    from . import period
    if not can(role, "accounting.close"): return _deny(c, actor, "fiscal.close", None, "réservé au propriétaire")
    year = _int(data.get("year"), "L'exercice", 2000, 2100)
    pv = closing_preview(c, year)
    if not pv["can_close"]: return {"status": "invalid", "message": " ".join(pv["blocked"])}
    if pv["warnings"] and not data.get("accept_warnings"): return {"status": "invalid", "message": "Avertissements à lire avant de clôturer.", "warnings": pv["warnings"]}
    alloc = data.get("allocation") or "none"
    if alloc not in ("none", "carry_forward", "reserves"): raise OpError("Affectation inconnue.")
    a, b = year_bounds(year); mov = _sum_lines([e for e in entries_between(c, a, b) if e["journal"] != "CL"]); mp = get_settings(c)["map"]
    lines1 = []
    for k in sorted(mov):
        if not (is_product(k) or is_charge(k)): continue
        bal = mov[k][0] - mov[k][1]
        if bal > 0: lines1.append((k, 0, bal))              # solde débiteur (charge) : on crédite pour solder
        elif bal < 0: lines1.append((k, -bal, 0))           # solde créditeur (produit) : on débite pour solder
    result = pv["result"]
    if result > 0: lines1.append(("131", 0, result))
    elif result < 0: lines1.append(("139", -result, 0))
    entries = [(1, f"Clôture de l'exercice {year} : solde des comptes de charges et de produits, résultat net", lines1)]
    if alloc != "none" and result != 0:
        if result > 0:
            dest = "121" if alloc == "carry_forward" else "106"
            entries.append((2, f"Affectation du résultat {year} (bénéfice) au compte {dest}", [("131", result, 0), (dest, 0, result)]))
        else:
            if alloc == "reserves": return {"status": "invalid", "message": "Une perte ne peut pas être affectée aux réserves : choisissez le report à nouveau ou laissez-la en résultat à affecter."}
            entries.append((2, f"Affectation du résultat {year} (perte) au report à nouveau", [("129", -result, 0), ("139", 0, -result)]))
    for no, label, ls in entries:
        td, tc = sum(x[1] for x in ls), sum(x[2] for x in ls)
        if td != tc: raise OpError("Écriture de clôture déséquilibrée : clôture annulée (anomalie).")
    note = _text(data.get("note"), "La note", 200)
    c.execute("INSERT INTO fiscal_years(year,start_date,end_date,status,result,allocation,note,version,seq,closed_at,closed_by) VALUES(?,?,?,'closed',?,?,?,1,?,?,?)", (year, a, b, result, alloc, note, next_seq(c), now, user["id"]))
    for no, label, ls in entries:
        for acc, dr, cr in ls: c.execute("INSERT INTO fiscal_lines(year,entry_no,label,account,debit,credit) VALUES(?,?,?,?,?,?)", (year, no, label, acc, dr, cr))
    cur = period.locked_through(c)
    if not cur or cur < b: c.execute("INSERT INTO meta(key,value) VALUES('acct_lock',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (b,))
    audit.record(c, actor, "fiscal.close", "fiscal_year", str(year), None, {"exercice": year, "résultat": result, "affectation": alloc, "avertissements acceptés": len(pv["warnings"]), "verrouillé jusqu'au": b}, note=note or None)
    return {"status": "ok", "year": year, "result": result}


def _fiscal_reopen(c, user, actor, data, role, now):
    if not can(role, "accounting.close"): return _deny(c, actor, "fiscal.reopen", None, "réservé au propriétaire")
    year = _int(data.get("year"), "L'exercice", 2000, 2100)
    done = closed_years(c)
    if year not in done: return {"status": "invalid", "message": "Cet exercice n'est pas clôturé."}
    if year != max(done): return {"status": "invalid", "message": f"Seul le dernier exercice clôturé ({max(done)}) peut être rouvert."}
    reason = _text(data.get("reason"), "Le motif", 200, True)
    if len(reason) < 3: raise OpError("Le motif de réouverture est obligatoire.")
    fy = c.execute("SELECT * FROM fiscal_years WHERE year=?", (year,)).fetchone()
    c.execute("DELETE FROM fiscal_lines WHERE year=?", (year,)); c.execute("DELETE FROM fiscal_years WHERE year=?", (year,))
    prev = max([y for y in done if y < year], default=None)
    new_lock = year_bounds(prev)[1] if prev else ""
    c.execute("INSERT INTO meta(key,value) VALUES('acct_lock',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (new_lock,))
    c.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='epoch'")
    next_seq(c)
    audit.record(c, actor, "fiscal.reopen", "fiscal_year", str(year), {"exercice": year, "résultat": fy["result"], "affectation": fy["allocation"], "clôturé le": fy["closed_at"]}, {"statut": "rouvert", "motif": reason, "verrou": new_lock or None})
    return {"status": "ok", "year": year}


HANDLERS = {"fiscal.close": _fiscal_close, "fiscal.reopen": _fiscal_reopen, "account.create": _account_create, "journal.create": _journal_create, "journal.cancel": _journal_cancel, "accounting.settings": _settings_update, "accounting.lock": _lock}
