import uuid
from datetime import date, timedelta

from conftest import op, push

from gestion import assets

TODAY = date.today()
T = TODAY.isoformat()
MOIS_CLOS = 1 if (TODAY + timedelta(days=1)).month != TODAY.month else 0      # le dernier jour du mois, la dotation du mois est déjà passée


def rep(c, kind="statements", **params):
    q = "&".join(f"{k}={v}" for k, v in {"kind": kind, "from": "2000-01-01", "to": T, **params}.items())
    r = c.get("/api/accounting/report?" + q)
    assert r.status_code == 200, r.text
    return r.json()


def bal(c):
    return {r["account"]: r for r in rep(c, "balance")["rows"]}


def create(c, **extra):
    data = {"id": str(uuid.uuid4()), "name": "Camion", "category": "Matériel de transport", "origin": "purchase", "acquisition_date": "2010-01-05", "cost": 10_000_000,
            "pay_mode": "Virement bancaire", "life_years": 5, "scheme": "degressive", **extra}
    return push(c, op("asset.create", data))[0]


def test_courbe_exemple_classique():
    # 5 ans, coefficient 1,75 (taux 35 %), valeur 1 000 000 : exemple de manuel
    c = assets._curve(1_000_000, 60, 175)
    assert [c[12 * i] for i in range(6)] == [0, 350_000, 577_500, 725_375, 862_687, 1_000_000]       # bascule en linéaire en 4e année, dernière année solde
    c3 = assets._curve(1_000_000, 36, 125)
    assert [c3[12 * i] for i in range(4)] == [0, 416_667, 708_333, 1_000_000]
    for curve in (c, c3, assets._curve(777_777, 96, 225)):
        assert all(b >= a for a, b in zip(curve, curve[1:])) and curve[-1] in (1_000_000, 777_777)    # croissante et solde exactement la base
    assert assets.default_coeff(3) == 125 and assets.default_coeff(5) == 175 and assets.default_coeff(8) == 225


def test_plan_degressif_complet_et_comptes_equilibres(owner):
    r = create(owner)
    assert r["status"] == "ok" and r["asset"]["scheme"] == "degressive" and r["asset"]["dec_coeff"] == 1.75
    a = r["asset"]
    assert a["accumulated"] == 10_000_000 and a["net_value"] == 0 and a["fully_depreciated_on"] == "2014-12-31"       # 2010 : bien ancien, tout est amorti
    p = owner.get(f"/api/accounting/report?kind=schedule&asset_id={a['id']}&from=2000-01-01&to={T}").json()
    assert [y["year"] for y in p["years"]] == [2010, 2011, 2012, 2013, 2014]
    assert p["years"][0]["charge"] == 3_500_000 and p["years"][-1]["accumulated"] == 10_000_000                   # 35 % la première année
    assert sum(y["charge"] for y in p["years"]) == 10_000_000
    assert p["years"][0]["charge"] > p["years"][1]["charge"] > p["years"][2]["charge"]                            # dotations décroissantes
    b = bal(owner)
    assert b["245"]["close_debit"] == 10_000_000 and b["284"]["close_credit"] == 10_000_000 and b["681"]["close_debit"] == 10_000_000
    assert rep(owner, "balance")["balanced"] is True and rep(owner)["balance_sheet"]["balanced"] is True
    assert owner.get("/api/audit/verify").json()["ok"] is True


def test_bien_recent_dotation_du_prochain_mois_et_cumul_conforme(owner):
    start = date(TODAY.year - 1, TODAY.month, 1)
    a = create(owner, acquisition_date=start.isoformat(), life_years=5)["asset"]
    k = TODAY.year * 12 + TODAY.month - (start.year * 12 + start.month) + MOIS_CLOS           # mois entiers terminés
    curve = assets._curve(10_000_000, 60, 175)
    assert a["accumulated"] == curve[k] and a["net_value"] == 10_000_000 - curve[k]
    assert a["monthly"] == curve[k + 1] - curve[k] and a["monthly"] > 0                # le mois à venir (la dotation change chaque année)
    assert a["year_charge"] >= 0
    assert rep(owner, "balance")["balanced"] is True


def test_reprise_d_un_bien_degressif(owner):
    start = date(TODAY.year - 2, TODAY.month, 1)
    r = create(owner, origin="opening", pay_mode=None, acquisition_date=T, start_date=start.isoformat(), name="Camion repris")
    assert r["status"] == "ok", r
    a = r["asset"]
    assert a["months_already"] == 24 and a["opening_accum"] == 5_775_000                # cumul dégressif après 24 mois (pas 4 000 000 comme en linéaire)
    b = bal(owner)
    curve = assets._curve(10_000_000, 60, 175)
    assert b["284"]["close_credit"] == curve[24 + MOIS_CLOS] and b["101"]["close_credit"] == 10_000_000 - 5_775_000
    assert rep(owner, "balance")["balanced"] is True and rep(owner)["balance_sheet"]["balanced"] is True
    x = owner.get("/api/sync/pull?since=0").json()["assets"][0]
    assert x["accumulated"] == curve[24 + MOIS_CLOS]                                    # aucune dotation avant la fin du mois courant : on reste au cumul d'ouverture


def test_cession_d_un_bien_degressif(owner):
    a = create(owner, acquisition_date="2020-01-05", cost=6_000_000, life_years=4)["asset"]
    assert a["dec_coeff"] == 1.25
    d = push(owner, op("asset.dispose", {"id": a["id"], "base_version": a["version"], "kind": "sold", "price": 500_000, "method": "Espèces", "date": "2022-06-20"}))[0]
    assert d["status"] == "ok"
    expected = assets._curve(6_000_000, 48, 125)
    assert d["asset"]["accumulated"] == expected[30]                                    # janvier 2020 à juin 2022 = 30 mois, dont le mois de sortie (comme en linéaire)
    assert d["asset"]["monthly"] == 0
    assert rep(owner, "balance")["balanced"] is True and rep(owner)["balance_sheet"]["balanced"] is True


def test_validations(owner):
    ko = lambda **kw: create(owner, **kw)["status"] == "invalid"
    assert ko(life_years=2)                                         # dégressif : au moins 3 ans
    assert ko(coeff=5) and ko(coeff=0.5) and ko(coeff="abc")
    assert ko(scheme="exotique")
    assert ko(category="Terrains")                                  # un terrain ne s'amortit pas
    assert create(owner, coeff="1,5")["asset"]["dec_coeff"] == 1.5  # virgule décimale acceptée
    assert create(owner, scheme="linear")["asset"]["scheme"] == "linear"
    assert create(owner, scheme=None)["asset"]["scheme"] == "linear"


def test_ancienne_base_recoit_la_colonne(owner):
    from gestion import db
    with db.read() as c:
        assert "coeff" in {r["name"] for r in c.execute("PRAGMA table_info(fixed_assets)")}
