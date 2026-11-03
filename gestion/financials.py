"""États financiers de synthèse : bilan (brut, amortissements, net), compte de résultat par soldes intermédiaires, tableau des flux de trésorerie.

AVERTISSEMENT : présentation INSPIRÉE de la structure SYSCOHADA (bilan actif/passif, marge commerciale, valeur ajoutée, excédent brut d'exploitation, résultats
d'exploitation, financier, HAO et net ; flux d'exploitation, d'investissement et de financement). Elle est établie sans accès au texte officiel complet : ni les
libellés, ni les rubriques, ni les codes de renvoi, ni les notes annexes ne sont ceux de la liasse officielle. Ces états aident à préparer le travail de
l'expert-comptable ; ils ne remplacent pas la liasse à déposer.

Tout est calculé à partir des mêmes écritures que la balance : le bilan est équilibré par construction et le résultat net de ces états est contrôlé contre celui
du compte de résultat de l'onglet Synthèse.
"""
from __future__ import annotations

import sqlite3

from . import accounting as acc

# ---------------------------------------------------------------- compte de résultat par soldes intermédiaires
BUCKET_LABELS = {
    "ventes_march": "Ventes de marchandises", "achats_march": "Achats de marchandises", "var_march": "Variation de stocks de marchandises",
    "ventes_prod": "Ventes de produits et services", "accessoires": "Produits accessoires", "production_autre": "Production stockée ou immobilisée",
    "consommations": "Autres achats, transports et services extérieurs", "subventions": "Subventions d'exploitation", "impots": "Impôts et taxes", "personnel": "Charges de personnel",
    "autres_produits": "Autres produits", "reprises": "Reprises et transferts de charges", "dotations": "Dotations aux amortissements et provisions", "autres_charges": "Autres charges",
    "autres_exploit": "Autres éléments d'exploitation (comptes non classés)", "fin_prod": "Revenus et gains financiers", "fin_charges": "Frais et pertes financiers",
    "hao_prod": "Produits hors activités ordinaires (dont cessions d'immobilisations)", "hao_charges": "Charges hors activités ordinaires (dont valeur comptable des cessions)",
    "participation": "Participation des travailleurs", "impot": "Impôts sur le résultat",
}


def bucket(k: str) -> str | None:
    """Rubrique d'un compte de gestion (classes 6, 7, 8) ; None pour les comptes de bilan."""
    p2, p3 = k[:2], k[:3]
    if k[0] == "6":
        if p3 == "601": return "achats_march"
        if p3 == "603": return "var_march"
        if p2 in ("60", "61", "62", "63"): return "consommations"
        return {"64": "impots", "65": "autres_charges", "66": "personnel", "67": "fin_charges", "68": "dotations", "69": "dotations"}.get(p2, "autres_exploit")
    if k[0] == "7":
        if p3 == "701": return "ventes_march"
        if p3 == "707": return "accessoires"
        if p2 == "70": return "ventes_prod"
        return {"71": "subventions", "72": "production_autre", "73": "production_autre", "75": "autres_produits", "77": "fin_prod", "78": "reprises", "79": "reprises"}.get(p2, "autres_exploit")
    if k[0] == "8":
        return {"81": "hao_charges", "83": "hao_charges", "85": "hao_charges", "82": "hao_prod", "84": "hao_prod", "86": "hao_prod", "88": "hao_prod", "87": "participation", "89": "impot"}.get(p2, "autres_exploit")
    return None


def income_statement(mov: dict) -> dict:
    """mov : {compte: [débit, crédit]} de la période. Chaque rubrique est exprimée en contribution au résultat (produit +, charge −)."""
    b = {k: 0 for k in BUCKET_LABELS}
    for a, (dr, cr) in mov.items():
        k = bucket(a)
        if k: b[k] += cr - dr
    mc = b["ventes_march"] + b["achats_march"] + b["var_march"]
    va = mc + b["ventes_prod"] + b["accessoires"] + b["production_autre"] + b["consommations"]
    ebe = va + b["subventions"] + b["impots"] + b["personnel"]
    re_ = ebe + b["autres_produits"] + b["reprises"] + b["dotations"] + b["autres_charges"] + b["autres_exploit"]
    rf = b["fin_prod"] + b["fin_charges"]
    rao = re_ + rf
    hao = b["hao_prod"] + b["hao_charges"]
    net = rao + hao + b["participation"] + b["impot"]
    lines = []

    def add(key, kind="line"):
        lines.append({"label": BUCKET_LABELS[key], "amount": b[key], "kind": kind})

    def tot(label, v): lines.append({"label": label, "amount": v, "kind": "total"})
    for k in ("ventes_march", "achats_march", "var_march"): add(k)
    tot("MARGE COMMERCIALE", mc)
    for k in ("ventes_prod", "accessoires", "production_autre", "consommations"): add(k)
    tot("VALEUR AJOUTÉE", va)
    for k in ("subventions", "impots", "personnel"): add(k)
    tot("EXCÉDENT BRUT D'EXPLOITATION", ebe)
    for k in ("autres_produits", "reprises", "dotations", "autres_charges", "autres_exploit"):
        if b[k] or k in ("dotations",): add(k)
    tot("RÉSULTAT D'EXPLOITATION", re_)
    for k in ("fin_prod", "fin_charges"): add(k)
    tot("RÉSULTAT FINANCIER", rf)
    tot("RÉSULTAT DES ACTIVITÉS ORDINAIRES", rao)
    for k in ("hao_prod", "hao_charges"): add(k)
    tot("RÉSULTAT HORS ACTIVITÉS ORDINAIRES", hao)
    for k in ("participation", "impot"):
        if b[k]: add(k)
    tot("RÉSULTAT NET", net)
    return {"lines": lines, "turnover": b["ventes_march"] + b["ventes_prod"] + b["accessoires"], "gross_margin": mc, "added_value": va, "ebitda": ebe, "operating_result": re_,
            "financial_result": rf, "ordinary_result": rao, "extraordinary_result": hao, "net_result": net}


