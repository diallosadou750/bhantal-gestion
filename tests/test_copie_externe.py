import hashlib

import pytest
from conftest import make_user

from gestion import backup, config


@pytest.fixture()
def usb(tmp_path_factory):
    """Un dossier HORS du dossier des données (tmp_path est le dossier des données dans ces tests)."""
    return tmp_path_factory.mktemp("cle_usb")


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_validation_du_dossier(owner, tmp_path, usb):
    for mauvais, mot in (("", "Indiquez"), ("sauvegardes", "chemin complet"), (str(tmp_path / "absent"), "n'existe pas"), (str(config.data_dir()), "données elles-mêmes"),
                         (str(backup.backup_dir()), "données elles-mêmes")):
        r = owner.post("/api/backup/extra", json={"dir": mauvais})
        assert r.status_code == 422 and mot in r.json()["error"], (mauvais, r.text)
    assert backup.extra_dir() is None                                     # rien n'a été enregistré
    assert backup.check_extra_dir(f'  "{usb}"  ') == usb                  # espaces et guillemets autour du chemin tolérés
    assert not list(usb.iterdir())                                        # l'essai d'écriture ne laisse aucune trace


def test_activation_copie_verifiee_et_journal(owner, tmp_path, usb):
    r = owner.post("/api/backup/extra", json={"dir": str(usb)})
    assert r.status_code == 200 and r.json()["copy"]["ok"] is True
    copies = list(usb.glob("gestion_*"))
    assert len(copies) == 1 and copies[0].stat().st_size > 0
    local = backup.backup_dir() / copies[0].name
    assert local.exists() and sha(local) == sha(copies[0])               # copie identique à la sauvegarde locale
    lst = owner.get("/api/backup/list").json()
    assert lst["extra"]["dir"] == str(usb) and lst["extra"]["status"]["ok"] is True
    acts = [e["action"] for e in owner.get("/api/audit?limit=20").json()["entries"]]
    assert "backup.extra_set" in acts and "backup.created" in acts
    owner.post("/api/backup/run")                                         # chaque sauvegarde suivante est aussi copiée
    assert len(list(usb.glob("gestion_*"))) >= 1 and owner.get("/api/backup/list").json()["extra"]["status"]["ok"] is True


def test_rotation_des_30_dernieres(owner, tmp_path, usb):
    for i in range(35): (usb / f"gestion_2020010{i % 10}_0000{i:02d}.sqlite3").write_bytes(b"x")
    assert owner.post("/api/backup/extra", json={"dir": str(usb)}).status_code == 200
    noms = sorted(p.name for p in usb.glob("gestion_*"))
    assert len(noms) == backup.KEEP and noms[-1] >= "gestion_2026"


def test_echec_de_la_copie_ne_bloque_pas_la_sauvegarde(owner, tmp_path, usb):
    assert owner.post("/api/backup/extra", json={"dir": str(usb)}).status_code == 200
    usb.rmdir() if not list(usb.iterdir()) else [p.unlink() for p in usb.iterdir()] and usb.rmdir()      # la clé USB est débranchée
    r = owner.post("/api/backup/run")
    assert r.status_code == 200                                           # la sauvegarde locale a réussi malgré tout
    st = owner.get("/api/backup/list").json()["extra"]["status"]
    assert st["ok"] is False and "n'existe pas" in st["message"]
    assert (backup.backup_dir() / r.json()["name"]).exists()
    usb.mkdir()                                                           # clé rebranchée : la copie suivante réussit
    owner.post("/api/backup/run")
    assert owner.get("/api/backup/list").json()["extra"]["status"]["ok"] is True


def test_desactivation(owner, tmp_path, usb):
    owner.post("/api/backup/extra", json={"dir": str(usb)})
    assert owner.delete("/api/backup/extra").status_code == 200
    lst = owner.get("/api/backup/list").json()["extra"]
    assert lst["dir"] == "" and lst["status"] is None
    n = len(list(usb.glob("gestion_*")))
    owner.post("/api/backup/run")
    assert len(list(usb.glob("gestion_*"))) == n                          # plus aucune copie
    assert "backup.extra_cleared" in [e["action"] for e in owner.get("/api/audit?limit=20").json()["entries"]]


def test_droits(owner, app_env, usb):
    for nom, role in (("compta", "accountant"), ("marie", "sales"), ("expert", "auditor"), ("caisse", "cashier")):
        c, _ = make_user(owner, app_env, nom, role)
        assert c.post("/api/backup/extra", json={"dir": str(usb)}).status_code == 403 and c.delete("/api/backup/extra").status_code == 403
    assert backup.extra_dir() is None
