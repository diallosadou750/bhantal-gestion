import re
import uuid
from datetime import date

from conftest import make_user, op, push

T = date.today().isoformat()


def bal(c):
    r = c.get(f"/api/accounting/report?kind=balance&from=2000-01-01&to={T}").json()
    assert r["balanced"] is True
    return {x["account"]: x for x in r["rows"]}


def prod(c, name, price, **extra):
    r = push(c, op("product.create", {"id": str(uuid.uuid4()), "name": name, "price": price, **extra}))[0]
    assert r["status"] == "ok", r
    return r["product"]


def client(c):
    return push(c, op("client.create", {"id": str(uuid.uuid4()), "name": "Camara", "credit_limit": 50_000_000}))[0]["client"]["id"]


def vente(c, cid, lines):
    r = push(c, op("sale.create", {"client_id": cid, "kind": "cash", "pay_method": "Espèces", "lines": lines}))[0]
    assert r["status"] == "ok", r
    return r["sale"]


def test_ventilation_par_ligne_en_comptabilite_et_facture(owner):
    assert push(owner, op("settings.update", {"tax_rate": 18}))[0]["status"] == "ok"
    a = prod(owner, "Ciment", 100_000)                               # taux de l'entreprise (18 %)
    b = prod(owner, "Riz (exonéré)", 50_000, tax_rate=0)
    assert a["tax_rate"] is None and b["tax_rate"] == 0
    s = vente(owner, client(owner), [{"product_id": a["id"], "qty": 1}, {"product_id": b["id"], "qty": 1}])
    assert [l["tax_rate"] for l in s["lines"]] == [18, 0]          # le taux est figé sur la ligne au moment de la vente
    x = bal(owner)
    assert x["701"]["close_credit"] == 134_746 and x["4431"]["close_credit"] == 15_254 and x["411"]["close_debit"] == 0 and x["571"]["close_debit"] == 150_000
    page = re.sub(r"\s+", " ", owner.get(f"/api/invoice/{s['id']}").text)             # les espaces des milliers peuvent être insécables
    assert "<th>TVA</th>" in page and "Taxe (18 %)" in page and "Total hors taxe" in page and "134 746" in page and "15 254" in page and "Taxe (0 %)" not in page
    rep = owner.get(f"/api/accounting/report?kind=statements&from=2000-01-01&to={T}").json()
    assert rep["balance_sheet"]["balanced"] is True
    sales_rates = {r["rate"]: r for r in rep["vat"]["by_rate"]["sales"]}
    assert sales_rates[18]["base"] == 84_746 and sales_rates[18]["tax"] == 15_254 and sales_rates[0]["base"] == 50_000 and sales_rates[0]["tax"] == 0


def test_taux_fige_apres_changement_de_produit_et_avoir_proportionnel(owner):
    push(owner, op("settings.update", {"tax_rate": 18}))
    a = prod(owner, "Ciment", 100_000); b = prod(owner, "Riz", 50_000, tax_rate=0)
    s = vente(owner, client(owner), [{"product_id": a["id"], "qty": 1}, {"product_id": b["id"], "qty": 1}])
    cur = owner.get("/api/sync/pull?since=0").json()["products"]
    ver = next(p["version"] for p in cur if p["id"] == a["id"])
    assert push(owner, op("product.update", {"id": a["id"], "base_version": ver, "changes": {"tax_rate": 0}}))[0]["product"]["tax_rate"] == 0
    assert bal(owner)["4431"]["close_credit"] == 15_254                                        # la vente passée garde son taux
    n = push(owner, op("creditnote.create", {"sale_id": s["id"], "amount": 75_000, "settlement": "refund", "reason": "retour partiel"}))[0]
    assert n["status"] == "ok"
    x = bal(owner)
    assert x["4431"]["close_credit"] == 15_254 - 7_627 and x["701"]["close_credit"] == 134_746 - 67_373        # l'avoir reprend la TVA au prorata de la facture
    s2 = vente(owner, client(owner), [{"product_id": a["id"], "qty": 1}])                       # nouvelle vente : le nouveau taux (0 %) s'applique
    assert s2["lines"][0]["tax_rate"] == 0 and bal(owner)["4431"]["close_credit"] == 15_254 - 7_627


def test_ligne_libre_achat_par_ligne_et_validations(owner):
    push(owner, op("settings.update", {"tax_rate": 18}))
    a = prod(owner, "Ciment", 100_000)
    s = vente(owner, client(owner), [{"name": "Transport", "qty": 1, "unit_price": 10_000, "tax_rate": 5}, {"name": "Main d'œuvre", "qty": 1, "unit_price": 10_000}])
    assert [l["tax_rate"] for l in s["lines"]] == [5, 18]
    assert bal(owner)["4431"]["close_credit"] == (10_000 - round(10_000 * 100 / 105)) + (10_000 - round(10_000 * 100 / 118))
    sup = push(owner, op("supplier.create", {"id": str(uuid.uuid4()), "name": "F"}))[0]["supplier"]["id"]
    p = push(owner, op("purchase.create", {"supplier_id": sup, "kind": "cash", "pay_method": "Espèces", "lines": [{"product_id": a["id"], "qty": 2, "unit_cost": 59_000, "tax_rate": 18}, {"product_id": a["id"], "qty": 1, "unit_cost": 10_000}]}))[0]
    assert p["status"] == "ok" and [l["tax_rate"] for l in p["purchase"]["lines"]] == [18, None]
    x = bal(owner)
    assert x["4452"]["close_debit"] == 118_000 - round(118_000 * 100 / 118)                    # TVA récupérable seulement sur la ligne à 18 % (taux par défaut des achats : 0)
    for bad in ({"tax_rate": 31}, {"tax_rate": -1}, {"tax_rate": "abc"}):
        assert push(owner, op("product.create", {"id": str(uuid.uuid4()), "name": "X", "price": 1, **bad}))[0]["status"] == "invalid", bad
    assert prod(owner, "Y", 1, tax_rate="")["tax_rate"] is None


def test_devis_conserve_le_taux_et_droits(owner, app_env):
    push(owner, op("settings.update", {"tax_rate": 18}))
    b = prod(owner, "Riz", 50_000, tax_rate=0)
    cid = client(owner)
    q = push(owner, op("quote.create", {"id": str(uuid.uuid4()), "client_id": cid, "lines": [{"product_id": b["id"], "qty": 2}, {"name": "Livraison", "qty": 1, "unit_price": 20_000, "tax_rate": 5}]}))[0]["quote"]
    assert [l["tax_rate"] for l in q["lines"]] == [0, 5]
    r = push(owner, op("quote.convert", {"id": q["id"], "base_version": q["version"], "kind": "cash", "pay_method": "Espèces"}))[0]
    assert r["status"] == "ok" and [l["tax_rate"] for l in r["sale"]["lines"]] == [0, 5]
    page = owner.get(f"/api/quote/{q['id']}").text
    assert "Taxe (5 %)" in page
    marie, _ = make_user(owner, app_env, "marie", "sales")
    assert push(marie, op("product.update", {"id": b["id"], "base_version": 1, "changes": {"tax_rate": 18}}))[0]["status"] == "denied"      # seuls ceux qui gèrent le catalogue changent la TVA d'un produit
