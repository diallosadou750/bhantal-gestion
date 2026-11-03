import json
import sqlite3
import subprocess
import sys

import pytest
from conftest import make_user

SEED = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")


def test_vecteurs_rfc8032():
    from gestion import signature as s
    assert s.public_from_seed(SEED).hex() == "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
    sig = s.sign(SEED, b"")
    assert sig.hex().startswith("e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155")
    pub = s.public_from_seed(SEED)
    assert s.verify(pub, b"", sig) and not s.verify(pub, b"x", sig) and not s.verify(pub, b"", sig[:-1] + bytes([sig[-1] ^ 1]))
    assert not s.verify(b"\0" * 32, b"", sig) and not s.verify(pub, b"", b"")


def cle_proprietaire():
    import secrets
    from gestion import signature
    seed = secrets.token_bytes(32)
    return seed, signature.public_from_seed(seed).hex()


def signer(seed, pt):
    from gestion import signature
    return signature.sign(seed, signature.message(pt["id"], pt["hash"])).hex()


def test_parcours_complet_des_sceaux(owner, app_env):
    seed, pub = cle_proprietaire()
    st = owner.get("/api/audit/seal").json()
    assert st["public_key"] is None and st["count"] == 0 and st["checkpoint"]["id"] > 0
    assert owner.post("/api/audit/seal/key", json={"public_key": "zz"}).status_code == 422
    assert owner.post("/api/audit/seal", json={"id": 1, "hash": "0" * 64, "sig": "00"}).status_code == 422           # pas de clé enregistrée
    assert owner.post("/api/audit/seal/key", json={"public_key": pub}).status_code == 200
    pt = owner.get("/api/audit/seal").json()["checkpoint"]
    assert owner.post("/api/audit/seal", json={**{k: pt[k] for k in ("id", "hash")}, "sig": "ab" * 64}).status_code == 422          # fausse signature
    other, _ = cle_proprietaire()
    assert owner.post("/api/audit/seal", json={"id": pt["id"], "hash": pt["hash"], "sig": signer(other, pt)}).status_code == 422     # signée par une autre clé
    assert owner.post("/api/audit/seal", json={"id": pt["id"], "hash": "f" * 64, "sig": signer(seed, {"id": pt["id"], "hash": "f" * 64})}).status_code == 422      # empreinte fausse
    r = owner.post("/api/audit/seal", json={"id": pt["id"], "hash": pt["hash"], "sig": signer(seed, pt)})
    assert r.status_code == 200, r.text and r.json()["count"] == 1 and r.json()["ok"] is True
    assert owner.post("/api/audit/seal", json={"id": pt["id"], "hash": pt["hash"], "sig": signer(seed, pt)}).status_code == 422       # pas deux fois le même point
    v = owner.get("/api/audit/verify").json()
    assert v["ok"] is True and v["seals"]["count"] == 1 and v["seals"]["entries_since_last"] >= 1 and v["seals"]["last"]["id"] == pt["id"]
    acts = [e["action"] for e in owner.get("/api/audit?limit=20").json()["entries"]]
    assert "audit.sealed" in acts and "audit.seal_key" in acts


def test_une_reecriture_de_l_historique_est_detectee(owner, app_env):
    """Quelqu'un qui contrôle le serveur recalcule toute la chaîne : la chaîne reste cohérente, mais le sceau ne correspond plus."""
    from gestion import audit, config
    seed, pub = cle_proprietaire()
    owner.post("/api/audit/seal/key", json={"public_key": pub})
    pt = owner.get("/api/audit/seal").json()["checkpoint"]
    owner.post("/api/audit/seal", json={"id": pt["id"], "hash": pt["hash"], "sig": signer(seed, pt)})
    c = sqlite3.connect(config.db_path()); c.row_factory = sqlite3.Row
    c.execute("DROP TRIGGER audit_no_update")                              # l'attaquant a la main sur la base : il retire le verrou d'ajout seul
    rows =c.execute("SELECT * FROM audit_log ORDER BY id").fetchall(); prev = audit.GENESIS
    for r in rows:                                                          # on change une note, puis on recalcule toutes les empreintes
        note = "falsifié" if r["id"] == 1 else r["note"]
        f = audit._fields(r["ts"], r["user_id"], r["username"], r["role"], r["action"], r["entity"], r["entity_id"],
                          None if r["before_json"] is None else json.loads(r["before_json"]), None if r["after_json"] is None else json.loads(r["after_json"]), r["device"], r["ip"], note)
        h = audit._digest(prev, f); c.execute("UPDATE audit_log SET note=?, prev_hash=?, hash=? WHERE id=?", (note, prev, h, r["id"])); prev = h
    c.commit(); c.close()
    v = owner.get("/api/audit/verify").json()
    assert v["chain_ok"] is True and v["ok"] is False and v["seals"]["broken"] == [pt["id"]]


def test_droits_et_remplacement_de_cle(owner, app_env):
    seed, pub = cle_proprietaire(); _, pub2 = cle_proprietaire()
    compta, _ = make_user(owner, app_env, "compta", "accountant")
    assert compta.post("/api/audit/seal/key", json={"public_key": pub}).status_code == 403
    assert compta.get("/api/audit/seal").status_code == 200                                  # lecture seule pour qui peut lire le journal
    assert owner.post("/api/audit/seal/key", json={"public_key": pub}).status_code == 200
    assert owner.post("/api/audit/seal/key", json={"public_key": pub2}).status_code == 409
    assert owner.post("/api/audit/seal/key", json={"public_key": pub2, "replace": True}).status_code == 200
    assert owner.get("/api/audit/seal").json()["public_key"] == pub2


def test_outil_en_ligne_de_commande(tmp_path, owner, app_env):
    cle = tmp_path / "cle.txt"
    py = [sys.executable, "-m", "gestion.signature"]
    out = subprocess.run(py + ["generer", str(cle)], capture_output=True, text=True, check=True).stdout.strip().splitlines()[-1]
    assert len(out) == 64 and subprocess.run(py + ["generer", str(cle)], capture_output=True, text=True).returncode == 2        # n'écrase jamais
    assert owner.post("/api/audit/seal/key", json={"public_key": out}).status_code == 200
    pt = owner.get("/api/audit/seal").json()["checkpoint"]
    sealed = subprocess.run(py + ["signer", str(cle), json.dumps(pt)], capture_output=True, text=True, check=True).stdout
    assert owner.post("/api/audit/seal", json=json.loads(sealed)).status_code == 200
