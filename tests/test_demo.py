import sqlite3

import pytest
from conftest import H, PW


def demo_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from gestion import api, demo
    monkeypatch.setenv("GESTION_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GESTION_DEMO", "0")                      # prepare() modifie l'environnement : monkeypatch le remettra en place
    demo.prepare(tmp_path / "d")
    return TestClient(api.app, headers=H)


def test_demo_donnees_fictives_et_roles(tmp_path, monkeypatch):
    with demo_client(tmp_path, monkeypatch) as c:
        assert c.get("/api/state").json()["demo"] is True
        assert c.post("/api/demo/login", json={"role": "owner"}).status_code == 200
        r = c.get("/api/sync/pull?since=0").json()
        assert len(r["clients"]) == 6 and len(r["sales"]) >= 10 and len(r["products"]) == 8 and all("fictif" in x["note"] for x in r["clients"])
        assert any(s["balance"] > 0 and s["due_date"] < __import__("datetime").date.today().isoformat() for s in r["sales"])      # des retards pour la démonstration
        assert c.get("/api/audit/verify").json()["ok"] is True
        b = c.get("/api/accounting/report?kind=balance&from=2000-01-01").json()                     # la comptabilité de démonstration doit être équilibrée
        s = c.get("/api/accounting/report?kind=statements&from=2000-01-01").json()
        assert b["balanced"] is True and s["balance_sheet"]["balanced"] is True and s["income"]["sales"] > 0 and len(b["rows"]) > 10
        c.post("/api/demo/login", json={"role": "sales"})
        mine = c.get("/api/sync/pull?since=0").json()
        assert 0 < len(mine["clients"]) < 6 and all(cl["owner_user_id"] for cl in mine["clients"]) and len(mine["sales"]) < len(r["sales"])
        c.post("/api/demo/login", json={"role": "cashier"})
        assert all("credit_limit" not in cl for cl in c.get("/api/sync/pull?since=0").json()["clients"])
        assert c.post("/api/demo/login", json={"role": "inconnu"}).status_code == 404


def test_demo_sans_mot_de_passe_connu(tmp_path, monkeypatch):
    with demo_client(tmp_path, monkeypatch) as c:
        assert c.post("/api/login", json={"username": "demo_owner", "password": PW}).status_code == 401           # pas de mot de passe utilisable


def test_pas_de_demo_hors_mode_demo(app_env, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.delenv("GESTION_DEMO", raising=False)
    with TestClient(app_env, headers=H) as c:
        assert c.get("/api/state").json()["demo"] is False
        assert c.post("/api/demo/login", json={"role": "owner"}).status_code == 404


def test_demo_exige_le_marqueur_du_dossier(app_env, monkeypatch, tmp_path):
    """Même si GESTION_DEMO=1 est posé par erreur, un dossier de vraies données (sans marqueur .demo) n'ouvre pas de connexion sans mot de passe."""
    from fastapi.testclient import TestClient
    monkeypatch.setenv("GESTION_DEMO", "1")
    with TestClient(app_env, headers=H) as c:
        c.post("/api/setup", json={"username": "patron", "full_name": "Le Patron", "password": PW})
        c2 = TestClient(app_env, headers=H)
        assert c2.get("/api/state").json()["demo"] is False
        assert c2.post("/api/demo/login", json={"role": "owner"}).status_code == 404


def test_prepare_refuse_un_dossier_de_vraies_donnees(tmp_path, monkeypatch):
    from gestion import demo
    d = tmp_path / "reel"; d.mkdir()
    sqlite3.connect(d / "gestion.sqlite3").execute("CREATE TABLE x(a)").connection.commit()
    with pytest.raises(SystemExit):
        demo.prepare(d)


def test_demo_refusee_en_reseau(monkeypatch):
    from gestion import main
    monkeypatch.setattr("sys.argv", ["gestion", "--demo", "--lan"])
    with pytest.raises(SystemExit) as e:
        main.main()
    assert "refusé en réseau" in str(e.value)
