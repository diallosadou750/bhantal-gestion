from datetime import date

from conftest import make_user

MONTH = date.today().isoformat()[:7]


def employe(c, name="Mariama", salary=1_000_000, **kw):
    r = c.post("/api/payroll/employees", json={"name": name, "position": "Vendeuse", "base_salary": salary, **kw})
    assert r.status_code == 200, r.text
    return r.json()["employee"]


def reglages(c, emp=500, er=1800, ceiling=0):
    r = c.post("/api/payroll/settings", json={"employee_bp": emp, "employer_bp": er, "ceiling": ceiling})
    assert r.status_code == 200, r.text


def bulletin(c, eid, **kw):
    return c.post("/api/payroll/slips", json={"employee_id": eid, "period": kw.pop("period", MONTH), **kw})


def test_calcul_et_figeage_des_taux(owner):
    reglages(owner, 500, 1800)                                   # 5 % salarial, 18 % patronal (valeurs de test : aucun taux légal n'est codé en dur)
    e = employe(owner)
    r = bulletin(owner, e["id"], bonus=200_000, its=30_000)
    assert r.status_code == 200, r.text
    s = r.json()["slip"]
    assert s["number"] == "BP-0001" and s["gross"] == 1_200_000 and s["social_employee"] == 60_000 and s["social_employer"] == 216_000
    assert s["net"] == 1_200_000 - 60_000 - 30_000 and s["employer_cost"] == 1_416_000 and s["paid"] is False
    reglages(owner, 700, 2000)                                   # un changement de taux ne touche pas le bulletin déjà fait
    ov = owner.get("/api/payroll?month=" + MONTH).json()
    assert ov["slips"][0]["social_employee"] == 60_000 and ov["settings"]["employee_bp"] == 700
    assert ov["totals"]["net"] == s["net"] and ov["to_generate"] == []


def test_plafond_et_taux_a_zero(owner):
    e = employe(owner, salary=3_000_000)
    s = bulletin(owner, e["id"]).json()["slip"]
    assert s["social_employee"] == 0 and s["net"] == 3_000_000
    assert owner.get("/api/payroll").json()["rates_missing"] is True
    reglages(owner, 500, 1800, ceiling=2_000_000)
    e2 = employe(owner, "Alpha", salary=3_000_000)
    s2 = bulletin(owner, e2["id"]).json()["slip"]
    assert s2["social_employee"] == 100_000 and s2["social_employer"] == 360_000       # calculé sur le plafond, pas sur le brut


def test_validations(owner):
    e = employe(owner)
    assert bulletin(owner, e["id"], period="2020-13").status_code == 422
    assert bulletin(owner, e["id"], period="2099-01").status_code == 422
    assert bulletin(owner, e["id"], its=5_000_000).status_code == 422           # retenues > brut
    assert bulletin(owner, "inconnu").status_code == 422
    assert bulletin(owner, e["id"]).status_code == 200
    assert bulletin(owner, e["id"]).status_code == 422                          # un seul bulletin actif par mois
    assert owner.post("/api/payroll/employees", json={"name": "", "base_salary": 1}).status_code == 422
    assert owner.post("/api/payroll/settings", json={"employee_bp": 9999, "employer_bp": 0}).status_code == 422


def test_generation_en_lot(owner):
    a, b = employe(owner, "A", 500_000), employe(owner, "B", 700_000)
    owner.post("/api/payroll/employees/" + b["id"], json={"active": False})
    r = owner.post("/api/payroll/generate", json={"period": MONTH}).json()
    assert len(r["created"]) == 1 and r["blocked"] == []
    assert owner.post("/api/payroll/generate", json={"period": MONTH}).json()["created"] == []


