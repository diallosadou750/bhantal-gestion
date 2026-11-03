import uuid
from datetime import date, timedelta

from conftest import make_user, op, push

TODAY = date.today()
T = TODAY.isoformat()


def day(n):
    return (TODAY - timedelta(days=n)).isoformat()


def pull(c, since=0):
    r = c.get(f"/api/sync/pull?since={since}")
    assert r.status_code == 200, r.text
    return r.json()


def expense(c, amount=50_000, method="Espèces", category="Transport et livraison", **extra):
    return push(c, op("expense.create", {"id": str(uuid.uuid4()), "category": category, "amount": amount, "method": method, "payee": "Chauffeur", **extra}))[0]


def product(c, price=100_000):
    return push(c, op("product.create", {"id": str(uuid.uuid4()), "name": "Ciment", "price": price}))[0]["product"]["id"]


def client(c):
    return push(c, op("client.create", {"id": str(uuid.uuid4()), "name": "Client", "credit_limit": 50_000_000}))[0]["client"]["id"]


def sell(c, cid, pid, qty, method="Espèces", **extra):
    r = push(c, op("sale.create", {"id": str(uuid.uuid4()), "client_id": cid, "kind": "cash", "pay_method": method, "lines": [{"product_id": pid, "qty": qty}], **extra}))[0]
    assert r["status"] == "ok", r
    return r["sale"]


def cash(c):
    return pull(c)["cash"]


def close(c, counted, **extra):
    return push(c, op("cash.close", {"id": str(uuid.uuid4()), "counted": counted, **extra}))[0]


def test_depenses_droits_numerotation_et_categories(owner, app_env):
    e = expense(owner)
    assert e["status"] == "ok" and e["expense"]["number"] == "DP-0001" and e["expense"]["nature"] == "Variable" and e["expense"]["account"] == "612"
    assert expense(owner, category="Loyer et charges locatives")["expense"]["nature"] == "Fixe"
    assert "inconnue" in expense(owner, category="Inventée")["message"]
    assert push(owner, op("expense.create", {"category": "Autres charges", "amount": 10, "method": "Espèces"}))[0]["message"].startswith("Indiquez le bénéficiaire")
    marie, _ = make_user(owner, app_env, "marie", "sales")
    assert expense(marie)["status"] == "denied" and pull(marie)["expenses"] == [] and pull(marie)["cash"] is None
    assert [x["number"] for x in pull(owner)["expenses"]] == ["DP-0001", "DP-0002"]


def test_caissier_plafond_visibilite_et_annulation(owner, app_env):
    cash_u, _ = make_user(owner, app_env, "caisse", "cashier")
    compta, _ = make_user(owner, app_env, "compta", "accountant")
    assert expense(cash_u, 100_000)["status"] == "ok"
    big = expense(cash_u, 100_001)
    assert big["status"] == "invalid" and "comptable" in big["message"]
    assert "antidater" in expense(cash_u, 1000, expense_date=day(10))["message"]
    expense(owner, 700_000)                                                              # dépense d'un autre : le caissier ne la voit pas
    assert [x["amount"] for x in pull(cash_u)["expenses"]] == [100_000] and len(pull(compta)["expenses"]) == 2
    e = pull(cash_u)["expenses"][0]
    assert push(cash_u, op("expense.cancel", {"id": e["id"], "base_version": 1, "reason": "erreur"}))[0]["status"] == "denied"
    assert "motif" in push(compta, op("expense.cancel", {"id": e["id"], "base_version": 1, "reason": ""}))[0]["message"].lower()
    assert push(compta, op("expense.cancel", {"id": e["id"], "base_version": 1, "reason": "saisie en double"}))[0]["status"] == "ok"
    assert [x for x in pull(owner)["expenses"] if x["id"] == e["id"]][0]["status"] == "cancelled"


def test_caisse_attendue_se_calcule_et_ignore_le_mobile_money(owner):
    pid, cid = product(owner), client(owner)
    assert cash(owner)["needs_opening"] is True
    sell(owner, cid, pid, 3)                                                              # 300 000 en espèces
    sell(owner, cid, pid, 5, method="Orange Money")                                       # hors caisse
    expense(owner, 40_000)                                                                # sortie espèces
    expense(owner, 90_000, method="Orange Money")                                         # hors caisse
    push(owner, op("cash.move", {"kind": "bank_deposit", "amount": 100_000, "note": "bordereau 123"}))
    st = cash(owner)
    assert st["in"]["sales"] == 300_000 and st["out"]["expenses"] == 40_000 and st["out"]["bank_deposit"] == 100_000 and st["in_total"] == 300_000 and st["out_total"] == 140_000
    assert st["expected"] == 160_000 and st["opening"] is None


