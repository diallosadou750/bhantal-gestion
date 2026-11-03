import uuid
from datetime import date

from conftest import make_user, op, push

T = date.today().isoformat()


def test_auditeur_lit_tout_et_ne_modifie_rien(owner, app_env):
    pid = push(owner, op("product.create", {"id": str(uuid.uuid4()), "name": "Ciment", "price": 100_000}))[0]["product"]["id"]
    cid = push(owner, op("client.create", {"id": str(uuid.uuid4()), "name": "Camara", "credit_limit": 1_000_000}))[0]["client"]["id"]
    push(owner, op("sale.create", {"client_id": cid, "kind": "cash", "pay_method": "Espèces", "lines": [{"product_id": pid, "qty": 2}]}))
    aud, user = make_user(owner, app_env, "expert", "auditor")
    assert user["role"] == "auditor"
    r = aud.get("/api/sync/pull?since=0").json()
    assert len(r["clients"]) == 1 and len(r["sales"]) == 1 and r["accounting"] is not None
    for url in ("/api/accounting/report?kind=statements&from=2000-01-01&to=" + T, "/api/accounting/report?kind=financials&from=2000-01-01&to=" + T, "/api/audit?limit=5", "/api/audit/verify"):
        assert aud.get(url).status_code == 200, url
    ops = [("client.create", {"id": str(uuid.uuid4()), "name": "X"}), ("product.create", {"id": str(uuid.uuid4()), "name": "Y", "price": 1}),
           ("sale.create", {"client_id": cid, "kind": "cash", "pay_method": "Espèces", "lines": [{"product_id": pid, "qty": 1}]}),
           ("expense.create", {"category": "Loyer et charges locatives", "amount": 1000, "method": "Espèces", "payee": "x"}),
           ("journal.create", {"entry_date": T, "label": "x", "lines": [{"account": "571", "debit": 1}, {"account": "101", "credit": 1}]}),
           ("accounting.lock", {"through": T}), ("fiscal.close", {"year": 2020}), ("settings.update", {"tax_rate": 5}),
           ("asset.create", {"id": str(uuid.uuid4()), "name": "x", "category": "Terrains", "origin": "purchase", "cost": 5, "pay_mode": "owner"}),
           ("provision.create", {"id": str(uuid.uuid4()), "kind": "other", "label": "xxx", "amount": 5}), ("cash.close", {"close_date": T, "opening": 0, "counted": 0}),
           ("bank.opening", {"account": "bank", "balance": 1, "date": T}), ("stock.adjust", {"product_id": pid, "qty": 1, "reason": "x"})]
    for kind, data in ops:
        res = push(aud, op(kind, data))[0]
        assert res["status"] in ("denied", "invalid") and res["status"] != "ok", (kind, res)
    assert aud.post("/api/users", json={"username": "z", "full_name": "Z", "role": "owner", "temp_password": "Temporaire2026ab"}).status_code in (401, 403)
    assert len(owner.get("/api/sync/pull?since=0").json()["clients"]) == 1 and len(owner.get("/api/sync/pull?since=0").json()["sales"]) == 1
