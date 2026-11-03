import uuid
from datetime import date, timedelta

from conftest import make_user, op, push

TODAY = date.today()
T = TODAY.isoformat()
LAST = TODAY.year - 1
MOIS_CLOS = 1 if (TODAY + timedelta(days=1)).month != TODAY.month else 0      # le dernier jour du mois, la dotation du mois est déjà passée


def pull(c):
    r = c.get("/api/sync/pull?since=0")
    assert r.status_code == 200, r.text
    return r.json()


def rep(c, kind="statements", **params):
    q = "&".join(f"{k}={v}" for k, v in {"kind": kind, "from": "2000-01-01", "to": T, **params}.items())
    r = c.get("/api/accounting/report?" + q)
    assert r.status_code == 200, r.text
    return r.json()


def bal(c, **params):
    return {r["account"]: r for r in rep(c, "balance", **params)["rows"]}


def new_asset(c, **extra):
    data = {"id": str(uuid.uuid4()), "name": "Ordinateur", "category": "Matériel informatique et de bureau", "origin": "purchase", "acquisition_date": f"{LAST}-01-05",
            "cost": 1_200_000, "pay_mode": "Virement bancaire", "life_years": 3, **extra}
    r = push(c, op("asset.create", data))[0]
    return r


def months_elapsed(start: date) -> int:
    """Mois entiers terminés, à partir du mois de mise en service."""
    return (TODAY.year - start.year) * 12 + TODAY.month - start.month + MOIS_CLOS


def assets(c):
    return pull(c)["assets"]


def test_achat_amortissement_lineaire_et_comptes_equilibres(owner):
    r = new_asset(owner)
    assert r["status"] == "ok" and r["asset"]["number"] == "IM-0001"
    k = min(months_elapsed(date(LAST, 1, 1)), 36)
    expected = round(1_200_000 * k / 36)
    a = assets(owner)[0]
    assert a["accumulated"] == expected and a["net_value"] == 1_200_000 - expected and a["monthly"] == 33_333 and a["asset_account"] == "244" and a["dep_account"] == "284"
    b = bal(owner)
    assert b["244"]["close_debit"] == 1_200_000 and b["284"]["close_credit"] == expected and b["681"]["close_debit"] == expected and b["521"]["close_credit"] == 1_200_000
    assert rep(owner, "balance")["balanced"] is True
    s = rep(owner)
    assert s["balance_sheet"]["balanced"] is True and s["income"]["result"] == -expected
    assert any(x["account"] == "284" and x["amount"] == -expected for x in s["balance_sheet"]["assets"])          # amortissements présentés en déduction de l'actif
    assert owner.get("/api/audit/verify").json()["ok"] is True


def test_somme_exacte_sur_toute_la_duree(owner):
    new_asset(owner, acquisition_date="2010-03-17", cost=1_000_001, residual=1, life_years=4)
    a = assets(owner)[0]
    assert a["accumulated"] == 1_000_000 and a["net_value"] == 1 and a["fully_depreciated_on"] == "2014-02-28"
    p = owner.get(f"/api/accounting/report?kind=schedule&asset_id={a['id']}").json()
    assert [y["year"] for y in p["years"]] == [2010, 2011, 2012, 2013, 2014] and p["years"][-1]["accumulated"] == 1_000_000 and p["years"][-1]["net_value"] == 1
    assert sum(y["charge"] for y in p["years"]) == 1_000_000


def test_bien_deja_possede_reprise(owner):
    start = date(TODAY.year - 2, TODAY.month, 1)
    r = push(owner, op("asset.create", {"id": str(uuid.uuid4()), "name": "Camion", "category": "Matériel de transport", "origin": "opening", "acquisition_date": T, "start_date": start.isoformat(), "cost": 10_000_000, "life_years": 5}))[0]
    assert r["status"] == "ok" and r["asset"]["months_already"] == 24 and r["asset"]["opening_accum"] == 4_000_000
    b = bal(owner)
    dotation = round(10_000_000 * 25 / 60) - 4_000_000 if MOIS_CLOS else 0
    assert b["245"]["close_debit"] == 10_000_000 and b["284"]["close_credit"] == 4_000_000 + dotation and b["101"]["close_credit"] == 6_000_000 and ("681" not in b if not dotation else b["681"]["close_debit"] == dotation)
    assert rep(owner, "balance")["balanced"] is True and rep(owner)["balance_sheet"]["balanced"] is True
    assert push(owner, op("asset.create", {"id": str(uuid.uuid4()), "name": "X", "category": "Matériel de transport", "origin": "opening", "acquisition_date": f"{LAST}-01-01", "start_date": T, "cost": 1000}))[0]["status"] == "invalid"


def test_cession_plus_value_et_mise_au_rebut(owner):
    a = new_asset(owner, name="Imprimante", acquisition_date=f"{LAST}-01-05", cost=360_000, life_years=3)["asset"]
    acc = assets(owner)[0]["accumulated"]
    d = push(owner, op("asset.dispose", {"id": a["id"], "base_version": a["version"], "kind": "sold", "price": 300_000, "method": "Espèces", "date": T}))[0]
    assert d["status"] == "ok"
    vnc = 360_000 - d["asset"]["accumulated"]
    assert d["asset"]["status"] == "disposed" and d["asset"]["gain_loss"] == 300_000 - vnc
    b = bal(owner)
    assert b["821"]["close_credit"] == 300_000 and b["811"]["close_debit"] == vnc and b["244"]["close_debit"] == 0 and b.get("284", {"close_credit": 0})["close_credit"] == 0
    assert rep(owner, "balance")["balanced"] is True and rep(owner)["balance_sheet"]["balanced"] is True
    assert push(owner, op("asset.dispose", {"id": a["id"], "base_version": 99, "kind": "scrapped", "date": T}))[0]["status"] == "invalid"            # déjà sortie
    b2 = new_asset(owner, name="Table")["asset"]
    z = push(owner, op("asset.dispose", {"id": b2["id"], "base_version": b2["version"], "kind": "scrapped", "date": T}))[0]
    assert z["status"] == "ok" and z["asset"]["disposal_price"] == 0 and z["asset"]["gain_loss"] < 0
    assert rep(owner)["balance_sheet"]["balanced"] is True


