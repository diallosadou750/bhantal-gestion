import uuid
from datetime import date, timedelta

from conftest import make_user, op, push

TODAY = date.today()
T = TODAY.isoformat()


def day(n):
    return (TODAY - timedelta(days=n)).isoformat()


def rep(c, kind="statements", **params):
    q = "&".join(f"{k}={v}" for k, v in {"kind": kind, "from": "2000-01-01", "to": T, **params}.items())
    r = c.get("/api/accounting/report?" + q)
    assert r.status_code == 200, r.text
    return r.json()


def bal(c, **params):
    return {r["account"]: r for r in rep(c, "balance", **params)["rows"]}


def setup(c):
    pid = push(c, op("product.create", {"id": str(uuid.uuid4()), "name": "Ciment", "price": 100_000}))[0]["product"]["id"]
    sup = push(c, op("supplier.create", {"id": str(uuid.uuid4()), "name": "F"}))[0]["supplier"]["id"]
    cid = push(c, op("client.create", {"id": str(uuid.uuid4()), "name": "Camara", "credit_limit": 50_000_000}))[0]["client"]["id"]
    return pid, sup, cid


def scenario(c):
    pid, sup, cid = setup(c)
    push(c, op("purchase.create", {"supplier_id": sup, "kind": "cash", "pay_method": "Espèces", "purchase_date": day(5), "lines": [{"product_id": pid, "qty": 100, "unit_cost": 60_000}]}))
    s = push(c, op("sale.create", {"client_id": cid, "kind": "credit", "sale_date": day(3), "due_date": (TODAY + timedelta(days=20)).isoformat(), "paid_at_sale": 200_000, "pay_method": "Virement bancaire", "lines": [{"product_id": pid, "qty": 10}]}))[0]["sale"]
    push(c, op("payment.create", {"sale_id": s["id"], "amount": 300_000, "method": "Orange Money", "pay_date": day(2)}))
    push(c, op("expense.create", {"category": "Transport et livraison", "amount": 50_000, "method": "Espèces", "payee": "Chauffeur", "expense_date": day(1)}))
    push(c, op("cash.move", {"kind": "owner_in", "amount": 500_000, "note": "apport du patron", "move_date": day(1)}))
    return pid, sup, cid, s


def test_cycle_complet_equilibre_resultat_et_bilan(owner):
    scenario(owner)
    b = rep(owner, "balance")
    assert b["balanced"] is True and b["totals"]["md"] == b["totals"]["mc"]
    rows = {r["account"]: r for r in b["rows"]}
    assert rows["411"]["close_debit"] == 500_000 and rows["521"]["close_debit"] == 200_000 and rows["5211"]["close_debit"] == 300_000 and rows["462"]["close_credit"] == 500_000
    assert rows["701"]["credit"] == 1_000_000 and rows["601"]["debit"] == 6_000_000 and rows["612"]["debit"] == 50_000
    assert rows["311"]["close_debit"] == 5_400_000 and rows["603"]["credit"] == 5_400_000                   # 90 sacs restants valorisés à 60 000
    st = rep(owner)
    inc, bs = st["income"], st["balance_sheet"]
    assert inc["sales"] == 1_000_000 and inc["charges_total"] == 650_000 and inc["result"] == 350_000 and inc["gross_margin"] == 400_000   # (100 000 − 60 000) × 10
    assert bs["balanced"] is True and bs["assets_total"] == bs["liabilities_total"] == 6_400_000
    assert any(x["account"] == "130" and x["amount"] == 350_000 for x in bs["liabilities"])


def test_tva_ventes_achats_et_reglages(owner):
    pid, sup, cid = setup(owner)
    assert push(owner, op("settings.update", {"tax_rate": 18}))[0]["status"] == "ok"
    assert push(owner, op("accounting.settings", {"purchase_tax_rate": 18}))[0]["status"] == "ok"
    push(owner, op("sale.create", {"client_id": cid, "kind": "cash", "pay_method": "Espèces", "lines": [{"name": "Article", "qty": 1, "unit_price": 1_180_000}]}))
    push(owner, op("purchase.create", {"supplier_id": sup, "kind": "cash", "pay_method": "Espèces", "lines": [{"product_id": pid, "qty": 10, "unit_cost": 59_000}]}))
    v = rep(owner)["vat"]
    assert v["collected"] == 180_000 and v["deductible"] == 90_000 and v["due"] == 90_000 and v["rows"][0]["month"] == T[:7] and v["sales_rate"] == 18 and v["purchase_rate"] == 18
    b = bal(owner)
    assert b["701"]["credit"] == 1_000_000 and b["4431"]["credit"] == 180_000 and b["601"]["debit"] == 500_000 and b["4452"]["debit"] == 90_000 and b["401"]["debit"] == 590_000 and b["401"]["close_debit"] == 0


