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


def client(c):
    return push(c, op("client.create", {"id": str(uuid.uuid4()), "name": "Client Camara", "credit_limit": 50_000_000}))[0]["client"]["id"]


def product(c):
    return push(c, op("product.create", {"id": str(uuid.uuid4()), "name": "Ciment", "price": 100_000}))[0]["product"]["id"]


def credit_sale(c, cid, pid, qty, due=20):
    r = push(c, op("sale.create", {"id": str(uuid.uuid4()), "client_id": cid, "kind": "credit", "due_date": (TODAY + timedelta(days=due)).isoformat(), "lines": [{"product_id": pid, "qty": qty}]}))[0]
    assert r["status"] == "ok", r
    return r["sale"]


def pay(c, sale, amount, method="Virement bancaire", ref="", pay_date=None):
    r = push(c, op("payment.create", {"id": str(uuid.uuid4()), "sale_id": sale["id"], "amount": amount, "method": method, "reference": ref, **({"pay_date": pay_date} if pay_date else {})}))[0]
    assert r["status"] == "ok", r
    return r["payment"]


def opening(c, account="bank", balance=10_000_000, d=None):
    return push(c, op("bank.opening", {"account": account, "balance": balance, "date": d or day(60)}))[0]


def imp(c, lines, account="bank"):
    return push(c, op("bank.import", {"account": account, "lines": lines}))[0]


def ln(d, amount, label="", ref=""):
    return {"date": d, "amount": amount, "label": label, "reference": ref}


def lines(c, account="bank"):
    return [x for x in pull(c)["bank_lines"] if x["account"] == account]


def items(c, account="bank"):
    return pull(c)["bank_items"][account]


def test_droits_et_aucune_donnee_bancaire_hors_comptable(owner, app_env):
    compta, _ = make_user(owner, app_env, "compta", "accountant")
    assert opening(compta)["status"] == "ok"
    for role in ("cashier", "sales"):
        u, _ = make_user(owner, app_env, "u_" + role, role)
        assert opening(u)["status"] == "denied" and imp(u, [ln(T, 1, "x")])["status"] == "denied"
        r = pull(u)
        assert r["bank_lines"] == [] and r["bank_items"] == {} and r["bank_meta"] == {}
    assert set(pull(compta)["bank_items"]) == {"bank", "orange", "mtn"}


def test_le_livre_distingue_banque_mobile_money_et_especes(owner):
    cid, pid = client(owner), product(owner)
    s = credit_sale(owner, cid, pid, 20)
    pay(owner, s, 100_000, "Virement bancaire"); pay(owner, s, 200_000, "Chèque"); pay(owner, s, 300_000, "Orange Money"); pay(owner, s, 400_000, "MTN MoMo"); pay(owner, s, 500_000, "Espèces")
    push(owner, op("expense.create", {"category": "Frais bancaires", "amount": 15_000, "method": "Virement bancaire", "payee": "Banque"}))
    push(owner, op("cash.move", {"kind": "bank_deposit", "amount": 50_000, "note": "bordereau 1"}))
    assert sorted(i["amount"] for i in items(owner, "bank")) == [-15_000, 50_000, 100_000, 200_000]
    assert [i["amount"] for i in items(owner, "orange")] == [300_000] and [i["amount"] for i in items(owner, "mtn")] == [400_000]


def test_import_idempotent_et_lignes_identiques(owner):
    opening(owner)
    batch = [ln(day(5), 100_000, "VIR CAMARA", "R1"), ln(day(4), 25_000, "Frais"), ln(day(4), 25_000, "Frais")]       # deux frais identiques le même jour : deux lignes
    r = imp(owner, batch)
    assert r["created"] == 3 and r["skipped"] == 0
    again = imp(owner, batch)
    assert again["created"] == 0 and again["skipped"] == 3 and len(lines(owner)) == 3
    assert imp(owner, [ln("pas une date", 5)])["status"] == "invalid"
    bad = imp(owner, [ln(day(1), 10, "ok"), ln(day(1), 0, "nul")])
    assert bad["created"] == 1 and any("montant nul" in e for e in bad["errors"])


