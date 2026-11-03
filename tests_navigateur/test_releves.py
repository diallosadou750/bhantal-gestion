"""Lecture des relevés bancaires (fonction JavaScript G.bankParse) : formats variés des banques, sans exemple réel nécessaire."""
from __future__ import annotations

import pytest
from conftest import Page


@pytest.fixture(scope="module")
def p(navigateur, serveur):
    pg = Page(navigateur, serveur)
    pg.page.goto("/"); pg.page.wait_for_function("() => !!(window.G && window.G.bankParse)")
    yield pg
    pg.close()


def lire(p, texte, override=None):
    return p.page.evaluate("([t, o]) => { const r = G.bankParse(t, o); return { lines: r.lines, errors: r.errors, headerRow: r.headerRow, columns: r.columns }; }", [texte, override])


def test_format_simple_debit_credit(p):
    r = lire(p, "Date;Libellé;Débit;Crédit;Référence\n05/10/2026;VIREMENT CAMARA;;2 000 000;VIR-1\n06/10/2026;FRAIS DE TENUE DE COMPTE;15 000;;")
    assert r["errors"] == [] and r["lines"] == [{"date": "2026-10-05", "label": "VIREMENT CAMARA", "amount": 2_000_000, "reference": "VIR-1"}, {"date": "2026-10-06", "label": "FRAIS DE TENUE DE COMPTE", "amount": -15_000, "reference": ""}]


def test_lignes_d_en_tete_de_la_banque_avant_les_titres_et_total_a_la_fin(p):
    t = ("RELEVE DE COMPTE;;;;\nTitulaire : EXEMPLE SARL;;;;\nCompte : 0000 0000 0000;;;;\n\nDate opération;Date valeur;Libellé de l'opération;Débit;Crédit;Solde\n"
         "03/10/2026;03/10/2026;VIR RECU CLIENT;;1 500 000;5 000 000\n04/10/2026;04/10/2026;RETRAIT DAB;200 000;;4 800 000\nTOTAL;;;200 000;1 500 000;")
    r = lire(p, t)
    assert r["headerRow"] == 3 and r["errors"] == [] and [x["amount"] for x in r["lines"]] == [1_500_000, -200_000]
    assert r["lines"][0]["label"] == "VIR RECU CLIENT"


def test_montant_signe_et_colonne_sens(p):
    r = lire(p, "Date,Description,Montant,Sens\n2026-10-01,Dépôt espèces,\"1,250,000\",C\n2026-10-02,Frais,5000,D\n2026-10-03,Virement,\"-300,000\",")
    assert r["errors"] == [] and [x["amount"] for x in r["lines"]] == [1_250_000, -5_000, -300_000]


def test_dates_variees_annees_a_deux_chiffres_et_mois_en_lettres(p):
    r = lire(p, "Date;Libellé;Montant\n05/10/26;A;1000\n07-oct.-2026;B;-2000\n8 Octobre 2026;C;3000\n31/02/2026;D;1")
    assert [x["date"] for x in r["lines"]] == ["2026-10-05", "2026-10-07", "2026-10-08"] and len(r["errors"]) == 1 and "date illisible" in r["errors"][0]


def test_formats_de_nombres(p):
    r = lire(p, "Date;Libellé;Montant\n01/10/2026;a;1.234.567\n01/10/2026;b;1 234 567\n01/10/2026;c;(2 500)\n01/10/2026;d;1234,56\n01/10/2026;e;1,234.50\n01/10/2026;f;12 500 GNF\n01/10/2026;g;−700")
    assert [x["amount"] for x in r["lines"]] == [1_234_567, 1_234_567, -2_500, 1235, 1235, 12_500, -700]


def test_titres_introuvables_puis_choix_manuel_des_colonnes(p):
    t = "Jour;Intitulé;Entrée;Sortie\n05/10/2026;Vente;100 000;\n06/10/2026;Achat;;40 000"
    r = lire(p, t)
    assert r["lines"] == [] and "choisir les colonnes" in r["errors"][0]
    r = lire(p, t, {"headerRow": 0, "columns": {"date": 0, "label": 1, "credit": 2, "debit": 3, "amount": -1, "sens": -1, "ref": -1}})
    assert r["errors"] == [] and [x["amount"] for x in r["lines"]] == [100_000, -40_000]


def test_windows_1252_et_bom(p):
    ok = p.page.evaluate("""() => { const b = new Uint8Array([0x44,0x61,0x74,0x65,0x3b,0x4c,0x69,0x62,0x65,0x6c,0x6c,0xe9,0x3b,0x4d,0x6f,0x6e,0x74,0x61,0x6e,0x74,0x0a,
        0x30,0x35,0x2f,0x31,0x30,0x2f,0x32,0x30,0x32,0x36,0x3b,0x43,0x61,0x66,0xe9,0x3b,0x31,0x30,0x30,0x30]);
        const t = new TextDecoder('windows-1252').decode(b); const r = G.bankParse('\\ufeff' + t); return r.lines.length === 1 && r.lines[0].label === 'Café'; }""")
    assert ok
