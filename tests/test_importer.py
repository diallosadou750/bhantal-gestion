from datetime import date, timedelta

import pytest

openpyxl = pytest.importorskip("openpyxl")
from conftest import PW, make_user, op, push  # noqa: E402


def workbook(path, mutate=None):
    wb = openpyxl.Workbook()
    ws = wb.active; ws.title = "Clients"
    for r, row in enumerate([("C001", "Boutique Diallo", "Commerçant", "Alimentaire", "224620000001", "Madina", "Commercial 1", 5_000_000, date(2026, 8, 10), "Radio"),
                             ("C002", "Quincaillerie Camara", "Revendeur", "Construction", "224620000002", "Matoto", "Commercial 2", 15_000_000, date(2026, 7, 1), "Parrainage"),
                             ("C003", None, None, None, None, None, None, None, None, None)], 5):
        for c, v in enumerate(row, 1): ws.cell(row=r, column=c, value=v)
    ws = wb.create_sheet("Produits")
    for r, row in enumerate([("ALI-001", "Riz 50 kg", "Alimentaire", "sac", 350_000, 20, 0.01, 50, 0.02, 100, 0.03), ("CON-001", "Ciment 50 kg", "Construction", "sac", 85_000, 150, 0.02, 300, 0.03, 600, 0.04)], 5):
        for c, v in enumerate(row, 1): ws.cell(row=r, column=c, value=v)
    ws = wb.create_sheet("Ventes")
    sales = [(date(2026, 9, 5), "FV-0001-A", "C001", "Riz 50 kg", 10, "Crédit", "Orange Money", 1_000_000, date(2026, 10, 5)),
             (date(2026, 9, 5), "FV-0001-B", "C001", "Ciment 50 kg", 200, "Crédit", "Orange Money", 0, date(2026, 10, 5)),      # 2e ligne de la même facture, remise 2 %
             (date(2026, 10, 2), "FV-0007-A", "C002", "Ciment 50 kg", 4, "Comptant", "Espèces", 340_000, None)]
    for r, row in enumerate(sales, 5):
        for c, v in zip((1, 2, 3, 5, 6, 9, 10, 11, 12), row): ws.cell(row=r, column=c, value=v)
    ws = wb.create_sheet("Paiements")
    for r, row in enumerate([(date(2026, 9, 25), "C001", "FV-0001-A", 500_000, "Orange Money", "OM-1")], 5):
        for c, v in zip((1, 2, 4, 5, 6, 7), row): ws.cell(row=r, column=c, value=v)
    wb.create_sheet("Relances_Avoirs")["A5"] = date(2026, 10, 1); wb["Relances_Avoirs"]["B5"] = "C001"
    if mutate: mutate(wb)
    wb.save(path)
    return path


def db_count(table):
    from gestion import db
    with db.read() as c:
        return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_essai_a_blanc_n_ecrit_rien(owner, tmp_path):
    from gestion import importer
    rep = importer.run(workbook(tmp_path / "a.xlsx"), "patron", apply=False)
    assert rep["applied"] is False and rep["errors"] == [] and rep["counts"]["clients"]["créés"] == 2 and rep["counts"]["ventes"]["créés"] == 2 and rep["counts"]["paiements"]["créés"] == 1
    assert db_count("clients") == 0 and db_count("sales") == 0 and db_count("payments") == 0
    assert rep["not_imported"] == {} and rep["counts"]["relances"]["créés"] == 1


def test_relances_reprises_dans_la_note_du_client_sans_doublon(owner, tmp_path):
    from gestion import importer

    def relances(wb):
        ws = wb["Relances_Avoirs"]
        for c, v in zip("CDEFGH", ("Boutique", "WhatsApp", "Relance crédit", "Promesse de paiement", date(2026, 10, 18), 500_000)): ws[f"{c}5"] = v
        ws["A6"] = date(2026, 10, 2); ws["B6"] = "C999"                           # client inconnu : signalé, pas d'erreur bloquante
    f = workbook(tmp_path / "a.xlsx", relances)
    rep = importer.run(f, "patron", apply=True)
    assert rep["applied"] is True and rep["counts"]["relances"]["créés"] == 1 and any("C999" in w for w in rep["warnings"])
    note = [c for c in owner.get("/api/sync/pull?since=0").json()["clients"] if c["code"] == "C001"][0]["note"]
    assert "Relance du 2026-10-01 (WhatsApp, Relance crédit) : Promesse de paiement, promis 500000 GNF pour le 2026-10-18" in note
    importer.run(f, "patron", apply=True)
    assert [c for c in owner.get("/api/sync/pull?since=0").json()["clients"] if c["code"] == "C001"][0]["note"].count("Relance du") == 1