def test_paiement_depense_et_annulation(owner):
    reglages(owner)
    e = employe(owner)
    s = bulletin(owner, e["id"]).json()["slip"]
    assert owner.post(f"/api/payroll/slips/{s['id']}/pay", json={"method": "Troc"}).status_code == 422
    r = owner.post(f"/api/payroll/slips/{s['id']}/pay", json={"method": "Espèces"})
    assert r.status_code == 200, r.text
    p = r.json()["slip"]
    assert p["paid"] and p["expense_number"].startswith("DP-") and p["pay_method"] == "Espèces"
    exp = [x for x in owner.get("/api/sync/pull?since=0").json()["expenses"] if x["number"] == p["expense_number"]][0]
    assert exp["amount"] == s["net"] and exp["category"] == "Paie : salaires nets versés" and exp["payee"] == "Mariama"
    assert owner.post(f"/api/payroll/slips/{s['id']}/pay", json={"method": "Espèces"}).status_code == 422       # déjà payé
    assert owner.post(f"/api/payroll/slips/{s['id']}/cancel", json={"reason": "x"}).status_code == 422          # motif trop court
    c = owner.post(f"/api/payroll/slips/{s['id']}/cancel", json={"reason": "Erreur de salaire"})
    assert c.status_code == 200 and c.json()["slip"]["status"] == "cancelled"
    assert [x for x in owner.get("/api/sync/pull?since=0").json()["expenses"] if x["number"] == p["expense_number"]][0]["status"] == "cancelled"
    assert bulletin(owner, e["id"]).status_code == 200                           # le mois peut être refait


def test_paiement_annule_a_part_remet_le_bulletin_a_payer(owner):
    e = employe(owner)
    s = bulletin(owner, e["id"]).json()["slip"]
    p = owner.post(f"/api/payroll/slips/{s['id']}/pay", json={"method": "Espèces"}).json()["slip"]
    exp = [x for x in owner.get("/api/sync/pull?since=0").json()["expenses"] if x["number"] == p["expense_number"]][0]
    from conftest import op, push
    assert push(owner, op("expense.cancel", {"id": exp["id"], "base_version": exp["version"], "reason": "erreur de saisie"}))[0]["status"] == "ok"
    again = owner.get("/api/payroll?month=" + MONTH).json()["slips"][0]
    assert again["paid"] is False
    assert owner.post(f"/api/payroll/slips/{s['id']}/pay", json={"method": "Virement bancaire"}).json()["slip"]["paid"] is True


def test_comptabilite_equilibree(owner):
    reglages(owner, 500, 1800)
    e = employe(owner)
    s = bulletin(owner, e["id"], bonus=200_000, its=30_000).json()["slip"]
    owner.post(f"/api/payroll/slips/{s['id']}/pay", json={"method": "Espèces"})
    j = owner.get("/api/accounting/report?kind=journal")
    assert j.status_code == 200, j.text
    ents = [x for x in j.json()["entries"] if x["ref"] == s["number"]]
    assert len(ents) == 1
    l = {x["account"]: (x["debit"], x["credit"]) for x in ents[0]["lines"]}
    assert l["661"] == (1_200_000, 0) and l["664"] == (216_000, 0) and l["422"] == (0, s["net"]) and l["431"] == (0, 60_000 + 216_000) and l["4471"] == (0, 30_000)
    assert sum(d for d, _ in l.values()) == sum(c for _, c in l.values())
    pay = [x for x in j.json()["entries"] if x["ref"].startswith("DP-")][0]
    assert {x["account"] for x in pay["lines"]} == {"422", "571"}                 # le paiement solde le personnel dû


def avance(c, eid, amount=300_000, **kw):
    return c.post("/api/payroll/advances", json={"employee_id": eid, "amount": amount, "method": "Espèces", **kw})


def employe_vue(c, eid):
    return [x for x in c.get("/api/payroll?month=" + MONTH).json()["employees"] if x["id"] == eid][0]


