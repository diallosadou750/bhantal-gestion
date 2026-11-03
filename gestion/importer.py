"""Import de l'ancien classeur Excel (clients, produits, ventes, paiements, dépenses, fournisseurs, entrées de stock, stock initial) dans l'application.

    python -m gestion.importer Classeur.xlsx --user patron            # essai à blanc : rien n'est écrit, un rapport est affiché
    python -m gestion.importer Classeur.xlsx --user patron --apply    # import réel

Garanties :
- l'essai à blanc exécute exactement les mêmes règles que l'import réel, puis annule tout (aucune trace dans la base) ;
- relancer l'import ne crée jamais de doublon (identifiants déterministes) ;
- chaque ligne refusée est listée avec sa feuille et son numéro de ligne ; avec --apply, rien n'est écrit s'il y a des refus (sauf --ignore-errors) ;
- tout est journalisé (qui a importé, quand, quel fichier, son empreinte) ;
- les codes clients (C001…) et numéros de facture (FV-0001…) d'origine sont conservés ; les règles de crédit et de remise ne s'appliquent pas au passé ;
- importés aussi : les dépenses (feuille Depenses, catégories identiques à celles de l'application) ;
- fournisseurs, entrées de stock et stock initial (feuille Stocks, colonnes Q à W = entrées ; colonnes D et H = stock initial et seuil d'alerte) et factures fournisseurs (feuille Fournisseurs) :
    * chaque fournisseur nommé dans les entrées ou les factures est créé une fois (même nom, majuscules ou espaces différents = même fournisseur) ;
    * les entrées d'une même facture (N° bon / facture) forment UN achat ; si la facture existe dans la feuille Fournisseurs, l'échéance et le « payé à ce jour » en viennent ;
      sans facture correspondante l'achat est importé comme une DETTE à payer, échéance = date d'achat (visible dans « Dettes », à pointer ou à payer) : mieux vaut une dette de trop
      qu'une dette cachée ; le mode de paiement des acomptes est inconnu : « Autre » ;
    * le stock initial devient un ajustement « stock initial » daté du jour qui précède le premier mouvement (option --stock-initial-date) ;
    * une facture fournisseur sans entrée de stock ne peut pas devenir un achat (un achat porte des produits) : elle est listée dans le rapport, à saisir à la main (dépense) ;
    * si le classeur a été enregistré par Excel, le stock obtenu est comparé au « stock théorique » du classeur, produit par produit ;
- les relances (feuille Relances_Avoirs) sont ajoutées à la note du client, avec leur date : elles restent lisibles sans changer l'état des relances de l'application ;
- ne sont PAS importés : écritures comptables (non reprises).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import uuid
from collections import OrderedDict
from datetime import date, datetime
from pathlib import Path

from . import audit, db, ops, sales_ops
from .audit import Actor
from .permissions import METHODS

NS = uuid.UUID("6f2c1b0e-93f4-4b7e-8a51-5d1e0c9d7a11")
NOTE_MAX = 500


class ImportProblem(Exception):
    pass


class _DryRun(Exception):
    pass


def _uid(kind: str, key: str) -> str:
    return str(uuid.uuid5(NS, f"{kind}:{key}"))


def _iso(v):
    if isinstance(v, datetime): return v.date().isoformat()
    if isinstance(v, date): return v.isoformat()
    if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v.strip()): return v.strip()
    return None


def _num(v):
    if v in (None, ""): return None
    try: return int(round(float(v)))
    except (TypeError, ValueError): return None


def _s(v) -> str:
    if v is None: return ""
    if isinstance(v, float) and v.is_integer(): v = int(v)
    return str(v).strip()


def _rows(ws, r0: int, r1: int, cols: tuple[int, ...]):
    for r in range(r0, min(r1, ws.max_row) + 1):
        vals = [ws.cell(row=r, column=c).value for c in cols]
        if any(v not in (None, "") for v in vals): yield r, vals


def _discount(qty: int, p: dict) -> int:
    for qmin, rate in ((p["t3"], p["r3"]), (p["t2"], p["r2"]), (p["t1"], p["r1"])):
        if qmin and qty >= qmin: return int(round((rate or 0) * 100))
    return 0


def run(path: Path, username: str, apply: bool = False, ignore_errors: bool = False, stock_initial_date: str | None = None) -> dict:
    try:
        import openpyxl
    except ImportError as e:                                           # pragma: no cover
        raise ImportProblem("Le module openpyxl est nécessaire : pip install openpyxl") from e
    path = Path(path)
    if not path.exists(): raise ImportProblem(f"Fichier introuvable : {path}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    wb = openpyxl.load_workbook(path)                                  # formules : on lit les cellules de saisie
    try: wbv = openpyxl.load_workbook(path, data_only=True)            # valeurs enregistrées par Excel, si le fichier a été sauvegardé par Excel
    except Exception: wbv = None
    for need in ("Clients", "Produits", "Ventes", "Paiements"):
        if need not in wb.sheetnames: raise ImportProblem(f"Feuille « {need} » absente : ce n'est pas le classeur de gestion attendu.")
    rep = {"file": path.name, "sha256": digest, "applied": False, "errors": [], "warnings": [], "counts": {}, "not_imported": {}, "verification": {}}
    err = lambda sheet, row, msg: rep["errors"].append(f"Feuille {sheet}, ligne {row} : {msg}")
    warn = lambda sheet, row, msg: rep["warnings"].append(f"Feuille {sheet}, ligne {row} : {msg}")

    # --- lecture des feuilles
    clients = OrderedDict()
    for r, v in _rows(wb["Clients"], 5, 504, tuple(range(2, 11))):
        code = _s(wb["Clients"].cell(row=r, column=1).value)
        name = _s(v[0])
        if not name: continue
        if not re.fullmatch(r"C\d{3,6}", code): err("Clients", r, f"code client « {code} » invalide (attendu : C001…)."); continue
        if code in clients: err("Clients", r, f"code {code} en double."); continue
        notes = " · ".join(x for x in (("Secteur : " + _s(v[2])) if _s(v[2]) else "", ("Source : " + _s(v[8])) if _s(v[8]) else "", ("Commercial (Excel) : " + _s(v[5])) if _s(v[5]) else "", "importé du classeur Excel") if x)
        clients[code] = dict(row=r, data={"id": _uid("client", code), "code": code, "name": name, "kind": _s(v[1]), "phone": _s(v[3]), "zone": _s(v[4]), "credit_limit": _num(v[6]) or 0, "note": notes})
    relances = {}
    if "Relances_Avoirs" in wb.sheetnames:
        for r, v in _rows(wb["Relances_Avoirs"], 5, 1004, (1, 2, 4, 5, 6, 7, 8)):
            code = _s(v[1])
            if code not in clients: warn("Relances_Avoirs", r, f"relance du client {code or '(vide)'} absent de la feuille Clients : non reprise."); continue
            how = ", ".join(x for x in (_s(v[2]), _s(v[3])) if x); amount, promised = _num(v[6]), _iso(v[5])
            text = f"Relance du {_iso(v[0]) or '(sans date)'}" + (f" ({how})" if how else "") + (f" : {_s(v[4])}" if _s(v[4]) else "")
            if amount or promised: text += ", promis" + (f" {amount} GNF" if amount else "") + (f" pour le {promised}" if promised else "")
            data = clients[code]["data"]
            if len(data["note"]) + len(text) + 3 > NOTE_MAX: warn("Relances_Avoirs", r, f"note du client {code} pleine : relance non reprise."); continue
            data["note"] += " · " + text; relances[code] = relances.get(code, 0) + 1
    products, by_name = OrderedDict(), {}
    for r, v in _rows(wb["Produits"], 5, 204, tuple(range(1, 12))):
        code, name = _s(v[0]), _s(v[1])
        if not name: continue
        price = _num(v[4])
        if price is None or price < 0: err("Produits", r, f"prix invalide pour « {name} »."); continue
        if name in by_name: err("Produits", r, f"produit « {name} » en double."); continue
        if not code or code in products: code = ""                      # le serveur attribue un code
        key = code or name
        products[key] = dict(row=r, data={"id": _uid("product", key), "code": code, "name": name, "category": _s(v[2]), "unit": _s(v[3]), "price": price})
        by_name[name] = dict(id=products[key]["data"]["id"], t1=_num(v[5]), r1=v[6], t2=_num(v[7]), r2=v[8], t3=_num(v[9]), r3=v[10])
    sales = OrderedDict()
    ws, wsv = wb["Ventes"], (wbv["Ventes"] if wbv else None)
    for r, v in _rows(ws, 5, 2004, (1, 2, 3, 5, 6, 9, 10, 11, 12)):
        number = _s(v[1])
        if not number: err("Ventes", r, "numéro de facture manquant."); continue
        base = re.sub(r"-[A-Za-z]$", "", number)
        d, cid, prod, qty, kind, method, paid, due = _iso(v[0]), _s(v[2]), _s(v[3]), _num(v[4]), _s(v[5]).lower(), _s(v[6]), _num(v[7]) or 0, _iso(v[8])
        if d is None: err("Ventes", r, "date invalide."); continue
        if cid not in clients: err("Ventes", r, f"client {cid or '(vide)'} absent de la feuille Clients."); continue
        if prod not in by_name: err("Ventes", r, f"produit « {prod} » absent de la feuille Produits."); continue
        if not qty or qty <= 0: err("Ventes", r, "quantité invalide."); continue
        k = {"comptant": "cash", "crédit": "credit", "credit": "credit"}.get(kind)
        if k is None: err("Ventes", r, "type de vente : comptant ou crédit attendu."); continue
        if method and method not in METHODS: warn("Ventes", r, f"mode « {method} » inconnu : remplacé par « Autre »."); method = "Autre"
        g = sales.setdefault(base, dict(row=r, number=base if re.fullmatch(r"FV-\d{4,8}", base) else None, date=d, client=cid, kind=k, due=due, method=method, paid=0, lines=[], excel_total=0, excel_rows=[]))
        if (g["client"], g["date"], g["kind"]) != (cid, d, k): err("Ventes", r, f"la facture {base} mélange des clients, dates ou types différents."); continue
        p = by_name[prod]
        price = ws.cell(row=r, column=7).value                            # le prix réellement facturé (colonne G) prime sur le catalogue : il inclut déjà une éventuelle remise
        if isinstance(price, str) and price.startswith("="): price = wsv.cell(row=r, column=7).value if wsv is not None else None       # formule : valeur enregistrée par Excel, sinon le catalogue
        price = _num(price)
        g["lines"].append({"product_id": p["id"], "qty": qty, "unit_price": price, "discount_pct": 0} if price is not None and price >= 0 else {"product_id": p["id"], "qty": qty, "discount_pct": _discount(qty, p)}); g["paid"] += paid; g["method"] = g["method"] or method; g["due"] = g["due"] or due
        g["excel_rows"].append(r)
        if wsv is not None and isinstance(wsv.cell(row=r, column=8).value, (int, float)): g["excel_total"] = (g["excel_total"] or 0) + wsv.cell(row=r, column=8).value
        else: g["excel_total"] = None
    # --- Stocks (stock initial, seuil d'alerte, entrées) et Fournisseurs (factures)
    opening, entries = [], []
    st_ws = wb["Stocks"] if "Stocks" in wb.sheetnames else None
    if st_ws is not None:
        for p_ in products.values():
            r = p_["row"]; seuil, init = _num(st_ws.cell(row=r, column=8).value), _num(st_ws.cell(row=r, column=4).value)
            if seuil and seuil > 0: p_["data"]["reorder_level"] = seuil
            if init and init < 0: err("Stocks", r, f"stock initial négatif pour « {p_['data']['name']} ».")
            elif init: opening.append(dict(row=r, product=p_["data"]["id"], name=p_["data"]["name"], qty=init))
        for r, v in _rows(st_ws, 5, 1004, (17, 18, 19, 20, 21, 23)):
            d, sup, prod, qty, cost, ref = _iso(v[0]), re.sub(r"\s+", " ", _s(v[1])), _s(v[2]), _num(v[3]), _num(v[4]), _s(v[5])
            if d is None: err("Stocks", r, "entrée de stock : date invalide."); continue
            if not sup: err("Stocks", r, "entrée de stock : fournisseur manquant."); continue
            if prod not in by_name: err("Stocks", r, f"entrée de stock : produit « {prod or '(vide)'} » absent de la feuille Produits."); continue
            if not qty or qty <= 0: err("Stocks", r, "entrée de stock : quantité manquante ou invalide."); continue
            if cost is None or cost < 0: err("Stocks", r, f"entrée de stock : prix d'achat manquant ou invalide pour « {prod} »."); continue
            entries.append(dict(row=r, date=d, sup=sup, product=by_name[prod]["id"], qty=qty, cost=cost, ref=ref))
    invoices = {}
    if "Fournisseurs" in wb.sheetnames:
        for r, v in _rows(wb["Fournisseurs"], 5, 504, (1, 2, 3, 4, 5, 6)):
            ref, sup, ttc = _s(v[2]), re.sub(r"\s+", " ", _s(v[1])), _num(v[3])
            if not sup and not ref and not ttc: continue
            d = _iso(v[0])
            if d is None: err("Fournisseurs", r, "facture fournisseur : date invalide."); continue
            if not sup or not ref: err("Fournisseurs", r, "facture fournisseur : fournisseur ou n° de facture manquant."); continue
            if ttc is None or ttc < 0: err("Fournisseurs", r, f"facture {ref} : montant TTC invalide."); continue
            if (sup.casefold(), ref) in invoices: err("Fournisseurs", r, f"facture {ref} de {sup} en double."); continue
            due, paid = _iso(v[4]), _num(v[5]) or 0
            if paid < 0 or paid > ttc: warn("Fournisseurs", r, f"facture {ref} : « payé à ce jour » ({paid}) hors de 0 à {ttc} : ramené dans cet intervalle."); paid = max(0, min(paid, ttc))
            invoices[(sup.casefold(), ref)] = dict(row=r, supplier=sup, ref=ref, date=d, due=due, ttc=ttc, paid=paid)
    suppliers = OrderedDict()
    for e in entries: suppliers.setdefault(e["sup"].casefold(), dict(row=e["row"], sheet="Stocks", name=e["sup"]))
    for inv in invoices.values(): suppliers.setdefault(inv["supplier"].casefold(), dict(row=inv["row"], sheet="Fournisseurs", name=inv["supplier"]))
    groups = OrderedDict()
    for e in entries:
        g = groups.setdefault((e["sup"].casefold(), e["ref"] or e["date"]), dict(row=e["row"], sup=e["sup"], ref=e["ref"], dates=set(), lines=[]))
        g["dates"].add(e["date"]); g["lines"].append(e)
    purchases, matched = [], set()
    for (supk, key), g in groups.items():
        inv = invoices.get((supk, g["ref"])) if g["ref"] else None
        pdate = min(g["dates"]); total = sum(l["qty"] * l["cost"] for l in g["lines"])
        if inv is None:                                                    # pas de n° de facture sur les entrées : on cherche UNE facture du même fournisseur pour exactement ce montant
            cands = [i for k2, i in invoices.items() if k2[0] == supk and k2 not in matched and i["ttc"] == total]
            if len(cands) == 1:
                inv = cands[0]; g["ref"] = g["ref"] or inv["ref"]
                warn("Stocks", g["row"], f"entrées de {g['sup']} ({total}) rapprochées de la facture {inv['ref']} (feuille Fournisseurs, ligne {inv['row']}) par le fournisseur et le montant : vérifiez.")
            elif len(cands) > 1: warn("Stocks", g["row"], f"entrées de {g['sup']} ({total}) : plusieurs factures du même montant ({', '.join(i['ref'] for i in cands)}) : pas de rapprochement automatique.")
        if len(g["dates"]) > 1: warn("Stocks", g["row"], f"les entrées de {g['ref'] or key} ont des dates différentes : achat daté du plus ancien ({pdate}).")
        if inv:
            matched.add((inv["supplier"].casefold(), inv["ref"])); due = inv["due"] or pdate; paid = min(inv["paid"], total)
            if inv["ttc"] != total: warn("Fournisseurs", inv["row"], f"facture {g['ref']} : montant TTC {inv['ttc']} ≠ total des entrées de stock {total} : le total des entrées est repris.")
            if due < pdate: warn("Fournisseurs", inv["row"], f"facture {g['ref']} : échéance avant la date d'entrée : mise à la date d'entrée."); due = pdate
        else:
            due, paid = pdate, 0
            warn("Stocks", g["row"], f"entrées de {g['sup']} ({g['ref'] or 'sans n° de facture'}, {total}) sans facture correspondante dans la feuille Fournisseurs : importées comme dette à payer, échéance = date d'achat.")
        for part, i in enumerate(range(0, len(g["lines"]), 30)):
            chunk = g["lines"][i:i + 30]
            purchases.append(dict(row=g["row"], uid=f"{supk}|{key}|{part}", supplier=g["sup"], date=pdate, due=due, ref=(g["ref"] + (f" (suite {part + 1})" if part else "")) if g["ref"] else "",
                                  paid=paid if part == 0 else 0, lines=[{"product_id": l["product"], "qty": l["qty"], "unit_cost": l["cost"]} for l in chunk]))
    orphans = [inv for k, inv in invoices.items() if k not in matched]
    if orphans:
        rep["not_imported"]["Factures fournisseurs sans entrée de stock"] = len(orphans)
        for inv in orphans: warn("Fournisseurs", inv["row"], f"facture {inv['ref']} de {inv['supplier']} ({inv['ttc']}) sans entrée de stock : non importée (un achat porte des produits) ; à saisir à la main si elle est due.")
    movement_dates = [e["date"] for e in entries] + [g["date"] for g in sales.values()]
    opening_date = stock_initial_date or ((date.fromisoformat(min(movement_dates)) - __import__("datetime").timedelta(days=1)).isoformat() if movement_dates else date.today().isoformat())
    if opening_date > date.today().isoformat(): opening_date = date.today().isoformat()
    expenses = []
    if "Depenses" in wb.sheetnames:
        from .cash_ops import CAT
        for r, v in _rows(wb["Depenses"], 5, 1004, (1, 2, 3, 4, 5, 6, 8)):
            amount = _num(v[4])
            if not amount: continue
            d = _iso(v[0])
            if d is None: err("Depenses", r, "date invalide."); continue
            cat = _s(v[1])
            if cat not in CAT: warn("Depenses", r, f"catégorie « {cat or '(vide)'} » inconnue : classée dans « Autres charges »."); cat = "Autres charges"
            method = _s(v[5])
            if method not in METHODS: warn("Depenses", r, f"mode « {method or '(vide)'} » inconnu : remplacé par « Autre »."); method = "Autre"
            if not (_s(v[2]) or _s(v[3])): err("Depenses", r, "ni bénéficiaire ni description."); continue
            expenses.append(dict(row=r, date=d, category=cat, payee=_s(v[2]), desc=_s(v[3]), amount=amount, method=method, ref=_s(v[6])))
    pays = []
    for r, v in _rows(wb["Paiements"], 5, 2004, (1, 2, 4, 5, 6, 7)):
        amount = _num(v[3])
        if not amount: continue
        d = _iso(v[0])
        if d is None: err("Paiements", r, "date invalide."); continue
        pays.append(dict(row=r, date=d, client=_s(v[1]), base=re.sub(r"-[A-Za-z]$", "", _s(v[2])), amount=amount, method=_s(v[4]) if _s(v[4]) in METHODS else "Autre", ref=_s(v[5])))

    # --- application (dans une transaction ; annulée si essai à blanc)
    counts = {k: {"créés": 0, "déjà présents": 0, "refusés": 0} for k in ("clients", "produits", "ventes", "paiements", "dépenses", "fournisseurs", "stock initial", "achats", "relances")}
    try:
        with db.transaction() as c:
            user = c.execute("SELECT * FROM users WHERE username=? AND role='owner' AND active=1", (username,)).fetchone()
            if user is None: raise ImportProblem(f"« {username} » n'est pas un propriétaire actif.")
            actor = Actor(user["id"], user["username"], user["role"], f"import Excel : {path.name}", "")

            def go(kind, sheet, row, op):
                res = ops.apply_op(c, user, actor, op)
                if res["status"] == "ok": counts[kind]["déjà présents" if res.get("replayed") else "créés"] += 1
                else: counts[kind]["refusés"] += 1; err(sheet, row, res.get("message") or "refusé.")
                return res
            with ops.import_mode():
                for cl in clients.values():
                    res = go("clients", "Clients", cl["row"], {"op_id": _uid("op-client", cl["data"]["code"]), "type": "client.create", "data": cl["data"]})
                    counts["relances"]["refusés" if res["status"] != "ok" else "déjà présents" if res.get("replayed") else "créés"] += relances.get(cl["data"]["code"], 0)
                for p in products.values(): go("produits", "Produits", p["row"], {"op_id": _uid("op-product", p["data"]["id"]), "type": "product.create", "data": p["data"]})
                for sp in suppliers.values(): go("fournisseurs", sp["sheet"], sp["row"], {"op_id": _uid("op-supplier", sp["name"].casefold()), "type": "supplier.create", "data": {"id": _uid("supplier", sp["name"].casefold()), "name": sp["name"], "note": "importé du classeur Excel"}})
                for o in opening:
                    go("stock initial", "Stocks", o["row"], {"op_id": _uid("op-opening", o["product"]), "type": "stock.adjust",
                                                             "data": {"id": _uid("adjust", o["product"]), "product_id": o["product"], "qty": o["qty"], "reason": "initial", "adj_date": opening_date, "note": "Stock initial repris du classeur Excel"}})
                for pu in sorted(purchases, key=lambda x: (x["date"], x["row"])):
                    go("achats", "Stocks", pu["row"], {"op_id": _uid("op-purchase", pu["uid"]), "type": "purchase.create",
                                                       "data": {"id": _uid("purchase", pu["uid"]), "supplier_id": _uid("supplier", pu["supplier"].casefold()), "kind": "credit", "purchase_date": pu["date"], "due_date": pu["due"],
                                                                "paid_at_purchase": pu["paid"], "pay_method": "Autre" if pu["paid"] else "", "supplier_ref": pu["ref"], "note": "Importé du classeur Excel", "lines": pu["lines"]}})
                made = {}
                for base, g in sales.items():
                    if g["kind"] == "credit" and not g["due"]:
                        g["due"] = date.fromisoformat(g["date"]).replace().isoformat(); warn("Ventes", g["row"], f"facture {base} à crédit sans échéance : échéance mise à la date de vente.");
                    data = {"id": _uid("sale", base), "client_id": clients[g["client"]]["data"]["id"], "kind": g["kind"], "sale_date": g["date"], "lines": g["lines"], "pay_method": g["method"] or ("Autre" if g["kind"] == "cash" or g["paid"] else ""),
                            "due_date": g["due"] if g["kind"] == "credit" else None, "paid_at_sale": min(g["paid"], 10 ** 12) if g["kind"] == "credit" else 0, "note": "Importé du classeur Excel"}
                    if g["number"]: data["number"] = g["number"]
                    res = go("ventes", "Ventes", g["row"], {"op_id": _uid("op-sale", base), "type": "sale.create", "data": data})
                    if res["status"] == "ok":
                        made[base] = res["sale"]
                        if g["kind"] == "credit" and g["paid"] > res["sale"]["total"]: warn("Ventes", g["row"], f"acompte supérieur au total pour {base}.")
                for p in sorted(pays, key=lambda x: (x["date"], x["row"])):
                    s = made.get(p["base"])
                    if s is None: counts["paiements"]["refusés"] += 1; err("Paiements", p["row"], f"facture {p['base'] or '(vide)'} introuvable (ou refusée) : paiement non importé."); continue
                    own_client = clients.get(p["client"], {}).get("data", {}).get("id")
                    if own_client != s["client_id"]: counts["paiements"]["refusés"] += 1; err("Paiements", p["row"], f"la facture {p['base']} n'appartient pas au client {p['client'] or '(vide)'}."); continue
                    go("paiements", "Paiements", p["row"], {"op_id": _uid("op-pay", f"{p['base']}|{p['date']}|{p['amount']}|{p['ref']}|{p['row']}"), "type": "payment.create",
                                                           "data": {"id": _uid("payment", f"{p['base']}|{p['date']}|{p['amount']}|{p['ref']}|{p['row']}"), "sale_id": s["id"], "amount": p["amount"], "method": p["method"], "reference": p["ref"], "pay_date": p["date"]}})
                for e in expenses:
                    key = f"{e['date']}|{e['category']}|{e['amount']}|{e['payee']}|{e['ref']}|{e['row']}"
                    go("dépenses", "Depenses", e["row"], {"op_id": _uid("op-expense", key), "type": "expense.create", "data": {"id": _uid("expense", key), "expense_date": e["date"], "category": e["category"], "payee": e["payee"],
                                                                                                                                    "description": e["desc"], "amount": e["amount"], "method": e["method"], "reference": e["ref"]}})
            # --- vérification des montants contre les valeurs enregistrées par Excel (si le classeur a été sauvegardé par Excel)
            checked = mismatches = 0
            for base, g in sales.items():
                if base not in made: continue
                row = c.execute("SELECT * FROM sales WHERE id=?", (made[base]["id"],)).fetchone()
                tot = sales_ops.sale_totals(c, row)
                if g["excel_total"] is not None:
                    checked += 1
                    if abs(g["excel_total"] - row["total"]) > 1:
                        mismatches += 1; rep["warnings"].append(f"Facture {base} : total Excel {g['excel_total']:.0f} ≠ total calculé {row['total']} (ligne {g['excel_rows'][0]}).")
            from . import stock_ops
            levels = stock_ops.stock_levels(c); stock_checked = stock_mismatches = 0
            if wbv is not None and "Stocks" in wbv.sheetnames and (entries or opening):
                for p_ in products.values():
                    ex = wbv["Stocks"].cell(row=p_["row"], column=7).value
                    if isinstance(ex, (int, float)) and p_["data"]["id"] in levels:
                        stock_checked += 1
                        if abs(ex - levels[p_["data"]["id"]]) > 0:
                            stock_mismatches += 1; rep["warnings"].append(f"Stock de « {p_['data']['name']} » : stock théorique Excel {ex:.0f} ≠ stock calculé {levels[p_['data']['id']]} (Stocks, ligne {p_['row']}).")
            rep["verification"] = {"factures comparées au classeur": checked, "écarts": mismatches, "stocks comparés au classeur": stock_checked, "écarts de stock": stock_mismatches, "remarque": "" if checked else "Le classeur n'a pas de valeurs enregistrées (jamais ouvert et sauvegardé dans Excel) : les totaux sont recalculés par l'application et non comparés."}
            rep["counts"] = counts
            if rep["errors"] and apply and not ignore_errors: raise _DryRun()
            if apply:
                audit.record(c, actor, "import.excel", "import", digest[:12], None, {"fichier": path.name, "empreinte": digest, **{k: v["créés"] for k, v in counts.items()}}, note=f"{len(rep['errors'])} refus, {len(rep['warnings'])} avertissements")
                rep["applied"] = True
            else:
                raise _DryRun()
    except _DryRun:
        pass
    rep["counts"] = counts
    return rep


def format_report(rep: dict) -> str:
    out = [f"Fichier : {rep['file']}  (empreinte {rep['sha256'][:12]})", "Mode : " + ("IMPORT RÉEL — écrit dans la base" if rep["applied"] else "ESSAI À BLANC — rien n'a été écrit"), ""]
    for k, v in rep["counts"].items(): out.append(f"  {k:10s} créés : {v['créés']:4d} · déjà présents : {v['déjà présents']:4d} · refusés : {v['refusés']:4d}")
    if rep["not_imported"]: out += ["", "Non importé (pas encore géré par l'application) : " + ", ".join(f"{k} ({n} ligne(s))" for k, n in rep["not_imported"].items())]
    if rep["verification"]: out += ["", "Vérification : " + ", ".join(f"{k} : {v}" for k, v in rep["verification"].items() if v != "")]
    if rep["warnings"]: out += ["", f"Avertissements ({len(rep['warnings'])}) :"] + ["  - " + w for w in rep["warnings"][:30]]
    if rep["errors"]: out += ["", f"REFUS ({len(rep['errors'])}) :"] + ["  - " + e for e in rep["errors"][:50]]
    return "\n".join(out)


def main() -> int:
    p = argparse.ArgumentParser(description="Importe le classeur Excel de gestion (clients, produits, ventes, paiements, dépenses, fournisseurs, entrées de stock, stock initial).")
    p.add_argument("--stock-initial-date", help="date du stock initial (AAAA-MM-JJ) ; par défaut : la veille du premier mouvement du classeur")
    p.add_argument("fichier"); p.add_argument("--user", required=True, help="identifiant d'un propriétaire actif : l'import est journalisé à son nom")
    p.add_argument("--apply", action="store_true", help="écrire réellement (sans cette option : essai à blanc)")
    p.add_argument("--ignore-errors", action="store_true", help="avec --apply : importer les lignes valides même s'il y a des refus")
    a = p.parse_args()
    db.init_db()
    try: rep = run(Path(a.fichier), a.user, apply=a.apply, ignore_errors=a.ignore_errors, stock_initial_date=a.stock_initial_date)
    except ImportProblem as e:
        print("Import impossible :", e); return 2
    print(format_report(rep))
    if rep["errors"] and a.apply and not a.ignore_errors:
        print("\nRien n'a été écrit à cause des refus ci-dessus. Corrigez le classeur, ou relancez avec --ignore-errors pour importer le reste."); return 1
    if not a.apply: print("\nPour importer réellement : relancez la même commande avec --apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
