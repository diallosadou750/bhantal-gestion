import time

from conftest import H, PW, make_user, new_session


def code_now(secret, offset=0):
    from gestion import security
    return security.totp_code(secret, int(time.time() // 30) + offset)


def test_vecteur_rfc6238():
    from gestion import security
    s = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"                       # « 12345678901234567890 » en base32 : vecteur de test de la RFC 6238
    assert security.totp_code(s, 59 // 30) == "287082" and security.totp_code(s, 1111111109 // 30) == "081804"
    assert security.verify_totp(s, "287082", at=59) == 1 and security.verify_totp(s, "287082", last_step=1, at=59) is None      # un code ne sert qu'une fois
    assert security.verify_totp(s, "12345", at=59) is None and security.verify_totp(s, "abcdef", at=59) is None


def activer(c):
    r = c.post("/api/2fa/begin"); assert r.status_code == 200, r.text
    d = r.json(); assert d["uri"].startswith("otpauth://totp/") and d["secret"] in d["uri"]
    assert c.post("/api/2fa/enable", json={"code": "000000"}).status_code == 422
    assert c.post("/api/2fa/enable", json={"code": code_now(d["secret"])}).status_code == 200
    return d["secret"]


def test_activation_et_connexion_avec_code(owner, app_env):
    secret = activer(owner)
    assert owner.get("/api/me").json()["user"]["totp_on"] is True
    c = new_session(app_env)
    r = c.post("/api/login", json={"username": "patron", "password": PW})
    assert r.status_code == 401 and r.json()["code"] == "code_requis"
    assert c.get("/api/me").status_code == 401                                              # pas de session sans code
    r = c.post("/api/login", json={"username": "patron", "password": PW, "code": "123456"})
    assert r.status_code == 401 and r.json()["code"] == "code_invalide"
    good = code_now(secret, 1)                                                              # le pas suivant est accepté (tolérance d'horloge) ...
    assert c.post("/api/login", json={"username": "patron", "password": PW, "code": good}).status_code == 200
    c2 = new_session(app_env)
    assert c2.post("/api/login", json={"username": "patron", "password": PW, "code": good}).status_code == 401      # ... mais un code déjà utilisé est refusé
    actions = [e["action"] for e in c.get("/api/audit?limit=50").json()["entries"]]
    assert "2fa.enabled" in actions and "login.2fa_required" in actions


def test_verrouillage_apres_codes_faux(owner, app_env):
    activer(owner)
    c = new_session(app_env)
    for _ in range(10):
        c.post("/api/login", json={"username": "patron", "password": PW, "code": "000000"})
    assert c.post("/api/login", json={"username": "patron", "password": PW, "code": "000000"}).status_code == 423


def test_desactivation_et_reinitialisation(owner, app_env):
    secret = activer(owner)
    assert owner.post("/api/2fa/disable", json={"password": "faux", "code": code_now(secret, 1)}).status_code == 403
    assert owner.post("/api/2fa/disable", json={"password": PW, "code": code_now(secret, 1)}).status_code == 200
    assert new_session(app_env).post("/api/login", json={"username": "patron", "password": PW}).status_code == 200
    marie, u = make_user(owner, app_env, "marie", "sales")
    s2 = activer(marie)
    assert marie.post("/api/2fa/begin").status_code == 409                                  # déjà active
    assert marie.post(f"/api/users/{u['id']}/2fa-reset").status_code == 403                 # seul le propriétaire réinitialise
    assert owner.post(f"/api/users/{u['id']}/2fa-reset").status_code == 200
    assert marie.get("/api/me").status_code == 401                                          # ses sessions sont fermées
    assert new_session(app_env).post("/api/login", json={"username": "marie", "password": PW}).status_code == 200
