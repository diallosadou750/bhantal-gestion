import uuid
from datetime import date

from conftest import make_user, op, push

TODAY = date.today()
T = TODAY.isoformat()
Y = TODAY.year - 1


def rep(c, kind="statements", **params):
    q = "&".join(f"{k}={v}" for k, v in {"kind": kind, "from": "2000-01-01", "to": T, **params}.items())
    r = c.get("/api/accounting/report?" + q)
    assert r.status_code == 200, r.text
    return r.json()


def bal(c, **params):
    return {r["account"]: r for r in rep(c, "balance", **params)["rows"]}


def preview(c, year):
    r = c.get(f"/api/accounting/closing?year={year}")
    assert r.status_code == 200, r.text
    return r.json()


def close(c, year=Y, **extra):
    return push(c, op("fiscal.close", {"year": year, "accept_warnings": True, **extra}))[0]


def docs_last_year(c):
    pid = push(c, op("product.create", {"id": str(uuid.uuid4()), "name": "Ciment", "price": 100_000}))[0]["product"]["id"]
    sup = push(c, op("supplier.create", {"id": str(uuid.uuid4()), "name": "F"}))[0]["supplier"]["id"]
    cid = push(c, op("client.create", {"id": str(uuid.uuid4()), "name": "Camara", "credit_limit": 50_000_000}))[0]["client"]["id"]
    push(c, op("purchase.create", {"supplier_id": sup, "kind": "cash", "pay_method": "Virement bancaire", "purchase_date": f"{Y}-03-01", "lines": [{"product_id": pid, "qty": 100, "unit_cost": 60_000}]}))
    s = push(c, op("sale.create", {"client_id": cid, "kind": "credit", "sale_date": f"{Y}-06-01", "due_date": f"{Y}-07-01", "lines": [{"product_id": pid, "qty": 10}]}))[0]["sale"]
    push(c, op("payment.create", {"sale_id": s["id"], "amount": 300_000, "method": "Virement bancaire", "pay_date": f"{Y}-06-15"}))
    push(c, op("expense.create", {"category": "Loyer et charges locatives", "amount": 50_000, "method": "Virement bancaire", "payee": "Bailleur", "expense_date": f"{Y}-09-10"}))
    return pid, sup, cid


def this_year_sale(c, cid, pid, qty=5):
    return push(c, op("sale.create", {"client_id": cid, "kind": "cash", "pay_method": "Orange Money", "lines": [{"product_id": pid, "qty": qty}]}))[0]["sale"]


def test_apercu_et_regles_de_cloture(owner, app_env):
    pid, sup, cid = docs_last_year(owner)
    pv = preview(owner, Y)
    assert pv["can_close"] is True and pv["result"] == 350_000 and pv["sales"] == 1_000_000 and pv["errors"] == [] and any("valider" in w for w in pv["warnings"])
    cur = preview(owner, TODAY.year)
    assert cur["can_close"] is False and any("pas terminé" in r for r in cur["blocked"])
    assert push(owner, op("fiscal.close", {"year": TODAY.year, "accept_warnings": True}))[0]["status"] == "invalid"
    compta, _ = make_user(owner, app_env, "compta", "accountant")
    assert push(compta, op("fiscal.close", {"year": Y, "accept_warnings": True}))[0]["status"] == "denied"
    r = push(owner, op("fiscal.close", {"year": Y}))[0]
    assert r["status"] == "invalid" and r["warnings"]                                  # les avertissements doivent être acceptés explicitement
    assert push(owner, op("fiscal.close", {"year": Y, "accept_warnings": True, "allocation": "bizarre"}))[0]["status"] == "invalid"


def test_cloture_solde_les_comptes_et_reporte_le_resultat(owner):
    pid, sup, cid = docs_last_year(owner)
    this_year_sale(owner, cid, pid)
    assert close(owner)["status"] == "ok"
    # le résultat de l'exercice clôturé se lit toujours AVANT ses écritures de clôture
    old = rep(owner, **{"from": f"{Y}-01-01", "to": f"{Y}-12-31"})
    assert old["income"]["result"] == 350_000 and old["income"]["sales"] == 1_000_000
    b_old = bal(owner, **{"from": f"{Y}-01-01", "to": f"{Y}-12-31"})
    assert b_old["701"]["close_debit"] == 0 and b_old["701"]["close_credit"] == 0 and b_old["601"]["close_debit"] == 0 and b_old["603"]["close_credit"] == 0       # comptes de gestion soldés
    assert b_old["131"]["close_credit"] == 350_000
    assert any(e["journal"] == "CL" for e in rep(owner, "journal", **{"from": f"{Y}-12-31", "to": f"{Y}-12-31"})["entries"])
    # nouvel exercice : on repart à zéro en charges et produits, les soldes de bilan sont repris
    b_new = bal(owner, **{"from": f"{TODAY.year}-01-01"})
    assert b_new["131"]["open_credit"] == 350_000 and b_new["311"]["open_debit"] == 5_400_000 and "701" in b_new and b_new["701"]["open_credit"] == 0 and b_new["701"]["credit"] == 500_000
    cur = rep(owner, **{"from": f"{TODAY.year}-01-01"})
    assert cur["income"]["sales"] == 500_000 and cur["income"]["result"] == 200_000            # 5 sacs × (100 000 − 60 000)
    bs = cur["balance_sheet"]
    assert bs["balanced"] is True and any(x["account"] == "130" and x["amount"] == 200_000 for x in bs["liabilities"]) and any(x["account"] == "131" and x["amount"] == 350_000 for x in bs["liabilities"])
    assert rep(owner, "balance")["balanced"] is True


