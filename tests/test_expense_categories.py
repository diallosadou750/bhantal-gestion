import uuid
from datetime import date

from conftest import make_user, op, push

T = date.today().isoformat()


def pull(c):
    return c.get("/api/sync/pull?since=0").json()


def cat(c, key="Frais de gardiennage", account="638", nature="Fixe"):
    return push(c, op("expcat.create", {"key": key, "account": account, "nature": nature}))[0]


def depense(c, category, amount=100_000, **extra):
    return push(c, op("expense.create", {"category": category, "amount": amount, "method": "Espèces", "payee": "Prestataire", **extra}))[0]


def bal(c, to=T):
    r = c.get(f"/api/accounting/report?kind=balance&from=2000-01-01&to={to}").json()
    assert r["balanced"] is True
    return {x["account"]: x for x in r["rows"]}


def test_creation_et_utilisation(owner):
    r = cat(owner)
    assert r["status"] == "ok" and r["category"]["custom"] is True and r["category"]["account"] == "638"
    cats = {x["key"]: x for x in pull(owner)["expense_categories"]}
    assert "Frais de gardiennage" in cats and cats["Frais de gardiennage"]["nature"] == "Fixe" and cats["Loyer et charges locatives"]["custom"] is False
    e = depense(owner, "Frais de gardiennage")
    assert e["status"] == "ok" and e["expense"]["nature"] == "Fixe" and e["expense"]["account"] == "638"
    assert bal(owner)["638"]["close_debit"] == 100_000                                       # écriture sur le compte choisi
    rt = pull(owner)["rentability"]
    assert rt is None or rt["fixed"] >= 100_000                                              # la nature « fixe » compte dans le seuil de rentabilité
    b = push(owner, op("budget.set", {"category": "Frais de gardiennage", "amount": 150_000}))[0]
    assert b["status"] == "ok" and b["budgets"]["Frais de gardiennage"] == 150_000


def test_validations(owner):
    for bad in ({"key": "Ab"}, {"key": "Loyer et charges locatives"}, {"key": "LOYER ET CHARGES LOCATIVES"}, {"key": "Nouvelle", "account": "701"}, {"key": "Nouvelle", "account": "691"},
                {"key": "Nouvelle", "account": "699999"}, {"key": "Nouvelle", "nature": "Autre"}, {"key": ""}):
        data = {"key": "Catégorie test", "account": "638", "nature": "Fixe", **bad}
        assert push(owner, op("expcat.create", data))[0]["status"] == "invalid", bad
    assert cat(owner)["status"] == "ok" and cat(owner, "frais de GARDIENNAGE")["status"] == "invalid"        # doublon sans tenir compte des majuscules


def test_desactivation_et_modification(owner):
    c0 = cat(owner)["category"]
    e = depense(owner, "Frais de gardiennage")
    u = push(owner, op("expcat.update", {"key": "Frais de gardiennage", "base_version": c0["version"], "changes": {"active": False}}))[0]
    assert u["status"] == "ok" and u["category"]["active"] is False
    assert depense(owner, "Frais de gardiennage")["status"] == "invalid"                     # plus de nouvelle dépense dans une catégorie désactivée
    assert any(x["id"] == e["expense"]["id"] for x in pull(owner)["expenses"])               # l'historique reste
    assert push(owner, op("expcat.update", {"key": "Frais de gardiennage", "base_version": 1, "changes": {"active": True}}))[0]["status"] == "conflict"
    v = u["category"]["version"]
    r = push(owner, op("expcat.update", {"key": "Frais de gardiennage", "base_version": v, "changes": {"active": True, "nature": "Variable", "account": "628"}}))[0]
    assert r["status"] == "ok" and r["category"]["nature"] == "Variable" and r["category"]["account"] == "628"
    assert bal(owner)["628"]["close_debit"] == 100_000 and "638" not in bal(owner)           # le nouveau compte s'applique aux dépenses existantes
    assert push(owner, op("expcat.update", {"key": "Loyer et charges locatives", "base_version": 1, "changes": {"nature": "Variable"}}))[0]["status"] == "invalid"     # catégories de base : intouchables
    assert push(owner, op("expcat.update", {"key": "Frais de gardiennage", "base_version": r["category"]["version"], "changes": {"account": "701"}}))[0]["status"] == "invalid"


def test_changement_de_compte_refuse_apres_verrou(owner):
    c0 = cat(owner)["category"]
    depense(owner, "Frais de gardiennage")
    assert push(owner, op("accounting.lock", {"through": T}))[0]["status"] == "ok"
    r = push(owner, op("expcat.update", {"key": "Frais de gardiennage", "base_version": c0["version"], "changes": {"account": "628"}}))[0]
    assert r["status"] == "invalid" and "verrouillée" in r["message"]
    assert push(owner, op("expcat.update", {"key": "Frais de gardiennage", "base_version": c0["version"], "changes": {"nature": "Variable"}}))[0]["status"] == "ok"      # la nature, elle, reste modifiable


def test_recurrentes_etalements_droits(owner, app_env):
    cat(owner)
    rc = push(owner, op("recurring.create", {"id": str(uuid.uuid4()), "label": "Gardiennage", "category": "Frais de gardiennage", "amount": 80_000, "method": "Espèces", "day": 1, "start_date": T}))[0]
    assert rc["status"] == "ok"
    marie, _ = make_user(owner, app_env, "marie", "sales")
    assert cat(marie, "Catégorie de Marie")["status"] == "denied"
    comp, _ = make_user(owner, app_env, "compta", "accountant")
    assert cat(comp, "Catégorie du comptable")["status"] == "ok"
    cash, _ = make_user(owner, app_env, "paul", "cashier")
    assert depense(cash, "Frais de gardiennage", 20_000)["status"] == "ok"                   # un caissier peut utiliser une catégorie personnalisée (dans sa limite)
    ex = push(owner, op("expense.create", {"category": "Frais de gardiennage", "amount": 1_200_000, "method": "Virement bancaire", "payee": "Assureur", "expense_date": T}))[0]["expense"]
    d = push(owner, op("deferral.create", {"id": str(uuid.uuid4()), "kind": "charge", "label": "Gardiennage annuel", "expense_id": ex["id"], "start_date": T, "end_date": f"{date.today().year + 1}-{date.today().month:02d}-01"}))[0]
    assert d["status"] == "ok" and d["deferral"]["account"] == "638"                        # l'étalement reprend le compte de la catégorie
    assert owner.get("/api/audit/verify").json()["ok"] is True