def test_annulation_retire_les_ecritures_et_avoir(owner):
    pid, sup, cid, s = scenario(owner)
    push(owner, op("creditnote.create", {"sale_id": s["id"], "amount": 100_000, "settlement": "deduct", "reason": "sac abîmé"}))
    b = bal(owner)
    assert b["701"]["credit"] == 1_000_000 and b["701"]["debit"] == 100_000 and b["411"]["close_debit"] == 400_000
    exp = [e for e in rep(owner, "journal")["entries"] if e["label"].startswith("Dépense")][0]
    assert exp["journal"] == "CA" and exp["lines"][0]["account"] == "612"
    e = [x for x in owner.get("/api/sync/pull?since=0").json()["expenses"]][0]
    push(owner, op("expense.cancel", {"id": e["id"], "base_version": 1, "reason": "saisie en double"}))
    assert "612" not in bal(owner)                                                                           # une dépense annulée ne produit plus d'écriture
    assert rep(owner, "balance")["balanced"] is True


def test_ecritures_manuelles_regles_droits_et_annulation(owner, app_env):
    compta, _ = make_user(owner, app_env, "compta", "accountant")
    marie, _ = make_user(owner, app_env, "marie", "sales")
    line = lambda a, dr=0, cr=0: {"account": a, "debit": dr, "credit": cr}
    mk = lambda c, lines, label="Apport en capital": push(c, op("journal.create", {"id": str(uuid.uuid4()), "entry_date": day(2), "label": label, "lines": lines}))[0]
    assert mk(marie, [line("521", 100), line("101", 0, 100)])["status"] == "denied"
    r = mk(compta, [line("521", 5_000_000), line("101", 0, 5_000_000)])
    assert r["status"] == "ok" and r["number"] == "OD-0001"
    assert "déséquilibrée" in mk(compta, [line("521", 100), line("101", 0, 90)])["message"]
    assert "inconnu" in mk(compta, [line("999", 100), line("101", 0, 100)])["message"]
    assert "soit un débit, soit un crédit" in mk(compta, [line("521", 100, 100), line("101", 0, 0)])["message"]
    assert mk(compta, [line("521", 100)])["status"] == "invalid"
    assert bal(owner)["101"]["close_credit"] == 5_000_000
    ent = [e for e in owner.get("/api/sync/pull?since=0").json()["journal_entries"]][0]
    assert push(compta, op("journal.cancel", {"id": ent["id"], "base_version": 1, "reason": ""}))[0]["status"] == "invalid"
    assert push(compta, op("journal.cancel", {"id": ent["id"], "base_version": 1, "reason": "montant erroné"}))[0]["status"] == "ok"
    assert "101" not in bal(owner)
    assert any(e["action"] == "journal.create" and e["user"] == "compta" for e in owner.get("/api/audit").json()["entries"])


def test_verrou_de_periode(owner, app_env):
    compta, _ = make_user(owner, app_env, "compta", "accountant")
    pid, sup, cid = setup(owner)
    old = push(owner, op("sale.create", {"client_id": cid, "kind": "cash", "pay_method": "Espèces", "sale_date": day(4), "lines": [{"product_id": pid, "qty": 1}]}))[0]["sale"]
    assert push(compta, op("accounting.lock", {"through": day(2)}))[0]["status"] == "denied"
    assert push(owner, op("accounting.lock", {"through": (TODAY + timedelta(days=3)).isoformat()}))[0]["status"] == "invalid"
    assert push(owner, op("accounting.lock", {"through": day(2)}))[0]["status"] == "ok"
    refused = push(owner, op("sale.create", {"client_id": cid, "kind": "cash", "pay_method": "Espèces", "sale_date": day(3), "lines": [{"product_id": pid, "qty": 1}]}))[0]
    assert refused["status"] == "invalid" and "verrouillée jusqu'au" in refused["message"]
    assert push(owner, op("sale.cancel", {"id": old["id"], "base_version": 1, "reason": "test"}))[0]["status"] == "invalid"
    assert push(owner, op("expense.create", {"category": "Autres charges", "amount": 10, "method": "Espèces", "payee": "x", "expense_date": day(3)}))[0]["status"] == "invalid"
    assert push(compta, op("journal.create", {"entry_date": day(3), "label": "x", "lines": [{"account": "521", "debit": 5}, {"account": "101", "credit": 5}]}))[0]["status"] == "invalid"
    assert push(owner, op("sale.create", {"client_id": cid, "kind": "cash", "pay_method": "Espèces", "lines": [{"product_id": pid, "qty": 1}]}))[0]["status"] == "ok"      # aujourd'hui reste ouvert
    assert push(compta, op("accounting.settings", {"purchase_tax_rate": 5}))[0]["status"] == "invalid"
    assert "motif" in push(owner, op("accounting.lock", {"through": ""}))[0]["message"].lower()
    assert push(owner, op("accounting.lock", {"through": "", "reason": "correction convenue avec l'expert-comptable"}))[0]["status"] == "ok"
    assert push(owner, op("sale.cancel", {"id": old["id"], "base_version": 1, "reason": "test"}))[0]["status"] == "ok"
    assert [e["action"] for e in owner.get("/api/audit?action=accounting.lock").json()["entries"]].count("accounting.lock") == 2


