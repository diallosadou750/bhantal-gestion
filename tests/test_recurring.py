import uuid
from datetime import date, timedelta

from conftest import make_user, op, push

TODAY = date.today()


def pull(c):
    return c.get("/api/sync/pull?since=0").json()


def months_ago(n, day=5):
    y, m = TODAY.year, TODAY.month - n
    while m <= 0: y, m = y - 1, m + 12
    return date(y, m, day)


def model(c, **extra):
    data = {"id": str(uuid.uuid4()), "label": "Loyer du dépôt", "category": "Loyer et charges locatives", "amount": 500_000, "method": "Virement bancaire", "payee": "Bailleur", "day": 5, "start_date": months_ago(3, 1).isoformat(), **extra}
    return push(c, op("recurring.create", data))[0]


def test_echeances_generation_et_idempotence(owner):
    r = model(owner)
    assert r["status"] == "ok" and r["recurring"]["number"] == "RC-0001"
    due = len(r["recurring"]["pending"])
    assert due in (3, 4)                                                               # du 1er il y a 3 mois jusqu'à aujourd'hui (le mois en cours compte s'il est échu)
    g = push(owner, op("recurring.generate", {}))[0]
    assert g["status"] == "ok" and len(g["created"]) == due and g["blocked"] == []
    exps = pull(owner)["expenses"]
    assert len(exps) == due and all(e["amount"] == 500_000 and "RC-0001" in e["description"] for e in exps)
    assert pull(owner)["recurring"][0]["pending"] == [] and pull(owner)["recurring"][0]["last_generated"][:4].isdigit()
    assert push(owner, op("recurring.generate", {}))[0]["created"] == []               # jamais deux fois le même mois
    bal = owner.get(f"/api/accounting/report?kind=balance&from=2000-01-01&to={TODAY.isoformat()}").json()
    assert bal["balanced"] is True and any(x["account"] == "622" and x["close_debit"] == due * 500_000 for x in bal["rows"])


def test_annulation_d_une_depense_ne_regenere_pas_et_modification(owner):
    r = model(owner)["recurring"]
    push(owner, op("recurring.generate", {}))
    e = pull(owner)["expenses"][0]
    push(owner, op("expense.cancel", {"id": e["id"], "base_version": e["version"], "reason": "payé autrement"}))
    assert push(owner, op("recurring.generate", {}))[0]["created"] == []
    cur = pull(owner)["recurring"][0]
    u = push(owner, op("recurring.update", {"id": r["id"], "base_version": cur["version"], "changes": {"amount": 600_000, "end_date": None}}))[0]
    assert u["status"] == "ok" and u["recurring"]["amount"] == 600_000
    assert push(owner, op("recurring.update", {"id": r["id"], "base_version": 1, "changes": {"amount": 1}}))[0]["status"] == "conflict"
    assert push(owner, op("recurring.update", {"id": r["id"], "base_version": u["recurring"]["version"], "changes": {"active": False}}))[0]["recurring"]["active"] is False


def test_fin_de_mois_court_et_validations(owner):
    r = model(owner, day=31, start_date=months_ago(2, 1).isoformat())["recurring"]
    assert all(d[8:] in ("28", "29", "30", "31") for d in r["pending"])               # le 31 devient le dernier jour des mois courts
    for bad in ({"category": "Inconnue"}, {"amount": 0}, {"day": 32}, {"method": "Troc"}, {"label": ""}, {"end_date": "2000-01-01"}):
        assert model(owner, **bad)["status"] == "invalid", bad
    fut = model(owner, start_date=(TODAY + timedelta(days=40)).isoformat())["recurring"]
    assert fut["pending"] == []                                                         # pas d'échéance dans le futur


def test_verrou_bloque_et_droits(owner, app_env):
    r = model(owner)["recurring"]
    assert push(owner, op("accounting.lock", {"through": months_ago(2, 28).isoformat()}))[0]["status"] == "ok"
    g = push(owner, op("recurring.generate", {}))[0]
    assert g["created"] == [] and len(g["blocked"]) == 1 and "verrouillée" in g["blocked"][0]["message"]        # la période verrouillée n'est jamais touchée
    marie, _ = make_user(owner, app_env, "marie", "sales")
    cash, _ = make_user(owner, app_env, "paul", "cashier")
    for c in (marie, cash):
        assert push(c, op("recurring.generate", {}))[0]["status"] == "denied" and pull(c)["recurring"] == []
    comp, _ = make_user(owner, app_env, "compta", "accountant")
    assert len(pull(comp)["recurring"]) == 1 and push(comp, op("recurring.generate", {}))[0]["status"] == "ok"