def test_alerte_vente_sans_stock_sans_refus(owner):
    from conftest import op, push
    cid = push(owner, op("client.create", {"name": "Client", "phone": "1"}))[0]["client"]["id"]
    pid = push(owner, op("product.create", {"name": "Riz", "price": 100}))[0]["product"]["id"]
    r = push(owner, op("sale.create", {"client_id": cid, "kind": "cash", "pay_method": "Espèces", "lines": [{"product_id": pid, "qty": 3}]}))[0]
    assert r["status"] == "ok" and "stock insuffisant (-3" in r["stock_warnings"][0]
    assert any(e["action"] == "sale.stock_warning" for e in owner.get("/api/audit").json()["entries"])
    push(owner, op("stock.adjust", {"product_id": pid, "qty": 10, "reason": "initial"}))
    r = push(owner, op("sale.create", {"client_id": cid, "kind": "cash", "pay_method": "Espèces", "lines": [{"product_id": pid, "qty": 1}]}))[0]
    assert r["stock_warnings"] == []


def test_import_reel_conserve_codes_numeros_et_totaux(owner, tmp_path):
    from gestion import importer
    rep = importer.run(workbook(tmp_path / "a.xlsx"), "patron", apply=True)
    assert rep["applied"] is True and not rep["errors"]
    r = owner.get("/api/sync/pull?since=0").json()
    assert sorted(c["code"] for c in r["clients"]) == ["C001", "C002"]
    by = {s["number"]: s for s in r["sales"]}
    assert set(by) == {"FV-0001", "FV-0007"}                                    # numéros d'origine conservés, lignes regroupées
    s1 = by["FV-0001"]
    assert len(s1["lines"]) == 2 and s1["total"] == 3_500_000 + 16_660_000 and s1["lines"][1]["discount_pct"] == 2
    assert s1["balance"] == s1["total"] - 1_000_000 - 500_000 and s1["due_date"] == "2026-10-05"
    assert by["FV-0007"]["balance"] == 0 and by["FV-0007"]["kind"] == "cash"
    assert any(e["action"] == "import.excel" and e["user"] == "patron" for e in owner.get("/api/audit").json()["entries"])
    # la numérotation continue après l'historique
    cid = [c for c in r["clients"] if c["code"] == "C002"][0]["id"]
    nouvelle = push(owner, op("sale.create", {"client_id": cid, "kind": "cash", "pay_method": "Espèces", "lines": [{"name": "Article", "qty": 1, "unit_price": 100}]}))[0]
    assert nouvelle["sale"]["number"] == "FV-0008"
    assert owner.get("/api/audit/verify").json()["ok"] is True


def test_prix_reellement_facture_conserve_et_formules_vides_ignorees(owner, tmp_path):
    from gestion import importer

    def prix(wb):
        ws = wb["Ventes"]
        ws["G5"] = 300_000                                                       # prix négocié, différent du catalogue (350 000) : aucune remise par palier en plus
        ws["G6"] = 80_000
        ws["G9"] = '=IF(E9="","",1)'                                             # ligne vide du modèle : une formule de prix ne crée pas de vente
    rep = importer.run(workbook(tmp_path / "a.xlsx", prix), "patron", apply=True)
    assert rep["applied"] is True and not rep["errors"]
    by = {s["number"]: s for s in owner.get("/api/sync/pull?since=0").json()["sales"]}
    assert by["FV-0001"]["total"] == 10 * 300_000 + 200 * 80_000 and by["FV-0001"]["lines"][1]["discount_pct"] == 0
    assert by["FV-0007"]["total"] == 4 * 85_000                                  # sans prix saisi : le catalogue, comme avant
    assert len(by) == 2


def test_reimport_sans_doublon(owner, tmp_path):
    from gestion import importer
    f = workbook(tmp_path / "a.xlsx")
    importer.run(f, "patron", apply=True)
    rep = importer.run(f, "patron", apply=True)
    assert rep["counts"]["clients"]["déjà présents"] == 2 and rep["counts"]["clients"]["créés"] == 0 and rep["counts"]["ventes"]["créés"] == 0
    assert db_count("clients") == 2 and db_count("sales") == 2 and db_count("payments") == 1


