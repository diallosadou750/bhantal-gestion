"""Migration SQLite -> PostgreSQL : exige un serveur PostgreSQL de test (GESTION_TEST_PG)."""
import os
import uuid

import pytest
from conftest import PW, H, op, push

PG = os.environ.get("GESTION_TEST_PG")
pytestmark = pytest.mark.skipif(not PG, reason="GESTION_TEST_PG non défini")


def test_migration_complete_et_verifiee(tmp_path, monkeypatch):
    import psycopg

    from gestion import audit, db, dbpg, vers_postgres
    from fastapi.testclient import TestClient
    monkeypatch.setenv("GESTION_DATA_DIR", str(tmp_path)); monkeypatch.setenv("GESTION_NO_AUTOBACKUP", "1"); monkeypatch.setenv("GESTION_CHIFFREMENT", "0")
    monkeypatch.delenv("GESTION_DB_URL", raising=False); monkeypatch.delenv("GESTION_DB_SCHEMA", raising=False)
    from gestion import api
    with TestClient(api.app, headers=H) as c:                                  # 1. une vraie base SQLite avec des données de toutes sortes
        assert c.post("/api/setup", json={"username": "patron", "full_name": "Patron", "password": PW}).status_code == 200
        cl = push(c, op("client.create", {"id": str(uuid.uuid4()), "name": "Camara", "credit_limit": 9_000_000_000}))[0]["client"]
        pr = push(c, op("product.create", {"id": str(uuid.uuid4()), "name": "Ciment", "price": 4_000_000_000}))[0]["product"]
        s = push(c, op("sale.create", {"client_id": cl["id"], "kind": "cash", "pay_method": "Espèces", "lines": [{"product_id": pr["id"], "qty": 2}]}))[0]["sale"]
        assert s["total"] == 8_000_000_000                                      # au-delà de 2^31 : exige des BIGINT
        push(c, op("expense.create", {"category": "transport", "description": "é à ç ½", "amount": 25_000, "method": "Espèces"}))
        c.post("/api/logo", json={"image": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="})
        avant = c.get("/api/sync/pull?since=0").json()
    schema = "m_" + uuid.uuid4().hex[:8]
    with psycopg.connect(PG, autocommit=True) as p: p.execute(f'CREATE SCHEMA "{schema}"')
    monkeypatch.setenv("GESTION_DB_URL", PG); monkeypatch.setenv("GESTION_DB_SCHEMA", schema)
    try:
        r = vers_postgres.migrer()                                              # 2. copie
        assert r["lignes"] > 50 and r["journal"]["ok"] is True
        with pytest.raises(SystemExit): vers_postgres.migrer()                  # 3. refuse d'écraser une base déjà remplie
        with TestClient(api.app, headers=H) as c:                               # 4. l'application tourne sur PostgreSQL avec les mêmes données
            assert c.post("/api/login", json={"username": "patron", "password": PW}).status_code == 200
            apres = c.get("/api/sync/pull?since=0").json()
            assert [x["name"] for x in apres["clients"]] == ["Camara"] and apres["sales"][0]["total"] == 8_000_000_000
            assert [x["id"] for x in apres["products"]] == [x["id"] for x in avant["products"]]
            assert c.get("/api/audit/verify").json()["ok"] is True
            n = push(c, op("client.create", {"id": str(uuid.uuid4()), "name": "Nouveau après migration"}))[0]       # les compteurs reprennent sans collision
            assert n["status"] == "ok" and n["client"]["code"] == "C002"
            assert c.post("/api/logo", json={"image": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="}).status_code == 200
            with db.read() as d: assert audit.verify(d)["ok"] is True
    finally:
        dbpg.reset_pool()
        with psycopg.connect(PG, autocommit=True) as p: p.execute(f'DROP SCHEMA "{schema}" CASCADE')
