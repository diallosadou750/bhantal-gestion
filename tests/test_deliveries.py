import uuid
from datetime import date

from conftest import make_user, op, push

T = date.today().isoformat()


def pull(c):
    return c.get("/api/sync/pull?since=0").json()


def vente(c, qtys=((10, 100_000), (4, 50_000))):
    pids = []
    for i, (_, price) in enumerate(qtys):
        pids.append(push(c, op("product.create", {"id": str(uuid.uuid4()), "name": f"Produit {i + 1}", "price": price}))[0]["product"]["id"])
    cid = push(c, op("client.create", {"id": str(uuid.uuid4()), "name": "Camara", "credit_limit": 9_000_000}))[0]["client"]["id"]
    return push(c, op("sale.create", {"client_id": cid, "kind": "cash", "pay_method": "Espèces", "lines": [{"product_id": p, "qty": q} for p, (q, _) in zip(pids, qtys)]}))[0]["sale"]


def bl(c, sale, lines=None, **extra):
    data = {"id": str(uuid.uuid4()), "sale_id": sale["id"], **extra}
    if lines is not None: data["lines"] = [{"sale_position": p, "qty": q} for p, q in lines]
    return push(c, op("delivery.create", data))[0]


def test_livraison_partielle_puis_complete(owner):
    s = vente(owner)
    r = bl(owner, s, [(1, 6)], recipient="Mamadou (chauffeur)")
    assert r["status"] == "ok" and r["delivery"]["number"] == "BL-0001" and r["delivery"]["recipient"] == "Mamadou (chauffeur)" and r["sale"]["delivered_at"] is None
    assert bl(owner, s, [(1, 5)])["status"] == "invalid"                                    # il ne reste que 4 de la ligne 1
    r2 = bl(owner, s, [(1, 4), (2, 4)])
    assert r2["status"] == "ok" and r2["sale"]["delivered_at"] == T and r2["delivery"]["number"] == "BL-0002"      # tout est livré : la vente passe à « livrée »
    assert bl(owner, s)["status"] == "invalid"                                              # plus rien à livrer
    assert len(pull(owner)["delivery_notes"]) == 2


def test_annulation_remet_la_vente_en_attente(owner):
    s = vente(owner)
    a = bl(owner, s)                                                                        # sans lignes : tout ce qui reste
    assert a["status"] == "ok" and a["sale"]["delivered_at"] == T and len(a["delivery"]["lines"]) == 2
    assert push(owner, op("delivery.cancel", {"id": a["delivery"]["id"], "base_version": a["delivery"]["version"], "reason": ""}))[0]["status"] == "invalid"
    assert push(owner, op("delivery.cancel", {"id": a["delivery"]["id"], "base_version": 5, "reason": "erreur de bon"}))[0]["status"] == "conflict"
    c = push(owner, op("delivery.cancel", {"id": a["delivery"]["id"], "base_version": a["delivery"]["version"], "reason": "erreur de bon"}))[0]
    assert c["status"] == "ok" and c["sale"]["delivered_at"] is None
    assert bl(owner, s, [(1, 10)])["status"] == "ok"                                        # les quantités annulées redeviennent livrables


def test_marquer_livree_cree_un_bon_et_ancien_comportement(owner):
    s = vente(owner)
    r = push(owner, op("sale.deliver", {"id": s["id"], "base_version": s["version"]}))[0]
    assert r["status"] == "ok" and r["sale"]["delivered_at"] == T
    notes = pull(owner)["delivery_notes"]
    assert len(notes) == 1 and sum(l["qty"] for l in notes[0]["lines"]) == 14
    s2 = vente(owner)
    part = bl(owner, s2, [(1, 3)])
    r = push(owner, op("sale.deliver", {"id": s2["id"], "base_version": part["sale"]["version"]}))[0]
    assert r["status"] == "ok" and r["sale"]["delivered_at"] == T
    assert sorted(l["qty"] for n in pull(owner)["delivery_notes"] if n["sale_id"] == s2["id"] for l in n["lines"]) == [3, 4, 7]       # le reste a été livré d'un coup


def test_vente_annulee_validations_droits_et_page(owner, app_env):
    s = vente(owner)
    for bad in ({"delivery_date": "2999-01-01"}, {"delivery_date": "2000-01-01"}):
        assert bl(owner, s, [(1, 1)], **bad)["status"] == "invalid", bad
    assert bl(owner, s, [(9, 1)])["status"] == "invalid"
    ok = bl(owner, s, [(1, 2)], recipient="<b>x</b>")
    page = owner.get(f"/api/delivery/{ok['delivery']['id']}")
    assert page.status_code == 200 and "BON DE LIVRAISON" in page.text and "BL-0001" in page.text and "&lt;b&gt;x&lt;/b&gt;" in page.text and "Signature" in page.text
    assert "100 000" not in page.text                                                       # un bon de livraison n'affiche aucun prix
    aud, _ = make_user(owner, app_env, "expert", "auditor")
    assert bl(aud, s, [(1, 1)])["status"] == "denied" and len(pull(aud)["delivery_notes"]) == 1 and aud.get(f"/api/delivery/{ok['delivery']['id']}").status_code == 200
    marie, _ = make_user(owner, app_env, "marie", "sales")
    assert pull(marie)["delivery_notes"] == [] and marie.get(f"/api/delivery/{ok['delivery']['id']}").status_code == 404           # pas la vente d'un autre commercial
    push(owner, op("sale.cancel", {"id": s["id"], "base_version": pull(owner)["sales"][0]["version"], "reason": "client parti"}))
    assert bl(owner, s, [(1, 1)])["status"] == "invalid"