def test_refus_listes_avec_ligne_et_rien_n_est_ecrit(owner, tmp_path):
    from gestion import importer

    def bad(wb):
        wb["Ventes"]["C8"] = "C099"; wb["Ventes"]["A8"] = date(2026, 10, 3); wb["Ventes"]["B8"] = "FV-0008-A"; wb["Ventes"]["E8"] = "Riz 50 kg"; wb["Ventes"]["F8"] = 1; wb["Ventes"]["I8"] = "Comptant"; wb["Ventes"]["J8"] = "Espèces"
        wb["Paiements"]["A6"] = date(2026, 9, 26); wb["Paiements"]["B6"] = "C001"; wb["Paiements"]["D6"] = "FV-0001-A"; wb["Paiements"]["E6"] = 999_999_999; wb["Paiements"]["F6"] = "Espèces"
    rep = importer.run(workbook(tmp_path / "b.xlsx", bad), "patron", apply=True)
    assert rep["applied"] is False
    assert any("Ventes, ligne 8" in e and "C099" in e for e in rep["errors"])
    assert any("Paiements, ligne 6" in e and "dépasse le reste" in e for e in rep["errors"])
    assert db_count("clients") == 0                                                           # tout ou rien
    ok = importer.run(workbook(tmp_path / "b.xlsx", bad), "patron", apply=True, ignore_errors=True)
    assert ok["applied"] is True and db_count("sales") == 2 and db_count("payments") == 1


def test_import_reserve_au_proprietaire(owner, app_env, tmp_path):
    from gestion import importer
    make_user(owner, app_env, "marie", "sales")
    with pytest.raises(importer.ImportProblem):
        importer.run(workbook(tmp_path / "a.xlsx"), "marie", apply=True)
    with pytest.raises(importer.ImportProblem):
        importer.run(tmp_path / "absent.xlsx", "patron")


def test_le_mode_import_n_est_pas_accessible_par_l_api(owner):
    """Un utilisateur ne peut pas obtenir les dérogations d'import (code client imposé, numéro imposé) en envoyant des champs supplémentaires."""
    r = push(owner, op("client.create", {"name": "Client", "code": "C777"}))[0]
    assert r["client"]["code"] == "C001"
    cid = r["client"]["id"]
    s = push(owner, op("sale.create", {"client_id": cid, "kind": "cash", "pay_method": "Espèces", "number": "FV-9999", "lines": [{"name": "A", "qty": 1, "unit_price": 10}]}))[0]
    assert s["sale"]["number"] == "FV-0001"


def test_import_des_depenses(owner, tmp_path):
    from gestion import importer

    def with_expenses(wb):
        ws = wb.create_sheet("Depenses")
        rows = [(date(2026, 10, 1), "Loyer et charges locatives", "Propriétaire", "Loyer d'octobre", 2_000_000, "Virement bancaire", None, "DEP-001"),
                (date(2026, 10, 3), "Catégorie inventée", "Garage", "Réparation", 150_000, "Troc", None, "DEP-002"),
                (date(2026, 10, 4), "Transport et livraison", None, None, 50_000, "Espèces", None, "DEP-003")]
        for r, row in enumerate(rows, 5):
            for c, v in enumerate(row, 1): ws.cell(row=r, column=c, value=v)
    rep = importer.run(workbook(tmp_path / "e.xlsx", with_expenses), "patron", apply=True, ignore_errors=True)
    assert rep["counts"]["dépenses"]["créés"] == 2 and rep["counts"]["dépenses"]["refusés"] == 0
    assert any("inconnue" in w and "Autres charges" in w for w in rep["warnings"]) and any("Troc" in w for w in rep["warnings"])
    assert any("Depenses, ligne 7" in e and "ni bénéficiaire ni description" in e for e in rep["errors"])
    exps = owner.get("/api/sync/pull?since=0").json()["expenses"]
    assert sorted(e["category"] for e in exps) == ["Autres charges", "Loyer et charges locatives"] and {e["method"] for e in exps} == {"Virement bancaire", "Autre"}
    again = importer.run(workbook(tmp_path / "e.xlsx", with_expenses), "patron", apply=True, ignore_errors=True)
    assert again["counts"]["dépenses"]["déjà présents"] == 2 and len(owner.get("/api/sync/pull?since=0").json()["expenses"]) == 2
