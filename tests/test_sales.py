import uuid
from datetime import date, timedelta

import pytest
from conftest import make_user, op, push

TODAY = date.today().isoformat()


def mk_client(c, name="Client", limit=0, **extra):
    cid = str(uuid.uuid4())
    r = push(c, op("client.create", {"id": cid, "name": name, "credit_limit": limit, **extra}))[0]
    assert r["status"] == "ok", r
    return cid


def mk_product(c, name="Ciment 50 kg", price=85000):
    pid = str(uuid.uuid4())
    r = push(c, op("product.create", {"id": pid, "name": name, "price": price, "unit": "sac"}))[0]
    assert r["status"] == "ok", r
    return pid


def sale(c, client_id, lines, kind="cash", **extra):
    data = {"id": str(uuid.uuid4()), "client_id": client_id, "kind": kind, "lines": lines, "pay_method": "Espèces", **extra}
    return push(c, op("sale.create", data))[0], data["id"]


def pull(c, since=0, epoch=None, db=None):
    url = f"/api/sync/pull?since={since}" + (f"&epoch={epoch}" if epoch is not None else "") + (f"&db={db}" if db else "")
    r = c.get(url); assert r.status_code == 200, r.text
    return r.json()


def get_sale(c, sid):
    return next(s for s in pull(c)["sales"] if s["id"] == sid)


def test_produits_droits_et_catalogue(owner, app_env):
    mk_product(owner)
    sales, _ = make_user(owner, app_env, "marie", "sales")
    assert push(sales, op("product.create", {"name": "X", "price": 1}))[0]["status"] == "denied"
    assert [p["name"] for p in pull(sales)["products"]] == ["Ciment 50 kg"]


def test_vente_comptant_totaux_numerotation(owner):
    cid, pid = mk_client(owner), mk_product(owner)
    r, sid = sale(owner, cid, [{"product_id": pid, "qty": 10, "discount_pct": 2}])
    s = r["sale"]
    assert r["status"] == "ok" and s["number"] == "FV-0001" and s["total"] == 833000 and s["paid_total"] == 833000 and s["balance"] == 0 and s["due_date"] is None
    assert sale(owner, cid, [{"product_id": pid, "qty": 1}])[0]["sale"]["number"] == "FV-0002"
    assert push(owner, op("sale.create", {"client_id": cid, "kind": "cash", "lines": [{"product_id": pid, "qty": 1}]}))[0]["message"].startswith("Une vente au comptant demande")


def test_commercial_prix_catalogue_impose_et_remise_limitee(owner, app_env):
    sales, su = make_user(owner, app_env, "marie", "sales")
    pid = mk_product(owner)
    cid = mk_client(sales, "Boutique", limit=5_000_000)
    r, _ = sale(sales, cid, [{"product_id": pid, "qty": 2, "unit_price": 1}])           # prix triché : ignoré
    assert r["sale"]["lines"][0]["unit_price"] == 85000 and r["sale"]["total"] == 170000 and r["sale"]["seller_user_id"] == su["id"]
    assert "limitée à 10 %" in sale(sales, cid, [{"product_id": pid, "qty": 1, "discount_pct": 11}])[0]["message"]
    assert sale(owner, cid, [{"product_id": pid, "qty": 1, "discount_pct": 11}])[0]["status"] == "ok"
    assert "catalogue" in sale(sales, cid, [{"name": "Libre", "qty": 1, "unit_price": 100}])[0]["message"]


def test_vente_credit_limite_et_derogation(owner, app_env):
    pid = mk_product(owner, price=1_000_000)
    sales, _ = make_user(owner, app_env, "marie", "sales")
    sans_limite = mk_client(owner, "Sans limite", limit=0)
    avec = mk_client(owner, "Avec limite", limit=3_000_000)
    due = (date.today() + timedelta(days=30)).isoformat()
    r, _ = sale(owner, sans_limite, [{"product_id": pid, "qty": 1}], "credit", due_date=due)
    assert r["status"] == "invalid" and "Aucune limite" in r["message"]
    assert sale(owner, avec, [{"product_id": pid, "qty": 1}], "credit")[0]["message"].startswith("Une vente à crédit demande une date")
    ok, sid = sale(owner, avec, [{"product_id": pid, "qty": 2}], "credit", due_date=due)
    assert ok["status"] == "ok" and ok["sale"]["balance"] == 2_000_000
    r2, _ = sale(owner, avec, [{"product_id": pid, "qty": 2}], "credit", due_date=due)       # 4 M > limite de 3 M
    assert r2["status"] == "invalid" and "Limite de crédit dépassée" in r2["message"]
    forced, _ = sale(owner, avec, [{"product_id": pid, "qty": 2}], "credit", due_date=due, override_credit=True)
    assert forced["status"] == "ok"
    actions = [e["action"] for e in owner.get("/api/audit?limit=100").json()["entries"]]
    assert "sale.create.refused" in actions and "sale.credit_override" in actions
    assert sale(sales, mk_client(sales, "À Marie", limit=1000), [{"product_id": pid, "qty": 1}], "credit", due_date=due, override_credit=True)[0]["status"] == "invalid"   # un commercial ne peut pas déroger


