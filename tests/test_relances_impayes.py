import uuid
from datetime import date, timedelta

from conftest import make_user, op, push

T = date.today()


def mk(c, jours_retard, qty=10, kind="credit"):
    pid = push(c, op("product.create", {"id": str(uuid.uuid4()), "name": "Riz " + uuid.uuid4().hex[:3], "price": 100_000}))[0]["product"]["id"]
    cid = push(c, op("client.create", {"id": str(uuid.uuid4()), "name": "Client " + uuid.uuid4().hex[:3], "phone": "+224620000000", "credit_limit": 90_000_000}))[0]["client"]["id"]
    due = (T - timedelta(days=jours_retard)).isoformat()
    data = {"client_id": cid, "kind": kind, "lines": [{"product_id": pid, "qty": qty}], "due_date": due, "sale_date": (T - timedelta(days=jours_retard + 30)).isoformat()}
    if kind == "cash": data["pay_method"] = "Espèces"
    r = push(c, op("sale.create", data))[0]
    assert r["status"] == "ok", r
    return r["sale"]


def lst(c, **q):
    r = c.get("/api/dunning", params=q); assert r.status_code == 200, r.text
    return r.json()


def test_niveaux_et_messages(owner):
    from gestion import dunning
    assert [dunning.level_of(d) for d in (1, 7, 8, 21, 22, 45, 46, 200)] == [1, 1, 2, 2, 3, 3, 4, 4]
    mk(owner, 3); mk(owner, 15); mk(owner, 30); mk(owner, 60)
    mk(owner, 0)                                                     # échéance aujourd'hui : pas en retard
    d = lst(owner)
    assert [x["level"] for x in d["items"]] == [4, 3, 2, 1] and d["totals"]["count"] == 4 and d["totals"]["amount"] == 4_000_000 and d["totals"]["to_do"] == 4
    n4 = d["items"][0]
    assert n4["level_label"] == "Dernier avertissement" and "recouvrement" in n4["message"] and n4["number"] in n4["message"] and "1 000 000 GNF" in n4["message"]
    assert "amical" in d["items"][3]["message"] and n4["phone"] == "+224620000000"


def test_journal_de_relances_et_promesse(owner):
    s = mk(owner, 12)
    r = owner.post("/api/dunning", json={"sale_id": s["id"], "channel": "whatsapp", "note": "message envoyé"})
    assert r.status_code == 200 and r.json()["level"] == 2
    it = lst(owner)["items"][0]
    assert it["state"] == "relance" and it["attempts"] == 1 and it["last"]["channel"] == "whatsapp" and it["last"]["user"] == "patron"       # relancé récemment : pas à refaire tout de suite
    assert lst(owner, only_due=True)["items"] == []
    promesse = (T + timedelta(days=5)).isoformat()
    assert owner.post("/api/dunning", json={"sale_id": s["id"], "channel": "appel", "promise": promesse}).status_code == 200
    it = lst(owner)["items"][0]
    assert it["state"] == "promesse" and it["promise"] == promesse and "remercions" in it["message"] and lst(owner)["totals"]["promised_amount"] == 1_000_000
    acts = [e["action"] for e in owner.get("/api/audit?limit=5").json()["entries"]]
    assert acts.count("dunning.logged") == 2 and owner.get("/api/audit/verify").json()["ok"] is True


def test_refus(owner, app_env):
    ok = mk(owner, 10); cash = mk(owner, 10, kind="cash")
    for body in ({"sale_id": ok["id"], "channel": "pigeon"}, {"sale_id": "inconnue", "channel": "appel"}, {"sale_id": ok["id"], "channel": "appel", "promise": "2000-01-01"},
                 {"sale_id": ok["id"], "channel": "appel", "promise": (T + timedelta(days=400)).isoformat()}, {"sale_id": ok["id"], "channel": "appel", "promise": "pas-une-date"}):
        assert owner.post("/api/dunning", json=body).status_code == 422, body
    assert owner.post("/api/dunning", json={"sale_id": cash["id"], "channel": "appel"}).status_code == 422
    aud, _ = make_user(owner, app_env, "expert", "auditor")
    assert aud.get("/api/dunning").status_code == 200 and aud.post("/api/dunning", json={"sale_id": ok["id"], "channel": "appel"}).status_code == 403
    sell, _ = make_user(owner, app_env, "marie", "sales")
    assert sell.get("/api/dunning").status_code in (200, 403)


def test_paiement_solde_la_relance(owner):
    s = mk(owner, 20)
    assert lst(owner)["totals"]["count"] == 1
    push(owner, op("payment.create", {"sale_id": s["id"], "amount": 1_000_000, "method": "Espèces", "pay_date": T.isoformat()}))
    assert lst(owner)["items"] == []