# ---------------------------------------------------------------- bilan présenté
A_GROUPS = ["Immobilisations incorporelles", "Immobilisations corporelles", "Immobilisations financières", "Stocks", "Créances et emplois assimilés", "Trésorerie-actif"]
P_GROUPS = ["Capitaux propres", "Dettes financières et ressources assimilées", "Passif circulant", "Trésorerie-passif"]


def _asset_group(k: str) -> str:
    p2 = k[:2]
    if k[0] == "2":
        if p2 in ("26", "27"): return A_GROUPS[2]
        return A_GROUPS[0] if p2 == "21" else A_GROUPS[1]
    if k[0] == "3": return A_GROUPS[3]
    if k[0] == "5": return A_GROUPS[5]
    return A_GROUPS[4]


def _contra_group(k: str) -> str:
    p2, d = k[:2], k[2]
    if p2 in ("28", "29"): return A_GROUPS[0] if d == "1" else (A_GROUPS[2] if d in ("6", "7") else A_GROUPS[1])
    if p2 == "39": return A_GROUPS[3]
    if p2 == "49": return A_GROUPS[4]
    return A_GROUPS[5]


def _liab_group(k: str) -> str:
    p2 = k[:2]
    if p2 in ("10", "11", "12", "13", "14"): return P_GROUPS[0]
    if p2 in ("15", "16", "17", "18", "19") or k.startswith("462"): return P_GROUPS[1]
    if k[0] == "5": return P_GROUPS[3]
    return P_GROUPS[2]


CONTRA = ("28", "29", "39", "49", "59")


def balance_sheet(c: sqlite3.Connection, b: str, allen=None) -> dict:
    allen = allen if allen is not None else acc.auto_entries(c); names = acc.account_names(c)
    cum = acc._sum_lines(acc.entries_between(c, None, b, allen))
    ag: dict[str, dict] = {g: {"gross": 0, "dep": 0, "rows": []} for g in A_GROUPS}
    pg: dict[str, dict] = {g: {"amount": 0, "rows": []} for g in P_GROUPS}
    res = 0
    for k in sorted(cum):
        bal = cum[k][0] - cum[k][1]
        if k[0] in "678":
            res += -bal
            continue
        if not bal: continue
        if k[:2] in CONTRA:
            g = _contra_group(k)
            if bal < 0: ag[g]["dep"] += -bal; ag[g]["rows"].append({"account": k, "name": names.get(k, ""), "amount": bal})
            else: ag[_asset_group(k)]["gross"] += bal; ag[_asset_group(k)]["rows"].append({"account": k, "name": names.get(k, ""), "amount": bal})
        elif k[:2] in ("10", "11", "12", "13", "14"):                                   # capitaux : un solde débiteur (report à nouveau débiteur, perte) vient en déduction
            g = P_GROUPS[0]; pg[g]["amount"] += -bal; pg[g]["rows"].append({"account": k, "name": names.get(k, ""), "amount": -bal})
        elif bal > 0:
            g = _asset_group(k); ag[g]["gross"] += bal; ag[g]["rows"].append({"account": k, "name": names.get(k, ""), "amount": bal})
        else:
            g = _liab_group(k); pg[g]["amount"] += -bal; pg[g]["rows"].append({"account": k, "name": names.get(k, ""), "amount": -bal})
    if res:
        pg[P_GROUPS[0]]["amount"] += res
        pg[P_GROUPS[0]]["rows"].append({"account": "130" if res > 0 else "139", "name": "Résultat des exercices non clôturés " + ("(bénéfice)" if res > 0 else "(perte)"), "amount": res})
    actif = [{"group": g, "gross": v["gross"], "dep": v["dep"], "net": v["gross"] - v["dep"], "rows": v["rows"]} for g, v in ag.items() if v["rows"]]
    passif = [{"group": g, "amount": v["amount"], "rows": v["rows"]} for g, v in pg.items() if v["rows"]]
    at, pt = sum(x["net"] for x in actif), sum(x["amount"] for x in passif)
    return {"as_of": b, "assets": actif, "liabilities": passif, "assets_total": at, "liabilities_total": pt, "gross_total": sum(x["gross"] for x in actif), "dep_total": sum(x["dep"] for x in actif), "balanced": at == pt}