def test_achats_et_reglements_fournisseurs_en_especes_sortent_de_la_caisse(owner):
    pid = product(owner)
    sup = push(owner, op("supplier.create", {"name": "F"}))[0]["supplier"]["id"]
    r = push(owner, op("purchase.create", {"supplier_id": sup, "kind": "cash", "pay_method": "Espèces", "lines": [{"product_id": pid, "qty": 10, "unit_cost": 20_000}]}))[0]
    p2 = push(owner, op("purchase.create", {"supplier_id": sup, "kind": "credit", "due_date": (TODAY + timedelta(days=9)).isoformat(), "lines": [{"product_id": pid, "qty": 10, "unit_cost": 10_000}]}))[0]["purchase"]
    push(owner, op("supplierpay.create", {"purchase_id": p2["id"], "amount": 30_000, "method": "Espèces"}))
    push(owner, op("supplierpay.create", {"purchase_id": p2["id"], "amount": 20_000, "method": "Chèque"}))
    st = cash(owner)
    assert st["out"]["purchases"] == 200_000 and st["out"]["supplier_payments"] == 30_000


def test_cloture_ouverture_ecart_et_enchainement(owner):
    pid, cid = product(owner), client(owner)
    assert "solde d'ouverture" in close(owner, 100_000)["message"]
    first = close(owner, 500_000, opening=500_000, close_date=day(1))                    # veille : caisse de départ 500 000, rien ne bouge
    assert first["status"] == "ok" and first["difference"] == 0
    sell(owner, cid, pid, 2)                                                              # +200 000
    expense(owner, 50_000)
    r = close(owner, 650_000)                                                             # l'ouverture est reprise du comptage de la veille : attendu 650 000
    assert r["status"] == "ok" and r["difference"] == 0 and r["closing"]["expected"] == 650_000 and r["closing"]["opening"] == 500_000
    assert "déjà clôturée" in close(owner, 1)["message"] and "déjà clôturée" in close(owner, 1, close_date=day(1))["message"]
    cl = pull(owner)["cash_closings"]
    assert [x["drift"] for x in cl] == [0, 0] and cash(owner)["opening"] == 650_000 and cash(owner)["last_close_date"] == T


def test_ecart_de_caisse_exige_une_explication(owner):
    pid, cid = product(owner), client(owner)
    sell(owner, cid, pid, 1)
    r = close(owner, 90_000, opening=0)                                                   # attendu 100 000, compté 90 000
    assert r["status"] == "invalid" and "expliquez" in r["message"] and "-10000" in r["message"]
    ok = close(owner, 90_000, opening=0, note="billet de 10 000 donné en trop au client")
    assert ok["status"] == "ok" and ok["difference"] == -10_000
    e = [x for x in owner.get("/api/audit?action=cash.close").json()["entries"]][0]
    assert e["after"]["écart"] == -10_000 and "ÉCART" in e["note"] and e["user"] == "patron"


def test_periode_cloturee_derive_et_blocage_du_caissier(owner, app_env):
    cash_u, _ = make_user(owner, app_env, "caisse", "cashier")
    close(owner, 0, opening=0)                                                            # clôture d'aujourd'hui, caisse vide
    r = expense(cash_u, 1000)                                                             # dépense du jour : période clôturée
    assert r["status"] == "invalid" and "clôturée" in r["message"]
    late = expense(owner, 25_000)                                                         # le propriétaire peut, mais c'est signalé
    assert late["status"] == "ok" and late["closed_period_warning"] is True
    assert pull(owner)["cash_closings"][0]["drift"] == -25_000                            # la clôture ne correspond plus aux opérations
    assert any("ATTENTION" in (e["note"] or "") for e in owner.get("/api/audit?action=expense.create").json()["entries"])


