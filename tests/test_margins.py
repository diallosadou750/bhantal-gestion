import uuid
from datetime import date, timedelta

from conftest import make_user, op, push

TODAY = date.today()
T = TODAY.isoformat()


def iso(n):
    return (TODAY + timedelta(days=n)).isoformat()


def prod(c, name, price, **extra):
    return push(c, op("product.create", {"id": str(uuid.uuid4()), "name": name, "price": price, **extra}))[0]["product"]["id"]


def client(c, name):
    return push(c, op("client.create", {"id": str(uuid.uuid4()), "name": name, "credit_limit": 90_000_000}))[0]["client"]["id"]


def achat(c, pid, qty, cost):
    sup = push(c, op("supplier.create", {"id": str(uuid.uuid4()), "name": "F" + uuid.uuid4().hex[:3]}))[0]["supplier"]["id"]
    assert push(c, op("purchase.create", {"supplier_id": sup, "kind": "cash", "pay_method": "Espèces", "purchase_date": iso(-30), "lines": [{"product_id": pid, "qty": qty, "unit_cost": cost}]}))[0]["status"] == "ok"


def vente(c, cid, lines, days_ago=0):
    r = push(c, op("sale.create", {"client_id": cid, "kind": "cash", "pay_method": "Espèces", "sale_date": iso(-days_ago), "lines": lines}))[0]
    assert r["status"] == "ok", r
    return r["sale"]


def margins(c, **q):
    qs = "&".join(f"{k}={v}" for k, v in q.items())
    r = c.get("/api/reports/margins" + ("?" + qs if qs else ""))
    assert r.status_code == 200, r.text
    return r.json()


def test_marges_par_produit_et_client_hors_taxe(owner):
    push(owner, op("settings.update", {"tax_rate": 18}))
    a = prod(owner, "Ciment", 100_000); b = prod(owner, "Riz", 50_000, tax_rate=0)
    achat(owner, a, 100, 60_000); achat(owner, b, 10, 30_000)
    c1, c2 = client(owner, "Camara"), client(owner, "Bah")
    vente(owner, c1, [{"product_id": a, "qty": 10}, {"product_id": b, "qty": 4}])
    vente(owner, c2, [{"product_id": a, "qty": 5, "discount_pct": 10}])
    m = margins(owner)
    p = {x["name"]: x for x in m["by_product"]}
    # ciment : 15 sacs, CA TTC 1 000 000 + 450 000 (remise 10 %) → HT = 847 458 + 381 356 ; coût = 15 × 60 000 (achat sans TVA récupérable)
    assert p["Ciment"]["qty"] == 15 and p["Ciment"]["revenue"] == 847_458 + 381_356 and p["Ciment"]["cost"] == 900_000 and p["Ciment"]["margin"] == 847_458 + 381_356 - 900_000
    assert p["Riz"]["revenue"] == 200_000 and p["Riz"]["cost"] == 120_000 and p["Riz"]["margin"] == 80_000 and p["Riz"]["margin_pct"] == 40.0       # produit exonéré : rien à retirer
    assert [x["name"] for x in m["by_product"]][0] == "Ciment"                                  # triés par marge décroissante
    t = m["totals"]
    assert t["revenue"] == p["Ciment"]["revenue"] + 200_000 and t["margin"] == p["Ciment"]["margin"] + 80_000 and t["revenue_unknown"] == 0
    cl = {x["name"]: x for x in m["by_client"]}
    assert cl["Camara"]["revenue"] == 847_458 + 200_000 and cl["Camara"]["cost"] == 600_000 + 120_000 and cl["Camara"]["sales"] == 1
    assert cl["Bah"]["revenue"] == 381_356 and cl["Bah"]["cost"] == 300_000 and cl["Bah"]["margin"] == 81_356
    assert sum(x["margin"] for x in m["by_client"]) == t["margin"]                              # clients et produits donnent la même marge totale
    assert abs(sum(x["share_of_margin"] for x in m["by_product"]) - 100) < 0.2


def test_cout_inconnu_periode_et_avoirs(owner):
    a = prod(owner, "Ciment", 100_000); jamais = prod(owner, "Jamais acheté", 40_000)
    achat(owner, a, 50, 60_000)
    c1 = client(owner, "Camara")
    s = vente(owner, c1, [{"product_id": a, "qty": 2}, {"product_id": jamais, "qty": 1}, {"name": "Transport", "qty": 1, "unit_price": 10_000}])
    vente(owner, c1, [{"product_id": a, "qty": 1}], days_ago=60)                                # hors période
    m = margins(owner, **{"from": iso(-10), "to": T})
    p = {x["name"]: x for x in m["by_product"]}
    assert p["Ciment"]["qty"] == 2 and p["Jamais acheté"]["margin"] is None and p["Jamais acheté"]["revenue_unknown"] == 40_000
    assert p["Articles libres (sans coût connu)"]["revenue_unknown"] == 10_000
    assert m["totals"]["revenue_unknown"] == 50_000 and m["totals"]["margin"] == 200_000 - 120_000        # seule la part au coût connu entre dans la marge
    assert m["by_product"][-1]["margin"] is None and m["by_product"][-2]["margin"] is None                  # sans marge : en bas de liste
    assert push(owner, op("creditnote.create", {"sale_id": s["id"], "amount": 20_000, "settlement": "refund", "reason": "remise"}))[0]["status"] == "ok"
    m2 = margins(owner, **{"from": iso(-10), "to": T})
    assert m2["credit_notes_total"] == 20_000 and m2["totals"]["margin"] == m["totals"]["margin"]          # montrés à part, non déduits
    cancelled = vente(owner, c1, [{"product_id": a, "qty": 3}])
    push(owner, op("sale.cancel", {"id": cancelled["id"], "base_version": cancelled["version"], "reason": "annulée"}))
    assert margins(owner, **{"from": iso(-10), "to": T})["totals"]["revenue"] == m["totals"]["revenue"]      # une vente annulée ne compte plus
    assert margins(owner, **{"from": iso(-400), "to": iso(-300)})["totals"]["revenue"] == 0


def test_validation_csv_et_droits(owner, app_env):
    a = prod(owner, "=CMD|x", 100_000); achat(owner, a, 10, 60_000); vente(owner, client(owner, "@Client"), [{"product_id": a, "qty": 1}])
    assert owner.get("/api/reports/margins?from=2026-13-01").status_code == 422 and owner.get("/api/reports/margins?from=2026-05-01&to=2026-01-01").status_code == 422
    r = owner.get("/api/reports/margins.csv?kind=product")
    assert r.status_code == 200 and "'=CMD|x" in r.content.decode("utf-8-sig") and ";=CMD" not in r.content.decode("utf-8-sig")
    assert "'@Client" in owner.get("/api/reports/margins.csv?kind=client").content.decode("utf-8-sig") and owner.get("/api/reports/margins.csv?kind=autre").status_code == 422
    assert any(e["action"] == "report.exported" for e in owner.get("/api/audit?limit=10").json()["entries"])
    marie, _ = make_user(owner, app_env, "marie", "sales")
    cash, _ = make_user(owner, app_env, "paul", "cashier")
    assert marie.get("/api/reports/margins").status_code == 403 and cash.get("/api/reports/margins.csv").status_code == 403         # les coûts ne se voient pas
    for role, name in (("accountant", "compta"), ("auditor", "expert")):
        u, _ = make_user(owner, app_env, name, role)
        assert u.get("/api/reports/margins").status_code == 200