# ---------------------------------------------------------------- tableau des flux de trésorerie
CASH_PREFIX = ("52", "53", "57", "58")


def cash_flow(c: sqlite3.Connection, a: str | None, b: str, allen=None) -> dict:
    allen = allen if allen is not None else acc.auto_entries(c)
    before = acc._sum_lines(acc.entries_between(c, None, acc._day_before(a), allen) if a else [])
    mov = acc._sum_lines([e for e in acc.entries_between(c, a, b, allen) if e["journal"] != "CL"])
    cashbal = lambda t: sum(v[0] - v[1] for k, v in t.items() if k[:2] in CASH_PREFIX)
    opening = cashbal(before); change = cashbal(mov)
    g = {"result": 0, "addback": 0, "bfr": 0, "invest": 0, "fin": 0, "other": 0}
    for k, (dr, cr) in mov.items():
        if k[:2] in CASH_PREFIX: continue
        f = -(dr - cr); p2 = k[:2]
        if k[0] in "678": g["result"] += f
        elif p2 in ("28", "29", "39", "49", "59", "19"): g["addback"] += f
        elif k[0] == "2": g["invest"] += f
        elif p2 in ("10", "11", "12", "13", "14", "15", "16", "17", "18", "19") or k.startswith("462"): g["fin"] += f
        elif k[0] in "34": g["bfr"] += f
        else: g["other"] += f
    op = g["result"] + g["addback"] + g["bfr"] + g["other"]
    total = op + g["invest"] + g["fin"]
    lines = [{"label": "Trésorerie au début de la période", "amount": opening, "kind": "total"},
             {"label": "Résultat net de la période", "amount": g["result"], "kind": "line"},
             {"label": "Amortissements, dépréciations et provisions (charges sans décaissement, nettes des reprises et sorties)", "amount": g["addback"], "kind": "line"},
             {"label": "Variation des stocks, créances et dettes d'exploitation", "amount": g["bfr"] + g["other"], "kind": "line"},
             {"label": "FLUX DE TRÉSORERIE D'EXPLOITATION", "amount": op, "kind": "total"},
             {"label": "Acquisitions et cessions d'immobilisations (valeur brute)", "amount": g["invest"], "kind": "line"},
             {"label": "FLUX D'INVESTISSEMENT", "amount": g["invest"], "kind": "total"},
             {"label": "Capital, réserves, comptes d'associés et emprunts", "amount": g["fin"], "kind": "line"},
             {"label": "FLUX DE FINANCEMENT", "amount": g["fin"], "kind": "total"},
             {"label": "VARIATION DE TRÉSORERIE", "amount": total, "kind": "total"},
             {"label": "Trésorerie à la fin de la période", "amount": opening + change, "kind": "total"}]
    return {"from": a, "to": b, "lines": lines, "opening": opening, "closing": opening + change, "change": change, "operating": op, "investing": g["invest"], "financing": g["fin"], "balanced": total == change,
            "note": "Méthode indirecte simplifiée. La trésorerie regroupe banque, mobile money et caisse. Le produit d'une cession figure dans le résultat et les amortissements, non dans l'investissement."}


# ---------------------------------------------------------------- ensemble
def financial_statements(c: sqlite3.Connection, a: str | None, b: str) -> dict:
    allen = acc.auto_entries(c)
    mov = acc._sum_lines([e for e in acc.entries_between(c, a, b, allen) if e["journal"] != "CL"])
    inc = income_statement(mov); bs = balance_sheet(c, b, allen); cf = cash_flow(c, a, b, allen)
    ref = acc.statements(c, a, b)["income"]["result"]
    checks = [{"label": "Bilan équilibré (actif = passif)", "ok": bs["balanced"]}, {"label": "Résultat net identique à celui de la synthèse", "ok": inc["net_result"] == ref},
              {"label": "Tableau des flux : variation = trésorerie fin − début", "ok": cf["balanced"]}]
    co = c.execute("SELECT value FROM meta WHERE key='company'").fetchone()
    return {"from": a, "to": b, "income": inc, "balance_sheet": bs, "cash_flow": cf, "checks": checks, "all_ok": all(x["ok"] for x in checks),
            "notes": ["Comptabilité tenue en partie double, écritures déduites des documents de l'application.", "Amortissements : linéaires, au mois entier, durées par catégorie indicatives.",
                      "Stocks : inventaire intermittent, valorisés au coût moyen d'achat (estimation).", "Exercice : année civile.",
                      "Présentation inspirée de la structure SYSCOHADA, sans les rubriques, codes et notes annexes officiels : à faire établir et valider par un expert-comptable avant tout dépôt."]}
