import uuid
from datetime import date, timedelta

from conftest import make_user, op, push

TODAY = date.today()
T = TODAY.isoformat()
PREV = (TODAY.replace(day=1) - timedelta(days=1)).replace(day=10)           # le 10 du mois précédent
PM, CM = PREV.isoformat()[:7], T[:7]


def rep(c, **q):
    qs = "&".join(f"{k}={v}" for k, v in q.items())
    r = c.get("/api/reports/sales" + ("?" + qs if qs else ""))
    assert r.status_code == 200, r.text
    return r.json()


def prod(c, price=100_000):
    return push(c, op("product.create", {"id": str(uuid.uuid4()), "name": "Ciment " + uuid.uuid4().hex[:3], "price": price}))[0]["product"]["id"]


def client(c, name="Client"):
    return push(c, op("client.create", {"id": str(uuid.uuid4()), "name": name + uuid.uuid4().hex[:3], "credit_limit": 90_000_000}))[0]["client"]["id"]


def vente(c, cid, pid, qty, date_=None, kind="cash", **extra):
    data = {"client_id": cid, "kind": kind, "lines": [{"product_id": pid, "qty": qty, **extra}]}
    if kind == "cash": data["pay_method"] = "Espèces"
    else: data["due_date"] = (TODAY + timedelta(days=30)).isoformat()
    if date_: data["sale_date"] = date_
    r = push(c, op("sale.create", data))[0]
    assert r["status"] == "ok", r
    return r["sale"]


def scenario(owner, app_env):
    marie, mu = make_user(owner, app_env, "marie", "sales", "Marie Camara")
    pid = prod(owner)
    vente(owner, client(owner), pid, 10)                                      # patron, ce mois-ci : 1 000 000
    s_prev = vente(owner, client(owner), pid, 5, PREV.isoformat())            # patron, mois précédent : 500 000
    cm = client(marie)
    vente(marie, cm, pid, 5, discount_pct=10)                                 # Marie : 450 000 (remise de 50 000 sur 500 000)
    cr = vente(marie, cm, pid, 2, kind="credit")                              # Marie, à crédit : 200 000
    push(marie, op("payment.create", {"sale_id": cr["id"], "amount": 50_000, "method": "Espèces", "pay_date": T}))
    return marie, pid, s_prev


def test_par_vendeur_et_par_mois(owner, app_env):
    scenario(owner, app_env)
    r = rep(owner, **{"from": PREV.isoformat()[:8] + "01", "to": T})
    s = {x["name"]: x for x in r["by_seller"]}
    assert s["Le Patron"]["sales"] == 2 and s["Le Patron"]["revenue"] == 1_500_000 and s["Le Patron"]["share"] == round(1_500_000 * 100 / 2_150_000, 1)
    m = s["Marie Camara"]
    assert m["sales"] == 2 and m["revenue"] == 650_000 and m["credit_sales"] == 1 and m["credit_share"] == 50.0 and m["discount"] == 50_000 and m["discount_pct"] == round(50_000 * 100 / 700_000, 1)
    assert m["collected"] == 450_000 + 50_000 and m["balance"] == 150_000 and m["avg_basket"] == 325_000
    assert [x["name"] for x in r["by_seller"]][0] == "Le Patron"                # classés par montant décroissant
    assert r["months"] == [PM, CM] and r["totals"]["revenue"] == 2_150_000 and r["totals"]["collected"] == 2_000_000 and r["totals"]["balance"] == 150_000
    mo = {x["month"]: x for x in r["by_month"]}
    assert mo[PM]["sales"] == 1 and mo[PM]["revenue"] == 500_000 and mo[PM]["growth_pct"] is None
    assert mo[CM]["sales"] == 3 and mo[CM]["revenue"] == 1_650_000 and mo[CM]["growth_pct"] == 230.0 and mo[CM]["avg_basket"] == 550_000
    assert mo[CM]["collected"] == 1_000_000 + 450_000 + 50_000                  # acomptes à la vente + paiements du mois
    assert m["by_month"][CM] == 650_000 and s["Le Patron"]["by_month"][PM] == 500_000


def test_annulee_hors_taxe_et_periode(owner, app_env):
    marie, pid, s_prev = scenario(owner, app_env)
    push(owner, op("settings.update", {"tax_rate": 18}))
    v = vente(owner, client(owner), pid, 1)
    assert rep(owner, **{"from": T, "to": T})["totals"]["revenue_ht"] > 0
    r = rep(owner, **{"from": PREV.isoformat()[:8] + "01", "to": T})
    assert r["by_seller"][0]["revenue_ht"] <= r["by_seller"][0]["revenue"]
    push(owner, op("sale.cancel", {"id": s_prev["id"], "base_version": s_prev["version"], "reason": "client parti"}))
    r2 = rep(owner, **{"from": PREV.isoformat()[:8] + "01", "to": T})
    patron = next(x for x in r2["by_seller"] if x["name"] == "Le Patron")
    assert patron["cancelled"] == 1 and patron["cancelled_amount"] == 500_000 and patron["revenue"] == 1_100_000 and r2["totals"]["cancelled"] == 1       # annulée : signalée, jamais comptée
    assert rep(owner, **{"from": "2000-01-01", "to": "2000-02-01"})["totals"]["sales"] == 0


