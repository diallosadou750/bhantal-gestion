"""Mode démonstration : une base jetable, remplie de données FICTIVES, où l'on entre sans identifiant pour découvrir l'application.

Sécurité : ce mode n'existe que si l'application est lancée avec --demo (ou GESTION_DEMO=1), uniquement sur 127.0.0.1, et sur un dossier
de données réservé (marqué par un fichier .demo). Il refuse de s'activer sur un dossier contenant de vraies données.
"""
from __future__ import annotations

import os
import secrets
from datetime import date, timedelta
from pathlib import Path

from . import db, ops, security, services
from .audit import Actor

MARKER = ".demo"
DEMO_ROLES = (("owner", "Démo Propriétaire"), ("accountant", "Démo Comptable"), ("sales", "Démo Commercial (Marie)"), ("cashier", "Démo Caissier"), ("auditor", "Démo Auditeur (lecture seule)"))


def prepare(path: Path) -> Path:
    """Crée le dossier de démonstration ou vérifie qu'il en est bien un. Le remplit à la première fois."""
    path.mkdir(parents=True, exist_ok=True)
    marker = path / MARKER
    has_db = (path / "gestion.sqlite3").exists()
    if has_db and not marker.exists():
        raise SystemExit(f"Refus : {path} contient déjà des données qui ne sont pas de la démonstration.")
    os.environ["GESTION_DATA_DIR"] = str(path); os.environ["GESTION_DEMO"] = "1"
    marker.write_text("Données de démonstration fictives : ce dossier peut être supprimé à tout moment.\n", encoding="utf-8")
    db.init_db()
    if not services.users_exist():
        seed()
    return path


def _pw() -> str:
    return "Dm" + secrets.token_urlsafe(14) + "9"        # jamais affiché : on entre par « Mode démonstration », pas par mot de passe


