import sqlite3
import uuid

from conftest import PW, make_user, op, push


def create(c, name="Boutique Diallo", **extra):
    cid = str(uuid.uuid4())
    res = push(c, op("client.create", {"id": cid, "name": name, **extra}))[0]
    return cid, res


def test_creation_et_champs_normalises(owner):
    cid, res = create(owner, phone="224 620-00-01 02", credit_limit="5000000")
    assert res["status"] == "ok"
    cl = res["client"]
    assert cl["code"] == "C001" and cl["phone"] == "224620000102" and cl["credit_limit"] == 5000000 and cl["version"] == 1
    assert push(owner, op("client.create", {"name": ""}))[0]["status"] == "invalid"
    assert create(owner, "Deuxième")[1]["client"]["code"] == "C002"


def test_rejeu_idempotent(owner):
    o = op("client.create", {"id": str(uuid.uuid4()), "name": "Une seule fois"})
    a, b = push(owner, o)[0], push(owner, o)[0]
    assert a["status"] == "ok" and b.get("replayed") is True
    assert len(owner.get("/api/clients").json()["clients"]) == 1


def test_conflit_de_version_sans_ecrasement(owner, app_env):
    cid, res = create(owner)
    v = res["client"]["version"]
    compta, _ = make_user(owner, app_env, "compta", "accountant")
    assert push(owner, op("client.update", {"id": cid, "base_version": v, "changes": {"zone": "Kaloum"}}))[0]["status"] == "ok"
    r = push(compta, op("client.update", {"id": cid, "base_version": v, "changes": {"zone": "Matam"}}))[0]
    assert r["status"] == "conflict" and r["server"]["zone"] == "Kaloum" and r["server"]["version"] == v + 1
    assert owner.get("/api/clients").json()["clients"][0]["zone"] == "Kaloum"
    assert any(e["action"] == "client.conflict" and e["user"] == "compta" for e in owner.get("/api/audit").json()["entries"])


def test_commercial_ne_voit_que_ses_clients(owner, app_env):
    marie, mu = make_user(owner, app_env, "marie", "sales")
    paul, pu = make_user(owner, app_env, "paul", "sales")
    c_marie, r1 = create(marie, "Client de Marie")
    c_paul, _ = create(paul, "Client de Paul")
    assert r1["client"]["owner_user_id"] == mu["id"]                       # imposé par le serveur
    assert [c["name"] for c in marie.get("/api/clients").json()["clients"]] == ["Client de Marie"]
    assert len(owner.get("/api/clients").json()["clients"]) == 2
    r = push(marie, op("client.update", {"id": c_paul, "base_version": 1, "changes": {"name": "Piraté"}}))[0]
    assert r["status"] == "invalid" or r["status"] == "denied"             # fiche invisible ou refusée : jamais modifiée
    assert owner.get("/api/clients").json()["clients"][1]["name"] == "Client de Paul"
    assert push(marie, op("client.delete", {"id": c_marie, "base_version": 1}))[0]["status"] == "denied"


def test_commercial_ne_peut_pas_se_reattribuer_un_client(owner, app_env):
    marie, _ = make_user(owner, app_env, "marie", "sales")
    paul, pu = make_user(owner, app_env, "paul", "sales")
    cid, _ = create(marie)
    push(marie, op("client.update", {"id": cid, "base_version": 1, "changes": {"owner_user_id": pu["id"], "zone": "Ratoma"}}))
    cl = marie.get("/api/clients").json()["clients"][0]
    assert cl["zone"] == "Ratoma" and cl["owner_user_id"] != pu["id"]


def test_caissier_lecture_sans_champs_sensibles(owner, app_env):
    create(owner, "Client", credit_limit=1000000, note="mauvais payeur")
    cash, _ = make_user(owner, app_env, "caisse", "cashier")
    cl = cash.get("/api/clients").json()["clients"][0]
    assert "note" not in cl and "credit_limit" not in cl and cl["name"] == "Client"
    assert push(cash, op("client.create", {"name": "Non"}))[0]["status"] == "denied"


def test_suppression_logique_et_synchronisation(owner):
    cid, res = create(owner)
    cursor = owner.get("/api/clients").json()["cursor"]
    assert push(owner, op("client.delete", {"id": cid, "base_version": res["client"]["version"]}))[0]["status"] == "ok"
    delta = owner.get(f"/api/clients?since={cursor}").json()["clients"]
    assert len(delta) == 1 and delta[0]["deleted"] is True
    from gestion import db
    with db.read() as c:
        assert c.execute("SELECT COUNT(*) FROM clients").fetchone()[0] == 1       # la fiche est conservée


def test_journal_complet_et_chaine_valide(owner):
    cid, res = create(owner, "Boutique")
    push(owner, op("client.update", {"id": cid, "base_version": 1, "changes": {"credit_limit": 200}}))
    entries = owner.get("/api/audit?entity=client").json()["entries"]
    upd = [e for e in entries if e["action"] == "client.update"][0]
    assert upd["user"] == "patron" and upd["role"] == "owner" and upd["before"] == {"credit_limit": 0} and upd["after"] == {"credit_limit": 200}
    assert upd["device"].startswith("test-device") and upd["ts"].endswith("Z") and upd["entity_id"] == cid
    v = owner.get("/api/audit/verify").json()
    assert v["ok"] is True and v["entries"] >= 4


def test_journal_infalsifiable(owner):
    create(owner, "A"); create(owner, "B")
    from gestion import config, db
    con = db.connect()                                             # SQLite ou PostgreSQL selon l'environnement de test
    for sql in ("UPDATE audit_log SET username='fraude' WHERE id=2", "DELETE FROM audit_log WHERE id=2"):
        try:
            con.execute(sql); raise AssertionError("modification acceptée")
        except db.errors() as e:
            assert "ajout seul" in str(e)
    # Un attaquant qui a accès à la base retire les déclencheurs puis triche : la chaîne le détecte.
    con.execute("DROP TRIGGER audit_no_change ON audit_log" if config.db_url() else "DROP TRIGGER audit_no_update"); con.execute("UPDATE audit_log SET username='fraude' WHERE id=3"); con.commit()
    v = owner.get("/api/audit/verify").json()
    assert v["ok"] is False and v["broken_at"] == 3
    con.close()


def test_export_csv_contre_injection_de_formule(owner):
    create(owner, "=1+1")
    r = owner.get("/api/audit/export.csv")
    assert r.status_code == 200 and "text/csv" in r.headers["content-type"]
    for line in r.text.splitlines()[1:]:
        for cell in line.split(";"):
            assert not cell.startswith(("=", "+", "@")), cell
    assert any(e["action"] == "audit.exported" for e in owner.get("/api/audit").json()["entries"])


def test_interface_servie_avec_en_tetes_de_securite(client):
    r = client.get("/")
    assert r.status_code == 200 and "default-src 'self'" in r.headers["content-security-policy"] and r.headers["x-frame-options"] == "DENY"
