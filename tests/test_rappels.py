import base64
import json
import socketserver
import threading
import uuid
from datetime import date, datetime, timedelta

import pytest
from conftest import make_user, op, push

TODAY = date.today()


def iso(n):
    return (TODAY + timedelta(days=n)).isoformat()


# ---------------------------------------------------------------- faux serveur d'e-mail (aucun message ne sort de l'ordinateur)
class FauxSMTP:
    def __init__(self, user=None, password=None):
        self.messages, self.auths = [], []; outer = self

        class H(socketserver.StreamRequestHandler):
            def handle(self):
                w = lambda s: (self.wfile.write((s + "\r\n").encode()), self.wfile.flush())
                w("220 faux.local ESMTP"); mail = {"to": []}
                while True:
                    line = self.rfile.readline().decode("utf-8", "replace").rstrip("\r\n")
                    if not line: return
                    cmd = line.split(" ", 1)[0].upper()
                    if cmd == "EHLO": w("250-faux.local"); w("250 AUTH PLAIN") if outer.user else w("250 8BITMIME")
                    elif cmd == "AUTH":
                        creds = base64.b64decode(line.split()[-1]).split(b"\0"); outer.auths.append((creds[1].decode(), creds[2].decode()))
                        w("235 ok") if (creds[1].decode(), creds[2].decode()) == (outer.user, outer.password) else w("535 5.7.8 refusé")
                    elif cmd == "MAIL": w("250 ok")
                    elif cmd == "RCPT": mail["to"].append(line.split(":", 1)[1].strip(" <>")); w("250 ok")
                    elif cmd == "DATA":
                        w("354 go"); data = []
                        while True:
                            l = self.rfile.readline().decode("utf-8", "replace")
                            if l.rstrip("\r\n") == ".": break
                            data.append(l)
                        outer.messages.append({"to": list(mail["to"]), "raw": "".join(data)}); mail = {"to": []}; w("250 queued")
                    elif cmd == "QUIT": w("221 bye"); return
                    else: w("250 ok")
        self.user, self.password = user, password
        self.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H); self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self): self.srv.shutdown(); self.srv.server_close()


@pytest.fixture()
def smtp():
    s = FauxSMTP(); yield s; s.close()


def donnees(c, noms=True):
    """Une créance en retard, un fournisseur en retard, un produit en rupture, un devis qui expire, une livraison en retard."""
    pid = push(c, op("product.create", {"id": str(uuid.uuid4()), "name": "Ciment 50 kg", "price": 100_000, "reorder_level": 5}))[0]["product"]["id"]
    cid = push(c, op("client.create", {"id": str(uuid.uuid4()), "name": "Quincaillerie Camara", "credit_limit": 900_000_000}))[0]["client"]["id"]
    sup = push(c, op("supplier.create", {"id": str(uuid.uuid4()), "name": "Fournisseur Bah"}))[0]["supplier"]["id"]
    assert push(c, op("purchase.create", {"supplier_id": sup, "kind": "credit", "due_date": iso(-3), "purchase_date": iso(-20), "lines": [{"product_id": pid, "qty": 3, "unit_cost": 60_000}]}))[0]["status"] == "ok"
    s = push(c, op("sale.create", {"client_id": cid, "kind": "credit", "sale_date": iso(-30), "due_date": iso(-10), "delivery_due": iso(-2), "lines": [{"product_id": pid, "qty": 2}]}))[0]
    assert s["status"] == "ok", s
    assert push(c, op("quote.create", {"id": str(uuid.uuid4()), "client_id": cid, "valid_until": iso(3), "lines": [{"product_id": pid, "qty": 1}]}))[0]["status"] == "ok"
    return s["sale"]["balance"]


def config_smtp(c, smtp, **extra):
    r = c.post("/api/notifications", json={"enabled": True, "host": "127.0.0.1", "port": smtp.port, "security": "none", "sender": "gestion@exemple.com", "to": ["patron@exemple.com"], "hour": "00:00", **extra})
    assert r.status_code == 200, r.text
    return r.json()["settings"]


