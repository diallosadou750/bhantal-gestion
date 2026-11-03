import json
import sqlite3

import pytest
from conftest import PW, new_session
from test_2fa import activer, code_now


def test_aller_retour_et_alteration(app_env):
    from gestion import secretbox
    s = secretbox.sceller("JBSWY3DPEHPK3PXP")
    assert s.startswith("gl1:") and "JBSWY3DPEHPK3PXP" not in s and secretbox.ouvrir(s) == "JBSWY3DPEHPK3PXP"
    assert secretbox.sceller(s) == s and secretbox.sceller("") == "" and secretbox.sceller(None) is None
    assert secretbox.sceller("JBSWY3DPEHPK3PXP") != s                              # nonce aléatoire : deux chiffrements diffèrent
    assert secretbox.ouvrir("ancien-clair") == "ancien-clair" and secretbox.ouvrir(None) == ""
    bad = s[:-3] + ("AAA" if not s.endswith("AAA") else "BBB")
    assert secretbox.ouvrir(bad) == "" and secretbox.ouvrir("gl1:%%%") == ""        # altéré ou illisible : vide, jamais d'exception


def test_cle_fournie_par_l_environnement(app_env, monkeypatch):
    from gestion import secretbox
    monkeypatch.setenv("GESTION_SECRET_KEY", "ab" * 32)
    s = secretbox.sceller("secret")
    monkeypatch.setenv("GESTION_SECRET_KEY", "cd" * 32)
    assert secretbox.ouvrir(s) == ""                                                 # autre clé : illisible
    monkeypatch.setenv("GESTION_SECRET_KEY", "ab" * 32)
    assert secretbox.ouvrir(s) == "secret"
    monkeypatch.setenv("GESTION_SECRET_KEY", "xyz")
    with pytest.raises(RuntimeError): secretbox.sceller("a")


def brut(app_env, sql, *a):
    from gestion import config
    c = sqlite3.connect(config.db_path()); c.row_factory = sqlite3.Row
    try: return c.execute(sql, a).fetchall()
    finally: c.close()


def test_cle_2fa_chiffree_dans_la_base_et_connexion_ok(owner, app_env):
    secret = activer(owner)
    stocke = brut(app_env, "SELECT totp_secret FROM users WHERE username='patron'")[0][0]
    assert stocke.startswith("gl1:") and secret not in stocke
    c = new_session(app_env)
    r = c.post("/api/login", json={"username": "patron", "password": PW, "code": code_now(secret, 1)})
    assert r.status_code == 200, r.text


def test_mot_de_passe_smtp_chiffre(owner, app_env):
    r = owner.post("/api/notifications", json={"host": "smtp.exemple.org", "port": 587, "user": "a@b.org", "password": "MotDePasseAppli123"})
    assert r.status_code == 200, r.text
    v = brut(app_env, "SELECT value FROM meta WHERE key='notif'")
    txt = v[0][0] if v else ""
    assert txt and "MotDePasseAppli123" not in txt and "gl1:" in txt
    pub = owner.get("/api/notifications").json()
    assert "MotDePasseAppli123" not in json.dumps(pub)


def test_migration_des_secrets_en_clair(owner, app_env):
    from gestion import config, db
    secret = activer(owner)
    with sqlite3.connect(config.db_path()) as c:
        c.execute("UPDATE users SET totp_secret=? WHERE username='patron'", (secret,))           # base d'avant cette version
    db.init_db()
    assert brut(app_env, "SELECT totp_secret FROM users WHERE username='patron'")[0][0].startswith("gl1:")
    assert new_session(app_env).post("/api/login", json={"username": "patron", "password": PW, "code": code_now(secret, 1)}).status_code == 200