def test_verrou_automatique_et_reglages_figes(owner, app_env):
    pid, sup, cid = docs_last_year(owner)
    close(owner)
    acc = owner.get("/api/sync/pull?since=0").json()["accounting"]
    assert acc["locked_through"] == f"{Y}-12-31" and acc["fiscal_years"][0]["year"] == Y and acc["fiscal_years"][0]["result"] == 350_000
    refused = push(owner, op("sale.create", {"client_id": cid, "kind": "cash", "pay_method": "Espèces", "sale_date": f"{Y}-12-30", "lines": [{"product_id": pid, "qty": 1}]}))[0]
    assert refused["status"] == "invalid" and "verrouillée" in refused["message"]
    low = push(owner, op("accounting.lock", {"through": f"{Y}-06-30", "reason": "essai"}))[0]
    assert low["status"] == "invalid" and "rouvrez d'abord l'exercice" in low["message"]
    assert push(owner, op("accounting.lock", {"through": "", "reason": "essai"}))[0]["status"] == "invalid"
    assert push(owner, op("settings.update", {"tax_rate": 18}))[0]["status"] == "invalid"
    assert push(owner, op("accounting.settings", {"purchase_tax_rate": 18}))[0]["status"] == "invalid"
    assert push(owner, op("accounting.lock", {"through": T}))[0]["status"] == "ok"            # on peut verrouiller PLUS loin, jamais en deçà
    assert close(owner)["status"] == "invalid"                                                 # déjà clôturé


def test_reouverture_proprietaire_seulement_motif_et_resultat_identique(owner, app_env):
    docs_last_year(owner)
    compta, _ = make_user(owner, app_env, "compta", "accountant")
    assert close(owner)["status"] == "ok"
    assert push(compta, op("fiscal.reopen", {"year": Y, "reason": "erreur"}))[0]["status"] == "denied"
    assert "motif" in push(owner, op("fiscal.reopen", {"year": Y, "reason": ""}))[0]["message"].lower()
    assert push(owner, op("fiscal.reopen", {"year": Y, "reason": "écriture oubliée, vue avec l'expert-comptable"}))[0]["status"] == "ok"
    acc = owner.get("/api/sync/pull?since=0").json()["accounting"]
    assert acc["fiscal_years"] == [] and acc["locked_through"] is None
    assert "131" not in bal(owner) and rep(owner, **{"from": f"{Y}-01-01", "to": f"{Y}-12-31"})["income"]["result"] == 350_000
    assert close(owner)["result"] == 350_000                                                   # la reclôture redonne le même résultat
    actions = [e["action"] for e in owner.get("/api/audit?limit=100").json()["entries"]]
    assert actions.count("fiscal.close") == 2 and actions.count("fiscal.reopen") == 1


def test_ordre_des_exercices_et_perte(owner):
    pid, sup, cid = docs_last_year(owner)
    push(owner, op("expense.create", {"category": "Loyer et charges locatives", "amount": 80_000, "method": "Virement bancaire", "payee": "Bailleur", "expense_date": f"{Y - 1}-05-05"}))
    pv = preview(owner, Y)
    assert pv["can_close"] is False and any("précédents" in r for r in pv["blocked"])
    assert close(owner, Y)["status"] == "invalid"
    assert close(owner, Y - 1, allocation="carry_forward")["result"] == -80_000
    b = bal(owner)
    assert b["129"]["close_debit"] == 80_000 and b["139"]["close_debit"] == 0 and b["139"]["close_credit"] == 0       # perte reportée à nouveau (débit) et compte de résultat soldé
    assert close(owner, Y)["status"] == "ok"
    assert rep(owner, "balance")["balanced"] is True and rep(owner)["balance_sheet"]["balanced"] is True


def test_affectation_du_benefice(owner):
    docs_last_year(owner)
    assert close(owner, allocation="reserves")["status"] == "ok"
    b = bal(owner)
    assert b["106"]["close_credit"] == 350_000 and b["131"]["close_credit"] == 0 and b["131"]["close_debit"] == 0
    push(owner, op("fiscal.reopen", {"year": Y, "reason": "choisir le report à nouveau"}))
    assert close(owner, allocation="carry_forward")["status"] == "ok"
    b = bal(owner)
    assert b["121"]["close_credit"] == 350_000 and "106" not in b


def test_avertissements_caisse_et_banque(owner):
    pid, sup, cid = docs_last_year(owner)
    push(owner, op("expense.create", {"category": "Transport et livraison", "amount": 10_000, "method": "Espèces", "payee": "x", "expense_date": f"{Y}-11-01"}))
    push(owner, op("bank.opening", {"account": "bank", "balance": 1_000_000, "date": f"{Y}-01-01"}))
    w = " ".join(preview(owner, Y)["warnings"])
    assert "caisse n'est pas clôturée" in w and "Banque : pas de rapprochement équilibré" in w
    push(owner, op("cash.close", {"close_date": f"{Y}-12-31", "opening": 0, "counted": 0, "note": "ouverture"}))
    assert "caisse n'est pas clôturée" not in " ".join(preview(owner, Y)["warnings"])


def test_acces_apercu_et_journal(owner, app_env):
    docs_last_year(owner)
    u, _ = make_user(owner, app_env, "marie", "sales")
    assert u.get(f"/api/accounting/closing?year={Y}").status_code == 403
    assert owner.get("/api/accounting/closing?year=1900").status_code == 422
    close(owner)
    assert owner.get("/api/audit/verify").json()["ok"] is True
