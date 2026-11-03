import uuid
from datetime import date, timedelta

from conftest import make_user, op, push

T = date.today().isoformat()


def pull(c):
    return c.get("/api/sync/pull?since=0").json()


def bal(c):
    r = c.get(f"/api/accounting/report?kind=balance&from=2000-01-01&to={T}").json()
    return {x["account"]: x for x in r["rows"]}, r["balanced"]


def achat(c, kind="cash", qty=100, cost=60_000, **extra):
    pid = push(c, op("product.create", {"id": str(uuid.uuid4()), "name": "Ciment", "price": 100_000}))[0]["product"]["id"]
    sup = push(c, op("supplier.create", {"id": str(uuid.uuid4()), "name": "Fournisseur"}))[0]["supplier"]["id"]
    data = {"supplier_id": sup, "kind": kind, "purchase_date": (date.today() - timedelta(days=3)).isoformat(), "lines": [{"product_id": pid, "qty": qty, "unit_cost": cost}], **extra}
    data.setdefault("pay_method", "Espèces")
    if kind != "cash": data["due_date"] = (date.today() + timedelta(days=20)).isoformat()
    r = push(c, op("purchase.create", data))[0]
    assert r["status"] == "ok", r
    return pid, r["purchase"]


def note(c, p, **extra):
    return push(c, op("snote.create", {"id": str(uuid.uuid4()), "purchase_id": p["id"], **extra}))[0]


def test_avoir_deduit_de_la_dette_et_stock(owner):
    pid, p = achat(owner, "credit", paid_at_purchase=1_000_000)                            # achat 6 000 000, payé 1 000 000, dette 5 000 000
    r = note(owner, p, settlement="deduct", reason="10 sacs défectueux rendus", lines=[{"product_id": pid, "qty": 10}])
    assert r["status"] == "ok" and r["supplier_note"]["number"] == "AF-0001" and r["supplier_note"]["amount"] == 600_000       # 10 × 60 000 : montant déduit des lignes
    assert r["purchase"]["balance"] == 4_400_000 and r["purchase"]["notes_total"] == 600_000 and r["purchase"]["net_total"] == 5_400_000
    assert pull(owner)["stock"][pid]["qty"] == 90
    b, ok = bal(owner)
    assert ok and b["401"]["close_credit"] == 4_400_000 and b["601"]["close_debit"] == 5_400_000
    assert owner.get(f"/api/accounting/report?kind=statements&from=2000-01-01&to={T}").json()["balance_sheet"]["balanced"] is True


def test_avoir_rembourse_en_especes(owner):
    pid, p = achat(owner, "cash")                                                          # 6 000 000 payés en espèces
    r = note(owner, p, settlement="refund", refund_method="Espèces", reason="erreur de prix", amount=500_000)
    assert r["status"] == "ok" and r["purchase"]["balance"] == 0 and r["purchase"]["refunds_total"] == 500_000
    b, ok = bal(owner)
    assert ok and b["571"]["close_credit"] == 5_500_000 and b["601"]["close_debit"] == 5_500_000 and b.get("401", {"close_credit": 0})["close_credit"] == 0
    assert pull(owner)["cash"]["in_total"] == 500_000 and pull(owner)["cash"]["out_total"] == 6_000_000          # le remboursement encaissé entre en caisse
    assert note(owner, p, settlement="refund", refund_method="Espèces", reason="trop", amount=5_600_000)["status"] == "invalid"       # au-delà de ce qui reste (net)


def test_limites_validations_et_annulation(owner):
    pid, p = achat(owner, "credit")
    assert note(owner, p, settlement="deduct", reason="trop", amount=6_000_001)["status"] == "invalid"
    assert note(owner, p, settlement="refund", refund_method="Espèces", reason="rien payé", amount=1000)["status"] == "invalid"     # rien n'a été payé
    assert note(owner, p, settlement="deduct", reason="x", amount=1000)["status"] == "invalid"                                        # motif trop court
    assert note(owner, p, settlement="deduct", reason="rendu", lines=[{"product_id": pid, "qty": 101}])["status"] == "invalid"       # plus que l'achat
    assert note(owner, p, settlement="deduct", reason="rendu", lines=[{"product_id": "inconnu", "qty": 1}])["status"] == "invalid"
    ok = note(owner, p, settlement="deduct", reason="rendu partiel", lines=[{"product_id": pid, "qty": 60}])
    assert ok["status"] == "ok"
    assert note(owner, p, settlement="deduct", reason="rendu encore", lines=[{"product_id": pid, "qty": 41}])["status"] == "invalid"  # 60 + 41 > 100
    n = ok["supplier_note"]
    assert push(owner, op("snote.cancel", {"id": n["id"], "base_version": 7, "reason": "erreur de saisie"}))[0]["status"] == "conflict"
    c = push(owner, op("snote.cancel", {"id": n["id"], "base_version": n["version"], "reason": "erreur de saisie"}))[0]
    assert c["status"] == "ok" and c["purchase"]["balance"] == 6_000_000 and pull(owner)["stock"][pid]["qty"] == 100
    assert bal(owner)[1] is True


def test_banque_pointage_droits_et_verrou(owner, app_env):
    push(owner, op("bank.opening", {"account": "bank", "balance": 0, "date": (date.today() - timedelta(days=30)).isoformat()}))
    pid, p = achat(owner, "credit", paid_at_purchase=2_000_000, pay_method="Virement bancaire")
    r = note(owner, p, settlement="refund", refund_method="Virement bancaire", reason="remise accordée", amount=300_000)
    assert r["status"] == "ok"
    its = pull(owner)["bank_items"]["bank"]
    assert any(i["type"] == "supplierrefund" and i["amount"] == 300_000 for i in its)
    assert push(owner, op("bank.import", {"account": "bank", "lines": [{"date": T, "label": "VIR FOURNISSEUR", "amount": 300_000, "reference": "R9"}]}))[0]["status"] == "ok"
    push(owner, op("bank.automatch", {"account": "bank"}))
    n = r["supplier_note"]
    assert push(owner, op("snote.cancel", {"id": n["id"], "base_version": n["version"], "reason": "essai"}))[0]["status"] == "invalid"        # pointé
    marie, _ = make_user(owner, app_env, "marie", "sales")
    assert note(marie, p, settlement="deduct", reason="essai commercial", amount=1000)["status"] == "denied" and pull(marie)["supplier_notes"] == []
    assert owner.get("/api/audit/verify").json()["ok"] is True
    assert push(owner, op("accounting.lock", {"through": T}))[0]["status"] == "ok"
    assert note(owner, p, settlement="deduct", reason="après verrou", amount=1000)["status"] == "invalid"