def test_acompte_et_paiements(owner):
    pid, cid = mk_product(owner, price=1_000_000), mk_client(owner, limit=5_000_000)
    due = (date.today() + timedelta(days=15)).isoformat()
    r, sid = sale(owner, cid, [{"product_id": pid, "qty": 3}], "credit", due_date=due, paid_at_sale=500_000)
    assert r["sale"]["balance"] == 2_500_000
    pay = lambda amount, ref="", method="Orange Money": push(owner, op("payment.create", {"sale_id": sid, "amount": amount, "method": method, "reference": ref}))[0]
    assert pay(1_000_000, "OM-1")["sale"]["balance"] == 1_500_000
    assert pay(1_000_000, "OM-1")["status"] == "invalid" and "déjà enregistré" in pay(1_000_000, "OM-1")["message"]
    assert "dépasse le reste" in pay(9_000_000)["message"]
    assert pay(1_500_000, "OM-2")["sale"]["balance"] == 0
    assert get_sale(owner, sid)["balance"] == 0 and len(pull(owner)["payments"]) == 2


def test_annulation_de_paiement_avec_motif(owner, app_env):
    pid, cid = mk_product(owner, price=100_000), mk_client(owner, limit=1_000_000)
    due = (date.today() + timedelta(days=15)).isoformat()
    _, sid = sale(owner, cid, [{"product_id": pid, "qty": 5}], "credit", due_date=due)
    p = push(owner, op("payment.create", {"sale_id": sid, "amount": 200_000, "method": "Espèces"}))[0]["payment"]
    cash, _ = make_user(owner, app_env, "caisse", "cashier")
    assert push(cash, op("payment.cancel", {"id": p["id"], "base_version": 1, "reason": "erreur"}))[0]["status"] == "denied"
    assert "motif" in push(owner, op("payment.cancel", {"id": p["id"], "base_version": 1, "reason": ""}))[0]["message"].lower()
    assert push(owner, op("payment.cancel", {"id": p["id"], "base_version": 1, "reason": "saisi deux fois"}))[0]["status"] == "ok"
    assert get_sale(owner, sid)["balance"] == 500_000                                    # le paiement annulé ne compte plus
    cancelled = [x for x in pull(owner)["payments"] if x["id"] == p["id"]][0]
    assert cancelled["status"] == "cancelled" and cancelled["cancel_reason"] == "saisi deux fois"


def test_avoirs(owner):
    pid, cid = mk_product(owner, price=100_000), mk_client(owner, limit=10_000_000)
    due = (date.today() + timedelta(days=15)).isoformat()
    _, sid = sale(owner, cid, [{"product_id": pid, "qty": 10}], "credit", due_date=due, paid_at_sale=300_000, pay_method="Espèces")
    note = lambda amount, settlement, reason="retour": push(owner, op("creditnote.create", {"sale_id": sid, "amount": amount, "settlement": settlement, "reason": reason}))[0]
    assert "reste à payer" in note(900_000, "deduct")["message"]               # 700 000 restent : déduire plus est impossible
    assert note(200_000, "deduct")["credit_note"]["number"] == "AV-0001"
    assert get_sale(owner, sid)["balance"] == 500_000 and get_sale(owner, sid)["net_total"] == 800_000
    assert "ne peut rembourser que" in note(400_000, "refund")["message"]       # 300 000 payés seulement
    assert note(100_000, "refund")["credit_note"]["number"] == "AV-0002"
    s = get_sale(owner, sid)
    assert s["net_total"] == 700_000 and s["paid_total"] == 200_000 and s["balance"] == 500_000
    assert note(1, "refund", reason="")["status"] == "invalid"


