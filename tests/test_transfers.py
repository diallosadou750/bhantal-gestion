import uuid
from datetime import date

from conftest import make_user, op, push

T = date.today().isoformat()


def pull(c):
    return c.get("/api/sync/pull?since=0").json()


def bal(c):
    r = c.get(f"/api/accounting/report?kind=balance&from=2000-01-01&to={T}").json()
    return {x["account"]: x for x in r["rows"]}, r["balanced"]


def prepare(c):
    push(c, op("payacct.create", {"label": "Virement UBA", "kind": "bank"}))
    for k in ("bank", "acct1", "orange"):
        assert push(c, op("bank.opening", {"account": k, "balance": 1_000_000, "date": "2026-01-01"}))[0]["status"] == "ok"


def vir(c, a="bank", b="acct1", amount=300_000, **extra):
    return push(c, op("transfer.create", {"id": str(uuid.uuid4()), "from_account": a, "to_account": b, "amount": amount, **extra}))[0]


def test_virement_ecritures_et_pointage(owner):
    prepare(owner)
    r = vir(owner, reference="VIR-77", note="renforcement du compte UBA")
    assert r["status"] == "ok" and r["transfer"]["number"] == "VI-0001"
    b, ok = bal(owner)
    assert ok and b["521"]["close_debit"] == 700_000 + 0 and b["52103"]["close_debit"] == 1_300_000 and b["5211"]["close_debit"] == 1_000_000      # ouvertures 1 000 000 chacune
    p = pull(owner)
    assert [i["amount"] for i in p["bank_items"]["bank"] if i["type"] == "transfer_out"] == [-300_000] and [i["amount"] for i in p["bank_items"]["acct1"] if i["type"] == "transfer_in"] == [300_000]
    assert p["bank_status"]["bank"]["book_balance"] == 700_000 and p["bank_status"]["acct1"]["book_balance"] == 1_300_000
    # chaque côté se pointe avec son propre relevé
    assert push(owner, op("bank.import", {"account": "bank", "lines": [{"date": T, "label": "VIR INTERNE", "amount": -300_000, "reference": "VIR-77"}]}))[0]["status"] == "ok"
    assert push(owner, op("bank.import", {"account": "acct1", "lines": [{"date": T, "label": "VIR RECU", "amount": 300_000, "reference": "VIR-77"}]}))[0]["status"] == "ok"
    for k in ("bank", "acct1"): assert push(owner, op("bank.automatch", {"account": k}))[0]["status"] == "ok"
    p = pull(owner)
    assert p["bank_items"]["bank"] == [] and p["bank_items"]["acct1"] == []
    assert push(owner, op("transfer.cancel", {"id": r["transfer"]["id"], "base_version": 1, "reason": "erreur"}))[0]["status"] == "invalid"      # pointé : dépointer d'abord


def test_annulation_et_validations(owner):
    prepare(owner)
    r = vir(owner)["transfer"]
    assert push(owner, op("transfer.cancel", {"id": r["id"], "base_version": 9, "reason": "erreur de saisie"}))[0]["status"] == "conflict"
    assert push(owner, op("transfer.cancel", {"id": r["id"], "base_version": 1, "reason": ""}))[0]["status"] == "invalid"
    assert push(owner, op("transfer.cancel", {"id": r["id"], "base_version": 1, "reason": "erreur de saisie"}))[0]["status"] == "ok"
    b, ok = bal(owner)
    assert ok and b["521"]["close_debit"] == 1_000_000 and b["52103"]["close_debit"] == 1_000_000
    for bad in ({"a": "bank", "b": "bank"}, {"a": "bank", "b": "inconnu"}, {"a": "bank", "b": "acct1", "amount": 0}, {"a": "bank", "b": "acct1", "transfer_date": "2999-01-01"}, {"a": "bank", "b": "acct1", "transfer_date": "2026-01-01"}):
        extra = {k: v for k, v in bad.items() if k not in ("a", "b", "amount")}
        assert vir(owner, bad["a"], bad["b"], bad.get("amount", 1000), **extra)["status"] == "invalid", bad
    pa = pull(owner)["pay_accounts"][0]
    push(owner, op("payacct.set_active", {"key": "acct1", "base_version": pa["version"], "active": False}))
    assert vir(owner)["status"] == "invalid"                                              # compte désactivé


def test_droits_et_verrou(owner, app_env):
    prepare(owner)
    marie, _ = make_user(owner, app_env, "marie", "sales")
    assert vir(marie)["status"] == "denied" and pull(marie)["transfers"] == []
    aud, _ = make_user(owner, app_env, "expert", "auditor")
    assert vir(aud)["status"] == "denied"
    vir(owner)
    assert len(pull(aud)["transfers"]) == 1
    assert owner.get("/api/audit/verify").json()["ok"] is True
    assert push(owner, op("accounting.lock", {"through": T}))[0]["status"] == "ok"
    assert vir(owner, transfer_date=T)["status"] == "invalid"
