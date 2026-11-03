import uuid
from datetime import date, timedelta

from conftest import make_user, op, push

T = date.today().isoformat()


def setup(owner, price=100_000):
    pid = push(owner, op("product.create", {"id": str(uuid.uuid4()), "name": "Ciment", "price": price}))[0]["product"]["id"]
    cid = push(owner, op("client.create", {"id": str(uuid.uuid4()), "name": "Camara", "credit_limit": 5_000_000}))[0]["client"]["id"]
    return pid, cid


def quote(c, cid, pid, qty=10, **extra):
    return push(c, op("quote.create", {"id": str(uuid.uuid4()), "client_id": cid, "lines": [{"product_id": pid, "qty": qty}], **extra}))[0]


def pull(c):
    return c.get("/api/sync/pull?since=0").json()


def test_creation_numerotation_et_pas_d_effet_comptable(owner):
    pid, cid = setup(owner)
    r = quote(owner, cid, pid)
    q = r["quote"]
    assert r["status"] == "ok" and q["number"] == "DV-0001" and q["total"] == 1_000_000 and q["valid_until"] == (date.today() + timedelta(days=30)).isoformat() and q["expired"] is False
    assert quote(owner, cid, pid)["quote"]["number"] == "DV-0002"
    p = pull(owner)
    assert len(p["quotes"]) == 2 and p["sales"] == []
    assert owner.get("/api/accounting/report?kind=balance&from=2000-01-01&to=" + T).json()["rows"] == []              # un devis ne touche pas la comptabilité
    page = owner.get(f"/api/quote/{q['id']}")
    assert page.status_code == 200 and "DEVIS" in page.text and "DV-0001" in page.text and "pas une facture" in page.text


def test_conversion_en_vente(owner):
    pid, cid = setup(owner)
    q = quote(owner, cid, pid)["quote"]
    bad = push(owner, op("quote.convert", {"id": q["id"], "base_version": q["version"], "kind": "cash"}))[0]
    assert bad["status"] == "invalid"                                                       # vente au comptant sans mode de paiement
    r = push(owner, op("quote.convert", {"id": q["id"], "base_version": q["version"], "kind": "cash", "pay_method": "Espèces"}))[0]
    assert r["status"] == "ok" and r["sale"]["total"] == 1_000_000 and r["quote"]["status"] == "converted" and r["quote"]["sale_number"] == r["sale"]["number"]
    p = pull(owner)
    assert len(p["sales"]) == 1 and p["sales"][0]["note"] == "Devis DV-0001"
    again = push(owner, op("quote.convert", {"id": q["id"], "base_version": r["quote"]["version"], "kind": "cash", "pay_method": "Espèces"}))[0]
    assert again["status"] == "invalid" and len(pull(owner)["sales"]) == 1                    # une seule vente par devis


def test_credit_limite_et_prix_change(owner, app_env):
    pid, cid = setup(owner)
    q = quote(owner, cid, pid, qty=100)["quote"]                                           # 10 000 000 > limite de 5 000 000
    r = push(owner, op("quote.convert", {"id": q["id"], "base_version": q["version"], "kind": "credit", "due_date": T}))[0]
    assert r["status"] == "invalid" and "Limite" in r["message"]
    assert pull(owner)["quotes"][0]["status"] == "open" and pull(owner)["sales"] == []
    marie, _ = make_user(owner, app_env, "marie", "sales")
    assert pull(marie)["quotes"] == []                                                      # elle ne voit pas les devis des clients des autres
    q2 = quote(owner, cid, pid)["quote"]
    assert push(owner, op("product.update", {"id": pid, "base_version": pull(owner)["products"][0]["version"], "changes": {"price": 120_000}}))[0]["status"] == "ok"
    r = push(owner, op("quote.convert", {"id": q2["id"], "base_version": q2["version"], "kind": "cash", "pay_method": "Espèces"}))[0]
    assert r["status"] == "ok" and r["sale"]["total"] == 1_000_000                          # le propriétaire peut facturer au prix du devis (droit de modifier les prix)


def test_prix_du_catalogue_change_refuse_au_commercial(owner, app_env):
    pid, _ = setup(owner)
    marie, mu = make_user(owner, app_env, "marie", "sales")
    cid = push(marie, op("client.create", {"id": str(uuid.uuid4()), "name": "Client M"}))[0]["client"]["id"]
    q = quote(marie, cid, pid)["quote"]
    assert push(owner, op("product.update", {"id": pid, "base_version": pull(owner)["products"][0]["version"], "changes": {"price": 120_000}}))[0]["status"] == "ok"
    r = push(marie, op("quote.convert", {"id": q["id"], "base_version": q["version"], "kind": "cash", "pay_method": "Espèces"}))[0]
    assert r["status"] == "invalid" and "prix du catalogue a changé" in r["message"]
    assert pull(owner)["sales"] == []


def test_expiration_cloture_et_droits(owner, app_env):
    pid, cid = setup(owner)
    old = (date.today() - timedelta(days=40)).isoformat()
    q = quote(owner, cid, pid, quote_date=old, valid_until=(date.today() - timedelta(days=10)).isoformat())["quote"]
    assert q["expired"] is True
    assert push(owner, op("quote.convert", {"id": q["id"], "base_version": q["version"], "kind": "cash", "pay_method": "Espèces"}))[0]["status"] == "invalid"
    assert push(owner, op("quote.close", {"id": q["id"], "base_version": q["version"], "outcome": "declined", "reason": ""}))[0]["status"] == "invalid"
    assert push(owner, op("quote.close", {"id": q["id"], "base_version": 9, "outcome": "declined", "reason": "trop cher"}))[0]["status"] == "conflict"
    assert push(owner, op("quote.close", {"id": q["id"], "base_version": q["version"], "outcome": "declined", "reason": "trop cher"}))[0]["status"] == "ok"
    assert pull(owner)["quotes"][0]["status"] == "declined"
    for bad in ({"valid_until": "2000-01-01"}, {"client_id": "x"}, {"lines": []}):
        assert quote(owner, cid, pid, **bad)["status"] == "invalid", bad
    aud, _ = make_user(owner, app_env, "expert", "auditor")
    assert quote(aud, cid, pid)["status"] == "denied" and len(pull(aud)["quotes"]) == 1