def test_annulation_de_vente_numero_conserve(owner):
    pid, cid = mk_product(owner), mk_client(owner)
    _, sid = sale(owner, cid, [{"product_id": pid, "qty": 1}])
    assert push(owner, op("sale.cancel", {"id": sid, "base_version": 1, "reason": "erreur de saisie"}))[0]["refund_due"] == 85000
    assert get_sale(owner, sid)["status"] == "cancelled" and get_sale(owner, sid)["balance"] == 0
    assert sale(owner, cid, [{"product_id": pid, "qty": 1}])[0]["sale"]["number"] == "FV-0002"     # FV-0001 n'est jamais réutilisé


def test_annulation_refusee_si_paiements(owner):
    pid, cid = mk_product(owner, price=100), mk_client(owner, limit=10_000)
    _, sid = sale(owner, cid, [{"product_id": pid, "qty": 10}], "credit", due_date=(date.today() + timedelta(days=5)).isoformat())
    push(owner, op("payment.create", {"sale_id": sid, "amount": 100, "method": "Espèces"}))
    r = push(owner, op("sale.cancel", {"id": sid, "base_version": 1, "reason": "test"}))[0]
    assert r["status"] == "invalid" and "annulez-les d'abord" in r["message"]


def test_visibilite_commercial_caissier(owner, app_env):
    pid = mk_product(owner)
    marie, _ = make_user(owner, app_env, "marie", "sales")
    paul, _ = make_user(owner, app_env, "paul", "sales")
    cash, _ = make_user(owner, app_env, "caisse", "cashier")
    c_m, c_p = mk_client(marie, "De Marie"), mk_client(paul, "De Paul")
    _, s_m = sale(marie, c_m, [{"product_id": pid, "qty": 1}])
    _, s_p = sale(paul, c_p, [{"product_id": pid, "qty": 1}])
    assert [s["id"] for s in pull(marie)["sales"]] == [s_m] and [s["id"] for s in pull(paul)["sales"]] == [s_p]
    assert len(pull(cash)["sales"]) == 2 and len(pull(owner)["sales"]) == 2
    assert sale(marie, c_p, [{"product_id": pid, "qty": 1}])[0]["status"] == "invalid"                 # client d'un collègue
    assert marie.get(f"/api/invoice/{s_p}").status_code == 404
    assert push(marie, op("payment.create", {"sale_id": s_p, "amount": 1, "method": "Espèces"}))[0]["status"] == "invalid"
    assert all("credit_limit" not in c for c in pull(cash)["clients"])


def test_caissier_pas_de_credit(owner, app_env):
    pid, cid = mk_product(owner), mk_client(owner, limit=10_000_000)
    cash, _ = make_user(owner, app_env, "caisse", "cashier")
    r, _ = sale(cash, cid, [{"product_id": pid, "qty": 1}], "credit", due_date=(date.today() + timedelta(days=5)).isoformat())
    assert r["status"] == "denied"
    assert sale(cash, cid, [{"name": "Article libre", "qty": 2, "unit_price": 500}])[0]["sale"]["total"] == 1000


def test_synchronisation_incrementale_epoch_et_idempotence(owner, app_env):
    pid, cid = mk_product(owner), mk_client(owner)
    o = op("sale.create", {"id": str(uuid.uuid4()), "client_id": cid, "kind": "cash", "pay_method": "Espèces", "lines": [{"product_id": pid, "qty": 1}]})
    a, b = push(owner, o)[0], push(owner, o)[0]
    assert a["sale"]["number"] == b["sale"]["number"] == "FV-0001" and b.get("replayed") and len(pull(owner)["sales"]) == 1
    first = pull(owner)
    assert first["full"] is True
    delta = pull(owner, first["cursor"], first["epoch"], first["db_id"])
    assert delta["full"] is False and delta["sales"] == [] and delta["clients"] == []
    due = (date.today() + timedelta(days=9)).isoformat()
    owner.post("/api/sync/push", json={"ops": [op("client.update", {"id": cid, "base_version": 1, "changes": {"credit_limit": 1000000}})]})
    _, sid = sale(owner, cid, [{"product_id": pid, "qty": 1}], "credit", due_date=due)
    cur = pull(owner)["cursor"]
    push(owner, op("payment.create", {"sale_id": sid, "amount": 5000, "method": "Espèces"}))
    meta = pull(owner); d = pull(owner, cur, meta["epoch"], meta["db_id"])
    assert [s["id"] for s in d["sales"]] == [sid] and d["sales"][0]["balance"] == 80000 and len(d["payments"]) == 1      # la vente remonte quand un paiement la touche
    marie, mu = make_user(owner, app_env, "marie", "sales")
    ep, dbid = pull(marie)["epoch"], pull(marie)["db_id"]
    push(owner, op("client.update", {"id": cid, "base_version": 2, "changes": {"owner_user_id": mu["id"]}}))
    assert pull(marie, 5, ep, dbid)["full"] is True                                                         # changement de droits : resynchronisation complète