def test_annulation_et_conflit(owner):
    a = new_asset(owner)["asset"]
    assert push(owner, op("asset.cancel", {"id": a["id"], "base_version": a["version"], "reason": ""}))[0]["status"] == "invalid"
    assert push(owner, op("asset.cancel", {"id": a["id"], "base_version": 7, "reason": "saisie en double"}))[0]["status"] == "conflict"
    assert push(owner, op("asset.cancel", {"id": a["id"], "base_version": a["version"], "reason": "saisie en double"}))[0]["status"] == "ok"
    assert "244" not in bal(owner) and assets(owner)[0]["status"] == "cancelled"
    assert rep(owner, "balance")["balanced"] is True


def test_droits_validations_et_verrou(owner, app_env):
    sales, _ = make_user(owner, app_env, "marie", "sales")
    cashier, _ = make_user(owner, app_env, "paul", "cashier")
    compta, _ = make_user(owner, app_env, "compta", "accountant")
    assert new_asset(sales)["status"] == "denied" and new_asset(cashier)["status"] == "denied"
    assert pull(sales)["assets"] == [] and pull(cashier)["assets"] == []
    assert new_asset(compta)["status"] == "ok"
    for bad in ({"category": "Inconnue"}, {"cost": 0}, {"cost": 100, "residual": 200}, {"pay_mode": "Troc"}, {"origin": "autre"}, {"acquisition_date": "2999-01-01"}, {"name": ""}, {"start_date": f"{LAST - 1}-01-01"}):
        assert new_asset(owner, **bad)["status"] == "invalid", bad
    assert push(owner, op("accounting.lock", {"through": f"{LAST}-12-31", "reason": "essai"}))[0]["status"] == "ok"
    assert new_asset(owner, acquisition_date=f"{LAST}-06-01")["status"] == "invalid"
    assert new_asset(owner, acquisition_date=T)["status"] == "ok"


def test_terrain_non_amortissable_et_modes_de_reglement(owner):
    t = new_asset(owner, name="Terrain", category="Terrains", cost=50_000_000, pay_mode="owner")["asset"]
    assert t["method"] == "none" and t["monthly"] == 0 and t["accumulated"] == 0
    new_asset(owner, name="Logiciel", category="Logiciels et licences", cost=600_000, pay_mode="credit", acquisition_date=T)
    b = bal(owner)
    assert b["221"]["close_debit"] == 50_000_000 and b["462"]["close_credit"] == 50_000_000 and b["213"]["close_debit"] == 600_000 and b["401"]["close_credit"] == 600_000
    assert rep(owner)["balance_sheet"]["balanced"] is True


def test_caisse_et_banque_voient_les_immobilisations(owner):
    push(owner, op("bank.opening", {"account": "bank", "balance": 5_000_000, "date": f"{LAST - 1}-01-01"}))
    before = pull(owner)["cash"]["expected"]
    new_asset(owner, name="Coffre", cost=200_000, pay_mode="Espèces", acquisition_date=T)
    assert pull(owner)["cash"]["expected"] == before - 200_000
    r = new_asset(owner, name="Ordinateur 2", cost=900_000, pay_mode="Virement bancaire", acquisition_date=T)["asset"]
    its = pull(owner)["bank_items"]["bank"]
    assert any(i["type"] == "asset" and i["amount"] == -900_000 for i in its)
    push(owner, op("asset.dispose", {"id": r["id"], "base_version": r["version"], "kind": "sold", "price": 500_000, "method": "Virement bancaire", "date": T}))
    assert any(i["type"] == "assetsale" and i["amount"] == 500_000 for i in pull(owner)["bank_items"]["bank"])


def test_registre_et_cloture_avec_immobilisations(owner):
    new_asset(owner, acquisition_date=f"{LAST - 1}-02-10", cost=720_000, life_years=3)
    reg = owner.get(f"/api/accounting/report?kind=assets&from={LAST}-01-01&to={LAST}-12-31").json()
    assert reg["rows"][0]["period_charge"] == 240_000 and reg["totals"]["period_charge"] == 240_000
    pv = owner.get(f"/api/accounting/closing?year={LAST - 1}").json()
    assert pv["can_close"] is True and pv["result"] == -round(720_000 * 11 / 36)              # février à décembre (11 mois), les arrondis se répartissent
    assert push(owner, op("fiscal.close", {"year": LAST - 1, "accept_warnings": True}))[0]["status"] == "ok"
    assert push(owner, op("fiscal.close", {"year": LAST, "accept_warnings": True}))[0]["status"] == "ok"
    assert rep(owner, "balance")["balanced"] is True and rep(owner)["balance_sheet"]["balanced"] is True
    b = bal(owner, **{"from": f"{LAST}-01-01", "to": f"{LAST}-12-31"})
    assert b["681"]["close_debit"] == 0 and b["681"]["debit"] == 240_000 and b["284"]["close_credit"] == round(720_000 * 23 / 36)         # charge soldée à la clôture, amortissements cumulés conservés