def test_avance_depense_reprise_et_comptabilite(owner):
    reglages(owner, 500, 1800)
    e = employe(owner)
    r = avance(owner, e["id"])
    assert r.status_code == 200, r.text
    a = r.json()["advance"]
    assert a["number"] == "AV-0001" and a["paid"] and employe_vue(owner, e["id"])["advance_balance"] == 300_000
    exp = [x for x in owner.get("/api/sync/pull?since=0").json()["expenses"] if x["number"] == a["expense_number"]][0]
    assert exp["amount"] == 300_000 and exp["category"] == "Paie : avances sur salaire"
    assert bulletin(owner, e["id"], advance=300_001).status_code == 422          # plus que ce qui est dû
    s = bulletin(owner, e["id"], advance=100_000).json()["slip"]
    assert s["advance"] == 100_000 and s["net"] == 1_000_000 - 50_000 - 100_000
    ov = owner.get("/api/payroll?month=" + MONTH).json()
    assert ov["totals"]["advances_open"] == 200_000 and ov["totals"]["advance"] == 100_000
    ents = owner.get("/api/accounting/report?kind=journal").json()["entries"]
    pay = [x for x in ents if x["ref"] == a["expense_number"]][0]
    assert {x["account"]: (x["debit"], x["credit"]) for x in pay["lines"]} == {"421": (300_000, 0), "571": (0, 300_000)}
    l = {x["account"]: (x["debit"], x["credit"]) for x in [x for x in ents if x["ref"] == s["number"]][0]["lines"]}
    assert l["421"] == (0, 100_000) and l["422"] == (0, s["net"]) and sum(d for d, _ in l.values()) == sum(c for _, c in l.values())
    assert "Avance sur salaire reprise" in owner.get("/api/payslip/" + s["id"]).text


def test_avance_reprise_ne_s_annule_pas_avant_le_bulletin(owner):
    e = employe(owner)
    a = avance(owner, e["id"]).json()["advance"]
    s = bulletin(owner, e["id"], advance=300_000).json()["slip"]
    assert owner.post(f"/api/payroll/advances/{a['id']}/cancel", json={"reason": "Erreur"}).status_code == 422
    exp = [x for x in owner.get("/api/sync/pull?since=0").json()["expenses"] if x["number"] == a["expense_number"]][0]
    from conftest import op, push
    assert push(owner, op("expense.cancel", {"id": exp["id"], "base_version": exp["version"], "reason": "erreur de saisie"}))[0]["status"] != "ok"      # même par la caisse
    assert owner.post(f"/api/payroll/slips/{s['id']}/cancel", json={"reason": "Refaire le mois"}).status_code == 200
    assert employe_vue(owner, e["id"])["advance_balance"] == 300_000            # le bulletin annulé rend l'avance due
    c = owner.post(f"/api/payroll/advances/{a['id']}/cancel", json={"reason": "Erreur"})
    assert c.status_code == 200 and c.json()["advance"]["status"] == "cancelled"
    assert employe_vue(owner, e["id"])["advance_balance"] == 0
    assert owner.post(f"/api/payroll/advances/{a['id']}/cancel", json={"reason": "Encore"}).status_code == 422


def test_preparer_tous_ne_reprend_aucune_avance(owner):
    e = employe(owner)
    avance(owner, e["id"])
    r = owner.post("/api/payroll/generate", json={"period": MONTH}).json()
    assert r["with_advance"] == ["Mariama"]
    s = owner.get("/api/payroll?month=" + MONTH).json()["slips"][0]
    assert s["advance"] == 0 and s["net"] == 1_000_000 and employe_vue(owner, e["id"])["advance_balance"] == 300_000


def test_bulletin_entierement_repris_rien_a_verser(owner):
    e = employe(owner, salary=300_000)
    avance(owner, e["id"])
    s = bulletin(owner, e["id"], advance=300_000).json()["slip"]
    assert s["net"] == 0 and "Rien à verser" in owner.get("/api/payslip/" + s["id"]).text
    assert owner.post(f"/api/payroll/slips/{s['id']}/pay", json={"method": "Espèces"}).status_code == 422


