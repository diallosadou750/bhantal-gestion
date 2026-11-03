import base64
import uuid
from datetime import date, timedelta

import pytest
from conftest import make_user, op, push

TODAY = date.today()
T = TODAY.isoformat()
PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
ALL = ["margins_product", "margins_client", "sales_seller", "sales_month", "financials"]


@pytest.fixture(autouse=True)
def pdf_en_clair():
    """Pas de compression pour pouvoir chercher le texte dans les octets du PDF."""
    import reportlab.rl_config as cfg
    old = cfg.pageCompression; cfg.pageCompression = 0
    yield
    cfg.pageCompression = old


def pdf(c, report, **q):
    qs = "&".join(f"{k}={v}" for k, v in {"report": report, "from": f"{TODAY.year}-01-01", "to": T, **q}.items())
    return c.get("/api/reports/pdf?" + qs)


def donnees(c):
    push(c, op("settings.update", {"company_name": "Exemple SARL", "address": "Conakry", "tax_rate": 18}))
    pid = push(c, op("product.create", {"id": str(uuid.uuid4()), "name": "Ciment 50 kg", "price": 100_000}))[0]["product"]["id"]
    cid = push(c, op("client.create", {"id": str(uuid.uuid4()), "name": "Quincaillerie Camara", "credit_limit": 50_000_000}))[0]["client"]["id"]
    sup = push(c, op("supplier.create", {"id": str(uuid.uuid4()), "name": "F"}))[0]["supplier"]["id"]
    push(c, op("purchase.create", {"supplier_id": sup, "kind": "cash", "pay_method": "Espèces", "purchase_date": (TODAY - timedelta(days=5)).isoformat(), "lines": [{"product_id": pid, "qty": 100, "unit_cost": 60_000}]}))
    push(c, op("sale.create", {"client_id": cid, "kind": "cash", "pay_method": "Espèces", "lines": [{"product_id": pid, "qty": 10}]}))


def test_chaque_rapport_donne_un_vrai_pdf(owner):
    donnees(owner)
    for rep in ALL:
        r = pdf(owner, rep)
        assert r.status_code == 200 and r.headers["content-type"] == "application/pdf", (rep, r.text[:200])
        assert r.content[:5] == b"%PDF-" and r.content.rstrip().endswith(b"%%EOF") and len(r.content) > 1500, rep
        assert f"filename={rep}_" in r.headers["content-disposition"] and "no-store" in r.headers["cache-control"]
    texte = pdf(owner, "margins_product").content
    assert b"Exemple SARL" in texte and b"Ciment 50 kg" in texte and b"Marge par produit" in texte and b"Page 1 / 1" in texte
    assert b"Quincaillerie Camara" in pdf(owner, "margins_client").content and b"Le Patron" in pdf(owner, "sales_seller").content
    fin = pdf(owner, "financials").content
    assert b"BILAN" in fin.upper() or b"Bilan" in fin
    assert b"expert-comptable" in fin and b"Tableau des flux" in fin


def test_les_chiffres_du_pdf_sont_ceux_de_l_ecran(owner):
    donnees(owner)
    from gestion import db, margins, pdf_reports
    with db.read() as c:
        user = c.execute("SELECT * FROM users LIMIT 1").fetchone()
        title, blocks, wide = pdf_reports.margins_blocks(c, f"{TODAY.year}-01-01", T, "product")
        r = margins.compute(c, f"{TODAY.year}-01-01", T)
    table = next(b for b in blocks if b["type"] == "table")
    g = r["by_product"][0]
    assert table["rows"][0][0] == "Ciment 50 kg" and table["rows"][0][2].replace(" ", "") == str(g["revenue"]) and table["rows"][0][4].replace(" ", "") == str(g["margin"])
    kp = next(b for b in blocks if b["type"] == "kpis")["items"]
    assert kp[1][1].replace(" ", "") == f"{r['totals']['margin']} GNF"
    assert title == "Marge par produit" and wide is True


def test_logo_et_caracteres_speciaux(owner):
    owner.post("/api/logo", json={"image": PNG})
    pid = push(owner, op("product.create", {"id": str(uuid.uuid4()), "name": "<b>Gras</b> & 'quote' é à ç ½", "price": 1000}))[0]["product"]["id"]
    cid = push(owner, op("client.create", {"id": str(uuid.uuid4()), "name": "Client"}))[0]["client"]["id"]
    push(owner, op("sale.create", {"client_id": cid, "kind": "cash", "pay_method": "Espèces", "lines": [{"product_id": pid, "qty": 1}]}))
    r = pdf(owner, "margins_product")
    assert r.status_code == 200 and r.content[:5] == b"%PDF-"                                  # le balisage dans un nom ne casse pas le document et ne s'interprète pas
    assert b"<b>" not in r.content


def test_periode_vide_validation_et_droits(owner, app_env):
    assert pdf(owner, "sales_month", **{"from": "2000-01-01", "to": "2000-03-01"}).status_code == 200      # aucune donnée : un PDF propre, pas une erreur
    assert pdf(owner, "inconnu").status_code == 422 and pdf(owner, "financials", **{"from": "2026-13-01"}).status_code == 422
    marie, _ = make_user(owner, app_env, "marie", "sales")
    cash, _ = make_user(owner, app_env, "paul", "cashier")
    assert pdf(marie, "sales_seller").status_code == 200 and pdf(marie, "sales_month").status_code == 200       # un commercial exporte SES ventes
    for rep in ("margins_product", "margins_client", "financials"): assert pdf(marie, rep).status_code == 403, rep
    for rep in ALL: assert pdf(cash, rep).status_code == 403, rep
    aud, _ = make_user(owner, app_env, "expert", "auditor")
    comp, _ = make_user(owner, app_env, "compta", "accountant")
    for u in (aud, comp):
        for rep in ALL: assert pdf(u, rep).status_code == 200, rep
    assert any("PDF" in (e["note"] or "") for e in owner.get("/api/audit?limit=60").json()["entries"])