def test_resume_du_jour_sans_noms_par_defaut(owner):
    solde = donnees(owner)
    d = owner.get("/api/digest").json()
    cles = {i["key"] for i in d["digest"]["items"]}
    assert {"overdue", "supplier_late", "stock_low", "deliveries", "quotes"} <= cles, cles
    it = {i["key"]: i for i in d["digest"]["items"]}
    assert it["overdue"]["count"] == 1 and it["overdue"]["amount"] == solde and it["supplier_late"]["amount"] == 180_000
    assert "Camara" not in d["text"] and "Ciment" not in d["text"] and "Bah" not in d["text"]                  # par défaut : des nombres, pas de noms
    assert f"{solde:,}".replace(",", " ") in d["text"]
    avec = owner.get("/api/digest?names=true").json()["text"]
    assert "Quincaillerie Camara" in avec and "Ciment 50 kg" in avec


def test_resume_vide(owner):
    d = owner.get("/api/digest").json()
    assert d["digest"]["empty"] is True and "Rien d'urgent" in d["text"]


def test_configuration_validations_et_mot_de_passe_jamais_renvoye(owner, smtp):
    post = lambda **kw: owner.post("/api/notifications", json=kw)
    assert post(enabled=True).status_code == 422                                              # rien à activer sans serveur ni destinataire
    assert post(host="mauvais host!").status_code == 422 and post(port=70000).status_code == 422 and post(port="abc").status_code == 422
    assert post(security="telnet").status_code == 422 and post(hour="7h30").status_code == 422 and post(hour="24:00").status_code == 422
    assert post(to=["pas-une-adresse"]).status_code == 422 and post(to=["a@b.com\nBcc: x@y.com"]).status_code == 422 and post(sender="x").status_code == 422
    assert post(to=[f"u{i}@exemple.com" for i in range(6)]).status_code == 422
    r = config_smtp(owner, smtp, user="patron@exemple.com", password="MotDePasseApplication42")
    assert r["has_password"] is True and "password" not in r
    got = owner.get("/api/notifications").json()
    assert "MotDePasseApplication42" not in json.dumps(got) and got["settings"]["has_password"] is True
    entries = owner.get("/api/audit?limit=30").json()["entries"]
    texte = json.dumps(entries)
    assert "MotDePasseApplication42" not in texte and any(e["action"] == "notify.settings" for e in entries)
    assert owner.post("/api/notifications", json={"user": ""}).status_code == 422                 # identifiant sans mot de passe : refusé tant que les rappels sont actifs
    assert owner.post("/api/notifications", json={"enabled": False, "user": "", "password": ""}).status_code == 200
    assert owner.get("/api/notifications").json()["settings"]["has_password"] is False


def test_envoi_d_essai_vers_le_faux_serveur(owner, smtp):
    donnees(owner)
    config_smtp(owner, smtp)
    r = owner.post("/api/notifications/test").json()
    assert r["ok"] is True and r["sent"] is True and "patron@exemple.com" in r["message"]
    m = smtp.messages[0]
    assert m["to"] == ["patron@exemple.com"]
    assert "Subject: =?utf-8?" in m["raw"] or "Subject: [ESSAI]" in m["raw"]
    import email
    msg = email.message_from_string(m["raw"]); corps = msg.get_payload(decode=True).decode("utf-8")
    assert "[ESSAI]" in str(email.header.make_header(email.header.decode_header(msg["Subject"]))) and "message d'essai" in corps and "Clients en retard de paiement : 1" in corps
    assert "Camara" not in corps and "MotDePasse" not in m["raw"]
    assert any(e["action"] == "notify.sent" for e in owner.get("/api/audit?limit=30").json()["entries"])


def test_identifiant_refuse_message_clair(owner):
    s = FauxSMTP(user="patron@exemple.com", password="BonMotDePasse"); donnees(owner)
    try:
        config_smtp(owner, s, user="patron@exemple.com", password="Mauvais1234")
        r = owner.post("/api/notifications/test").json()
        assert r["ok"] is False and "refuse l'identifiant" in r["message"] and "Mauvais1234" not in json.dumps(r) and not s.messages
        owner.post("/api/notifications", json={"password": "BonMotDePasse"})
        assert owner.post("/api/notifications/test").json()["ok"] is True and len(s.messages) == 1
        assert s.auths[-1] == ("patron@exemple.com", "BonMotDePasse")
    finally:
        s.close()