def test_pointage_automatique_prudent(owner):
    cid, pid = client(owner), product(owner)
    s = credit_sale(owner, cid, pid, 30)
    p1 = pay(owner, s, 100_000, ref="VIR-A", pay_date=day(5))
    p2 = pay(owner, s, 100_000, ref="VIR-B", pay_date=day(5))                  # même montant, même jour : ambigu sans la référence
    p3 = pay(owner, s, 70_000, ref="", pay_date=day(3))
    opening(owner)
    imp(owner, [ln(day(5), 100_000, "VIREMENT RECU", "VIR-B"), ln(day(4), 100_000, "VIREMENT RECU"), ln(day(3), 70_000, "VIR"), ln(day(2), 9_999, "INCONNU")])
    r = push(owner, op("bank.automatch", {"account": "bank"}))[0]
    assert r["status"] == "ok" and r["matched"] == 3 and r["ambiguous"] == 0     # VIR-B par la référence ; puis VIR-A devient le seul candidat restant ; le 70 000 est unique
    by = {x["amount"]: x for x in lines(owner)}
    assert by[70_000]["status"] == "matched" and by[9_999]["status"] == "open"
    matched_ids = {m["id"] for x in lines(owner) for m in x["matches"]}
    assert {p1["id"], p2["id"], p3["id"]} == matched_ids
    by_line = {x["reference"] or x["label"]: x for x in lines(owner)}
    assert by_line["VIR-B"]["matches"][0]["id"] == p2["id"]                         # la référence a départagé les deux 100 000


def test_pointage_manuel_somme_exacte_et_unicite(owner):
    cid, pid = client(owner), product(owner)
    s = credit_sale(owner, cid, pid, 30)
    a, b = pay(owner, s, 60_000, pay_date=day(3)), pay(owner, s, 40_000, pay_date=day(3))
    opening(owner)
    imp(owner, [ln(day(2), 100_000, "REMISE GROUPEE"), ln(day(2), 60_000, "AUTRE")])
    big, small = sorted(lines(owner), key=lambda x: -x["amount"])
    m = lambda line, *its: push(owner, op("bank.match", {"line_id": line["id"], "base_version": line["version"], "items": [{"type": "payment", "id": i["id"]} for i in its]}))[0]
    r = m(big, a)
    assert r["status"] == "invalid" and "écart de 40000" in r["message"]
    assert m(big, a, b)["status"] == "ok"
    r2 = m(lines(owner)[1] if lines(owner)[1]["amount"] == 60_000 else lines(owner)[0], a)
    assert r2["status"] == "invalid" and "déjà pointée" in r2["message"]
    assert items(owner) == []


def test_creation_de_depense_depuis_le_releve(owner):
    opening(owner)
    imp(owner, [ln(day(2), -15_000, "FRAIS DE TENUE DE COMPTE", "F123"), ln(day(2), 750_000, "VIREMENT INCONNU")])
    neg = [x for x in lines(owner) if x["amount"] < 0][0]; pos = [x for x in lines(owner) if x["amount"] > 0][0]
    assert "débit" in push(owner, op("bank.create_expense", {"line_id": pos["id"], "base_version": 1, "category": "Frais bancaires"}))[0]["message"]
    r = push(owner, op("bank.create_expense", {"line_id": neg["id"], "base_version": 1, "category": "Frais bancaires"}))[0]
    assert r["status"] == "ok" and r["expense"]["amount"] == 15_000 and r["expense"]["method"] == "Virement bancaire" and r["expense"]["reference"] == "F123" and r["expense"]["expense_date"] == day(2)
    assert [x["status"] for x in lines(owner) if x["amount"] < 0] == ["matched"] and items(owner) == []
    flag = push(owner, op("bank.flag", {"line_id": pos["id"], "base_version": 1, "note": "à demander à la banque"}))[0]
    assert flag["status"] == "ok" and [x for x in lines(owner) if x["amount"] > 0][0]["status"] == "flagged"


def test_retrait_et_depointage_avec_motif(owner):
    cid, pid = client(owner), product(owner)
    p = pay(owner, credit_sale(owner, cid, pid, 5), 50_000, pay_date=day(3))
    opening(owner); imp(owner, [ln(day(3), 50_000, "VIR"), ln(day(1), 5, "erreur d'import")])
    push(owner, op("bank.automatch", {"account": "bank"}))
    ls = {x["amount"]: x for x in lines(owner)}
    assert "motif" in push(owner, op("bank.unmatch", {"line_id": ls[50_000]["id"], "base_version": ls[50_000]["version"], "reason": ""}))[0]["message"].lower()
    r = push(owner, op("bank.unmatch", {"line_id": ls[50_000]["id"], "base_version": ls[50_000]["version"], "reason": "mauvais client"}))[0]
    assert r["status"] == "ok" and len(items(owner)) == 1
    assert push(owner, op("bank.void", {"line_id": ls[50_000]["id"], "base_version": ls[50_000]["version"] + 1, "reason": "doublon de relevé"}))[0]["status"] == "ok"
    assert [x["status"] for x in lines(owner) if x["amount"] == 50_000] == ["void"]
    assert push(owner, op("bank.void", {"line_id": ls[5]["id"], "base_version": 1, "reason": "erreur d'import"}))[0]["status"] == "ok"


