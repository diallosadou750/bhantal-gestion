from conftest import PW, make_user, new_session


def login(app_env, user="patron", pw=PW):
    c = new_session(app_env)
    assert c.post("/api/login", json={"username": user, "password": pw}).status_code == 200
    return c


def test_liste_et_fermeture_d_une_connexion(owner, app_env):
    other = login(app_env)
    s = owner.get("/api/sessions").json()["sessions"]
    assert len(s) == 2 and sum(1 for x in s if x["current"]) == 1 and all(x["user"] == "patron" for x in s)
    target = next(x for x in s if not x["current"])
    assert owner.post("/api/sessions/revoke", json={"id": target["id"]}).json()["revoked"] == 1
    assert other.get("/api/me").status_code == 401                                          # la connexion fermée est coupée immédiatement
    assert owner.get("/api/me").status_code == 200 and len(owner.get("/api/sessions").json()["sessions"]) == 1
    assert any(e["action"] == "session.revoked" for e in owner.get("/api/audit?limit=20").json()["entries"])


def test_fermer_toutes_les_autres(owner, app_env):
    others = [login(app_env) for _ in range(3)]
    assert owner.post("/api/sessions/revoke", json={"others": True}).json()["revoked"] == 3
    assert all(o.get("/api/me").status_code == 401 for o in others) and owner.get("/api/me").status_code == 200
    assert owner.post("/api/sessions/revoke", json={}).status_code == 422


def test_droits_sur_les_connexions_des_autres(owner, app_env):
    marie, _ = make_user(owner, app_env, "marie", "sales")
    marie2 = login(app_env, "marie")
    assert marie.get("/api/sessions?all=true").status_code == 403
    mine = marie.get("/api/sessions").json()["sessions"]
    assert all(x["user"] == "marie" for x in mine)
    everyone = owner.get("/api/sessions?all=true").json()["sessions"]
    theirs = next(x for x in everyone if x["user"] == "marie" and x["current"] is False)
    patron = next(x for x in everyone if x["current"])
    assert marie.post("/api/sessions/revoke", json={"id": patron["id"]}).status_code == 404         # on ne ferme pas la connexion de quelqu'un d'autre
    assert owner.post("/api/sessions/revoke", json={"id": theirs["id"]}).json()["revoked"] == 1       # le propriétaire peut fermer celle d'un membre
    assert owner.post("/api/sessions/revoke", json={"id": 99999}).status_code == 404
    assert owner.get("/api/audit/verify").json()["ok"] is True