def seed() -> None:
    owner = services.setup_owner("demo_owner", DEMO_ROLES[0][1], _pw(), "démo", "")
    with db.read() as c:
        admin = c.execute("SELECT * FROM users WHERE id=?", (owner["id"],)).fetchone()
    ids = {"owner": owner["id"]}
    for role, name in DEMO_ROLES[1:]:
        u = services.create_user(admin, f"demo_{role}", name, role, _pw(), "démo", "")
        ids[role] = u["id"]
    with db.transaction() as c:
        c.execute("UPDATE users SET must_change=0")
    today = date.today(); d = lambda n: (today - timedelta(days=n)).isoformat()

    def run(role: str, kind: str, data: dict) -> dict:
        with db.transaction() as c:
            user = c.execute("SELECT * FROM users WHERE id=?", (ids[role],)).fetchone()
            with ops.import_mode():
                res = ops.apply_op(c, user, Actor(user["id"], user["username"], user["role"], "démo", ""), {"op_id": secrets.token_hex(16), "type": kind, "data": data})
        assert res["status"] == "ok", (kind, res)
        return res

    prods = {}
    for name, cat, unit, price, reorder in (("Riz importé 50 kg", "Alimentaire", "sac", 350000, 30), ("Huile végétale 20 L", "Alimentaire", "bidon", 450000, 20), ("Sucre 50 kg", "Alimentaire", "sac", 380000, 25), ("Ciment 50 kg", "Construction", "sac", 85000, 150),
                                            ("Fer à béton 12 mm", "Construction", "barre", 95000, 100), ("Tôle ondulée", "Construction", "feuille", 60000, 30), ("Parpaing 15", "Construction", "pièce", 7000, 500), ("Peinture 20 L", "Construction", "seau", 420000, 10)):
        prods[name] = run("owner", "product.create", {"name": name, "category": cat, "unit": unit, "price": price, "reorder_level": reorder})["product"]["id"]
    sup = {k: run("owner", "supplier.create", {"name": n, "phone": ph, "zone": z})["supplier"]["id"] for k, n, ph, z in (("a", "Import Alimentaire Conakry", "224621000001", "Kaloum"), ("b", "Matériaux du Fouta", "224621000002", "Matoto"), ("c", "Quincaillerie Générale SARL", "224621000003", "Ratoma"))}
    due_soon, overdue = (today + timedelta(days=9)).isoformat(), (today - timedelta(days=6)).isoformat()
    for who, supplier, age, kind, lines, extra in (
            ("owner", "a", 80, "cash", [("Riz importé 50 kg", 40, 300000), ("Huile végétale 20 L", 60, 400000), ("Sucre 50 kg", 60, 340000)], {"pay_method": "Virement bancaire", "supplier_ref": "FA-2026-114"}),
            ("owner", "b", 78, "credit", [("Ciment 50 kg", 500, 70000), ("Fer à béton 12 mm", 300, 80000)], {"due_date": overdue, "paid_at_purchase": 20_000_000, "pay_method": "Virement bancaire", "supplier_ref": "FB-2026-031"}),
            ("accountant", "c", 70, "credit", [("Tôle ondulée", 100, 50000), ("Parpaing 15", 2000, 5000), ("Peinture 20 L", 30, 350000)], {"due_date": due_soon, "paid_at_purchase": 0, "pay_method": "Chèque", "supplier_ref": "FC-2026-007"}),
            ("owner", "b", 25, "credit", [("Ciment 50 kg", 100, 77000)], {"due_date": (today + timedelta(days=18)).isoformat(), "paid_at_purchase": 0, "pay_method": "Virement bancaire", "supplier_ref": "FB-2026-058"})):
        data = {"supplier_id": sup[supplier], "kind": kind, "purchase_date": d(age), "lines": [{"product_id": prods[n], "qty": q, "unit_cost": cst} for n, q, cst in lines], **extra}
        run(who, "purchase.create", data)
    run("owner", "settings.update", {"tax_rate": 18})                       # démonstration de la TVA par ligne : taux de l'entreprise 18 %, un produit exonéré
    run("owner", "product.update", {"id": prods["Riz importé 50 kg"], "base_version": 1, "changes": {"tax_rate": 0}})
    cl = {}
    for key, name, kind, zone, limit, who in (("diallo", "Boutique Diallo & Frères", "Commerçant (boutique)", "Madina", 5_000_000, "sales"), ("bongout", "Restaurant Le Bon Goût", "Restaurant / traiteur", "Kaloum", 3_000_000, "sales"),
                                              ("camara", "Quincaillerie Camara", "Revendeur matériaux", "Matoto", 15_000_000, None), ("soumah", "Entreprise BTP Soumah", "Entreprise BTP", "Ratoma", 20_000_000, None),
                                              ("barry", "M. Barry (construction de maison)", "Particulier", "Dubréka", 2_000_000, "sales"), ("bah", "Grossiste Bah", "Grossiste", "Matam", 30_000_000, None)):
        cl[key] = run("owner", "client.create", {"name": name, "kind": kind, "zone": zone, "credit_limit": limit, "phone": "2246200000" + str(len(cl) + 1).zfill(2),
                                                 "owner_user_id": ids["sales"] if who else None, "note": "Client fictif de démonstration"})["client"]["id"]
    P = prods
    plan = [  # (role, client, jours, type, [(produit, qté, remise)], échéance_en_jours, acompte, mode)
        ("sales", "diallo", 50, "credit", [("Riz importé 50 kg", 10, 0)], -20, 500_000, "Orange Money"), ("owner", "camara", 45, "credit", [("Ciment 50 kg", 100, 0), ("Fer à béton 12 mm", 40, 0)], 15, 3_000_000, "Virement bancaire"),
        ("sales", "bongout", 30, "cash", [("Huile végétale 20 L", 5, 0)], None, 0, "Espèces"), ("owner", "soumah", 28, "credit", [("Fer à béton 12 mm", 200, 2), ("Tôle ondulée", 60, 0)], -3, 0, "Chèque"),
        ("cashier", "barry", 20, "cash", [("Tôle ondulée", 20, 0)], None, 0, "MTN MoMo"), ("owner", "bah", 18, "credit", [("Sucre 50 kg", 40, 0)], 12, 0, "Orange Money"),
        ("sales", "diallo", 9, "cash", [("Riz importé 50 kg", 3, 0), ("Sucre 50 kg", 2, 0)], None, 0, "Espèces"), ("owner", "camara", 6, "credit", [("Peinture 20 L", 12, 0)], 24, 1_000_000, "Espèces"),
        ("cashier", "bongout", 3, "cash", [("Huile végétale 20 L", 2, 0)], None, 0, "Orange Money"), ("accountant", "bah", 1, "cash", [("Parpaing 15", 600, 2)], None, 0, "Virement bancaire"),
        ("owner", "soumah", 0, "credit", [("Ciment 50 kg", 300, 3)], 30, 2_000_000, "Virement bancaire"),
    ]
    made = []
    for role, who, age, kind, lines, due, paid, method in plan:
        data = {"client_id": cl[who], "kind": kind, "sale_date": d(age), "pay_method": method, "lines": [{"product_id": P[n], "qty": q, "discount_pct": r} for n, q, r in lines]}
        if kind == "credit": data.update(due_date=(today + timedelta(days=due)).isoformat(), paid_at_sale=paid)
        if age == 3: data["delivery_due"] = d(1)
        made.append(run(role, "sale.create", data)["sale"])
    run("owner", "payment.create", {"sale_id": made[0]["id"], "amount": 500_000, "method": "Orange Money", "reference": "OM-DEMO-1", "pay_date": d(35)})
    run("owner", "payment.create", {"sale_id": made[1]["id"], "amount": 2_000_000, "method": "Virement bancaire", "reference": "VIR-DEMO-1", "pay_date": d(20)})
    wrong = run("cashier", "payment.create", {"sale_id": made[3]["id"], "amount": 1_000_000, "method": "Espèces", "reference": "ERREUR", "pay_date": d(10)})["payment"]
    run("accountant", "payment.cancel", {"id": wrong["id"], "base_version": wrong["version"], "reason": "Saisi sur la mauvaise facture"})
    for who, age, cat, amount, method, payee, desc in (("owner", 28, "Loyer et charges locatives", 2_000_000, "Virement bancaire", "Propriétaire du dépôt", "Loyer du mois"), ("owner", 26, "Salaires et rémunérations", 3_500_000, "Espèces", "Équipe", "Salaires"),
                                                       ("cashier", 3, "Électricité, eau, carburant", 80_000, "Espèces", "Station", "Carburant du camion"), ("cashier", 2, "Transport et livraison", 60_000, "Espèces", "Chauffeur", "Livraison chantier"),
                                                       ("accountant", 4, "Téléphone et internet", 150_000, "Orange Money", "Orange", "Forfait internet"), ("cashier", 1, "Fournitures et petits achats", 45_000, "Espèces", "Papeterie", "Cahiers et stylos"),
                                                       ("owner", 1, "Publicité et marketing", 250_000, "MTN MoMo", "Radio locale", "Annonce de la semaine")):
        run(who, "expense.create", {"expense_date": d(age), "category": cat, "amount": amount, "method": method, "payee": payee, "description": desc})
    run("owner", "budget.set", {"category": "Électricité, eau, carburant", "amount": 70_000}); run("owner", "budget.set", {"category": "Loyer et charges locatives", "amount": 2_000_000})
    run("owner", "cash.move", {"kind": "owner_in", "amount": 1_000_000, "note": "Apport du patron pour la caisse", "move_date": d(12)})
    run("cashier", "cash.move", {"kind": "bank_deposit", "amount": 300_000, "note": "Bordereau de dépôt n° 4521", "move_date": d(3)})
    from . import cash_ops
    with db.read() as c:
        f = cash_ops.flows(c, None, d(1))
    expected = 1_000_000 + f["in_total"] - f["out_total"]                       # caisse de départ 1 000 000 ; hier, 5 000 de moins que prévu
    run("cashier", "cash.close", {"close_date": d(1), "opening": 1_000_000, "counted": expected - 5000, "note": "Pièce de 5 000 introuvable au comptage"})
    py = today.year - 1                                                           # un peu d'activité l'année dernière : permet d'essayer la clôture d'exercice
    run("owner", "purchase.create", {"supplier_id": sup["c"], "kind": "cash", "purchase_date": f"{py}-11-05", "pay_method": "Virement bancaire", "lines": [{"product_id": prods["Parpaing 15"], "qty": 200, "unit_cost": 5000}]})
    run("owner", "sale.create", {"client_id": cl["bah"], "kind": "credit", "sale_date": f"{py}-12-10", "due_date": f"{py}-12-30", "paid_at_sale": 0, "pay_method": "", "lines": [{"product_id": prods["Parpaing 15"], "qty": 20}]})
    run("owner", "expense.create", {"expense_date": f"{py}-12-20", "category": "Loyer et charges locatives", "amount": 100_000, "method": "Virement bancaire", "payee": "Propriétaire du dépôt", "description": "Loyer de décembre"})
    from . import bank_ops
    run("accountant", "bank.opening", {"account": "bank", "balance": 90_000_000, "date": d(100)})
    run("accountant", "bank.opening", {"account": "orange", "balance": 1_000_000, "date": d(100)})
    with db.read() as c:
        bank_items, om_items = bank_ops.book_items(c, "bank"), bank_ops.book_items(c, "orange")
    stmt = [{"date": i["date"], "label": ("VIR " if i["amount"] > 0 else "PRELEV ") + i["label"][:40], "amount": i["amount"], "reference": i["ref"]} for i in bank_items[:-1]]     # la dernière opération du livre n'est pas encore au relevé
    stmt += [{"date": d(5), "label": "FRAIS DE TENUE DE COMPTE", "amount": -15_000, "reference": "FRAIS-OCT"}, {"date": d(2), "label": "VIREMENT RECU SANS REFERENCE", "amount": 750_000, "reference": ""}]
    run("accountant", "bank.import", {"account": "bank", "lines": stmt})
    run("accountant", "bank.automatch", {"account": "bank"})
    run("accountant", "bank.import", {"account": "orange", "lines": [{"date": i["date"], "label": "OM " + i["label"][:40], "amount": i["amount"], "reference": i["ref"]} for i in om_items[:2]]})
    run("accountant", "bank.automatch", {"account": "orange"})
    run("accountant", "asset.create", {"name": "Ordinateur de bureau (démonstration)", "category": "Matériel informatique et de bureau", "origin": "purchase", "acquisition_date": d(20), "cost": 1_500_000, "life_years": 3, "pay_mode": "owner", "note": "Exemple fictif"})
    run("accountant", "asset.create", {"name": "Camion de livraison (démonstration)", "category": "Matériel de transport", "origin": "opening", "acquisition_date": d(0), "start_date": (today.replace(year=today.year - 2, day=1)).isoformat(), "cost": 18_000_000, "life_years": 5, "note": "Bien déjà possédé repris dans l'application : exemple fictif"})
    run("accountant", "provision.create", {"kind": "litigation", "label": "Litige de livraison (démonstration)", "amount": 500_000, "prov_date": d(10), "note": "Exemple fictif"})
    ins = run("accountant", "expense.create", {"expense_date": d(20), "category": "Assurances", "amount": 1_200_000, "method": "Virement bancaire", "payee": "Assureur (fictif)", "description": "Assurance annuelle payée d'avance"})
    run("accountant", "deferral.create", {"kind": "charge", "label": "Assurance annuelle payée d'avance (démonstration)", "expense_id": ins["expense"]["id"], "start_date": d(20), "end_date": (today - timedelta(days=20) + timedelta(days=364)).isoformat(), "note": "Exemple fictif"})
    run("owner", "quote.create", {"client_id": cl["camara"], "quote_date": d(4), "valid_until": (today + timedelta(days=26)).isoformat(), "lines": [{"product_id": P["Ciment 50 kg"], "qty": 200, "discount_pct": 3}, {"product_id": P["Fer à béton 12 mm"], "qty": 80}], "note": "Devis fictif de démonstration"})
    run("owner", "quote.create", {"client_id": cl["barry"], "quote_date": d(45), "valid_until": d(15), "lines": [{"product_id": P["Tôle ondulée"], "qty": 40}], "note": "Devis fictif expiré"})
    run("owner", "delivery.create", {"sale_id": made[10]["id"], "lines": [{"sale_position": 1, "qty": 120}], "recipient": "Chauffeur du client (fictif)", "note": "Première livraison, le reste suit"})
    run("cashier", "delivery.create", {"sale_id": made[4]["id"], "recipient": "M. Barry (fictif)"})
    run("owner", "expcat.create", {"key": "Gardiennage (catégorie ajoutée, fictive)", "account": "638", "nature": "Fixe"})
    dep2 = run("owner", "depot.create", {"name": "Magasin du marché"})["depot"]["id"]
    run("owner", "stransfer.create", {"product_id": prods["Sucre 50 kg"], "from_depot": "main", "to_depot": dep2, "qty": 8, "note": "Réassort du marché (fictif)"})
    run("owner", "purchase.create", {"supplier_id": sup["a"], "kind": "credit", "due_date": (today + timedelta(days=30)).isoformat(), "purchase_date": d(3), "supplier_ref": "FA-LOT-1",
                                     "lines": [{"product_id": prods["Huile végétale 20 L"], "qty": 12, "unit_cost": 400_000, "lot_no": "LOT-H-2410", "expiry_date": (today + timedelta(days=20)).isoformat(), "depot_id": dep2}]})
    run("accountant", "stock.inventory", {"counts": [{"product_id": prods["Riz importé 50 kg"], "counted": 25}, {"product_id": prods["Huile végétale 20 L"], "counted": 53}], "note": "Inventaire de démonstration", "date": d(2)})
    run("accountant", "creditnote.create", {"sale_id": made[5]["id"], "amount": 380_000, "settlement": "deduct", "reason": "2 sacs abîmés retournés"})


def login_as(role: str, device: str, ip: str):
    """Ouvre une session sur le compte de démonstration du rôle demandé (sans mot de passe)."""
    if role not in dict(DEMO_ROLES):
        raise services.ServiceError(404, "Rôle de démonstration inconnu.", "introuvable")
    return services.demo_login(f"demo_{role}", device, ip)