def test_antidatage_limite(owner, app_env):
    pid, cid = mk_product(owner), mk_client(owner)
    old = (date.today() - timedelta(days=30)).isoformat()
    marie, _ = make_user(owner, app_env, "marie", "sales")
    cm = mk_client(marie, "Mienne")
    assert "Antidater" in sale(marie, cm, [{"product_id": pid, "qty": 1}], sale_date=old)[0]["message"]
    assert sale(owner, cid, [{"product_id": pid, "qty": 1}], sale_date=old)[0]["status"] == "ok"
    assert "futur" in sale(owner, cid, [{"product_id": pid, "qty": 1}], sale_date=(date.today() + timedelta(days=10)).isoformat())[0]["message"]


def test_facture_html_echappee_et_tracee(owner):
    pid = mk_product(owner, name="<b>Produit</b>")
    cid = mk_client(owner, name="<script>alert(1)</script>")
    _, sid = sale(owner, cid, [{"product_id": pid, "qty": 1}])
    r = owner.get(f"/api/invoice/{sid}")
    assert r.status_code == 200 and "<script>alert(1)" not in r.text and "&lt;script&gt;" in r.text and "&lt;b&gt;Produit" in r.text
    assert "default-src 'self'" in r.headers["content-security-policy"]
    pay = push(owner, op("payment.create", {"sale_id": sid, "amount": 1, "method": "Espèces"}))[0]
    assert pay["status"] == "invalid"                                                                   # déjà soldée : rien à payer
    assert any(e["action"] == "invoice.viewed" and e["user"] == "patron" for e in owner.get("/api/audit").json()["entries"])
    assert owner.get(f"/api/invoice/{uuid.uuid4()}").status_code == 404


def test_reglages_proprietaire_seulement(owner, app_env):
    marie, _ = make_user(owner, app_env, "marie", "sales")
    assert push(marie, op("settings.update", {"company_name": "Pirate"}))[0]["status"] == "denied"
    assert push(owner, op("settings.update", {"company_name": "Exemple SARL", "tax_rate": 18}))[0]["status"] == "ok"
    assert pull(owner)["settings"]["company_name"] == "Exemple SARL" and pull(marie)["settings"]["tax_rate"] == 18
    pid, cid = mk_product(owner, price=118), mk_client(owner)
    _, sid = sale(owner, cid, [{"product_id": pid, "qty": 1}])
    assert "Taxe (18 %)" in owner.get(f"/api/invoice/{sid}").text and "Exemple SARL" in owner.get(f"/api/invoice/{sid}").text


def test_journal_des_ventes_complet(owner):
    pid, cid = mk_product(owner), mk_client(owner)
    _, sid = sale(owner, cid, [{"product_id": pid, "qty": 2}])
    e = [x for x in owner.get("/api/audit?entity=sale").json()["entries"] if x["action"] == "sale.create"][0]
    assert e["user"] == "patron" and e["after"]["number"] == "FV-0001" and e["after"]["total"] == 170000
    assert owner.get("/api/audit/verify").json()["ok"] is True


def test_paiement_et_avoir_avec_identifiants_propres(owner):
    """Régression : l'identifiant du paiement (ou de l'avoir) ne doit pas être pris pour celui de la vente."""
    pid, cid = mk_product(owner, price=1000), mk_client(owner, limit=100000)
    _, sid = sale(owner, cid, [{"product_id": pid, "qty": 10}], "credit", due_date=(date.today() + timedelta(days=5)).isoformat())
    r = push(owner, op("payment.create", {"id": str(uuid.uuid4()), "sale_id": sid, "amount": 3000, "method": "Espèces"}))[0]
    assert r["status"] == "ok" and r["sale"]["balance"] == 7000
    n = push(owner, op("creditnote.create", {"id": str(uuid.uuid4()), "sale_id": sid, "amount": 1000, "settlement": "deduct", "reason": "retour"}))[0]
    assert n["status"] == "ok" and n["sale"]["balance"] == 6000
