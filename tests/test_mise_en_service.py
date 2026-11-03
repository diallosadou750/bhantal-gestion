from conftest import make_user
from test_2fa import activer


def etat(c, host="127.0.0.1:8090"):
    r = c.get("/api/readiness", headers={"host": host}); assert r.status_code == 200, r.text
    d = r.json(); return d, {i["key"]: i for i in d["items"]}


def test_installation_neuve(owner):
    d, it = etat(owner)
    assert d["ready"] is False and d["counts"]["todo"] >= 1                             # aucune sauvegarde, pas de nom d'entreprise
    assert it["company"]["level"] == "todo" and it["backup"]["level"] == "todo" and it["owner_2fa"]["level"] == "warn" and it["team"]["level"] == "warn"
    assert it["extra_copy"]["level"] == "warn" and it["audit"]["level"] == "warn" and it["network"]["level"] == "ok" and it["notify"]["level"] == "warn"
    assert d["items"][0]["level"] == "todo" and all(i["fix"] for i in d["items"] if i["level"] != "ok")        # les points à faire passent en premier, avec la marche à suivre
    assert d["counts"]["todo"] + d["counts"]["warn"] + d["counts"]["ok"] == d["total"] == len(d["items"])


def test_chaque_point_passe_au_vert_quand_c_est_fait(owner, app_env, tmp_path_factory):
    from conftest import op, push
    push(owner, op("settings.update", {"company_name": "Exemple SARL", "address": "Conakry", "phone": "+224620000000", "tax_id": "NIF123", "tax_rate": 18}))
    assert owner.post("/api/backup/run").status_code == 200
    activer(owner); make_user(owner, app_env, "marie", "sales")
    ext = tmp_path_factory.mktemp("usb")
    assert owner.post("/api/backup/extra", json={"dir": str(ext)}).status_code == 200
    d, it = etat(owner)
    for k in ("company", "backup", "owner_2fa", "team", "extra_copy", "tax"):
        assert it[k]["level"] == "ok", (k, it[k])
    assert d["ready"] is True


def test_reserve_au_proprietaire_et_reseau_http(owner, app_env):
    compta, _ = make_user(owner, app_env, "compta", "accountant")
    assert compta.get("/api/readiness").status_code == 403
    _, it = etat(owner, "192.168.1.20:8090")
    assert it["network"]["level"] == "warn" and "HTTPS" in it["network"]["fix"]


def test_alteration_du_journal_bloque(owner):
    import sqlite3
    from gestion import config
    c = sqlite3.connect(config.db_path()); c.execute("DROP TRIGGER audit_no_update"); c.execute("UPDATE audit_log SET username='fraude' WHERE id=1"); c.commit(); c.close()
    d, it = etat(owner)
    assert it["audit"]["level"] == "todo" and "ALTÉRATION" in it["audit"]["detail"] and d["ready"] is False
