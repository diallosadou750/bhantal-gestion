from datetime import date

import pytest

openpyxl = pytest.importorskip("openpyxl")
from test_importer import db_count, workbook  # noqa: E402


def avec_stock(wb, bad=False, g=None, many=0):
    """Ajoute les feuilles Stocks (stock initial, seuils, entrées en colonnes Q à W) et Fournisseurs (factures) au classeur d'essai."""
    ws = wb.create_sheet("Stocks")
    ws["D5"], ws["H5"] = 100, 20          # Riz 50 kg : stock initial 100, seuil 20
    ws["D6"], ws["H6"] = 500, 50          # Ciment 50 kg
    rows = [(date(2026, 9, 1), "Fournisseur A", "Riz 50 kg", 100, 320_000, "FA-2026-001"),
            (date(2026, 9, 1), "fournisseur  a", "Ciment 50 kg", 500, 70_000, "FA-2026-001"),       # même fournisseur écrit autrement, même facture
            (date(2026, 9, 10), "Fournisseur B", "Ciment 50 kg", 100, 72_000, "FB-2026-014")]       # sans facture dans la feuille Fournisseurs
    if bad: rows.append((date(2026, 9, 11), "Fournisseur B", "Produit inconnu", 5, 1000, "FB-2026-015"))
    for i in range(many): rows.append((date(2026, 9, 12), "Fournisseur D", "Riz 50 kg", 1, 1000 + i, "FD-BIG"))
    for r, row in enumerate(rows, 5):
        for col, v in zip((17, 18, 19, 20, 21, 23), row): ws.cell(row=r, column=col, value=v)
    if g: ws["G5"], ws["G6"] = g
    wf = wb.create_sheet("Fournisseurs")
    for r, row in enumerate([(date(2026, 9, 1), "Fournisseur A", "FA-2026-001", 67_000_000, date(2026, 10, 1), 20_000_000),
                             (date(2026, 10, 15), "Fournisseur C", "FC-2026-007", 5_000_000, date(2026, 10, 25), 0)], 5):
        for col, v in zip((1, 2, 3, 4, 5, 6), row): wf.cell(row=r, column=col, value=v)


def pull(c):
    return c.get("/api/sync/pull?since=0").json()


def test_essai_a_blanc_ne_cree_ni_fournisseur_ni_achat_ni_stock(owner, tmp_path):
    from gestion import importer
    rep = importer.run(workbook(tmp_path / "a.xlsx", avec_stock), "patron", apply=False)
    assert rep["errors"] == [] and rep["counts"]["fournisseurs"]["créés"] == 3 and rep["counts"]["achats"]["créés"] == 2 and rep["counts"]["stock initial"]["créés"] == 2
    assert db_count("suppliers") == 0 and db_count("purchases") == 0 and db_count("stock_adjustments") == 0
    assert rep["not_imported"]["Factures fournisseurs sans entrée de stock"] == 1


def test_import_fournisseurs_achats_stock_et_dettes(owner, tmp_path):
    from gestion import importer
    rep = importer.run(workbook(tmp_path / "a.xlsx", avec_stock), "patron", apply=True)
    assert rep["applied"] is True and rep["errors"] == []
    d = pull(owner)
    assert sorted(s["name"] for s in d["suppliers"]) == ["Fournisseur A", "Fournisseur B", "Fournisseur C"]       # « fournisseur  a » = Fournisseur A
    achats = {p["supplier_ref"]: p for p in d["purchases"]}
    fa, fb = achats["FA-2026-001"], achats["FB-2026-014"]
    assert fa["total"] == 67_000_000 and len(fa["lines"]) == 2 and fa["kind"] == "credit" and fa["due_date"] == "2026-10-01" and fa["balance"] == 47_000_000     # payé 20 000 000 sur la facture
    assert fb["total"] == 7_200_000 and fb["due_date"] == fb["purchase_date"] == "2026-09-10" and fb["balance"] == 7_200_000                                  # sans facture : dette à payer
    assert any("sans facture correspondante" in w and "FB-2026-014" in w for w in rep["warnings"])
    assert any("FC-2026-007" in w and "non importée" in w for w in rep["warnings"])
    produits = {p["name"]: p for p in d["products"]}
    assert produits["Riz 50 kg"]["reorder_level"] == 20 and produits["Ciment 50 kg"]["reorder_level"] == 50      # seuils d'alerte repris
    riz, ciment = produits["Riz 50 kg"]["id"], produits["Ciment 50 kg"]["id"]
    assert d["stock"][riz]["qty"] == 100 + 100 - 10 and d["stock"][ciment]["qty"] == 500 + 500 + 100 - 200 - 4          # initial + entrées - ventes
    adj = [a for a in d["adjustments"] if a["reason"] == "initial"]
    assert len(adj) == 2 and {a["adj_date"] for a in adj} == {"2026-08-31"}                                      # la veille du premier mouvement
    assert owner.get("/api/audit/verify").json()["ok"] is True
    q = "from=2000-01-01&to=" + date.today().isoformat()
    assert owner.get(f"/api/accounting/report?kind=balance&{q}").json()["balanced"] is True
    assert owner.get(f"/api/accounting/report?kind=statements&{q}").json()["balance_sheet"]["balanced"] is True


