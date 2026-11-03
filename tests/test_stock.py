import sqlite3
import uuid
from datetime import date, timedelta

from conftest import make_user, op, push

TODAY = date.today().isoformat()
DUE = (date.today() + timedelta(days=20)).isoformat()


def pull(c, since=0):
    r = c.get(f"/api/sync/pull?since={since}")
    assert r.status_code == 200, r.text
    return r.json()


def product(c, name="Ciment 50 kg", price=85000, **extra):
    r = push(c, op("product.create", {"id": str(uuid.uuid4()), "name": name, "price": price, **extra}))[0]
    assert r["status"] == "ok", r
    return r["product"]["id"]


def supplier(c, name="Fournisseur A"):
    r = push(c, op("supplier.create", {"id": str(uuid.uuid4()), "name": name, "phone": "224 620 11 22 33"}))[0]
    assert r["status"] == "ok", r
    return r["supplier"]["id"]


def purchase(c, sid, lines, kind="cash", **extra):
    data = {"id": str(uuid.uuid4()), "supplier_id": sid, "kind": kind, "pay_method": "Espèces", "lines": lines, **extra}
    return push(c, op("purchase.create", data))[0], data["id"]


def client(c, limit=10_000_000):
    r = push(c, op("client.create", {"id": str(uuid.uuid4()), "name": "Client", "credit_limit": limit}))[0]
    return r["client"]["id"]


def sell(c, cid, pid, qty):
    r = push(c, op("sale.create", {"id": str(uuid.uuid4()), "client_id": cid, "kind": "cash", "pay_method": "Espèces", "lines": [{"product_id": pid, "qty": qty}]}))[0]
    assert r["status"] == "ok", r
    return r["sale"]


def qty(c, pid):
    return pull(c)["stock"][pid]["qty"]


def test_fournisseurs_droits_et_codes(owner, app_env):
    sid = supplier(owner)
    f = pull(owner)["suppliers"][0]
    assert f["code"] == "F001" and f["phone"] == "224620112233" and f["id"] == sid
    marie, _ = make_user(owner, app_env, "marie", "sales")
    assert push(marie, op("supplier.create", {"name": "Pirate"}))[0]["status"] == "denied"
    assert pull(marie)["suppliers"] == [] and pull(marie)["purchases"] == []
    compta, _ = make_user(owner, app_env, "compta", "accountant")
    assert push(compta, op("supplier.create", {"name": "B"}))[0]["supplier"]["code"] == "F002"


def test_le_stock_se_deduit_des_mouvements(owner):
    pid, sid, cid = product(owner), supplier(owner), client(owner)
    r, pur = purchase(owner, sid, [{"product_id": pid, "qty": 100, "unit_cost": 70000}])
    assert r["purchase"]["number"] == "AC-0001" and r["purchase"]["total"] == 7_000_000 and r["purchase"]["balance"] == 0
    assert qty(owner, pid) == 100
    sale = sell(owner, cid, pid, 30)
    assert qty(owner, pid) == 70
    assert push(owner, op("sale.cancel", {"id": sale["id"], "base_version": 1, "reason": "erreur de saisie"}))[0]["status"] == "ok"
    assert qty(owner, pid) == 100                                                     # une vente annulée rend le stock
    assert purchase(owner, sid, [{"product_id": pid, "qty": 5, "unit_cost": 70000}])[0]["purchase"]["number"] == "AC-0002"


def test_achat_a_credit_reglements_et_annulation(owner):
    pid, sid = product(owner), supplier(owner)
    r, pur = purchase(owner, sid, [{"product_id": pid, "qty": 10, "unit_cost": 100_000}], "credit", due_date=DUE, paid_at_purchase=200_000)
    assert r["purchase"]["balance"] == 800_000 and r["purchase"]["due_date"] == DUE
    assert "échéance" in purchase(owner, sid, [{"product_id": pid, "qty": 1, "unit_cost": 1}], "credit")[0]["message"]
    pay = lambda amount, ref="": push(owner, op("supplierpay.create", {"id": str(uuid.uuid4()), "purchase_id": pur, "amount": amount, "method": "Virement bancaire", "reference": ref}))[0]
    a = pay(300_000, "VIR-1")
    assert a["status"] == "ok" and a["purchase"]["balance"] == 500_000
    assert "déjà enregistré" in pay(300_000, "VIR-1")["message"] and "dépasse" in pay(900_000)["message"]
    assert push(owner, op("purchase.cancel", {"id": pur, "base_version": 1, "reason": "erreur"}))[0]["message"].startswith("Cet achat a des règlements")
    p = a["supplier_payment"]
    assert "motif" in push(owner, op("supplierpay.cancel", {"id": p["id"], "base_version": 1, "reason": ""}))[0]["message"].lower()
    assert push(owner, op("supplierpay.cancel", {"id": p["id"], "base_version": 1, "reason": "mauvaise facture"}))[0]["status"] == "ok"
    assert [x for x in pull(owner)["purchases"] if x["id"] == pur][0]["balance"] == 800_000
    assert push(owner, op("purchase.cancel", {"id": pur, "base_version": 1, "reason": "livraison refusée"}))[0]["refund_due"] == 200_000