def test_soldes_d_ouverture_repris_des_modules(owner):
    push(owner, op("bank.opening", {"account": "bank", "balance": 3_000_000, "date": day(30)}))
    push(owner, op("cash.close", {"close_date": day(1), "counted": 400_000, "opening": 400_000}))
    b = bal(owner)
    assert b["521"]["close_debit"] == 3_000_000 and b["571"]["close_debit"] == 400_000 and b["101"]["close_credit"] == 3_400_000
    assert rep(owner, "balance")["balanced"] is True
    assert {e["journal"] for e in rep(owner, "journal")["entries"]} == {"OD"}


def test_grand_livre_et_periode_avec_ouverture(owner):
    scenario(owner)
    led = rep(owner, "ledger", account="411")
    assert [r["debit"] or -r["credit"] for r in led["rows"]] == [1_000_000, -200_000, -300_000] and led["closing"] == 500_000 and led["rows"][-1]["balance"] == 500_000
    part = rep(owner, "ledger", account="411", **{"from": day(2)})
    assert part["opening"] == 800_000 and part["closing"] == 500_000 and len(part["rows"]) == 1
    b = bal(owner, **{"from": day(1)})
    assert b["411"]["open_debit"] == 500_000 and b["411"]["debit"] == 0
    assert rep(owner, "balance", **{"from": day(1)})["balanced"] is True


def test_plan_comptable_et_correspondances(owner, app_env):
    compta, _ = make_user(owner, app_env, "compta", "accountant")
    ac = lambda code, name="Compte": push(compta, op("account.create", {"code": code, "name": name}))[0]
    assert ac("52101", "Banque principale")["status"] == "ok"
    assert ac("52101")["status"] == "invalid" and ac("9999")["status"] == "invalid" and ac("12")["status"] == "invalid"
    assert len(owner.get("/api/sync/pull?since=0").json()["accounting"]["accounts"]) >= 85
    assert push(owner, op("accounting.settings", {"map": {"sales": "999999"}}))[0]["status"] == "invalid"
    bad = push(owner, op("accounting.settings", {"map": {"sales": "12345678"}}))[0]
    assert bad["status"] == "invalid" and "n'existe pas" in bad["message"]
    pid, sup, cid = setup(owner)
    assert push(owner, op("accounting.settings", {"map": {"sales": "702"}}))[0]["status"] == "ok"
    push(owner, op("sale.create", {"client_id": cid, "kind": "cash", "pay_method": "Espèces", "lines": [{"product_id": pid, "qty": 1}]}))
    b = bal(owner)
    assert "702" in b and "701" not in b


def test_acces_export_csv_et_journal(owner, app_env):
    scenario(owner)
    for role in ("cashier", "sales"):
        u, _ = make_user(owner, app_env, "u_" + role, role)
        assert u.get("/api/accounting/report?kind=balance").status_code == 403
        assert u.get("/api/sync/pull?since=0").json()["accounting"] is None
    r = owner.get(f"/api/accounting/export.csv?kind=journal&from=2000-01-01&to={T}")
    assert r.status_code == 200 and "text/csv" in r.headers["content-type"] and "Vente FV-0001" in r.text
    assert owner.get("/api/accounting/export.csv?kind=statements").status_code == 422
    assert owner.get("/api/accounting/report?kind=balance&from=2026-13-01&to=2026-01-01").status_code == 422
    assert any(e["action"] == "accounting.exported" for e in owner.get("/api/audit").json()["entries"])
    assert owner.get("/api/audit/verify").json()["ok"] is True
