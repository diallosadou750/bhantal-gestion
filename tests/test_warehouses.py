import uuid
from datetime import date, timedelta

from conftest import make_user, op, push

TODAY = date.today()


def iso(n):
    return (TODAY + timedelta(days=n)).isoformat()


def pull(c):
    return c.get("/api/sync/pull?since=0").json()


def produit(c, name="Lait", price=10_000):
    return push(c, op("product.create", {"id": str(uuid.uuid4()), "name": name, "price": price}))[0]["product"]["id"]


def depot(c, name="Magasin du marché"):
    r = push(c, op("depot.create", {"name": name}))[0]
    assert r["status"] == "ok", r
    return r["depot"]


def achat(c, pid, qty, **line):
    sup = push(c, op("supplier.create", {"id": str(uuid.uuid4()), "name": "F" + uuid.uuid4().hex[:3]}))[0]["supplier"]["id"]
    r = push(c, op("purchase.create", {"supplier_id": sup, "kind": "cash", "pay_method": "Espèces", "purchase_date": iso(-5), "lines": [{"product_id": pid, "qty": qty, "unit_cost": 6_000, **line}]}))[0]
    return r


def vente(c, pid, qty, **line):
    cid = push(c, op("client.create", {"id": str(uuid.uuid4()), "name": "C" + uuid.uuid4().hex[:3]}))[0]["client"]["id"]
    return push(c, op("sale.create", {"client_id": cid, "kind": "cash", "pay_method": "Espèces", "lines": [{"product_id": pid, "qty": qty, **line}]}))[0]


def lots(c, pid):
    return {l["lot_no"]: l for l in pull(c)["lots"] if l["product_id"] == pid}


def test_depot_principal_et_stock_par_depot(owner):
    p = pull(owner)
    assert [d["name"] for d in p["depots"]] == ["Dépôt principal"] and p["depots"][0]["main"] is True
    pid = produit(owner)
    achat(owner, pid, 100)
    d2 = depot(owner)
    achat(owner, pid, 40, depot_id=d2["id"])
    s = vente(owner, pid, 30, depot_id=d2["id"])
    assert s["status"] == "ok"
    st = pull(owner)
    assert st["stock"][pid]["qty"] == 110 and st["stock_depots"][pid] == {"main": 100, d2["id"]: 10}          # le total reste la somme des dépôts
    assert push(owner, op("depot.create", {"name": "magasin du marché"}))[0]["status"] == "invalid"            # nom déjà pris (sans tenir compte des majuscules)
    assert vente(owner, pid, 1, depot_id="inconnu")["status"] == "invalid"


def test_transfert_entre_depots_et_annulation(owner):
    pid = produit(owner); achat(owner, pid, 50); d2 = depot(owner)
    t = push(owner, op("stransfer.create", {"product_id": pid, "from_depot": "main", "to_depot": d2["id"], "qty": 20, "note": "réassort du marché"}))[0]
    assert t["status"] == "ok" and t["transfer"]["number"] == "TS-0001"
    st = pull(owner)
    assert st["stock_depots"][pid] == {"main": 30, d2["id"]: 20} and st["stock"][pid]["qty"] == 50
    assert push(owner, op("stransfer.create", {"product_id": pid, "from_depot": "main", "to_depot": d2["id"], "qty": 31}))[0]["status"] == "invalid"        # plus que le dépôt d'origine
    assert push(owner, op("stransfer.create", {"product_id": pid, "from_depot": "main", "to_depot": "main", "qty": 1}))[0]["status"] == "invalid"
    vente(owner, pid, 15, depot_id=d2["id"])
    tt = t["transfer"]
    assert push(owner, op("stransfer.cancel", {"id": tt["id"], "base_version": tt["version"], "reason": "erreur"}))[0]["status"] == "invalid"     # une partie est déjà sortie du dépôt de destination
    assert push(owner, op("stransfer.cancel", {"id": tt["id"], "base_version": 9, "reason": "essai de conflit"}))[0]["status"] == "conflict"
    s2 = vente(owner, pid, 5, depot_id=d2["id"])["sale"]
    push(owner, op("sale.cancel", {"id": s2["id"], "base_version": s2["version"], "reason": "client parti"}))


def test_desactivation_de_depot(owner):
    pid = produit(owner); achat(owner, pid, 10); d2 = depot(owner)
    push(owner, op("stransfer.create", {"product_id": pid, "from_depot": "main", "to_depot": d2["id"], "qty": 4}))
    d = pull(owner)["depots"]; dd = next(x for x in d if x["id"] == d2["id"])
    assert push(owner, op("depot.update", {"id": d2["id"], "base_version": dd["version"], "changes": {"active": False}}))[0]["status"] == "invalid"       # il reste du stock
    assert push(owner, op("depot.update", {"id": "main", "base_version": 1, "changes": {"active": False}}))[0]["status"] == "invalid"
    t = pull(owner)["stock_transfers"][0]
    push(owner, op("stransfer.cancel", {"id": t["id"], "base_version": t["version"], "reason": "retour au principal"}))
    assert push(owner, op("depot.update", {"id": d2["id"], "base_version": dd["version"], "changes": {"active": False}}))[0]["status"] == "ok"
    assert vente(owner, pid, 1, depot_id=d2["id"])["status"] == "invalid"                    # un dépôt désactivé ne sert plus


