import gc
import os
import gc
import sqlite3

import gc
import pytest
from conftest import H, PW

from gestion import backup, chiffrement, config

pytestmark = pytest.mark.skipif(not chiffrement.disponible(), reason="sqlcipher3 non installé")

PHRASE = "phrase de passe de test"


def _client(monkeypatch, tmp_path, chiffre: bool, phrase: str | None = PHRASE):
    monkeypatch.setenv("GESTION_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GESTION_NO_AUTOBACKUP", "1")
    monkeypatch.setenv("GESTION_CHIFFREMENT", "1" if chiffre else "0")
    if phrase: monkeypatch.setenv("GESTION_CLE", phrase)
    else: monkeypatch.delenv("GESTION_CLE", raising=False)
    from fastapi.testclient import TestClient

    from gestion import api
    return TestClient(api.app, headers=H)


def _octets(tmp_path) -> bytes:
    return b"".join(p.read_bytes() for p in tmp_path.rglob("*") if p.is_file() and p.name.startswith("gestion"))


def _setup(c):
    r = c.post("/api/setup", json={"username": "patron", "full_name": "Mamadou Diallo Secret", "password": PW})
    assert r.status_code == 200, r.text


def test_base_neuve_chiffree_et_illisible_sans_la_cle(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path, True) as c:
        _setup(c)
        assert c.get("/api/me").json()["user"]["username"] == "patron"            # l'application fonctionne normalement
        f = config.db_path()
        assert not chiffrement.est_en_clair(f) and f.read_bytes()[:16] != chiffrement.MAGIC
        with pytest.raises(sqlite3.DatabaseError): sqlite3.connect(f).execute("SELECT * FROM users").fetchall()
    tout = _octets(tmp_path)
    assert b"Mamadou" not in tout and b"patron" not in tout and b"SQLite format" not in tout      # ni dans la base ni dans son journal WAL
    assert json_mode(tmp_path) == "phrase"


def json_mode(tmp_path):
    import json
    return json.loads((tmp_path / "chiffrement.json").read_text(encoding="utf-8"))["mode"]


def test_mauvaise_phrase_refusee_et_donnees_intactes(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path, True) as c: _setup(c)
    chiffrement._cle.cache_clear()
    monkeypatch.setenv("GESTION_CLE", "une autre phrase")
    with pytest.raises(Exception):
        with _client(monkeypatch, tmp_path, True, "une autre phrase") as c: c.get("/api/me")
    chiffrement._cle.cache_clear()
    with _client(monkeypatch, tmp_path, True) as c:                                # la bonne phrase rouvre tout
        assert c.post("/api/login", json={"username": "patron", "password": PW}).status_code == 200


def test_phrase_manquante_refus_clair(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path, True) as c: _setup(c)
    chiffrement._cle.cache_clear()
    monkeypatch.delenv("GESTION_CLE")
    with pytest.raises(chiffrement.CipherError, match="GESTION_CLE"): chiffrement.verifier_demarrage()


def test_migration_automatique_garde_l_original_et_les_donnees(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path, False, None) as c:                          # une vraie base en clair, avec des données et une sauvegarde
        _setup(c)
        assert c.post("/api/backup/run").status_code == 200
    assert chiffrement.est_en_clair(config.db_path())
    p = sqlite3.connect(config.db_path()); avant = p.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]; p.close()
    gc.collect()                                                                    # Windows : une connexion du premier serveur pas encore libérée verrouillerait le fichier à déplacer
    with _client(monkeypatch, tmp_path, True) as c:                                 # le démarrage migre
        assert not chiffrement.est_en_clair(config.db_path())
        assert c.post("/api/login", json={"username": "patron", "password": PW}).status_code == 200
        assert c.get("/api/audit/verify").json()["ok"] is True                      # la chaîne d'empreintes du journal est intacte
    k = chiffrement.cle_hex(); n = chiffrement.sqlite().connect(config.db_path()); chiffrement.appliquer_cle(n, k)
    assert n.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] >= avant
    n.close()
    clair = list((tmp_path / chiffrement.CLEAR_DIR).glob("gestion*"))
    assert any(p.name.startswith("gestion.sqlite3.") for p in clair) and len(clair) >= 2   # l'original ET la sauvegarde en clair sont conservés, jamais supprimés
    assert all(chiffrement.est_en_clair(p) for p in clair if not p.name.endswith(("-wal", "-shm")) and p.stat().st_size > 0)
    assert not chiffrement.est_en_clair(next((tmp_path / "sauvegardes").glob("gestion_*.sqlite3")))      # la sauvegarde restante est chiffrée
    assert chiffrement.statut()["fichier_chiffre"] is True


def test_sauvegarde_chiffree(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path, True) as c:
        _setup(c)
        assert c.post("/api/backup/run").status_code == 200
        dl = c.get("/api/backup/download")
        assert dl.status_code == 200 and dl.content[:16] != chiffrement.MAGIC and b"Mamadou" not in dl.content
    f = next((tmp_path / "sauvegardes").glob("gestion_*.sqlite3"))
    assert not chiffrement.est_en_clair(f)
    r = chiffrement.ouvrir(f)
    assert r.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    r.close()


def test_cle_de_secours_demande_confirmation(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("GESTION_DATA_DIR", str(tmp_path)); monkeypatch.setenv("GESTION_CHIFFREMENT", "1"); monkeypatch.setenv("GESTION_CLE", PHRASE)
    assert chiffrement.main(["afficher-cle"]) == 2 and PHRASE not in capsys.readouterr().out
    assert chiffrement.main(["afficher-cle", "--oui"]) == 0
    cle = capsys.readouterr().out.strip()
    assert len(cle) == 64 and int(cle, 16) >= 0


@pytest.mark.skipif(os.name != "nt", reason="DPAPI : Windows seulement")
def test_cle_protegee_par_windows(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path, True, None) as c: _setup(c)
    assert json_mode(tmp_path) == "dpapi"
    blob = (tmp_path / "cle.dpapi").read_bytes()
    assert chiffrement.cle_hex().encode() not in blob                              # la clé n'est pas écrite en clair sur le disque
    chiffrement._cle.cache_clear()
    with _client(monkeypatch, tmp_path, True, None) as c:                           # redémarrage : Windows redonne la clé
        assert c.post("/api/login", json={"username": "patron", "password": PW}).status_code == 200


def test_desactivation_et_demo(monkeypatch, tmp_path):
    monkeypatch.setenv("GESTION_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GESTION_CHIFFREMENT", "0")
    assert chiffrement.actif() is False and chiffrement.sqlite() is sqlite3
    monkeypatch.setenv("GESTION_CHIFFREMENT", "1")
    assert chiffrement.actif() is True
    (tmp_path / ".demo").write_text("x"); monkeypatch.setenv("GESTION_DEMO", "1")
    assert chiffrement.actif() is False                                             # la démonstration n'est jamais chiffrée
    assert backup.backup_dir().exists()
