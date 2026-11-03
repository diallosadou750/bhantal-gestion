import uuid
from datetime import date

from conftest import op, push

T = date.today().isoformat()
Y = date.today().year - 1


def fin(c, **params):
    q = "&".join(f"{k}={v}" for k, v in {"kind": "financials", "from": "2000-01-01", "to": T, **params}.items())
    r = c.get("/api/accounting/report?" + q)
    assert r.status_code == 200, r.text
    return r.json()


def line(sec, label):
    return next(x["amount"] for x in sec["lines"] if x["label"].startswith(label))


def activite(c):
    pid = push(c, op("product.create", {"id": str(uuid.uuid4()), "name": "Ciment", "price": 100_000}))[0]["product"]["id"]
    sup = push(c, op("supplier.create", {"id": str(uuid.uuid4()), "name": "F"}))[0]["supplier"]["id"]
    cid = push(c, op("client.create", {"id": str(uuid.uuid4()), "name": "Camara", "credit_limit": 50_000_000}))[0]["client"]["id"]
    push(c, op("bank.opening", {"account": "bank", "balance": 10_000_000, "date": f"{Y}-01-01"}))
    push(c, op("purchase.create", {"supplier_id": sup, "kind": "cash", "pay_method": "Virement bancaire", "purchase_date": f"{Y}-03-01", "lines": [{"product_id": pid, "qty": 100, "unit_cost": 60_000}]}))
    s = push(c, op("sale.create", {"client_id": cid, "kind": "credit", "sale_date": f"{Y}-06-01", "due_date": f"{Y}-07-01", "lines": [{"product_id": pid, "qty": 10}]}))[0]["sale"]
    push(c, op("payment.create", {"sale_id": s["id"], "amount": 300_000, "method": "Virement bancaire", "pay_date": f"{Y}-06-15"}))
    push(c, op("expense.create", {"category": "Loyer et charges locatives", "amount": 50_000, "method": "Virement bancaire", "payee": "Bailleur", "expense_date": f"{Y}-09-10"}))
    push(c, op("asset.create", {"id": str(uuid.uuid4()), "name": "Ordinateur", "category": "Matériel informatique et de bureau", "origin": "purchase", "acquisition_date": f"{Y}-04-01", "cost": 1_200_000, "life_years": 3, "pay_mode": "Virement bancaire"}))


def test_etats_coherents_et_soldes_intermediaires(owner):
    activite(owner)
    r = fin(owner, **{"from": f"{Y}-01-01", "to": f"{Y}-12-31"})
    assert r["all_ok"] is True and all(x["ok"] for x in r["checks"])
    inc = r["income"]
    assert inc["turnover"] == 1_000_000 and inc["gross_margin"] == 400_000 and inc["net_result"] == 400_000 - 50_000 - 300_000
    assert line(inc, "MARGE COMMERCIALE") == 400_000 and line(inc, "Dotations") == -300_000 and line(inc, "RÉSULTAT NET") == inc["net_result"]
    cf = r["cash_flow"]
    assert cf["opening"] == 0 and cf["financing"] == 10_000_000 and cf["investing"] == -1_200_000 and cf["closing"] == 10_000_000 - 6_000_000 - 50_000 - 1_200_000 + 300_000 and cf["balanced"] is True


def test_bilan_brut_amortissements_net(owner):
    activite(owner)
    bs = fin(owner)["balance_sheet"]
    assert bs["balanced"] is True and bs["assets_total"] == bs["liabilities_total"]
    imm = next(g for g in bs["assets"] if g["group"] == "Immobilisations corporelles")
    assert imm["gross"] == 1_200_000 and imm["dep"] > 0 and imm["net"] == imm["gross"] - imm["dep"]
    assert any(g["group"] == "Capitaux propres" for g in bs["liabilities"])


def test_apres_cloture_et_cession(owner):
    activite(owner)
    assert push(owner, op("fiscal.close", {"year": Y, "accept_warnings": True}))[0]["status"] == "ok"
    a = owner.get("/api/sync/pull?since=0").json()["assets"][0]
    push(owner, op("asset.dispose", {"id": a["id"], "base_version": a["version"], "kind": "sold", "price": 500_000, "method": "Virement bancaire", "date": T}))
    r = fin(owner)
    assert r["all_ok"] is True
    old = fin(owner, **{"from": f"{Y}-01-01", "to": f"{Y}-12-31"})
    assert old["all_ok"] is True and old["income"]["net_result"] == 400_000 - 50_000 - 300_000        # le résultat d'un exercice clôturé se lit avant ses écritures de clôture
    assert any(x["label"].startswith("Produits hors") and x["amount"] == 500_000 for x in r["income"]["lines"])


def test_droits_et_demo(owner, app_env):
    from conftest import make_user
    sales, _ = make_user(owner, app_env, "marie", "sales")
    assert sales.get(f"/api/accounting/report?kind=financials&from=2000-01-01&to={T}").status_code == 403