def test_ancienne_base_recoit_la_colonne_avance(owner, app_env):
    from gestion import db
    with db.connect() as conn:
        conn.execute("ALTER TABLE payslips DROP COLUMN advance")
        db._migrate(conn)
        assert "advance" in {r["name"] for r in conn.execute("PRAGMA table_info(payslips)")}


def test_registre_des_conges(owner):
    e = employe(owner)
    Y = MONTH[:4]
    conge = lambda **kw: owner.post("/api/payroll/leaves", json={"employee_id": e["id"], "kind": "Congé payé", "start": f"{Y}-02-02", "end": f"{Y}-02-06", "days": 5, **kw})
    assert conge(kind="Vacances").status_code == 422
    assert conge(end=f"{Y}-02-01").status_code == 422
    assert conge(days=6).status_code == 422                                      # plus de jours que la période
    r = conge()
    assert r.status_code == 200, r.text
    assert conge(start=f"{Y}-02-06", end=f"{Y}-02-07", days=2).status_code == 422 # chevauchement
    assert conge(start=f"{Y}-02-09", end=f"{Y}-02-09", days=1, kind="Maladie").status_code == 200
    assert employe_vue(owner, e["id"])["leave_days"] == 6
    s = bulletin(owner, e["id"]).json()["slip"]
    assert s["net"] == 1_000_000                                                  # aucun effet sur le bulletin
    assert owner.post(f"/api/payroll/leaves/{r.json()['leave']['id']}/cancel", json={"reason": "Reporté"}).status_code == 200
    assert employe_vue(owner, e["id"])["leave_days"] == 1
    assert conge().status_code == 200                                             # les dates sont libres à nouveau


def test_avances_et_conges_droits(owner, app_env):
    e = employe(owner)
    sell, _ = make_user(owner, app_env, "vendeur2", "sales"); aud, _ = make_user(owner, app_env, "expert2", "auditor")
    leave = {"employee_id": e["id"], "kind": "Maladie", "start": MONTH + "-01", "end": MONTH + "-01", "days": 1}
    for u in (sell, aud):
        assert avance(u, e["id"]).status_code == 403 and u.post("/api/payroll/leaves", json=leave).status_code == 403
    assert avance(owner, e["id"]).status_code == 200 and owner.post("/api/payroll/leaves", json=leave).status_code == 200
    assert {"advance.create", "leave.create"} <= {x["action"] for x in owner.get("/api/audit").json()["entries"]}


def test_bulletin_imprimable_et_droits(owner, app_env):
    reglages(owner)
    e = employe(owner)
    s = bulletin(owner, e["id"]).json()["slip"]
    r = owner.get("/api/payslip/" + s["id"])
    assert r.status_code == 200 and "BULLETIN DE PAIE" in r.text and "Mariama" in r.text and "Net à payer" in r.text
    assert owner.get("/api/payslip/inconnu").status_code == 404
    sell, _ = make_user(owner, app_env, "vendeur1", "sales"); caisse, _ = make_user(owner, app_env, "caisse1", "cashier")
    aud, _ = make_user(owner, app_env, "expert1", "auditor"); compta, _ = make_user(owner, app_env, "compta1", "accountant")
    for u in (sell, caisse):
        assert u.get("/api/payroll").status_code == 403 and u.get("/api/payslip/" + s["id"]).status_code == 403
        assert u.post("/api/payroll/employees", json={"name": "X", "base_salary": 1}).status_code == 403
    assert aud.get("/api/payroll").status_code == 200 and aud.get("/api/payslip/" + s["id"]).status_code == 200
    assert aud.post("/api/payroll/generate", json={"period": MONTH}).status_code == 403
    assert aud.post("/api/payroll/settings", json={"employee_bp": 1, "employer_bp": 1}).status_code == 403
    assert compta.post("/api/payroll/employees", json={"name": "Y", "base_salary": 10}).status_code == 200
    log = owner.get("/api/audit").json()["entries"]
    assert {"payslip.create", "payslip.viewed", "employee.create", "payroll.settings"} <= {x["action"] for x in log}
