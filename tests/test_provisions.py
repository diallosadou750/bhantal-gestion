import uuid
from datetime import date, timedelta

from conftest import make_user, op, push

TODAY = date.today()
T = TODAY.isoformat()


def pull(c):
    return c.get("/api/sync/pull?since=0").json()


def rep(c, kind="statements", **params):
    q = "&".join(f"{k}={v}" for k, v in {"kind": kind, "from": "2000-01-01", "to": T, **params}.items())
    r = c.get("/api/accounting/report?" + q)
    assert r.status_code == 200, r.text
    return r.json()


def bal(c, **params):
    return {r["account"]: r for r in rep(c, "balance", **params)["rows"]}


def creance(c, days_late=200, total=1_000_000, paid=0):
    pid = push(c, op("product.create", {"id": str(uuid.uuid4()), "name": "Ciment", "price": 100_000}))[0]["product"]["id"]
    cid = push(c, op("client.create", {"id": str(uuid.uuid4()), "name": "Camara", "credit_limit": 50_000_000}))[0]["client"]["id"]
    due = (TODAY - timedelta(days=days_late)).isoformat()
    s = push(c, op("sale.create", {"client_id": cid, "kind": "credit", "sale_date": (TODAY - timedelta(days=days_late + 5)).isoformat(), "due_date": due, "lines": [{"product_id": pid, "qty": total // 100_000}]}))[0]["sale"]
    if paid: push(c, op("payment.create", {"sale_id": s["id"], "amount": paid, "method": "Espèces", "pay_date": T}))
    return s


def prov(c, **extra):
    return push(c, op("provision.create", {"id": str(uuid.uuid4()), "kind": "litigation", "label": "Litige fournisseur", "amount": 400_000, "prov_date": T, **extra}))[0]


def test_suggestions_sur_creances_en_retard(owner):
    s = creance(owner, 200, 1_000_000)
    sg = pull(owner)["provision_suggestions"]
    assert len(sg) == 1 and sg[0]["sale_id"] == s["id"] and sg[0]["balance"] == 1_000_000 and sg[0]["suggested"] == 1_000_000
    owner2 = owner
    creance(owner2, 120, 600_000)
    assert sorted(x["suggested"] for x in pull(owner)["provision_suggestions"]) == [300_000, 1_000_000]
    creance(owner, 30, 500_000)
    assert len(pull(owner)["provision_suggestions"]) == 2                    # une créance récente n'est pas suggérée


def test_provision_creance_ecritures_et_bilan(owner):
    s = creance(owner)
    r = prov(owner, kind="receivable", label="Client introuvable", sale_id=s["id"], amount=600_000)
    assert r["status"] == "ok" and r["provision"]["number"] == "PV-0001" and r["provision"]["account"] == "491"
    b = bal(owner)
    assert b["691"]["close_debit"] == 600_000 and b["491"]["close_credit"] == 600_000
    assert pull(owner)["provision_suggestions"] == []                          # déjà provisionnée
    st = rep(owner)
    assert st["balance_sheet"]["balanced"] is True
    assert any(x["account"] == "491" and x["amount"] == -600_000 for x in st["balance_sheet"]["assets"])
    f = rep(owner, "financials")
    assert f["all_ok"] is True
    cl = next(g for g in f["balance_sheet"]["assets"] if g["group"].startswith("Créances"))
    assert cl["dep"] == 600_000 and cl["net"] == cl["gross"] - 600_000
    over = prov(owner, kind="receivable", label="Trop", sale_id=s["id"], amount=500_000)
    assert over["status"] == "invalid"                                         # dépasse le reste dû, compte tenu de l'existant
    assert prov(owner, kind="receivable", label="Sans facture", amount=1)["status"] == "invalid"


def test_reprise_partielle_totale_et_annulation(owner):
    p = prov(owner)["provision"]
    assert push(owner, op("provision.release", {"id": p["id"], "base_version": p["version"], "amount": 100_000, "reason": ""}))[0]["status"] == "invalid"
    r = push(owner, op("provision.release", {"id": p["id"], "base_version": p["version"], "amount": 150_000, "reason": "litige en partie réglé"}))[0]
    assert r["status"] == "ok" and r["provision"]["remaining"] == 250_000
    b = bal(owner)
    assert b["191"]["close_credit"] == 250_000 and b["791"]["close_credit"] == 150_000 and b["691"]["close_debit"] == 400_000
    assert push(owner, op("provision.release", {"id": p["id"], "base_version": 1, "amount": 10, "reason": "x y z"}))[0]["status"] == "conflict"
    v = r["provision"]
    assert push(owner, op("provision.release", {"id": p["id"], "base_version": v["version"], "amount": 300_000, "reason": "trop"}))[0]["status"] == "invalid"
    assert push(owner, op("provision.cancel", {"id": p["id"], "base_version": v["version"], "reason": "erreur"}))[0]["status"] == "invalid"       # des reprises existent
    rid = v["releases"][0]["id"]
    assert push(owner, op("provision.unrelease", {"id": p["id"], "release_id": rid, "base_version": v["version"], "reason": "reprise saisie par erreur"}))[0]["status"] == "ok"
    v2 = pull(owner)["provisions"][0]
    assert v2["remaining"] == 400_000 and "791" not in bal(owner)
    assert push(owner, op("provision.cancel", {"id": p["id"], "base_version": v2["version"], "reason": "erreur de saisie"}))[0]["status"] == "ok"
    assert "191" not in bal(owner) and rep(owner, "balance")["balanced"] is True
    assert owner.get("/api/audit/verify").json()["ok"] is True


def test_flux_de_tresorerie_et_etats_apres_provision(owner):
    prov(owner, amount=250_000)
    f = rep(owner, "financials")
    assert f["all_ok"] is True and f["cash_flow"]["financing"] == 0 and f["cash_flow"]["operating"] == 0 and f["income"]["net_result"] == -250_000


def test_droits_validations_et_verrou(owner, app_env):
    sales, _ = make_user(owner, app_env, "marie", "sales")
    assert prov(sales)["status"] == "denied" and pull(sales)["provisions"] == []
    compta, _ = make_user(owner, app_env, "compta", "accountant")
    assert prov(compta)["status"] == "ok"
    for bad in ({"kind": "autre"}, {"label": ""}, {"amount": 0}, {"prov_date": "2999-01-01"}):
        assert prov(owner, **bad)["status"] == "invalid", bad
    old = (TODAY - timedelta(days=400)).isoformat()
    assert push(owner, op("accounting.lock", {"through": old}))[0]["status"] == "ok"
    assert prov(owner, prov_date=old)["status"] == "invalid"