def test_serveur_injoignable_message_clair(owner):
    r = owner.post("/api/notifications", json={"enabled": True, "host": "127.0.0.1", "port": 1, "security": "none", "sender": "g@exemple.com", "to": ["p@exemple.com"]})
    assert r.status_code == 200
    t = owner.post("/api/notifications/test").json()
    assert t["ok"] is False and "injoignable" in t["message"]


def test_mot_de_passe_depuis_l_environnement(owner, monkeypatch):
    s = FauxSMTP(user="u@exemple.com", password="DepuisEnv99"); donnees(owner)
    try:
        monkeypatch.setenv("GESTION_SMTP_PASSWORD", "DepuisEnv99")
        r = config_smtp(owner, s, user="u@exemple.com")                                       # aucun mot de passe saisi : celui de l'environnement
        assert r["has_password"] is True and r["password_from_env"] is True
        assert owner.post("/api/notifications/test").json()["ok"] is True
    finally:
        s.close()


def test_planification_un_seul_envoi_par_jour_et_reessais(owner, smtp):
    from gestion import db, notify
    donnees(owner); config_smtp(owner, smtp, hour="07:30")
    midi = datetime.combine(TODAY, datetime.min.time()).replace(hour=12)
    avant = midi.replace(hour=6)
    with db.read() as c: s = notify.load(c)
    assert notify.due(s, avant) is False and notify.due(s, midi) is True                        # pas avant l'heure choisie
    assert notify.tick(avant) is None and not smtp.messages
    r = notify.tick(midi)
    assert r["sent"] is True and len(smtp.messages) == 1
    assert notify.tick(midi + timedelta(hours=1)) is None and len(smtp.messages) == 1           # déjà envoyé aujourd'hui
    assert notify.tick(midi + timedelta(days=1)) is not None and len(smtp.messages) == 2        # le lendemain, de nouveau


def test_rien_a_signaler_pas_de_message(owner, smtp):
    from gestion import notify
    config_smtp(owner, smtp)
    midi = datetime.combine(TODAY, datetime.min.time()).replace(hour=12)
    r = notify.tick(midi)
    assert r["ok"] is True and r["sent"] is False and "Rien à signaler" in r["message"] and not smtp.messages
    owner.post("/api/notifications", json={"always": True})
    assert notify.tick(midi + timedelta(days=1))["sent"] is True and len(smtp.messages) == 1  # « toujours envoyer » : un message même vide


def test_echecs_reessais_toutes_les_30_minutes_4_fois_au_plus(owner):
    from gestion import db, notify
    donnees(owner)
    owner.post("/api/notifications", json={"enabled": True, "host": "127.0.0.1", "port": 1, "security": "none", "sender": "g@exemple.com", "to": ["p@exemple.com"], "hour": "00:00"})
    t0 = datetime.combine(TODAY, datetime.min.time()).replace(hour=8)
    assert notify.tick(t0)["ok"] is False
    assert notify.tick(t0 + timedelta(minutes=10)) is None                                       # trop tôt pour un 2e essai
    for k in range(1, 4): assert notify.tick(t0 + timedelta(minutes=31 * k)) is not None         # essais 2, 3 et 4
    assert notify.tick(t0 + timedelta(minutes=31 * 4)) is None                                   # 4 essais au plus pour la journée
    with db.read() as c: assert notify.load(c)["tries"] == 4
    assert notify.tick(t0 + timedelta(days=1)) is not None                                       # le lendemain on recommence
    assert sum(1 for e in owner.get("/api/audit?limit=100").json()["entries"] if e["action"] == "notify.failed") >= 4


def test_droits(owner, app_env, smtp):
    donnees(owner)
    for nom, role in (("compta", "accountant"), ("marie", "sales"), ("caisse", "cashier"), ("expert", "auditor")):
        c, _ = make_user(owner, app_env, nom, role)
        assert c.get("/api/notifications").status_code == 403 and c.post("/api/notifications", json={"hour": "08:00"}).status_code == 403 and c.post("/api/notifications/test").status_code == 403
    comp, _ = make_user(owner, app_env, "compta2", "accountant"); aud, _ = make_user(owner, app_env, "expert2", "auditor"); ven, _ = make_user(owner, app_env, "marie2", "sales")
    assert comp.get("/api/digest").status_code == 200 and aud.get("/api/digest").status_code == 200 and ven.get("/api/digest").status_code == 403
