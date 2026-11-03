import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

H = {"X-Requested-With": "gestion", "X-Device-Id": "test-device"}
PW = "Motdepasse2026ok"


PG = os.environ.get("GESTION_TEST_PG")                     # URL d'un serveur PostgreSQL de test : toute la suite tourne alors sur PostgreSQL (un schéma neuf par test)
sqlite_seulement = pytest.mark.skipif(bool(PG), reason="lit ou écrit le fichier SQLite directement")


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    monkeypatch.setenv("GESTION_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GESTION_NO_AUTOBACKUP", "1")
    monkeypatch.setenv("GESTION_CHIFFREMENT", "0")        # les tests lisent la base directement ; le chiffrement a ses propres tests (test_chiffrement.py)
    schema = None
    if PG:
        import psycopg
        schema = "t_" + uuid.uuid4().hex[:10]
        with psycopg.connect(PG, autocommit=True) as c: c.execute(f'CREATE SCHEMA "{schema}"')
        monkeypatch.setenv("GESTION_DB_URL", PG); monkeypatch.setenv("GESTION_DB_SCHEMA", schema)
        import pgserver
        monkeypatch.setenv("GESTION_PG_DUMP", str(Path(pgserver.__file__).parent / "pginstall" / "bin" / "pg_dump.exe"))
    from gestion import api
    yield api.app
    if PG:
        from gestion import dbpg
        dbpg.reset_pool()
        import psycopg
        with psycopg.connect(PG, autocommit=True) as c: c.execute(f'DROP SCHEMA "{schema}" CASCADE')


@pytest.fixture()
def client(app_env):
    from fastapi.testclient import TestClient
    with TestClient(app_env, headers=H) as c:
        yield c


@pytest.fixture()
def owner(client):
    r = client.post("/api/setup", json={"username": "patron", "full_name": "Le Patron", "password": PW})
    assert r.status_code == 200, r.text
    return client


def new_session(app_env):
    from fastapi.testclient import TestClient
    return TestClient(app_env, headers=H)


def make_user(owner_client, app_env, username, role, full_name=None):
    r = owner_client.post("/api/users", json={"username": username, "full_name": full_name or username.title(), "role": role, "temp_password": "Temporaire2026ab"})
    assert r.status_code == 200, r.text
    c = new_session(app_env)
    assert c.post("/api/login", json={"username": username, "password": "Temporaire2026ab"}).status_code == 200
    assert c.post("/api/password", json={"old": "Temporaire2026ab", "new": PW}).status_code == 200
    return c, r.json()["user"]


def op(kind, data):
    return {"op_id": str(uuid.uuid4()), "type": kind, "data": data}


def push(c, *ops):
    r = c.post("/api/sync/push", json={"ops": list(ops)})
    assert r.status_code == 200, r.text
    return r.json()["results"]
