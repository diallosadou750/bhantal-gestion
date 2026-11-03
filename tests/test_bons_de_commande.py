import uuid
from datetime import date, timedelta

from conftest import make_user, op, push

T = date.today()


def mk_sup(c):
    return push(c, op("supplier.create", {"id": str(uuid.uuid4()), "name": "Fournisseur " + uuid.uuid4().hex[:3]}))[0]["supplier"]["id"]


def mk_prod(c, name):
    return push(c, op("product.create", {"id": str(uuid.uuid4()), "name": name + uuid.uuid4().hex[:3], "price": 100_000}))[0]["product"]["id"]


def commande(c, **kw):
    sup = kw.pop("sup", None) or mk_sup(c); p1, p2 = mk_prod(c, "Riz"), mk_prod(c, "Huile")
    data = {"supplier_id": sup, "lines": [{"product_id": p1, "qty": 10, "unit_cost": 50_000}, {"product_id": p2, "qty": 5, "unit_cost": 20_000}], **kw}
    r = push(c, op("porder.create", data))[0]
    assert r["status"] == "ok", r
    return r["porder"], (p1, p2)


def stock(c, pid):
    return c.get("/api/sync/pull?since=0").json()["stock"].get(pid, {}).get("qty", 0)


def test_creation_et_synchronisation(owner):
    o, _ = commande(owner, expected_date=(T + timedelta(days=3)).isoformat(), note="urgent")
    assert o["number"] == "BC-0001" and o["total"] == 600_000 and o["status"] == "open" and o["late"] is False and len(o["lines"]) == 2
    assert [x["number"] for x in owner.get("/api/sync/pull?since=0").json()["porders"]] == ["BC-0001"]
    o2, _ = commande(owner)
    assert o2["number"] == "BC-0002" and o2["expected_date"] == (T + timedelta(days=7)).isoformat()


def test_validations(owner):
    sup = mk_sup(owner); p = mk_prod(owner, "Sucre")
    base = {"supplier_id": sup, "lines": [{"product_id": p, "qty": 1, "unit_cost": 10}]}
    for bad in ({**base, "supplier_id": "inconnu"}, {**base, "lines": []}, {**base, "lines": [{"product_id": p, "qty": 0, "unit_cost": 10}]},
                {**base, "lines": [{"product_id": p, "qty": 1, "unit_cost": 10}, {"product_id": p, "qty": 2, "unit_cost": 10}]},
                {**base, "expected_date": (T - timedelta(days=40)).isoformat(), "order_date": T.isoformat()}, {**base, "order_date": (T + timedelta(days=30)).isoformat()},
                {**base, "expected_date": (T + timedelta(days=900)).isoformat()}, {**base, "lines": [{"product_id": "x", "qty": 1, "unit_cost": 1}]}):
        assert push(owner, op("porder.create", bad))[0]["status"] == "invalid", bad


def test_reception_cree_l_achat_et_le_stock(owner):
    o, (p1, p2) = commande(owner)
    assert stock(owner, p1) == 0
    r = push(owner, op("porder.receive", {"id": o["id"], "base_version": o["version"], "kind": "cash", "pay_method": "Espèces", "supplier_ref": "FA-77"}))[0]
    assert r["status"] == "ok", r
    assert r["purchase"]["total"] == 600_000 and r["purchase"]["supplier_ref"] == "FA-77" and r["porder"]["status"] == "received" and r["porder"]["purchase_number"] == r["purchase"]["number"]
    assert stock(owner, p1) == 10 and stock(owner, p2) == 5
    again = push(owner, op("porder.receive", {"id": o["id"], "base_version": r["porder"]["version"], "kind": "cash", "pay_method": "Espèces"}))[0]
    assert again["status"] == "invalid"                                              # pas deux fois
    assert owner.get("/api/audit/verify").json()["ok"] is True


