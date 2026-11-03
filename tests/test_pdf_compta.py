import re
import uuid
from datetime import date

import pytest
from conftest import make_user, op, push

TODAY = date.today()
T = TODAY.isoformat()
DEB = f"{TODAY.year}-01-01"


@pytest.fixture(autouse=True)
def pdf_en_clair():
    import reportlab.rl_config as cfg
    old = cfg.pageCompression; cfg.pageCompression = 0
    yield
    cfg.pageCompression = old


def chiffres(texte: str) -> str:
    """Retire les espaces (y compris insécables) d'un montant formaté : « 1 200 000 » -> « 1200000 »."""
    return re.sub(r"\s", "", texte)


def pdf(c, report, **q):
    qs = "&".join(f"{k}={v}" for k, v in {"report": report, "from": DEB, "to": T, **q}.items())
    return c.get("/api/reports/pdf?" + qs)


def donnees(c, ventes=2):
    push(c, op("settings.update", {"company_name": "Exemple SARL", "address": "Conakry", "tax_rate": 18}))
    pid = push(c, op("product.create", {"id": str(uuid.uuid4()), "name": "Ciment 50 kg", "price": 100_000}))[0]["product"]["id"]
    cid = push(c, op("client.create", {"id": str(uuid.uuid4()), "name": "Quincaillerie Camara", "credit_limit": 500_000_000}))[0]["client"]["id"]
    for _ in range(ventes):
        r = push(c, op("sale.create", {"client_id": cid, "kind": "cash", "pay_method": "Espèces", "lines": [{"product_id": pid, "qty": 3}]}))[0]
        assert r["status"] == "ok", r


def test_journal_balance_grand_livre_donnent_de_vrais_pdf(owner):
    donnees(owner)
    for rep, extra, titre in (("journal", {}, b"Journal g"), ("journal", {"journal": "VE"}, b"Journal VE"), ("balance", {}, b"Balance des comptes"), ("ledger", {"account": "571"}, b"Grand livre")):
        r = pdf(owner, rep, **extra)
        assert r.status_code == 200 and r.headers["content-type"] == "application/pdf", (rep, r.text[:200])
        assert r.content[:5] == b"%PDF-" and r.content.rstrip().endswith(b"%%EOF") and titre in r.content, rep
        assert b"Exemple SARL" in r.content and b"expert-comptable" in r.content                      # en-tête et rappel de validation
    assert "filename=ledger_571_" in pdf(owner, "ledger", account="571").headers["content-disposition"]


def test_les_chiffres_du_pdf_sont_ceux_de_l_ecran(owner):
    donnees(owner)
    from gestion import accounting, db, pdf_reports
    with db.read() as c:
        bal = accounting.trial_balance(c, DEB, T)
        _, bb, _ = pdf_reports.balance_blocks(c, DEB, T)
        jr = accounting.journal(c, DEB, T, None)
        _, jb, _ = pdf_reports.journal_blocks(c, DEB, T, None)
        lg = accounting.ledger(c, "571", DEB, T)
        _, lb, _ = pdf_reports.ledger_blocks(c, DEB, T, "571")
    t = next(b for b in bb if b["type"] == "table")
    assert len(t["rows"]) == len(bal["rows"]) + 1 and chiffres(t["rows"][-1][3]) == str(bal["totals"]["oc"])
    assert chiffres(t["rows"][-1][4]) == str(bal["totals"]["md"]) and chiffres(t["rows"][-1][5]) == str(bal["totals"]["mc"])
    assert t["rows"][-1][4] == t["rows"][-1][5]                                                           # débits = crédits
    assert next(b for b in bb if b["type"] == "p")["text"].startswith("✔")
    jt = next(b for b in jb if b["type"] == "table")
    assert sum(len(e["lines"]) for e in jr["entries"]) == len(jt["rows"]) - 1
    assert jt["rows"][-1][6] == jt["rows"][-1][7] and chiffres(jt["rows"][-1][6]) == str(sum(ln["debit"] for e in jr["entries"] for ln in e["lines"]))
    lt = next(b for b in lb if b["type"] == "table")
    assert chiffres(lt["rows"][-1][6]) == str(lg["closing"]) and len(lt["rows"]) == len(lg["rows"]) + 2


def test_journal_de_plusieurs_pages(owner):
    donnees(owner, ventes=70)
    r = pdf(owner, "journal")
    pages = re.findall(rb"Page (\d+) / (\d+)", r.content)
    assert r.status_code == 200 and pages and int(pages[-1][1]) >= 2 and pages[-1][0] == pages[-1][1]     # plusieurs pages, numérotées


def test_validations_et_droits(owner, app_env):
    donnees(owner)
    assert pdf(owner, "ledger").status_code == 422 and pdf(owner, "ledger", account="abc").status_code == 422
    assert pdf(owner, "journal", journal="ve").status_code == 422 and pdf(owner, "journal", journal="VE';DROP").status_code == 422
    assert pdf(owner, "balance", **{"from": T, "to": "2000-01-01"}).status_code == 422                    # période à l'envers
    for nom, role, attendu in (("compta", "accountant", 200), ("expert", "auditor", 200), ("marie", "sales", 403), ("caisse", "cashier", 403)):
        c, _ = make_user(owner, app_env, nom, role)
        for rep, extra in (("journal", {}), ("balance", {}), ("ledger", {"account": "571"})):
            assert pdf(c, rep, **extra).status_code == attendu, (nom, rep)
    acts = [e["action"] for e in owner.get("/api/audit?limit=60").json()["entries"]]
    assert "report.exported" in acts and "access.denied" in acts


def test_pdf_en_anglais(owner):
    donnees(owner)
    r = owner.get(f"/api/reports/pdf?report=balance&from={DEB}&to={T}", headers={"Accept-Language": "en"})
    assert r.status_code == 200 and r.content[:5] == b"%PDF-"