def test_mouvements_regles_par_role(owner, app_env):
    cash_u, _ = make_user(owner, app_env, "caisse", "cashier")
    compta, _ = make_user(owner, app_env, "compta", "accountant")
    close(owner, 0, opening=0, close_date=day(1))                                          # caisse ouverte à zéro la veille
    mv = lambda c, kind, amount, note="bordereau": push(c, op("cash.move", {"kind": kind, "amount": amount, "note": note}))[0]
    assert mv(cash_u, "owner_out", 10)["status"] == "denied" and mv(cash_u, "bank_withdrawal", 10)["status"] == "denied"
    assert mv(owner, "owner_in", 500_000, "apport du patron")["status"] == "ok"
    assert mv(cash_u, "bank_deposit", 100_000)["status"] == "ok"
    assert "n'a que" in mv(cash_u, "bank_deposit", 9_000_000)["message"]
    assert "note" in mv(compta, "bank_withdrawal", 5, "")["message"].lower()
    m = pull(owner)["cash_movements"][0]
    assert push(cash_u, op("cash.move_cancel", {"id": m["id"], "base_version": 1, "reason": "erreur"}))[0]["status"] == "denied"
    assert push(compta, op("cash.move_cancel", {"id": m["id"], "base_version": 1, "reason": "apport annulé"}))[0]["status"] == "ok"
    assert cash(owner)["in"]["owner_in"] == 0


def test_reouverture_reservee_au_proprietaire_et_a_la_derniere_cloture(owner, app_env):
    compta, _ = make_user(owner, app_env, "compta", "accountant")
    x = close(owner, 1000, opening=1000)["closing"]
    assert push(compta, op("cash.cancel_close", {"id": x["id"], "base_version": 1, "reason": "erreur de comptage"}))[0]["status"] == "denied"
    assert push(owner, op("cash.cancel_close", {"id": x["id"], "base_version": 1, "reason": "erreur de comptage"}))[0]["status"] == "ok"
    assert cash(owner)["needs_opening"] is True
    assert close(owner, 2000, opening=2000)["status"] == "ok"                              # on peut refaire la journée
    assert sorted(c["status"] for c in pull(owner)["cash_closings"]) == ["active", "cancelled"]


def test_budgets_et_visibilite(owner, app_env):
    compta, _ = make_user(owner, app_env, "compta", "accountant")
    marie, _ = make_user(owner, app_env, "marie", "sales")
    cashier, _ = make_user(owner, app_env, "caisse", "cashier")
    assert push(compta, op("budget.set", {"category": "Loyer et charges locatives", "amount": 2_000_000}))[0]["status"] == "ok"
    assert push(marie, op("budget.set", {"category": "Assurances", "amount": 1}))[0]["status"] == "denied"
    assert "inconnue" in push(owner, op("budget.set", {"category": "Zzz", "amount": 1}))[0]["message"]
    assert pull(owner)["budgets"] == {"Loyer et charges locatives": 2_000_000} and pull(cashier)["budgets"] == {} and pull(marie)["budgets"] == {}
    assert len(pull(owner)["expense_categories"]) == 17          # 15 + les deux catégories réservées à la paie


def test_seuil_de_rentabilite_du_mois(owner, app_env):
    pid, cid = product(owner, price=100_000), client(owner)
    sup = push(owner, op("supplier.create", {"name": "F"}))[0]["supplier"]["id"]
    push(owner, op("purchase.create", {"supplier_id": sup, "kind": "cash", "pay_method": "Espèces", "lines": [{"product_id": pid, "qty": 100, "unit_cost": 70_000}]}))
    sell(owner, cid, pid, 10)                                                             # 1 000 000 de ventes, coût estimé 700 000, marge 30 %
    expense(owner, 150_000, category="Loyer et charges locatives")                        # fixe
    expense(owner, 50_000, category="Transport et livraison")                             # variable
    r = pull(owner)["rentability"]
    assert r["sales"] == 1_000_000 and r["estimated_cost"] == 700_000 and r["margin_rate"] == 0.3 and r["fixed"] == 150_000 and r["variable"] == 50_000
    assert r["breakeven"] == 500_000 and r["reached"] == 2.0 and r["result"] == 1_000_000 - 700_000 - 150_000 - 50_000
    cashier, _ = make_user(owner, app_env, "caisse", "cashier")
    assert pull(cashier)["rentability"] is None


def test_journal_et_chaine_apres_la_caisse(owner):
    expense(owner); close(owner, 0, opening=0)
    assert owner.get("/api/audit/verify").json()["ok"] is True