def test_annulation_d_achat_refusee_si_deja_vendu(owner):
    pid, sid, cid = product(owner), supplier(owner), client(owner)
    _, pur = purchase(owner, sid, [{"product_id": pid, "qty": 10, "unit_cost": 1000}])
    sell(owner, cid, pid, 8)
    r = push(owner, op("purchase.cancel", {"id": pur, "base_version": 1, "reason": "test"}))[0]
    assert r["status"] == "invalid" and "déjà vendu" in r["message"]
    assert qty(owner, pid) == 2


def test_alerte_hausse_prix_et_cout_moyen(owner):
    pid, sid = product(owner), supplier(owner)
    purchase(owner, sid, [{"product_id": pid, "qty": 100, "unit_cost": 70000}])
    r, _ = purchase(owner, sid, [{"product_id": pid, "qty": 100, "unit_cost": 77000}])        # +10 % : alerte
    assert r["cost_alerts"][0]["pct"] == 10.0 and r["purchase"]["lines"][0]["prev_cost"] == 70000
    ok, _ = purchase(owner, sid, [{"product_id": pid, "qty": 1, "unit_cost": 77500}])           # +0,6 % : pas d'alerte
    assert ok["cost_alerts"] == []
    s = pull(owner)["stock"][pid]
    assert s["qty"] == 201 and s["avg_cost"] == round((100 * 70000 + 100 * 77000 + 77500) / 201) and s["last_cost"] == 77500
    notes = [e["note"] for e in owner.get("/api/audit?action=purchase.create").json()["entries"] if e["note"]]
    assert any("hausse de 10.0 %" in n for n in notes)


def test_prix_d_achat_caches_aux_commerciaux_et_caissiers(owner, app_env):
    pid, sid = product(owner, reorder_level=20), supplier(owner)
    purchase(owner, sid, [{"product_id": pid, "qty": 15, "unit_cost": 70000}])
    for role in ("sales", "cashier"):
        c, _ = make_user(owner, app_env, "u_" + role, role)
        r = pull(c)
        assert r["stock"][pid] == {"qty": 15, "reorder": 20}                              # la quantité oui, le coût non
        assert r["purchases"] == [] and r["suppliers"] == [] and r["adjustments"] == []
        assert push(c, op("purchase.create", {"supplier_id": sid, "kind": "cash", "pay_method": "Espèces", "lines": [{"product_id": pid, "qty": 1, "unit_cost": 1}]}))[0]["status"] == "denied"
        assert push(c, op("stock.adjust", {"product_id": pid, "qty": -1, "reason": "loss", "note": "casse"}))[0]["status"] == "denied"
    assert "avg_cost" in pull(owner)["stock"][pid]


def test_ajustements_regles_et_stock_jamais_negatif(owner):
    pid = product(owner)
    adj = lambda q, reason, note="": push(owner, op("stock.adjust", {"id": str(uuid.uuid4()), "product_id": pid, "qty": q, "reason": reason, "note": note}))[0]
    assert adj(50, "initial")["stock"] == 50
    assert "positive" in adj(-5, "initial")["message"] and "négative" in adj(5, "loss", "x")["message"]
    assert "Expliquez" in adj(-5, "loss", "")["message"]
    assert adj(-5, "loss", "sacs éventrés")["stock"] == 45
    assert adj(3, "return", "")["stock"] == 48
    r = adj(-100, "loss", "vol")
    assert r["status"] == "invalid" and "négatif" in r["message"] and qty(owner, pid) == 48
    assert adj(0, "other", "rien")["status"] == "invalid"


def test_inventaire_ecarts_valeur_et_journal(owner):
    p1, p2, sid = product(owner, "Riz"), product(owner, "Huile"), supplier(owner)
    purchase(owner, sid, [{"product_id": p1, "qty": 100, "unit_cost": 1000}, {"product_id": p2, "qty": 50, "unit_cost": 2000}])
    inv = push(owner, op("stock.inventory", {"id": str(uuid.uuid4()), "note": "inventaire de fin de mois", "counts": [{"product_id": p1, "counted": 96}, {"product_id": p2, "counted": 50}]}))[0]
    assert inv["status"] == "ok" and inv["counted"] == 2 and len(inv["gaps"]) == 1
    g = inv["gaps"][0]
    assert g["name"] == "Riz" and g["theoretical"] == 100 and g["counted"] == 96 and g["delta"] == -4 and g["value"] == -4000 and inv["loss_value"] == -4000
    assert qty(owner, p1) == 96 and qty(owner, p2) == 50
    again = push(owner, op("stock.inventory", {"id": str(uuid.uuid4()), "counts": [{"product_id": p1, "counted": 96}]}))[0]
    assert again["gaps"] == []                                                                  # rien à corriger : aucun ajustement créé
    adjs = pull(owner)["adjustments"]
    assert len(adjs) == 1 and adjs[0]["reason"] == "inventory" and adjs[0]["theoretical"] == 100 and adjs[0]["counted"] == 96
    e = [x for x in owner.get("/api/audit?action=stock.inventory").json()["entries"]][-1]
    assert e["user"] == "patron" and e["after"]["valeur des manques"] == -4000 and "Riz : 100 → 96" in e["after"]["écarts"][0]
    assert push(owner, op("stock.inventory", {"id": str(uuid.uuid4()), "counts": [{"product_id": p1, "counted": 1}, {"product_id": p1, "counted": 2}]}))[0]["status"] == "invalid"
    assert owner.get("/api/audit/verify").json()["ok"] is True