def test_lots_fefo_et_peremption(owner):
    pid = produit(owner)
    assert achat(owner, pid, 20, lot_no="L-B", expiry_date=iso(90))["status"] == "ok"
    assert achat(owner, pid, 10, lot_no="L-A", expiry_date=iso(20))["status"] == "ok"
    assert achat(owner, pid, 5)["status"] == "ok"                                              # stock sans lot
    lt = lots(owner, pid)
    assert lt["L-A"]["status"] == "soon" and lt["L-B"]["status"] == "ok" and lt["L-A"]["remaining"] == 10
    s = vente(owner, pid, 14)                                                                  # sort d'abord du lot qui périme le premier
    assert s["status"] == "ok"
    lt = lots(owner, pid)
    assert "L-A" not in lt and lt["L-B"]["remaining"] == 16                                    # L-A épuisé (10), 4 pris sur L-B
    assert pull(owner)["stock"][pid]["qty"] == 21
    s2 = s["sale"]
    push(owner, op("sale.cancel", {"id": s2["id"], "base_version": s2["version"], "reason": "annulée"}))
    lt = lots(owner, pid)
    assert lt["L-A"]["remaining"] == 10 and lt["L-B"]["remaining"] == 20                       # l'annulation remet les lots en l'état


def test_lot_perime_jamais_vendu_et_ajustement(owner):
    pid = produit(owner)
    achat(owner, pid, 10, lot_no="OLD", expiry_date=iso(-1))                                   # l'achat date de 5 jours : péremption après l'achat mais passée aujourd'hui
    achat(owner, pid, 10, lot_no="NEW", expiry_date=iso(200))
    assert lots(owner, pid)["OLD"]["status"] == "expired"
    s = vente(owner, pid, 5)
    assert s["status"] == "ok" and lots(owner, pid)["NEW"]["remaining"] == 5 and lots(owner, pid)["OLD"]["remaining"] == 10      # jamais depuis le lot périmé
    old = lots(owner, pid)["OLD"]
    assert push(owner, op("stock.adjust", {"product_id": pid, "qty": -11, "reason": "loss", "note": "lot périmé jeté", "lot_id": old["id"]}))[0]["status"] == "invalid"       # plus que le lot
    assert push(owner, op("stock.adjust", {"product_id": pid, "qty": 3, "reason": "loss", "note": "x y z", "lot_id": old["id"]}))[0]["status"] == "invalid"
    r = push(owner, op("stock.adjust", {"product_id": pid, "qty": -10, "reason": "loss", "note": "lot périmé jeté", "lot_id": old["id"]}))[0]
    assert r["status"] == "ok" and "OLD" not in lots(owner, pid) and pull(owner)["stock"][pid]["qty"] == 5


def test_validations_de_lot_et_achat_annule(owner):
    pid = produit(owner)
    assert achat(owner, pid, 5, lot_no="L1", expiry_date=iso(60))["status"] == "ok"
    assert achat(owner, pid, 5, lot_no="L1", expiry_date=iso(61))["status"] == "invalid"       # même lot, autre péremption
    assert achat(owner, pid, 5, lot_no="L1", expiry_date=iso(60))["status"] == "ok"            # même lot, même date : on complète le lot
    assert lots(owner, pid)["L1"]["remaining"] == 10
    assert achat(owner, pid, 5, lot_no="L2", expiry_date=iso(-30))["status"] == "invalid"      # péremption avant l'achat
    assert achat(owner, pid, 5, depot_id="inconnu")["status"] == "invalid"
    pur = achat(owner, pid, 5, lot_no="L3", expiry_date=iso(10))["purchase"]
    assert lots(owner, pid)["L3"]["remaining"] == 5
    push(owner, op("purchase.cancel", {"id": pur["id"], "base_version": pur["version"], "reason": "erreur"}))
    assert "L3" not in lots(owner, pid)                                                        # achat annulé : plus rien dans le lot


def test_inventaire_par_depot_retours_et_droits(owner, app_env):
    pid = produit(owner); a = achat(owner, pid, 30, lot_no="Z1", expiry_date=iso(100)); d2 = depot(owner)
    push(owner, op("stransfer.create", {"product_id": pid, "from_depot": "main", "to_depot": d2["id"], "qty": 10}))
    r = push(owner, op("stock.inventory", {"depot_id": d2["id"], "counts": [{"product_id": pid, "counted": 8}], "note": "comptage du marché"}))[0]
    assert r["status"] == "ok" and r["gaps"][0]["theoretical"] == 10 and r["gaps"][0]["delta"] == -2
    st = pull(owner)
    assert st["stock_depots"][pid] == {"main": 20, d2["id"]: 8}
    note = push(owner, op("snote.create", {"purchase_id": a["purchase"]["id"], "settlement": "refund", "refund_method": "Espèces", "reason": "lot abîmé rendu", "lines": [{"product_id": pid, "qty": 5}]}))[0]
    assert note["status"] == "ok" and lots(owner, pid)["Z1"]["remaining"] == 25 and pull(owner)["stock_depots"][pid]["main"] == 15      # le retour sort du dépôt et du lot d'origine
    marie, _ = make_user(owner, app_env, "marie", "sales")
    assert push(marie, op("depot.create", {"name": "Dépôt de Marie"}))[0]["status"] == "denied" and push(marie, op("stransfer.create", {"product_id": pid, "from_depot": "main", "to_depot": d2["id"], "qty": 1}))[0]["status"] == "denied"
    assert owner.get("/api/audit/verify").json()["ok"] is True
