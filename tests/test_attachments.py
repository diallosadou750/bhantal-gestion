import base64
import uuid

from conftest import make_user, op, push

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
JPG = b"\xff\xd8\xff\xe0" + b"0" * 200
PDF = b"%PDF-1.4\n" + b"x" * 300


def depense(c, amount=50_000, method="Espèces"):
    return push(c, op("expense.create", {"category": "Transport et livraison", "amount": amount, "method": method, "payee": "Taxi"}))[0]["expense"]


def up(c, entity, eid, data, name="recu.jpg"):
    return c.post(f"/api/attachments?entity={entity}&id={eid}&name={name}", content=data, headers={"Content-Type": "application/octet-stream"})


def test_ajout_liste_lecture_et_retrait(owner):
    e = depense(owner)
    r = up(owner, "expense", e["id"], JPG, "reçu taxi.jpg")
    assert r.status_code == 200 and r.json()["attachment"]["mime"] == "image/jpeg" and r.json()["attachment"]["name"] == "reçu taxi.jpg"
    aid = r.json()["attachment"]["id"]
    assert up(owner, "expense", e["id"], PDF, "facture.pdf").status_code == 200 and up(owner, "expense", e["id"], PNG, "photo").json()["attachment"]["name"] == "photo.png"
    lst = owner.get(f"/api/attachments?entity=expense&id={e['id']}").json()["attachments"]
    assert [x["name"] for x in lst] == ["reçu taxi.jpg", "facture.pdf", "photo.png"] and all("data" not in x for x in lst)
    f = owner.get(f"/api/attachments/{aid}")
    assert f.status_code == 200 and f.content == JPG and f.headers["content-type"] == "image/jpeg" and "sandbox" in f.headers["content-security-policy"] and f.headers["x-content-type-options"] == "nosniff"
    assert owner.post(f"/api/attachments/{aid}/remove", json={"reason": ""}).status_code == 422
    assert owner.post(f"/api/attachments/{aid}/remove", json={"reason": "photo floue"}).status_code == 200
    assert owner.get(f"/api/attachments/{aid}").status_code == 404 and len(owner.get(f"/api/attachments?entity=expense&id={e['id']}").json()["attachments"]) == 2
    acts = [x["action"] for x in owner.get("/api/audit?limit=30").json()["entries"]]
    assert {"attachment.added", "attachment.viewed", "attachment.removed"} <= set(acts)


def test_formats_taille_doublons_et_limite(owner):
    e = depense(owner)
    svg = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
    assert up(owner, "expense", e["id"], svg, "x.png").status_code == 422                      # le contenu compte, pas le nom
    assert up(owner, "expense", e["id"], b"<html><script>alert(1)</script></html>", "page.pdf").status_code == 422
    assert up(owner, "expense", e["id"], b"", "vide.jpg").status_code == 422
    assert up(owner, "expense", e["id"], b"\xff\xd8\xff" + b"0" * (5 * 1024 * 1024 + 10), "gros.jpg").status_code in (413, 422)
    assert up(owner, "expense", e["id"], JPG).status_code == 200 and up(owner, "expense", e["id"], JPG).status_code == 422       # doublon refusé
    for i in range(9): assert up(owner, "expense", e["id"], JPG + bytes([i + 1]) * 3).status_code == 200
    assert up(owner, "expense", e["id"], JPG + b"fin").status_code == 422                       # 10 pièces au plus
    assert up(owner, "expense", "pas-un-doc-valide", JPG).status_code in (403, 404, 422)
    assert up(owner, "inconnu", e["id"], JPG).status_code == 422


def test_droits_par_document(owner, app_env):
    e = depense(owner)
    cash, _ = make_user(owner, app_env, "paul", "cashier")
    mine = push(cash, op("expense.create", {"category": "Fournitures et petits achats", "amount": 20_000, "method": "Espèces", "payee": "Papeterie"}))[0]["expense"]
    assert up(cash, "expense", mine["id"], JPG).status_code == 200                              # le caissier joint le reçu de SA dépense
    assert up(cash, "expense", e["id"], JPG).status_code == 403 and cash.get(f"/api/attachments?entity=expense&id={e['id']}").status_code == 404      # pas celle des autres
    aid = owner.get(f"/api/attachments?entity=expense&id={mine['id']}").json()["attachments"][0]["id"]
    sales, _ = make_user(owner, app_env, "marie", "sales")
    assert sales.get(f"/api/attachments/{aid}").status_code == 404 and sales.post(f"/api/attachments/{aid}/remove", json={"reason": "essai de retrait"}).status_code == 422
    aud, _ = make_user(owner, app_env, "expert", "auditor")
    assert aud.get(f"/api/attachments/{aid}").status_code == 200 and up(aud, "expense", mine["id"], PNG).status_code == 403          # l'auditeur lit, n'ajoute rien
    pid = push(owner, op("product.create", {"id": str(uuid.uuid4()), "name": "Ciment", "price": 100_000}))[0]["product"]["id"]
    cid = push(owner, op("client.create", {"id": str(uuid.uuid4()), "name": "Client du patron"}))[0]["client"]["id"]
    s = push(owner, op("sale.create", {"client_id": cid, "kind": "cash", "pay_method": "Espèces", "lines": [{"product_id": pid, "qty": 1}]}))[0]["sale"]
    assert up(owner, "sale", s["id"], PDF, "bon-signe.pdf").status_code == 200 and sales.get(f"/api/attachments?entity=sale&id={s['id']}").status_code == 404


def test_achat_et_sauvegarde(owner, tmp_path):
    sup = push(owner, op("supplier.create", {"id": str(uuid.uuid4()), "name": "F"}))[0]["supplier"]["id"]
    pid = push(owner, op("product.create", {"id": str(uuid.uuid4()), "name": "Ciment", "price": 100_000}))[0]["product"]["id"]
    p = push(owner, op("purchase.create", {"supplier_id": sup, "kind": "cash", "pay_method": "Espèces", "lines": [{"product_id": pid, "qty": 1, "unit_cost": 60_000}]}))[0]["purchase"]
    assert up(owner, "purchase", p["id"], PDF, "FA-114.pdf").status_code == 200
    import sqlite3

    from gestion import config
    contenu = owner.get("/api/backup/download").content
    if config.db_url():
        from test_backup import restaurer_et_compter
        restaurer_et_compter(tmp_path, contenu, "attachments", 1)                                                                  # la sauvegarde PostgreSQL contient les pièces
    else:
        f = tmp_path / "b.sqlite3"; f.write_bytes(contenu)
        assert sqlite3.connect(f).execute("SELECT COUNT(*) FROM attachments WHERE status='active'").fetchone()[0] == 1               # la sauvegarde contient les pièces
    assert owner.get("/api/audit/verify").json()["ok"] is True
