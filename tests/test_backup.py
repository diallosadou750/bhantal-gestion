import io
import sqlite3
import uuid
import zipfile

from conftest import make_user, op, push


def donnees(owner):
    cid = push(owner, op("client.create", {"id": str(uuid.uuid4()), "name": "=CMD|x", "credit_limit": 1000}))[0]["client"]["id"]
    return cid


def restaurer_et_compter(tmp_path, contenu: bytes, table: str, attendu: int):
    """Restaure une sauvegarde PostgreSQL dans une base jetable et compte les lignes d'une table : prouve que la sauvegarde est réellement utilisable."""
    import os
    import subprocess
    import uuid as _u

    import psycopg
    from gestion import config
    f = tmp_path / "copie.dump"; f.write_bytes(contenu)
    base = "restore_" + _u.uuid4().hex[:8]
    admin = os.environ["GESTION_DB_URL"]
    with psycopg.connect(admin, autocommit=True) as c: c.execute(f'CREATE DATABASE "{base}"')
    try:
        cible = admin.rsplit("/", 1)[0] + "/" + base
        exe = os.path.join(os.path.dirname(os.environ["GESTION_PG_DUMP"]), "pg_restore.exe")
        r = subprocess.run([exe, "--no-owner", "--dbname", cible, str(f)], capture_output=True)
        assert r.returncode == 0, r.stderr.decode("utf-8", "replace")[-300:]
        with psycopg.connect(cible) as c:
            assert c.execute(f'SELECT COUNT(*) FROM "{config.db_schema()}"."{table}"').fetchone()[0] == attendu
    finally:
        with psycopg.connect(admin, autocommit=True) as c: c.execute(f'DROP DATABASE "{base}" WITH (FORCE)')


def test_telechargement_de_sauvegarde_coherente(owner, tmp_path):
    donnees(owner)
    r = owner.get("/api/backup/download")
    from gestion import config
    if config.db_url():                                                    # PostgreSQL : sauvegarde pg_dump, relue avec pg_restore dans une base neuve
        assert r.status_code == 200 and r.content[:5] == b"PGDMP"
        restaurer_et_compter(tmp_path, r.content, "clients", 1)
    else:
        assert r.status_code == 200 and r.content[:15] == b"SQLite format 3"
        f = tmp_path / "copie.sqlite3"; f.write_bytes(r.content)
        c = sqlite3.connect(f)
        assert c.execute("SELECT COUNT(*) FROM clients").fetchone()[0] == 1 and c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert any(e["action"] == "backup.downloaded" for e in owner.get("/api/audit?limit=20").json()["entries"])


def test_sauvegarde_serveur_liste_et_conservation(owner, monkeypatch):
    from gestion import backup
    r = owner.post("/api/backup/run")
    assert r.status_code == 200
    d = backup.backup_dir()
    for i in range(40):                                         # de fausses anciennes sauvegardes : seules les 30 dernières sont gardées
        (d / f"gestion_2020010{i % 10}_0000{i:02d}.dump").write_bytes(b"x")
    backup.run_backup()
    names = [b["name"] for b in owner.get("/api/backup/list").json()["backups"]]
    assert len(names) == backup.KEEP and names[0] >= "gestion_2026"


def test_export_complet_sans_secrets(owner, app_env):
    donnees(owner)
    r = owner.get("/api/export/all.zip")
    assert r.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = z.namelist()
    assert "clients.csv" in names and "audit_log.csv" in names and not any("user" in n or "session" in n for n in names)
    txt = z.read("clients.csv").decode("utf-8-sig")
    assert "'=CMD|x" in txt and ";=CMD" not in txt                  # pas d'injection de formule à l'ouverture dans Excel
    assert "scrypt" not in " ".join(z.read(n).decode("utf-8-sig", "ignore") for n in names)


def test_droits(owner, app_env):
    comp, _ = make_user(owner, app_env, "compta", "accountant")
    sales, _ = make_user(owner, app_env, "marie", "sales")
    aud, _ = make_user(owner, app_env, "expert", "auditor")
    for c in (comp, sales, aud):
        assert c.get("/api/backup/download").status_code == 403 and c.post("/api/backup/run").status_code == 403 and c.get("/api/backup/list").status_code == 403
    assert comp.get("/api/export/all.zip").status_code == 200 and aud.get("/api/export/all.zip").status_code == 200 and sales.get("/api/export/all.zip").status_code == 403
