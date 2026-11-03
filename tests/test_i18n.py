import json
import re
import uuid
from pathlib import Path

import pytest
from conftest import op, push

from gestion import i18n

DIR = Path(i18n.DIR)
CODES = [l["code"] for l in json.loads((DIR / "langues.json").read_text(encoding="utf-8")) if l["code"] != "fr"]


def test_langues_declarees_et_fichiers_valides():
    langs = json.loads((DIR / "langues.json").read_text(encoding="utf-8"))
    assert langs[0]["code"] == "fr" and {"en", "ar"} <= {l["code"] for l in langs}
    for code in CODES:
        d = json.loads((DIR / f"{code}.json").read_text(encoding="utf-8"))
        assert d["code"] == code and d["dir"] in ("ltr", "rtl") and len(d["textes"]) > 1000
        for m in d["motifs"]: re.compile(m["re"])                                   # une expression invalide casserait l'interface
        assert all(isinstance(v, str) and v.strip() for v in d["textes"].values())
    assert json.loads((DIR / "ar.json").read_text(encoding="utf-8"))["dir"] == "rtl"


@pytest.mark.parametrize("code", CODES)
def test_motifs_ne_perdent_aucun_groupe(code):
    d = i18n.load(code)
    for m in d["motifs"]:
        n = re.compile(m["re"]).groups
        used = {int(x) for x in re.findall(r"\$(\d)", m["to"])} | {int(x) for x in re.findall(r"\{t:(\d)\}", m["to"])}
        assert used <= set(range(1, n + 1)), m                                       # un $3 sans groupe 3 afficherait un texte vide


def test_traduction_texte_motif_et_repli():
    assert i18n.tr("Annuler", "en") == "Cancel"
    assert i18n.tr("  Annuler ", "en") == "  Cancel "                              # les espaces d'origine sont conservés
    assert i18n.tr("Vente FV-0004 enregistrée.", "fr") == "Vente FV-0004 enregistrée."
    assert i18n.tr("Annuler", "fr") == "Annuler"
    assert i18n.tr("Texte absolument inconnu du dictionnaire", "en") == "Texte absolument inconnu du dictionnaire"
    assert i18n.tr("Reste 12 000 GNF", "en") == "Balance 12 000 GNF"
    assert i18n.tr("Annuler", "ar") == "إلغاء"
    assert i18n.tr("Reste 12 000 GNF", "ar") == "المتبقي 12 000 GNF"
    assert i18n.tr("GNF", "en") == "GNF" and i18n.tr("123", "ar") == "123"           # unités et nombres seuls : jamais traduits
    assert i18n.tr("Annuler", "zz") == "Annuler" and i18n.tr("Annuler", "../en") == "Annuler"      # code inconnu ou hostile : français


def test_repli_par_morceaux():
    assert i18n.tr("Annuler · Supprimer", "en") == "Cancel · Delete"
    assert i18n.tr("Montant : Annuler", "en") == "Amount: Cancel"


def test_choix_de_la_langue_du_document():
    assert i18n.pick("en-US,en;q=0.9,fr;q=0.8", "auto") == "en"
    assert i18n.pick("ar-GN,ar;q=0.9", None) == "ar"
    assert i18n.pick("de-DE,de", "auto") == "fr"                                      # langue non fournie : français
    assert i18n.pick("en", "ar") == "ar"                                              # le réglage de l'entreprise l'emporte
    assert i18n.pick("ar", "auto", allowed={"en"}) == "fr"                            # PDF : pas d'arabe, repli sur le français
    assert i18n.pick("ar,en;q=0.5", "auto", allowed={"en"}) == "en"
    assert i18n.pick(None, "zz") == "fr"


def test_page_html_traduite_et_sens_de_lecture():
    html = '<html lang="fr"><head><title>Facture</title></head><body><p>Montant</p><script>var a = "Annuler";</script></body></html>'
    en = i18n.tr_html(html, "en")
    assert '<html lang="en">' in en and "<title>Invoice</title>" in en and "<p>Amount</p>" in en
    ar = i18n.tr_html(html, "ar")
    assert '<html lang="ar" dir="rtl">' in ar and "<p>المبلغ</p>" in ar
    assert i18n.tr_html(html, "fr") == html


def test_facture_en_anglais_et_en_arabe(owner):
    r = push(owner, op("client.create", {"id": str(uuid.uuid4()), "name": "Camara", "credit_limit": 1_000_000}))[0]
    p = push(owner, op("product.create", {"id": str(uuid.uuid4()), "name": "Ciment", "price": 100_000}))[0]["product"]
    s = push(owner, op("sale.create", {"client_id": r["client"]["id"], "kind": "cash", "pay_method": "Espèces", "lines": [{"product_id": p["id"], "qty": 1}]}))[0]["sale"]
    url = f"/api/invoice/{s['id']}"
    assert "FACTURE" in owner.get(url).text                                           # par défaut : français
    assert "INVOICE" in owner.get(url, headers={"Accept-Language": "en-GB,en;q=0.8"}).text
    assert 'dir="rtl"' in owner.get(url, headers={"Accept-Language": "ar"}).text
    assert push(owner, op("settings.update", {"doc_lang": "en"}))[0]["status"] == "ok"
    assert "INVOICE" in owner.get(url, headers={"Accept-Language": "fr"}).text        # le réglage de l'entreprise l'emporte
    assert push(owner, op("settings.update", {"doc_lang": "auto"}))[0]["status"] == "ok"
    assert "FACTURE" in owner.get(url, headers={"Accept-Language": "fr"}).text
    assert push(owner, op("settings.update", {"doc_lang": "klingon"}))[0]["status"] != "ok"


def test_pdf_en_anglais_et_arabe_retombe_sur_le_francais(owner):
    from datetime import date
    t = date.today().isoformat()
    for lang, size_ok in (("en", True), ("ar", True)):
        r = owner.get(f"/api/reports/pdf?report=financials&from=2000-01-01&to={t}", headers={"Accept-Language": lang})
        assert r.status_code == 200 and r.content[:5] == b"%PDF-" and size_ok
