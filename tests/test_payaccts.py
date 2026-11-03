import uuid
from datetime import date, timedelta

from conftest import make_user, op, push

T = date.today().isoformat()


def pull(c):
    return c.get("/api/sync/pull?since=0").json()


def rep(c, kind="balance", **params):
    q = "&".join(f"{k}={v}" for k, v in {"kind": kind, "from": "2000-01-01", "to": T, **params}.items())
    return c.get("/api/accounting/report?" + q).json()


def bal(c):
    return {r["account"]: r for r in rep(c)["rows"]}


def add(c, label="Virement UBA", kind="bank"):
    return push(c, op("payacct.create", {"label": label, "kind": kind}))[0]


def vente(c, method, qty=5):
    pid = push(c, op("product.create", {"id": str(uuid.uuid4()), "name": "Ciment " + uuid.uuid4().hex[:4], "price": 100_000}))[0]["product"]["id"]
    cid = push(c, op("client.create", {"id": str(uuid.uuid4()), "name": "Camara " + uuid.uuid4().hex[:4]}))[0]["client"]["id"]
    return push(c, op("sale.create", {"client_id": cid, "kind": "cash", "pay_method": method, "lines": [{"product_id": pid, "qty": qty}]}))[0]


def test_creation_et_mode_de_paiement(owner):
    r = add(owner)
    assert r["status"] == "ok" and r["pay_account"]["key"] == "acct1" and r["pay_account"]["ledger"] == "52103"
    p = pull(owner)
    assert "Virement UBA" in p["methods"] and "acct1" in p["bank_meta"] and p["bank_meta"]["acct1"]["methods"] == ["Virement UBA"]
    assert vente(owner, "Virement UBA")["status"] == "ok"
    assert vente(owner, "Banque inconnue")["status"] == "invalid"
    b = bal(owner)
    assert b["52103"]["close_debit"] == 500_000 and "521" not in b                          # rangé sur son propre compte comptable, pas sur la banque principale
    assert rep(owner, "statements")["balance_sheet"]["balanced"] is True
    assert any(a["code"] == "52103" and a["custom"] for a in p["accounting"]["accounts"])


def test_noms_refuses_et_deuxieme_compte(owner):
    for bad in ("Espèces", "orange money", "xy", "owner", "Autre"):
        assert add(owner, bad)["status"] == "invalid", bad
    assert add(owner, "UBA Guinée")["status"] == "ok" and add(owner, "uba guinée")["status"] == "invalid"
    assert add(owner, "Orange Business", "mobile")["pay_account"]["ledger"] == "52104"
    assert push(owner, op("payacct.create", {"label": "Compte x", "kind": "autre"}))[0]["status"] == "invalid"


def test_rapprochement_propre_a_chaque_compte(owner):
    add(owner)
    push(owner, op("bank.opening", {"account": "acct1", "balance": 1_000_000, "date": "2026-01-01"}))
    vente(owner, "Virement UBA", 3); vente(owner, "Virement bancaire", 2)
    p = pull(owner)
    assert [i["amount"] for i in p["bank_items"]["acct1"]] == [300_000] and [i["amount"] for i in p["bank_items"]["bank"]] == [200_000]
    assert push(owner, op("bank.import", {"account": "acct1", "lines": [{"date": T, "label": "VIR CAMARA", "amount": 300_000, "reference": "R1"}]}))[0]["status"] == "ok"
    assert push(owner, op("bank.automatch", {"account": "acct1"}))[0]["status"] == "ok"
    assert pull(owner)["bank_items"]["acct1"] == []
    assert push(owner, op("bank.import", {"account": "acct9", "lines": [{"date": T, "label": "x", "amount": 1}]}))[0]["status"] == "invalid"
    assert push(owner, op("bank.opening", {"account": "acct1", "balance": 1_000_000, "date": "2026-01-01"}))[0]["status"] == "ok"
    b = bal(owner)
    assert b["52103"]["close_debit"] == 1_300_000 and rep(owner)["balanced"] is True


def test_depot_d_especes_sur_le_compte_choisi(owner):
    add(owner)
    push(owner, op("cash.close", {"close_date": (date.today() - timedelta(days=1)).isoformat(), "opening": 0, "counted": 0, "note": "ouverture"}))
    push(owner, op("cash.move", {"kind": "owner_in", "amount": 1_000_000, "note": "apport du patron"}))
    r = push(owner, op("cash.move", {"kind": "bank_deposit", "amount": 400_000, "note": "bordereau 1", "account": "acct1"}))[0]
    assert r["status"] == "ok" and r["movement"]["account"] == "acct1"
    r2 = push(owner, op("cash.move", {"kind": "bank_deposit", "amount": 100_000, "note": "bordereau 2"}))[0]
    assert r2["movement"]["account"] == "bank"
    p = pull(owner)
    assert [i["amount"] for i in p["bank_items"]["acct1"]] == [400_000] and [i["amount"] for i in p["bank_items"]["bank"]] == [100_000]
    b = bal(owner)
    assert b["52103"]["close_debit"] == 400_000 and b["521"]["close_debit"] == 100_000
    mob = add(owner, "Wave", "mobile")
    assert push(owner, op("cash.move", {"kind": "bank_deposit", "amount": 1000, "note": "essai mobile", "account": "acct2"}))[0]["status"] == "invalid"       # un dépôt d'espèces va sur un compte bancaire
    assert push(owner, op("cash.move", {"kind": "bank_deposit", "amount": 1000, "note": "essai inconnu", "account": "zzz"}))[0]["status"] == "invalid"


def test_desactivation_droits_et_cloture(owner, app_env):
    a = add(owner)["pay_account"]
    vente(owner, "Virement UBA")
    assert push(owner, op("payacct.set_active", {"key": "acct1", "base_version": 9, "active": False}))[0]["status"] == "conflict"
    assert push(owner, op("payacct.set_active", {"key": "acct1", "base_version": a["version"], "active": False}))[0]["status"] == "ok"
    assert "Virement UBA" not in pull(owner)["methods"] and vente(owner, "Virement UBA")["status"] == "invalid"
    assert bal(owner)["52103"]["close_debit"] == 500_000                                    # l'historique reste
    marie, _ = make_user(owner, app_env, "marie", "sales")
    assert push(marie, op("payacct.create", {"label": "Compte de Marie", "kind": "bank"}))[0]["status"] == "denied" and pull(marie)["pay_accounts"] == []
    comp, _ = make_user(owner, app_env, "compta", "accountant")
    assert add(comp, "Compte compta")["status"] == "ok"
    assert owner.get("/api/audit/verify").json()["ok"] is True