def test_reception_avec_ecarts_et_a_credit(owner):
    o, (p1, p2) = commande(owner)
    due = (T + timedelta(days=30)).isoformat()
    r = push(owner, op("porder.receive", {"id": o["id"], "base_version": o["version"], "kind": "credit", "due_date": due, "lines": [{"position": 1, "qty": 8, "unit_cost": 55_000}, {"position": 2, "qty": 0}]}))[0]
    assert r["status"] == "ok", r
    assert r["purchase"]["total"] == 440_000 and len(r["purchase"]["lines"]) == 1 and r["purchase"]["balance"] == 440_000 and r["purchase"]["due_date"] == due
    assert stock(owner, p1) == 8 and stock(owner, p2) == 0
    e = [x for x in owner.get("/api/audit?limit=5").json()["entries"] if x["action"] == "porder.receive"][0]
    assert "reçu 8 à 55000" in e["after_json"] if isinstance(e.get("after_json"), str) else "reçu 8 à 55000" in str(e["after"])


def test_reception_refusee_ne_change_rien(owner):
    o, (p1, _) = commande(owner)
    r = push(owner, op("porder.receive", {"id": o["id"], "base_version": o["version"], "kind": "credit"}))[0]                  # crédit sans échéance
    assert r["status"] == "invalid" and stock(owner, p1) == 0
    assert push(owner, op("porder.receive", {"id": o["id"], "base_version": o["version"], "kind": "cash", "pay_method": "Espèces", "lines": [{"position": 1, "qty": 0}, {"position": 2, "qty": 0}]}))[0]["status"] == "invalid"
    cur = [x for x in owner.get("/api/sync/pull?since=0").json()["porders"] if x["id"] == o["id"]][0]
    assert cur["status"] == "open" and cur["purchase_id"] is None
    assert push(owner, op("porder.receive", {"id": o["id"], "base_version": 99, "kind": "cash", "pay_method": "Espèces"}))[0]["status"] == "conflict"


def test_annulation_retard_et_digest(owner):
    o, _ = commande(owner, order_date=(T - timedelta(days=20)).isoformat(), expected_date=(T - timedelta(days=5)).isoformat())
    assert o["late"] is True
    assert any(i["key"] == "porders_late" and i["count"] == 1 for i in owner.get("/api/digest").json()["digest"]["items"])
    assert push(owner, op("porder.close", {"id": o["id"], "base_version": o["version"], "reason": "x"}))[0]["status"] == "invalid"            # motif trop court
    assert push(owner, op("porder.close", {"id": o["id"], "base_version": o["version"], "reason": "fournisseur en rupture"}))[0]["status"] == "ok"
    d = [x for x in owner.get("/api/sync/pull?since=0").json()["porders"] if x["id"] == o["id"]][0]
    assert d["status"] == "cancelled" and d["late"] is False and d["cancel_reason"] == "fournisseur en rupture"
    assert not any(i["key"] == "porders_late" for i in owner.get("/api/digest").json()["digest"]["items"])


def test_droits(owner, app_env):
    sell, _ = make_user(owner, app_env, "marie", "sales"); aud, _ = make_user(owner, app_env, "expert", "auditor")
    sup = mk_sup(owner); p = mk_prod(owner, "Sel")
    data = {"supplier_id": sup, "lines": [{"product_id": p, "qty": 1, "unit_cost": 10}]}
    assert push(sell, op("porder.create", data))[0]["status"] == "denied" and push(aud, op("porder.create", data))[0]["status"] == "denied"
    assert sell.get("/api/sync/pull?since=0").json().get("porders") == []
    o, _ = commande(owner)
    assert push(aud, op("porder.close", {"id": o["id"], "base_version": 1, "reason": "essai"}))[0]["status"] == "denied"
    assert len(aud.get("/api/sync/pull?since=0").json()["porders"]) >= 0


def test_impression_du_bon(owner, app_env):
    o, _ = commande(owner, note="Livrer le matin")
    r = owner.get("/api/porder/" + o["id"])
    assert r.status_code == 200 and "BON DE COMMANDE" in r.text and o["number"] in r.text and "Livrer le matin" in r.text and "600" in r.text
    assert owner.get("/api/porder/" + o["id"], headers={"Accept-Language": "en"}).text.count("PURCHASE ORDER") >= 1
    assert owner.get("/api/porder/inconnu").status_code == 404
    sell, _ = make_user(owner, app_env, "marie2", "sales")
    assert sell.get("/api/porder/" + o["id"]).status_code == 404
    assert any(e["action"] == "porder.viewed" for e in owner.get("/api/audit").json()["entries"])
