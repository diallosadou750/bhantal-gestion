import io
import re
import uuid
import zipfile
from datetime import date, timedelta

from conftest import make_user, op, push

TODAY = date.today()
T = TODAY.isoformat()


def iso(n):
    return (TODAY + timedelta(days=n)).isoformat()


def pull(c):
    return c.get("/api/sync/pull?since=0").json()


def produit(c, name="Ciment", price=100_000, **extra):
    return push(c, op("product.create", {"id": str(uuid.uuid4()), "name": name, "price": price, **extra}))[0]["product"]["id"]


def achat(c, pid, qty, **line):
    sup = push(c, op("supplier.create", {"id": str(uuid.uuid4()), "name": "F" + uuid.uuid4().hex[:3]}))[0]["supplier"]["id"]
    r = push(c, op("purchase.create", {"supplier_id": sup, "kind": "cash", "pay_method": "Espèces", "purchase_date": iso(-5), "lines": [{"product_id": pid, "qty": qty, "unit_cost": 60_000, **line}]}))[0]
    assert r["status"] == "ok", r


def vente(c, lignes, kind="cash"):
    cid = push(c, op("client.create", {"id": str(uuid.uuid4()), "name": "C" + uuid.uuid4().hex[:3], "credit_limit": 900_000_000}))[0]["client"]["id"]
    data = {"client_id": cid, "kind": kind, "pay_method": "Espèces", "lines": lignes}
    if kind == "credit": data["due_date"] = iso(30)
    r = push(c, op("sale.create", data))[0]
    assert r["status"] == "ok", r
    return r["sale"]


def avoir(c, sale, amount, returns=None, settlement="refund", **kw):
    data = {"id": str(uuid.uuid4()), "sale_id": sale["id"], "amount": amount, "settlement": settlement, "reason": "Retour de marchandise", **kw}
    if returns is not None: data["returns"] = returns
    return push(c, op("creditnote.create", data))[0]


def stock(c, pid):
    return pull(c)["stock"][pid]["qty"]


def test_la_marchandise_rendue_remonte_dans_le_stock(owner):
    pid = produit(owner); achat(owner, pid, 20)
    s = vente(owner, [{"product_id": pid, "qty": 10}])
    assert stock(owner, pid) == 10
    r = avoir(owner, s, 300_000, [{"position": 1, "qty": 3}])
    assert r["status"] == "ok" and r["credit_note"]["number"] == "AV-0001"
    assert r["credit_note"]["returns"] == [{"position": 1, "product_id": pid, "name": "Ciment", "qty": 3, "depot_id": "main", "lot_no": None}]
    assert stock(owner, pid) == 13 and pull(owner)["stock_depots"][pid] == {"main": 13}               # 20 achetés - 10 vendus + 3 rendus
    ligne = next(x for x in pull(owner)["sales"] if x["id"] == s["id"])["lines"][0]
    assert ligne["returned"] == 3 and ligne["position"] == 1
    acts = owner.get("/api/audit?limit=20").json()["entries"]
    cree = next(e for e in acts if e["action"] == "creditnote.create")
    assert "3 × Ciment" in str(cree["after"])                                                          # le journal garde ce qui est remonté en stock


def test_un_avoir_sans_marchandise_ne_touche_pas_au_stock(owner):
    pid = produit(owner); achat(owner, pid, 20)
    s = vente(owner, [{"product_id": pid, "qty": 10}])
    assert avoir(owner, s, 50_000)["status"] == "ok" and avoir(owner, s, 10_000, [])["status"] == "ok"
    assert stock(owner, pid) == 10
    assert pull(owner)["credit_notes"][0]["returns"] == []


def test_on_ne_rend_pas_plus_que_vendu_et_rien_n_est_ecrit_en_cas_de_refus(owner):
    pid = produit(owner); achat(owner, pid, 20)
    s = vente(owner, [{"product_id": pid, "qty": 10}])
    assert avoir(owner, s, 100_000, [{"position": 1, "qty": 8}])["status"] == "ok"
    r = avoir(owner, s, 100_000, [{"position": 1, "qty": 3}])                                          # 8 + 3 > 10
    assert r["status"] == "invalid" and "déjà rendus" in r["message"]
    r2 = avoir(owner, s, 100_000, [{"position": 1, "qty": 1}, {"position": 1, "qty": 2}, {"position": 1, "qty": 1}])    # 1+2+1 = 4 > 2 restants : tout ou rien
    assert r2["status"] == "invalid"
    assert len(pull(owner)["credit_notes"]) == 1 and stock(owner, pid) == 20 - 10 + 8                 # aucune écriture partielle
    assert avoir(owner, s, 20_000, [{"position": 1, "qty": 2}])["status"] == "ok" and stock(owner, pid) == 20            # le solde exact passe
    assert push(owner, op("creditnote.create", {"id": str(uuid.uuid4()), "sale_id": s["id"], "amount": 1000, "settlement": "refund", "reason": "x", "returns": [{"position": 1, "qty": 1}]}))[0]["status"] == "invalid"


def test_articles_libres_et_produits_non_suivis(owner):
    libre = vente(owner, [{"name": "Livraison exceptionnelle", "qty": 1, "unit_price": 50_000}])
    r = avoir(owner, libre, 10_000, [{"position": 1, "qty": 1}])
    assert r["status"] == "invalid" and "article libre" in r["message"]
    svc = produit(owner, "Conseil", 80_000, track_stock=False)
    s = vente(owner, [{"product_id": svc, "qty": 2}])
    r = avoir(owner, s, 10_000, [{"position": 1, "qty": 1}])
    assert r["status"] == "invalid" and "pas suivi en stock" in r["message"]
    assert avoir(owner, s, 10_000, [{"position": 5, "qty": 1}])["status"] == "invalid" and avoir(owner, s, 10_000, [{"position": 0, "qty": 1}])["status"] == "invalid"      # ligne inexistante (les lignes se comptent à partir de 1)
    assert avoir(owner, s, 10_000, [{"position": 1, "qty": 0}])["status"] == "invalid" and avoir(owner, s, 10_000, [{"position": 1, "qty": "x"}])["status"] == "invalid"
    assert avoir(owner, s, 10_000, "pas une liste")["status"] == "invalid"