def test_inventaire_rejoue_sans_doublon(owner):
    pid, sid = product(owner), supplier(owner)
    purchase(owner, sid, [{"product_id": pid, "qty": 10, "unit_cost": 100}])
    o = op("stock.inventory", {"id": str(uuid.uuid4()), "counts": [{"product_id": pid, "counted": 7}]})
    push(owner, o); r = push(owner, o)[0]
    assert r.get("replayed") and qty(owner, pid) == 7 and len(pull(owner)["adjustments"]) == 1


def test_suivi_de_stock_desactive_et_seuil(owner):
    p = product(owner, "Service de livraison", track_stock=False)
    q = product(owner, "Sucre", reorder_level=30)
    s = pull(owner)
    assert p not in s["stock"] and s["stock"][q]["reorder"] == 30
    prods = {x["id"]: x for x in s["products"]}
    assert prods[p]["track_stock"] is False and prods[q]["reorder_level"] == 30 and prods[q]["track_stock"] is True
    assert push(owner, op("product.update", {"id": q, "base_version": 1, "changes": {"reorder_level": 50}}))[0]["status"] == "ok"
    assert pull(owner)["stock"][q]["reorder"] == 50
    assert push(owner, op("stock.adjust", {"product_id": p, "qty": 5, "reason": "initial"}))[0]["status"] == "invalid"


def test_fournisseur_non_supprimable_s_il_est_du_et_synchro_incrementale(owner):
    pid, sid = product(owner), supplier(owner)
    _, pur = purchase(owner, sid, [{"product_id": pid, "qty": 10, "unit_cost": 1000}], "credit", due_date=DUE)
    cur = pull(owner)["cursor"]
    assert "On doit encore" in push(owner, op("supplier.delete", {"id": sid, "base_version": 1}))[0]["message"]
    push(owner, op("supplierpay.create", {"purchase_id": pur, "amount": 4000, "method": "Espèces"}))
    d = pull(owner, cur)
    assert [p["id"] for p in d["purchases"]] == [pur] and d["purchases"][0]["balance"] == 6000 and len(d["supplier_payments"]) == 1
    push(owner, op("supplierpay.create", {"purchase_id": pur, "amount": 6000, "method": "Espèces"}))
    assert push(owner, op("supplier.delete", {"id": sid, "base_version": 1}))[0]["status"] == "ok"


def test_ancienne_base_migree_sans_perte(tmp_path, monkeypatch):
    """Une base créée avant les stocks reçoit les nouvelles colonnes, sans rien perdre."""
    monkeypatch.setenv("GESTION_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GESTION_CHIFFREMENT", "0")                  # ce test lit la base directement
    con = sqlite3.connect(tmp_path / "gestion.sqlite3")
    con.executescript("CREATE TABLE products(id TEXT PRIMARY KEY, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL, category TEXT NOT NULL DEFAULT '', unit TEXT NOT NULL DEFAULT '', price INTEGER NOT NULL DEFAULT 0,"
                      " version INTEGER NOT NULL DEFAULT 1, seq INTEGER NOT NULL, deleted INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by INTEGER, updated_by INTEGER);"
                      "INSERT INTO products(id,code,name,price,seq,created_at,updated_at) VALUES('x','P001','Ancien produit',500,1,'t','t');")
    con.commit(); con.close()
    from gestion import db
    db.init_db(); db.init_db()
    with sqlite3.connect(tmp_path / "gestion.sqlite3") as c:
        assert c.execute("SELECT name, price, reorder_level, track_stock FROM products").fetchall() == [("Ancien produit", 500, 0, 1)]


def test_base_remplacee_ou_restauree_force_une_resynchronisation_complete(owner):
    """Un appareil qui garde le curseur d'une autre base (restauration, base de démonstration refaite) ne doit jamais mélanger les deux."""
    pid = product(owner)
    first = pull(owner)
    assert first["full"] and first["db_id"]
    same = owner.get(f"/api/sync/pull?since={first['cursor']}&epoch={first['epoch']}&db={first['db_id']}").json()
    assert same["full"] is False
    autre = owner.get(f"/api/sync/pull?since={first['cursor']}&epoch={first['epoch']}&db=autre-base").json()
    assert autre["full"] is True and any(p["id"] == pid for p in autre["products"])
    en_avance = owner.get(f"/api/sync/pull?since={first['cursor'] + 500}&epoch={first['epoch']}&db={first['db_id']}").json()
    assert en_avance["full"] is True                                                           # curseur plus récent que le serveur : base restaurée plus ancienne