def test_rapprochement_equilibre_ecart_et_verrouillage(owner, app_env):
    compta, _ = make_user(owner, app_env, "compta", "accountant")
    cid, pid = client(owner), product(owner)
    s = credit_sale(owner, cid, pid, 30)
    pay(owner, s, 100_000, pay_date=day(5))                                           # au relevé
    pay(owner, s, 40_000, method="Chèque", pay_date=day(2))                           # chèque remis, pas encore crédité : en transit
    opening(owner, balance=1_000_000, d=day(30))
    imp(owner, [ln(day(5), 100_000, "VIR CAMARA"), ln(day(1), -12_000, "FRAIS")])
    push(owner, op("bank.automatch", {"account": "bank"}))
    fees = [x for x in lines(owner) if x["amount"] < 0][0]
    push(owner, op("bank.create_expense", {"line_id": fees["id"], "base_version": fees["version"], "category": "Frais bancaires"}))
    # livre = 1 000 000 + 100 000 + 40 000 − 12 000 = 1 128 000 ; relevé = 1 000 000 + 100 000 − 12 000 = 1 088 000 ; en transit : le chèque de 40 000
    r = push(owner, op("bank.reconcile", {"account": "bank", "date": T, "statement_balance": 1_088_000}))[0]
    assert r["status"] == "ok" and r["computation"]["book_balance"] == 1_128_000 and r["computation"]["book_only"] == 40_000 and r["computation"]["difference"] == 0
    bad = push(owner, op("bank.reconcile", {"account": "bank", "date": T, "statement_balance": 1_000_000}))[0]
    assert "Écart de" in bad["message"] and bad["computation"]["difference"] == -88_000
    ok = push(owner, op("bank.reconcile", {"account": "bank", "date": T, "statement_balance": 1_000_000, "note": "relevé incomplet : en attente de la banque"}))[0]
    assert ok["status"] == "ok" and ok["reconciliation"]["difference"] == -88_000
    e = owner.get("/api/audit?action=bank.reconcile").json()["entries"]
    assert any(x["after"]["écart"] == 0 and x["note"] == "équilibré" for x in e) and any("ÉCART" in (x["note"] or "") for x in e)
    # une ligne pointée dans la période rapprochée (équilibrée) ne se dépointe plus que par le propriétaire
    matched = [x for x in lines(owner) if x["status"] == "matched" and x["amount"] > 0][0]
    denied = push(compta, op("bank.unmatch", {"line_id": matched["id"], "base_version": matched["version"], "reason": "test de verrou"}))[0]
    assert denied["status"] == "invalid" and "propriétaire" in denied["message"]
    assert push(owner, op("bank.unmatch", {"line_id": matched["id"], "base_version": matched["version"], "reason": "correction du propriétaire"}))[0]["status"] == "ok"
    assert opening(owner, balance=5)["status"] == "invalid"                           # le solde d'ouverture ne bouge plus après un rapprochement


def test_operation_annulee_apres_pointage_est_signalee(owner):
    cid, pid = client(owner), product(owner)
    s = credit_sale(owner, cid, pid, 5)
    p = pay(owner, s, 50_000, pay_date=day(3))
    opening(owner); imp(owner, [ln(day(3), 50_000, "VIR")])
    push(owner, op("bank.automatch", {"account": "bank"}))
    before = pull(owner)
    push(owner, op("payment.cancel", {"id": p["id"], "base_version": 1, "reason": "erreur de saisie"}))
    after = pull(owner, before["cursor"])
    flagged = [x for x in after["bank_lines"] if x["matches"] and not all(m["active"] for m in x["matches"])]
    assert len(flagged) == 1 and flagged[0]["status"] == "matched"                    # la ligne remonte avec « opération annulée » même si elle n'a pas bougé
    assert pull(owner)["bank_status"]["bank"]["book_balance"] == 10_000_000


def test_import_hors_comptes_inconnus_et_journal(owner):
    assert imp(owner, [ln(T, 1)], account="compte_x")["status"] == "invalid"
    opening(owner); imp(owner, [ln(day(1), 10, "x")])
    assert any(e["action"] == "bank.import" for e in owner.get("/api/audit").json()["entries"]) and owner.get("/api/audit/verify").json()["ok"] is True