def test_reimport_sans_doublon(owner, tmp_path):
    from gestion import importer
    f = workbook(tmp_path / "a.xlsx", avec_stock)
    importer.run(f, "patron", apply=True)
    n = (db_count("suppliers"), db_count("purchases"), db_count("stock_adjustments"), db_count("purchase_lines"))
    rep = importer.run(f, "patron", apply=True)
    assert (db_count("suppliers"), db_count("purchases"), db_count("stock_adjustments"), db_count("purchase_lines")) == n
    assert rep["counts"]["achats"]["déjà présents"] == 2 and rep["counts"]["fournisseurs"]["déjà présents"] == 3 and rep["counts"]["stock initial"]["déjà présents"] == 2


def test_refus_listes_et_rien_n_est_ecrit(owner, tmp_path):
    from gestion import importer
    rep = importer.run(workbook(tmp_path / "b.xlsx", lambda wb: avec_stock(wb, bad=True)), "patron", apply=True)
    assert any("Produit inconnu" in e and "Stocks" in e for e in rep["errors"]) and rep["applied"] is False
    assert db_count("suppliers") == 0 and db_count("purchases") == 0 and db_count("clients") == 0


def test_comparaison_avec_le_stock_theorique_du_classeur(owner, tmp_path):
    from gestion import importer
    ok = importer.run(workbook(tmp_path / "ok.xlsx", lambda wb: avec_stock(wb, g=(190, 896))), "patron", apply=False)
    assert ok["verification"]["stocks comparés au classeur"] == 2 and ok["verification"]["écarts de stock"] == 0
    ko = importer.run(workbook(tmp_path / "ko.xlsx", lambda wb: avec_stock(wb, g=(190, 999))), "patron", apply=False)
    assert ko["verification"]["écarts de stock"] == 1 and any("Ciment 50 kg" in w and "999" in w for w in ko["warnings"])


def test_plus_de_30_lignes_pour_une_facture(owner, tmp_path):
    from gestion import importer
    rep = importer.run(workbook(tmp_path / "g.xlsx", lambda wb: avec_stock(wb, many=35)), "patron", apply=True)
    assert rep["errors"] == []
    refs = sorted(p["supplier_ref"] for p in pull(owner)["purchases"] if p["supplier_ref"].startswith("FD-BIG"))
    assert refs == ["FD-BIG", "FD-BIG (suite 2)"]                                                         # 35 lignes : 30 + 5


def test_option_date_du_stock_initial(owner, tmp_path):
    from gestion import importer
    importer.run(workbook(tmp_path / "d.xlsx", avec_stock), "patron", apply=True, stock_initial_date="2026-08-01")
    assert {a["adj_date"] for a in pull(owner)["adjustments"] if a["reason"] == "initial"} == {"2026-08-01"}


def sans_numero(wb, ambigu=False):
    """Comme le vrai classeur : les entrées de stock n'ont pas de n° de bon ou de facture ; la facture se retrouve par le fournisseur et le montant."""
    ws = wb.create_sheet("Stocks")
    for col, v in zip((17, 18, 19, 20, 21), (date(2026, 9, 1), "Fournisseur A", "Riz 50 kg", 100, 320_000)): ws.cell(row=5, column=col, value=v)
    wf = wb.create_sheet("Fournisseurs")
    lignes = [(date(2026, 10, 5), "Fournisseur A", "FA-2026-001", 32_000_000, date(2026, 11, 4), 20_000_000)]
    if ambigu: lignes.append((date(2026, 10, 6), "Fournisseur A", "FA-2026-002", 32_000_000, date(2026, 11, 5), 0))
    for r, row in enumerate(lignes, 5):
        for col, v in zip((1, 2, 3, 4, 5, 6), row): wf.cell(row=r, column=col, value=v)


def test_rapprochement_de_la_facture_par_le_montant(owner, tmp_path):
    from gestion import importer
    rep = importer.run(workbook(tmp_path / "m.xlsx", sans_numero), "patron", apply=True)
    assert rep["errors"] == [] and any("rapprochées de la facture FA-2026-001" in w for w in rep["warnings"]) and "Factures fournisseurs sans entrée de stock" not in rep["not_imported"]
    p = pull(owner)["purchases"][0]
    assert p["supplier_ref"] == "FA-2026-001" and p["due_date"] == "2026-11-04" and p["balance"] == 12_000_000          # payé 20 000 000 sur 32 000 000


def test_deux_factures_du_meme_montant_pas_de_rapprochement_automatique(owner, tmp_path):
    from gestion import importer
    rep = importer.run(workbook(tmp_path / "n.xlsx", lambda wb: sans_numero(wb, ambigu=True)), "patron", apply=True)
    assert any("plusieurs factures du même montant" in w for w in rep["warnings"])
    p = pull(owner)["purchases"][0]
    assert p["supplier_ref"] == "" and p["balance"] == 32_000_000 and p["due_date"] == p["purchase_date"]                # dette entière, à pointer à la main
    assert rep["not_imported"]["Factures fournisseurs sans entrée de stock"] == 2