def test_un_commercial_ne_voit_que_les_siennes_et_droits(owner, app_env):
    marie, pid, _ = scenario(owner, app_env)
    r = rep(marie, **{"from": PREV.isoformat()[:8] + "01", "to": T})
    assert r["scope"] == "own" and [x["name"] for x in r["by_seller"]] == ["Marie Camara"] and r["totals"]["revenue"] == 650_000 and "Vos ventes" in r["note"]
    assert rep(owner)["scope"] == "all"
    cash, _ = make_user(owner, app_env, "paul", "cashier")
    assert cash.get("/api/reports/sales").status_code == 403 and cash.get("/api/reports/sales.csv").status_code == 403
    for role, name in (("accountant", "compta"), ("auditor", "expert")):
        u, _ = make_user(owner, app_env, name, role)
        assert rep(u)["scope"] == "all"
    assert owner.get("/api/reports/sales?from=2026-13-01").status_code == 422


def test_csv_sans_injection(owner, app_env):
    evil, _ = make_user(owner, app_env, "evil", "sales", "=CMD|x")
    cid = client(evil)
    vente(evil, cid, prod(owner), 1)
    r = owner.get("/api/reports/sales.csv?kind=seller")
    txt = r.content.decode("utf-8-sig")
    assert r.status_code == 200 and "'=CMD|x" in txt and ";=CMD" not in txt
    assert "mois;ventes" in owner.get("/api/reports/sales.csv?kind=month").content.decode("utf-8-sig") and owner.get("/api/reports/sales.csv?kind=autre").status_code == 422
    assert any(e["action"] == "report.exported" for e in owner.get("/api/audit?limit=10").json()["entries"])


def prod_cat(c, cat, price=100_000):
    return push(c, op("product.create", {"id": str(uuid.uuid4()), "name": "P " + uuid.uuid4().hex[:4], "category": cat, "price": price}))[0]["product"]["id"]


def test_ventes_par_categorie(owner, app_env):
    mat, alim, sans = prod_cat(owner, "Matériaux"), prod_cat(owner, "Alimentaire", 50_000), prod_cat(owner, "")
    cid = client(owner)
    vente(owner, cid, mat, 3)                      # 300 000
    vente(owner, cid, alim, 4)                     # 200 000
    vente(owner, cid, mat, 1)                      # 100 000
    vente(owner, cid, sans, 1)                     # 100 000
    r = rep(owner)
    cats = {g["category"]: g for g in r["by_category"]}
    assert cats["Matériaux"]["revenue"] == 400_000 and cats["Matériaux"]["qty"] == 4 and cats["Matériaux"]["sales"] == 2 and cats["Matériaux"]["share"] == round(400_000 * 100 / 700_000, 1)
    assert cats["Alimentaire"]["revenue"] == 200_000 and cats["Sans catégorie"]["revenue"] == 100_000
    assert r["by_category"][0]["category"] == "Matériaux"                           # classées par montant décroissant
    assert sum(g["revenue"] for g in r["by_category"]) == r["totals"]["revenue"]
    txt = owner.get("/api/reports/sales.csv?kind=category").content.decode("utf-8-sig")
    assert "catégorie;quantités" in txt and "Matériaux" in txt
    pdf = owner.get("/api/reports/pdf?report=sales_category")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"


def test_objectifs_par_vendeur(owner, app_env):
    marie, mu = make_user(owner, app_env, "marie", "sales", "Marie Camara")
    jean, ju = make_user(owner, app_env, "jean", "sales", "Jean Sylla")
    pid = prod(owner)
    vente(marie, client(marie), pid, 5)                                           # 500 000 ce mois-ci
    assert not rep(owner)["has_targets"] and rep(owner)["by_seller"][0]["target_month"] is None
    r = owner.post("/api/reports/targets", json={"targets": {str(mu["id"]): 1_000_000, str(ju["id"]): 400_000}})
    assert r.status_code == 200, r.text
    assert {s["name"]: s["target"] for s in owner.get("/api/reports/targets").json()["sellers"]}["Marie Camara"] == 1_000_000
    s = {x["name"]: x for x in rep(owner, **{"from": CM + "-01", "to": T})["by_seller"]}
    assert s["Marie Camara"]["target_period"] == 1_000_000 and s["Marie Camara"]["progress_pct"] == 50.0
    assert s["Jean Sylla"]["sales"] == 0 and s["Jean Sylla"]["progress_pct"] == 0.0         # objectif sans vente : visible
    two = rep(owner, **{"from": PREV.isoformat()[:8] + "01", "to": T})
    assert {x["name"]: x for x in two["by_seller"]}["Marie Camara"]["target_period"] == 2_000_000          # 2 mois
    mine = rep(marie, **{"from": CM + "-01", "to": T})
    assert [x["name"] for x in mine["by_seller"]] == ["Marie Camara"] and mine["by_seller"][0]["progress_pct"] == 50.0
    assert "objectif de la période" in owner.get("/api/reports/sales.csv?kind=seller").content.decode("utf-8-sig")
    assert owner.get("/api/reports/pdf?report=sales_seller").content[:4] == b"%PDF"
    assert any(e["action"] == "targets.set" for e in owner.get("/api/audit?limit=10").json()["entries"])
    owner.post("/api/reports/targets", json={"targets": {str(mu["id"]): 0, str(ju["id"]): ""}})
    assert not rep(owner)["has_targets"]


def test_objectifs_droits_et_validation(owner, app_env):
    marie, mu = make_user(owner, app_env, "marie", "sales", "Marie Camara")
    assert marie.post("/api/reports/targets", json={"targets": {str(mu["id"]): 1}}).status_code == 403 and marie.get("/api/reports/targets").status_code == 403
    assert owner.post("/api/reports/targets", json={"targets": {"99999": 5}}).status_code == 422                       # pas un commercial
    assert owner.post("/api/reports/targets", json={"targets": {str(mu["id"]): -5}}).status_code == 422
    assert owner.post("/api/reports/targets", json={"targets": {str(mu["id"]): "abc"}}).status_code == 422
