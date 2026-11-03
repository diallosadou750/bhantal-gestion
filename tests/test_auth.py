from conftest import H, PW, make_user, new_session


def test_setup_une_seule_fois(client):
    assert client.get("/api/state").json()["setup_required"] is True
    assert client.post("/api/setup", json={"username": "patron", "full_name": "P", "password": "court"}).status_code == 422
    assert client.post("/api/setup", json={"username": "patron", "full_name": "Le Patron", "password": PW}).status_code == 200
    assert client.get("/api/state").json()["setup_required"] is False
    assert client.post("/api/setup", json={"username": "autre", "full_name": "X", "password": PW}).status_code == 409


def test_login_echec_verrouillage_et_audit(owner, app_env):
    c = new_session(app_env)
    for _ in range(4):
        assert c.post("/api/login", json={"username": "patron", "password": "mauvais"}).status_code == 401
    assert c.post("/api/login", json={"username": "patron", "password": "mauvais"}).status_code == 401      # 5e échec : verrouillage
    assert c.post("/api/login", json={"username": "patron", "password": PW}).status_code == 423             # même avec le bon mot de passe
    actions = [e["action"] for e in owner.get("/api/audit?limit=50").json()["entries"]]
    assert "login.locked" in actions and "login.blocked" in actions and actions.count("login.failed") == 4


def test_identifiant_inconnu_meme_reponse(client):
    r1 = client.post("/api/login", json={"username": "personne", "password": "x"})
    assert r1.status_code == 401 and r1.json()["code"] == "identifiants"


def test_csrf_en_tete_obligatoire(owner):
    r = owner.post("/api/logout", headers={"X-Requested-With": ""})
    assert r.status_code == 403 and r.json()["code"] == "csrf"


def test_mot_de_passe_provisoire_a_changer(owner, app_env):
    owner.post("/api/users", json={"username": "marie", "full_name": "Marie", "role": "sales", "temp_password": "Temporaire2026ab"})
    c = new_session(app_env)
    c.post("/api/login", json={"username": "marie", "password": "Temporaire2026ab"})
    assert c.get("/api/clients").status_code == 403 and c.get("/api/clients").json()["code"] == "changer_mot_de_passe"
    assert c.post("/api/password", json={"old": "Temporaire2026ab", "new": "Temporaire2026ab"}).status_code == 422
    assert c.post("/api/password", json={"old": "Temporaire2026ab", "new": PW}).status_code == 200
    assert c.get("/api/clients").status_code == 200


def test_roles_et_acces_admin(owner, app_env):
    sales, _ = make_user(owner, app_env, "marie", "sales")
    assert sales.get("/api/users").status_code == 403
    assert sales.get("/api/audit").status_code == 403
    assert sales.post("/api/users", json={"username": "x" * 5, "full_name": "X", "role": "owner", "temp_password": "Temporaire2026ab"}).status_code == 403
    denied = [e for e in owner.get("/api/audit?action=access.denied").json()["entries"]]
    assert len(denied) >= 2 and denied[0]["user"] == "marie"


def test_dernier_proprietaire_protege_et_session_revoquee(owner, app_env):
    me = owner.get("/api/me").json()["user"]
    assert owner.patch(f"/api/users/{me['id']}", json={"active": False}).status_code == 409
    assert owner.patch(f"/api/users/{me['id']}", json={"role": "sales"}).status_code == 409
    sales, u = make_user(owner, app_env, "marie", "sales")
    assert sales.get("/api/me").status_code == 200
    assert owner.patch(f"/api/users/{u['id']}", json={"active": False}).status_code == 200
    assert sales.get("/api/me").status_code == 401                       # session coupée immédiatement
    assert new_session(app_env).post("/api/login", json={"username": "marie", "password": PW}).status_code == 401


def test_deconnexion(owner):
    assert owner.post("/api/logout").status_code == 200
    assert owner.get("/api/me").status_code == 401
