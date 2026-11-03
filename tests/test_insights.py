import uuid
from datetime import date, timedelta

from conftest import make_user, op, push

TODAY = date.today()


def iso(n):
    return (TODAY + timedelta(days=n)).isoformat()


def insights(c):
    r = c.get("/api/reports/insights")
    assert r.status_code == 200, r.text
    return r.json()


def setup(c, name="Camara", limit=50_000_000):
    pid = push(c, op("product.create", {"id": str(uuid.uuid4()), "name": "Ciment " + uuid.uuid4().hex[:4], "price": 100_000}))[0]["product"]["id"]
    cid = push(c, op("client.create", {"id": str(uuid.uuid4()), "name": name, "credit_limit": limit}))[0]["client"]["id"]
    return pid, cid


def credit(c, pid, cid, sale_age, due_in, qty=10, paid_on=None):
    s = push(c, op("sale.create", {"client_id": cid, "kind": "credit", "sale_date": iso(-sale_age), "due_date": iso(due_in), "lines": [{"product_id": pid, "qty": qty}]}))[0]
    assert s["status"] == "ok", s
    s = s["sale"]
    if paid_on is not None:
        assert push(c, op("payment.create", {"sale_id": s["id"], "amount": qty * 100_000, "method": "Virement bancaire", "pay_date": iso(paid_on)}))[0]["status"] == "ok"
    return s


def by_name(r, name):
    return next(x for x in r["reliability"] if x["name"] == name)


def test_fiabilite_des_clients(owner):
    pid, bon = setup(owner, "Bon payeur")
    for age in (60, 45, 30):
        credit(owner, pid, bon, age, -(age - 20), paid_on=-(age - 10))                       # payé 10 jours avant l'échéance
    _, mauvais = setup(owner, "Mauvais payeur")
    credit(owner, pid, mauvais, 80, -50, paid_on=-20)                                       # payé 30 jours en retard
    credit(owner, pid, mauvais, 40, -10)                                                    # toujours impayé, en retard de 10 jours
    _, neuf = setup(owner, "Nouveau")
    credit(owner, pid, neuf, 5, 25)                                                         # pas encore échu : pas d'historique
    r = insights(owner)
    b, m, n = by_name(r, "Bon payeur"), by_name(r, "Mauvais payeur"), by_name(r, "Nouveau")
    assert b["score"] == 100 and b["label"] == "Fiable" and b["late"] == 0
    assert m["counted"] == 2 and m["late"] == 2 and m["overdue_amount"] == 1_000_000 and m["score"] < 50 and m["label"] == "Risqué"
    assert n["score"] is None and n["label"] == "Peu d'historique"
    assert r["reliability"][0]["name"] == "Mauvais payeur"                                  # les plus risqués en premier


def test_prevision_de_tresorerie(owner):
    pid, cid = setup(owner)
    credit(owner, pid, cid, 40, -5)                                                         # 1 000 000 en retard
    credit(owner, pid, cid, 10, 3, qty=5)                                                   # 500 000 cette semaine
    credit(owner, pid, cid, 5, 10, qty=2)                                                   # 200 000 semaine +1
    sup = push(owner, op("supplier.create", {"id": str(uuid.uuid4()), "name": "F"}))[0]["supplier"]["id"]
    push(owner, op("purchase.create", {"supplier_id": sup, "kind": "credit", "due_date": iso(9), "purchase_date": iso(-1), "lines": [{"product_id": pid, "qty": 10, "unit_cost": 40_000}]}))
    f = insights(owner)["forecast"]
    bk = {b["label"]: b for b in f["buckets"]}
    assert bk["En retard"]["in"] == 1_000_000 and bk["Cette semaine"]["in"] == 500_000 and bk["Semaine +1"]["in"] == 200_000 and bk["Semaine +1"]["out"] == 400_000
    last = f["buckets"][-1]
    assert last["position"] == f["start"]["total"] + sum(b["net"] for b in f["buckets"])
    assert "optimiste" in f["note"] or "Hypothèse" in f["note"]


def test_recurrentes_dans_la_prevision_et_droits(owner, app_env):
    push(owner, op("recurring.create", {"id": str(uuid.uuid4()), "label": "Loyer", "category": "Loyer et charges locatives", "amount": 300_000, "method": "Virement bancaire", "day": 15, "start_date": iso(-100)}))
    f = insights(owner)["forecast"]
    assert sum(b["out"] for b in f["buckets"]) >= 300_000                                  # au moins une échéance dans les quatre semaines ou en retard
    marie, _ = make_user(owner, app_env, "marie", "sales")
    assert marie.get("/api/reports/insights").status_code == 403
    aud, _ = make_user(owner, app_env, "expert", "auditor")
    assert aud.get("/api/reports/insights").status_code == 200