def test_retour_dans_un_autre_depot(owner):
    pid = produit(owner); achat(owner, pid, 20)
    d2 = push(owner, op("depot.create", {"name": "Magasin du marché"}))[0]["depot"]["id"]
    s = vente(owner, [{"product_id": pid, "qty": 10}])
    r = avoir(owner, s, 100_000, [{"position": 1, "qty": 4, "depot_id": d2}])
    assert r["status"] == "ok" and pull(owner)["stock_depots"][pid] == {"main": 10, d2: 4} and stock(owner, pid) == 14
    assert avoir(owner, s, 1000, [{"position": 1, "qty": 1, "depot_id": "inconnu"}])["status"] == "invalid"


def test_retour_dans_les_memes_lots(owner):
    pid = produit(owner)
    achat(owner, pid, 5, lot_no="L-A", expiry_date=iso(20))                                           # périme le premier
    achat(owner, pid, 10, lot_no="L-B", expiry_date=iso(90))
    s = vente(owner, [{"product_id": pid, "qty": 8}])                                                 # premier périmé, premier sorti : 5 de L-A puis 3 de L-B
    lots = lambda: {l["lot_no"]: l["remaining"] for l in pull(owner)["lots"] if l["product_id"] == pid}
    assert lots() == {"L-B": 7}                                                                       # L-A est vide, donc pas listé
    r = avoir(owner, s, 600_000, [{"position": 1, "qty": 6}])
    assert r["status"] == "ok"
    assert [(x["lot_no"], x["qty"]) for x in r["credit_note"]["returns"]] == [("L-A", 5), ("L-B", 1)]   # dans l'ordre du prélèvement
    assert lots() == {"L-A": 5, "L-B": 8} and stock(owner, pid) == 15 - 8 + 6
    r2 = avoir(owner, s, 200_000, [{"position": 1, "qty": 2}])                                        # le reste (2) : les 2 derniers de L-B
    assert [(x["lot_no"], x["qty"]) for x in r2["credit_note"]["returns"]] == [("L-B", 2)] and lots() == {"L-A": 5, "L-B": 10}


def test_comptabilite_et_stock_valorise(owner):
    pid = produit(owner); achat(owner, pid, 20)
    s = vente(owner, [{"product_id": pid, "qty": 10}])
    from gestion import accounting, db
    with db.read() as c: avant = accounting.stock_value(c, T)
    assert avoir(owner, s, 300_000, [{"position": 1, "qty": 3}])["status"] == "ok"
    with db.read() as c: apres = accounting.stock_value(c, T)
    assert apres - avant == 3 * 60_000                                                                 # 3 sacs de plus, valorisés au coût moyen d'achat
    q = f"from=2000-01-01&to={T}"
    assert owner.get(f"/api/accounting/report?kind=balance&{q}").json()["balanced"] is True
    assert owner.get(f"/api/accounting/report?kind=statements&{q}").json()["balance_sheet"]["balanced"] is True
    assert owner.get("/api/audit/verify").json()["ok"] is True


def test_facture_export_et_droits(owner, app_env):
    pid = produit(owner); achat(owner, pid, 20)
    s = vente(owner, [{"product_id": pid, "qty": 10}])
    assert avoir(owner, s, 100_000, [{"position": 1, "qty": 3}])["status"] == "ok"
    page = re.sub(r"\s+", " ", owner.get(f"/api/invoice/{s['id']}").text)
    assert "marchandise remise en stock : 3 × Ciment" in page
    z = zipfile.ZipFile(io.BytesIO(owner.get("/api/export/all.zip").content))
    assert "credit_note_lines.csv" in z.namelist() and "Ciment" in z.read("credit_note_lines.csv").decode("utf-8-sig")
    sales_c, _ = make_user(owner, app_env, "marie", "sales")
    assert push(sales_c, op("creditnote.create", {"id": str(uuid.uuid4()), "sale_id": s["id"], "amount": 1000, "settlement": "refund", "reason": "x", "returns": [{"position": 1, "qty": 1}]}))[0]["status"] in ("denied", "invalid")
    assert stock(owner, pid) == 20 - 10 + 3
    comp, _ = make_user(owner, app_env, "compta", "accountant")
    r = push(comp, op("creditnote.create", {"id": str(uuid.uuid4()), "sale_id": s["id"], "amount": 50_000, "settlement": "refund", "reason": "retour", "returns": [{"position": 1, "qty": 1}]}))[0]
    assert r["status"] == "ok" and stock(owner, pid) == 20 - 10 + 4


def test_la_vente_avec_retour_ne_s_annule_plus_sans_precaution(owner):
    pid = produit(owner); achat(owner, pid, 20)
    s = vente(owner, [{"product_id": pid, "qty": 10}])
    assert avoir(owner, s, 100_000, [{"position": 1, "qty": 3}])["status"] == "ok"
    r = push(owner, op("sale.cancel", {"id": s["id"], "base_version": 1, "reason": "erreur"}))[0]
    assert r["status"] in ("invalid", "conflict")                                                      # un avoir existe : on ne peut pas annuler la vente d'un coup
    assert stock(owner, pid) == 13
