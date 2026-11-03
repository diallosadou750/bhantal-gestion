import uuid
from datetime import date

from conftest import make_user, op, push

TODAY = date.today()
T = TODAY.isoformat()
Y = TODAY.year - 1


def rep(c, kind="statements", **params):
    q = "&".join(f"{k}={v}" for k, v in {"kind": kind, "from": "2000-01-01", "to": T, **params}.items())
    r = c.get("/api/accounting/report?" + q)
    assert r.status_code == 200, r.text
    return r.json()


def bal(c, **params):
    return {r["account"]: r for r in rep(c, "balance", **params)["rows"]}


def net(row, side="debit"):
    return row["close_debit"] - row["close_credit"] if side == "debit" else row["close_credit"] - row["close_debit"]


def loyer(c, amount=410_000, d=f"{Y}-11-10"):
    r = push(c, op("expense.create", {"category": "Loyer et charges locatives", "amount": amount, "method": "Virement bancaire", "payee": "Bailleur", "expense_date": d}))[0]
    assert r["status"] == "ok", r
    return r["expense"]


def defer(c, **extra):
    return push(c, op("deferral.create", {"id": str(uuid.uuid4()), "kind": "charge", "label": "Loyer payé d'avance", "start_date": f"{Y}-11-10", "end_date": f"{Y}-12-20", **extra}))[0]


def test_etalement_au_prorata_des_jours(owner):
    e = loyer(owner)
    r = defer(owner, expense_id=e["id"])
    assert r["status"] == "ok" and r["deferral"]["number"] == "CCA-0001" and r["deferral"]["remaining"] == 0 and r["deferral"]["recognized"] == 410_000
    mid = bal(owner, **{"to": f"{Y}-11-30"})
    assert net(mid["622"]) == 210_000 and net(mid["476"]) == 200_000                  # 21 jours sur 41 en novembre, le reste est une charge constatée d'avance
    end = bal(owner, **{"to": f"{Y}-12-31"})
    assert net(end["622"]) == 410_000 and net(end["476"]) == 0
    assert rep(owner, "balance")["balanced"] is True and rep(owner)["balance_sheet"]["balanced"] is True
    f = rep(owner, "financials", **{"to": f"{Y}-11-30"})
    assert f["all_ok"] is True
    assert any(x["account"] == "476" and x["amount"] == 200_000 for g in f["balance_sheet"]["assets"] for x in g["rows"])


def test_produit_constate_d_avance(owner):
    r = push(owner, op("deferral.create", {"id": str(uuid.uuid4()), "kind": "product", "label": "Abonnement facturé d'avance", "account": "706", "amount": 600_000, "book_date": f"{Y}-11-10", "start_date": f"{Y}-11-10", "end_date": f"{Y}-12-20"}))[0]
    assert r["status"] == "ok" and r["deferral"]["number"] == "PCA-0001"
    b = bal(owner, **{"to": f"{Y}-11-30"})
    assert b["477"]["close_credit"] - b["477"]["close_debit"] == round(600_000 * 20 / 41) and rep(owner, "balance")["balanced"] is True


def test_depense_annulee_et_annulation(owner):
    e = loyer(owner)
    d = defer(owner, expense_id=e["id"])["deferral"]
    assert defer(owner, expense_id=e["id"])["status"] == "invalid"                      # déjà étalée
    assert push(owner, op("deferral.cancel", {"id": d["id"], "base_version": d["version"], "reason": ""}))[0]["status"] == "invalid"
    assert push(owner, op("deferral.cancel", {"id": d["id"], "base_version": 9, "reason": "essai de conflit"}))[0]["status"] == "conflict"
    assert push(owner, op("deferral.cancel", {"id": d["id"], "base_version": d["version"], "reason": "étalement inutile"}))[0]["status"] == "ok"
    assert "476" not in bal(owner) and net(bal(owner)["622"]) == 410_000
    e2 = loyer(owner, d=f"{Y}-11-12")
    push(owner, op("deferral.create", {"id": str(uuid.uuid4()), "kind": "charge", "label": "Autre", "expense_id": e2["id"], "start_date": f"{Y}-11-12", "end_date": f"{Y}-12-20"}))
    push(owner, op("expense.cancel", {"id": e2["id"], "base_version": e2["version"], "reason": "saisie en double"}))
    assert "476" not in bal(owner) and rep(owner, "balance")["balanced"] is True
    assert any(x["orphan"] for x in owner.get("/api/sync/pull?since=0").json()["deferrals"])


def test_validations_droits_et_verrou(owner, app_env):
    e = loyer(owner)
    for bad in ({"start_date": f"{Y}-10-01"}, {"end_date": f"{Y}-11-25"}, {"end_date": f"{Y}-11-01"}, {"end_date": f"{Y + 9}-01-01"}, {"label": ""}, {"amount": 999_999}):
        assert defer(owner, expense_id=e["id"], **bad)["status"] == "invalid", bad
    assert defer(owner, kind="charge", account="701", amount=1000, book_date=f"{Y}-11-10")["status"] == "invalid"            # une charge exige un compte de classe 6
    sales, _ = make_user(owner, app_env, "marie", "sales")
    assert defer(sales, expense_id=e["id"])["status"] == "denied" and sales.get("/api/sync/pull?since=0").json()["deferrals"] == []
    assert push(owner, op("accounting.lock", {"through": f"{Y}-11-30"}))[0]["status"] == "ok"
    assert defer(owner, expense_id=e["id"])["status"] == "invalid"
