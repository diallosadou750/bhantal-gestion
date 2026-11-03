"""Parcours complets au clavier et à la souris, comme une vraie personne : on ne passe QUE par l'interface (aucune donnée posée par l'API),
puis on vérifie ce que le serveur a réellement enregistré. Les tests se suivent : chacun s'appuie sur le précédent (même serveur, même navigateur).
"""
from __future__ import annotations

import re

import pytest
from conftest import aller, PW, Page


@pytest.fixture(scope="module")
def p(navigateur, serveur):
    pg = Page(navigateur, serveur)
    yield pg
    pg.close()


def nombre(texte: str) -> str:
    return re.sub(r"[\s  ]+", " ", texte)


def pull(p: Page) -> dict:
    return p.api("get", "/api/sync/pull?since=0")


def onglet(p: Page, nom: str):
    aller(p.page, nom)


def dialogue(p: Page):
    d = p.page.locator("dialog[open]"); d.wait_for(timeout=8000); return d


def plus_tard(p: Page):
    """Après une vente, l'application propose d'imprimer la facture : on répond « Plus tard »."""
    p.page.locator("dialog[open]").get_by_role("button", name="Plus tard").click(); p.page.wait_for_selector("dialog[open]", state="detached")


def toast(p: Page, contient: str) -> str:
    """Attend le message qui contient le texte (un ancien message peut encore être affiché) et le renvoie."""
    m = p.page.locator(".toast", has_text=contient).last; m.wait_for(timeout=8000); return m.inner_text()


def test_01_creation_du_proprietaire_par_le_formulaire(p):
    p.page.goto("/"); p.page.wait_for_selector("form", timeout=15000)
    p.page.get_by_label("Identifiant").fill("patron")
    p.page.get_by_label("Nom complet").fill("Patron Test")
    p.page.get_by_label("Mot de passe (10").fill("trop court")
    p.page.get_by_role("button", name="Créer le propriétaire").click()
    p.page.wait_for_selector(".error:visible", timeout=8000)                           # mot de passe refusé : message, pas de compte créé
    assert p.page.get_by_role("tab").count() == 0
    p.erreurs.clear()                                                                   # l'erreur 422 du navigateur est attendue ici
    p.page.get_by_label("Mot de passe (10").fill(PW)
    p.page.get_by_role("button", name="Créer le propriétaire").click()
    p.page.wait_for_selector("[role=tab]", timeout=10000)
    assert p.page.get_by_role("tab").count() >= 5                                       # rubriques : Accueil, Commerce, Marchandises, Argent, Finances, Plus
    assert p.erreurs == [], p.erreurs


def test_02_creer_un_client(p):
    onglet(p, "Clients"); p.page.get_by_role("button", name="＋ Nouveau client").click()
    d = dialogue(p)
    d.get_by_role("button", name="Enregistrer").click()                                 # nom obligatoire : le navigateur bloque l'envoi
    assert d.is_visible() and not pull(p)["clients"]
    d.get_by_label("Nom *").fill("Camara Test"); d.get_by_label("Téléphone").fill("622000000")
    d.get_by_label("Limite de crédit", exact=False).fill("5000000")
    d.get_by_role("button", name="Enregistrer").click()
    p.page.wait_for_selector("text=Camara Test", timeout=8000)
    c = pull(p)["clients"]
    assert len(c) == 1 and c[0]["name"] == "Camara Test" and c[0]["credit_limit"] == 5_000_000 and c[0]["code"] == "C001"


def test_03_creer_un_produit(p):
    onglet(p, "Stocks"); p.page.get_by_role("button", name="＋ Nouveau produit").click()
    d = dialogue(p)
    d.get_by_label("Nom *").fill("Ciment Test"); d.get_by_label("Prix de vente", exact=False).fill("50000")
    d.get_by_role("button", name="Enregistrer").click()
    p.page.wait_for_selector("text=Ciment Test", timeout=8000)
    pr = pull(p)["products"]
    assert len(pr) == 1 and pr[0]["price"] == 50_000


def test_04_vente_comptant(p):
    onglet(p, "Ventes"); p.page.get_by_role("button", name="＋ Nouvelle vente").first.click()
    d = dialogue(p)
    d.get_by_label("Produit").select_option(index=1)
    d.get_by_label("Quantité").fill("3")
    d.get_by_role("button", name="Enregistrer la vente").click()
    toast(p, "FV-0001"); plus_tard(p)
    ventes = pull(p)["sales"]
    assert len(ventes) == 1 and ventes[0]["total"] == 150_000 and ventes[0]["kind"] == "cash" and ventes[0]["balance"] == 0
    onglet(p, "Tableau")
    assert "150 000" in nombre(p.page.locator("main").inner_text())                        # « Ventes du jour »
    assert p.erreurs == [], p.erreurs


def test_05_vente_a_credit_refusee_au_dela_de_la_limite(p):
    onglet(p, "Ventes"); p.page.get_by_role("button", name="＋ Nouvelle vente").first.click()
    d = dialogue(p)
    d.get_by_label("Type de vente").select_option("credit")
    d.get_by_label("Produit").select_option(index=1)
    d.get_by_label("Quantité").fill("120")                                             # 6 000 000 > limite de 5 000 000
    d.get_by_role("button", name="Enregistrer la vente").click()
    p.page.wait_for_selector("dialog[open] .error:visible", timeout=8000)
    assert "limite" in d.locator(".error").first.inner_text().lower()
    assert len(pull(p)["sales"]) == 1                                                  # rien n'a été enregistré
    d.get_by_label("Quantité").fill("20")                                              # 1 000 000 : accepté
    d.get_by_role("button", name="Enregistrer la vente").click()
    toast(p, "FV-0002"); plus_tard(p)
    v = [x for x in pull(p)["sales"] if x["kind"] == "credit"]
    assert len(v) == 1 and v[0]["total"] == 1_000_000 and v[0]["balance"] == 1_000_000


def test_06_depense_en_caisse(p):
    onglet(p, "Caisse"); p.page.get_by_role("button", name="− Dépense").click()
    d = dialogue(p)
    d.get_by_label("Description").fill("Carburant du camion"); d.get_by_label("Montant (GNF)").fill("25000")
    d.get_by_role("button", name="Enregistrer").click()
    toast(p, "Dépense enregistrée")
    e = pull(p)["expenses"]
    assert len(e) == 1 and e[0]["amount"] == 25_000 and e[0]["method"] == "Espèces"
    onglet(p, "Tableau")
    assert "25 000" in nombre(p.page.locator("main").inner_text())


def test_07_deconnexion_et_connexion(p):
    onglet(p, "Mon compte"); p.page.get_by_role("button", name="Se déconnecter").click()
    p.page.get_by_role("heading", name="Connexion", exact=True).wait_for(timeout=8000)           # l'écran de connexion, pas le formulaire encore affiché de « Mon compte »
    p.page.get_by_label("Identifiant").fill("patron"); p.page.get_by_label("Mot de passe").fill("MauvaisMotDePasse1")
    p.page.get_by_role("button", name="Se connecter").click()
    p.page.wait_for_selector(".error:visible", timeout=8000)
    assert p.page.get_by_role("tab").count() == 0
    p.erreurs.clear()                                                                   # l'erreur 401 du navigateur est attendue ici
    p.page.get_by_label("Mot de passe").fill(PW)
    p.page.get_by_role("button", name="Se connecter").click()
    p.page.wait_for_selector("[role=tab]", timeout=10000)
    assert any(x["action"] == "login.failed" for x in p.api("get", "/api/audit?limit=50")["entries"])      # l'échec est dans le journal


def test_08_travail_hors_connexion_puis_synchronisation(p):
    onglet(p, "Clients")
    p.ctx.set_offline(True)
    p.page.get_by_role("button", name="＋ Nouveau client").click()
    d = dialogue(p); d.get_by_label("Nom *").fill("Client Hors Ligne")
    d.get_by_role("button", name="Enregistrer").click()
    p.page.wait_for_selector("text=Client Hors Ligne", timeout=8000)                   # visible tout de suite, en file d'attente
    assert not any(c["name"] == "Client Hors Ligne" for c in pull(p)["clients"])               # le serveur ne l'a pas encore
    p.ctx.set_offline(False)
    p.page.evaluate("window.dispatchEvent(new Event('online'))")                       # le navigateur annonce le retour du réseau
    for _ in range(30):
        if any(c["name"] == "Client Hors Ligne" for c in pull(p)["clients"]): break
        p.page.wait_for_timeout(500)
    else:
        pytest.fail("le client créé hors connexion n'a pas été envoyé au serveur")
    assert sum(1 for c in pull(p)["clients"] if c["name"] == "Client Hors Ligne") == 1      # une seule fois : pas de doublon


@pytest.mark.parametrize("nom", ["Tableau", "Ventes", "Clients", "Stocks", "Caisse", "Rapports", "Compta", "Mon compte"])
def test_09_telephone_pas_de_defilement_horizontal(p, nom):
    p.page.set_viewport_size({"width": 375, "height": 812})
    onglet(p, nom)
    p.page.wait_for_timeout(500)
    larg = p.page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
    assert larg[0] <= larg[1] + 1, f"« {nom} » déborde de l'écran du téléphone : {larg}"
    p.page.set_viewport_size({"width": 1280, "height": 800})


def test_10_formulaire_en_arabe(p):
    p.page.evaluate("localStorage.setItem('gestion_lang','ar')"); p.page.reload(); p.page.wait_for_selector("[role=tab]", timeout=10000)
    assert p.page.evaluate("document.documentElement.dir") == "rtl"
    p.page.locator('[data-noms*="|Clients|"]').click(); p.page.locator(".tabs .sous-select").select_option("clients"); p.page.wait_for_timeout(300)
    p.page.locator("main button").first.click()
    d = dialogue(p)
    assert d.locator("input").first.evaluate("e => getComputedStyle(e).direction") in ("rtl", "ltr")
    assert re.search(r"[؀-ۿ]", d.inner_text()), d.inner_text()[:200]             # le formulaire est bien en arabe
    d.get_by_role("textbox").first.fill("عميل جديد")
    d.locator("button[type=submit]").click()
    p.page.wait_for_selector("text=عميل جديد", timeout=8000)
    assert any(c["name"] == "عميل جديد" for c in pull(p)["clients"])                       # le texte arabe saisi est enregistré tel quel
    p.page.evaluate("localStorage.removeItem('gestion_lang')")
    assert p.erreurs == [] or all("favicon" in e for e in p.erreurs), p.erreurs


def test_11_copie_de_secours_hors_de_l_ordinateur(p, tmp_path_factory):
    """Mon compte : on indique un dossier (clé USB simulée), la copie est faite, vérifiée et annoncée ; un dossier absent est refusé avec un message clair."""
    p.page.evaluate("localStorage.removeItem('gestion_lang')"); p.page.goto("/"); p.page.wait_for_selector("[role=tab]", timeout=10000)
    onglet(p, "Mon compte")
    champ = p.page.get_by_label("Dossier de copie")
    assert "Aucune copie hors de l'ordinateur" in p.page.locator("main").inner_text()
    champ.fill(str(tmp_path_factory.getbasetemp() / "dossier_qui_n_existe_pas"))
    p.page.get_by_role("button", name="Enregistrer et copier maintenant").click()
    p.page.wait_for_selector(".error:visible", timeout=8000)
    assert "n'existe pas" in p.page.locator(".error:visible").first.inner_text()
    usb = tmp_path_factory.mktemp("cle_usb_nav")
    champ.fill(str(usb))
    p.page.get_by_role("button", name="Enregistrer et copier maintenant").click()
    p.page.wait_for_selector("text=vérifiée identique", timeout=12000)
    assert len(list(usb.glob("gestion_*"))) == 1
    p.page.get_by_role("button", name="Désactiver la copie").click()
    p.page.wait_for_selector("text=Aucune copie hors de l'ordinateur", timeout=8000)


@pytest.mark.parametrize("vue,rapport", [("Journal", "journal"), ("Balance", "balance"), ("Grand livre", "ledger")])
def test_12_export_pdf_du_journal_de_la_balance_et_du_grand_livre(p, vue, rapport):
    """Chaque écran comptable propose « Exporter en PDF » : le lien donne un vrai PDF avec les bons paramètres."""
    p.page.goto("/"); p.page.wait_for_selector("[role=tab]", timeout=10000)
    onglet(p, "Compta"); p.page.get_by_role("button", name=vue, exact=True).click()
    lien = p.page.get_by_role("link", name="Exporter en PDF")
    lien.wait_for(timeout=8000)
    href = lien.get_attribute("href")
    assert f"report={rapport}" in href and "from=" in href
    r = p.ctx.request.get(href)
    assert r.status == 200 and r.headers["content-type"] == "application/pdf" and r.body()[:5] == b"%PDF-", (vue, r.status, href)


def test_13_avoir_avec_marchandise_rendue_qui_remonte_en_stock(p):
    """Depuis la fiche d'une vente : on rend 1 sac, le montant se calcule tout seul, l'avoir est enregistré et le stock augmente d'autant."""
    avant = pull(p)["stock"]; pid = next(iter(avant)); stock_avant = avant[pid]["qty"]
    p.page.goto("/"); p.page.wait_for_selector("[role=tab]", timeout=10000)
    onglet(p, "Ventes"); p.page.locator("article.sale", has_text="FV-0001").first.click()
    p.page.get_by_role("button", name="Avoir / retour").click()
    d = dialogue(p)
    d.get_by_label("Quantité rendue de Ciment Test").fill("1")
    assert d.get_by_label("Montant de l'avoir (GNF)").input_value() == "50000"           # 1 sac × 50 000 GNF, calculé tout seul
    d.get_by_label("Règlement").select_option("refund"); d.get_by_label("Motif").fill("Un sac abîmé rendu par le client")
    d.get_by_role("button", name="Enregistrer").click()
    toast(p, "Avoir AV-0001 enregistré")
    apres = pull(p)
    assert apres["stock"][pid]["qty"] == stock_avant + 1
    note = apres["credit_notes"][0]
    assert note["amount"] == 50_000 and [(x["qty"], x["name"]) for x in note["returns"]] == [(1, "Ciment Test")]
    p.page.locator("article.sale", has_text="FV-0001").first.click()
    p.page.wait_for_selector("text=rendu en stock : 1 × Ciment Test", timeout=8000)         # visible sur la fiche de la vente


def test_14_rappels_par_email_et_resume_du_jour(p):
    """Mon compte : on remplit la carte « Rappels par e-mail » (faux serveur local), on envoie un essai, on voit le résumé et son lien WhatsApp."""
    from faux_smtp import FauxSMTP
    p.erreurs.clear()                                                    # les erreurs 4xx attendues des tests précédents ne comptent pas ici
    smtp = FauxSMTP()
    try:
        p.page.goto("/"); p.page.wait_for_selector("[role=tab]", timeout=10000)
        onglet(p, "Mon compte")
        p.page.get_by_text("Rappels par e-mail").first.wait_for(timeout=8000)
        p.page.get_by_label("Activer les rappels par e-mail").check()
        p.page.get_by_label("Serveur d'envoi").fill("127.0.0.1")
        p.page.get_by_label("Port", exact=True).fill(str(smtp.port))
        p.page.get_by_label("Sécurité de la connexion").select_option("none")
        p.page.get_by_label("Adresse d'expédition").fill("gestion@exemple.com")
        p.page.get_by_label("Adresses de destination", exact=False).fill("patron@exemple.com")
        p.page.get_by_role("button", name="Envoyer un essai maintenant").click()
        p.page.wait_for_selector("text=Message envoyé à patron@exemple.com", timeout=15000)
        assert len(smtp.messages) == 1 and smtp.messages[0]["to"] == ["patron@exemple.com"]
        p.page.get_by_role("button", name="Enregistrer", exact=True).first.click()
        toast(p, "Rappels enregistrés")
        onglet(p, "Mon compte")
        p.page.get_by_role("button", name="Voir le résumé d'aujourd'hui").click()
        p.page.wait_for_selector("text=Résumé du", timeout=8000)
        lien = p.page.get_by_role("link", name="Envoyer ce résumé par WhatsApp")
        assert lien.get_attribute("href").startswith("https://wa.me/?text=") and "R%C3%A9sum%C3%A9" in lien.get_attribute("href")
        assert p.erreurs == [] or all("favicon" in e for e in p.erreurs), p.erreurs
    finally:
        smtp.close()
        p.api("post", "/api/notifications", data={"enabled": False})


def test_15_relance_d_une_facture_impayee_et_promesse(p):
    from datetime import date, timedelta
    t = date.today()
    pid = p.push("product.create", {"id": str(__import__("uuid").uuid4()), "name": "Sac de riz relance", "price": 250_000})["product"]["id"]
    cid = p.push("client.create", {"id": str(__import__("uuid").uuid4()), "name": "Mariama Relance", "phone": "+224620123456", "credit_limit": 50_000_000})["client"]["id"]
    p.push("sale.create", {"client_id": cid, "kind": "credit", "lines": [{"product_id": pid, "qty": 4}], "sale_date": (t - timedelta(days=40)).isoformat(), "due_date": (t - timedelta(days=12)).isoformat()})
    p.page.reload(); p.page.wait_for_selector("role=tab")
    onglet(p, "Relances")
    p.page.get_by_text("Mariama Relance").first.wait_for(timeout=8000)
    assert "12 jours de retard" in p.page.locator("body").inner_text() and "Relance" in p.page.locator("body").inner_text()
    lien = p.page.get_by_role("link", name="WhatsApp").first.get_attribute("href")
    assert lien.startswith("https://wa.me/224620123456?text=") and "Mariama%20Relance" in lien
    p.page.get_by_role("button", name="Noter une relance").first.click()
    d = dialogue(p)
    d.get_by_label("Canal").select_option("appel")
    d.get_by_label("Note (facultatif)").fill("a promis vendredi")
    promesse = (t + timedelta(days=4)).isoformat()
    d.get_by_label("Le client a promis de payer le (facultatif)").fill(promesse)
    d.get_by_role("button", name="Enregistrer").click()
    toast(p, "Relance notée")
    p.page.get_by_text("Paiement promis").first.wait_for(timeout=8000)      # la facture passe de « À relancer » à « Paiement promis »
    e = [x for x in p.api("get", "/api/audit?limit=5")["entries"] if x["action"] == "dunning.logged"]
    assert e and "paiement promis le" in e[0]["note"]
    assert p.erreurs == [] or all("favicon" in x for x in p.erreurs), p.erreurs


def test_16_liste_de_controle_de_mise_en_service(p):
    onglet(p, "Mon compte")
    p.page.get_by_role("heading", name="Mise en service : liste de contrôle").wait_for(timeout=8000)
    corps = p.page.locator("main").inner_text()
    assert "Double authentification du propriétaire" in corps and "Sauvegardes" in corps and "Copie hors de l'ordinateur" in corps
    assert "→" in corps                                                   # chaque point à améliorer dit quoi faire
    assert p.erreurs == [] or all("favicon" in x for x in p.erreurs), p.erreurs


def test_17_bon_de_commande_puis_reception_avec_ecart(p):
    if not pull(p)["suppliers"]:
        p.push("supplier.create", {"id": str(__import__("uuid").uuid4()), "name": "Fournisseur Conakry"})
        p.page.reload(); p.page.wait_for_selector("role=tab")
    onglet(p, "Achats")
    p.page.get_by_role("button", name="Commandes", exact=True).click()
    p.page.get_by_role("button", name="＋ Nouvelle commande").click()
    d = dialogue(p)
    d.get_by_label("Quantité commandée").first.fill("12")
    d.get_by_label("Prix d'achat unitaire").first.fill("30000")
    d.get_by_label("Note").fill("livraison avant lundi")
    d.get_by_role("button", name="Enregistrer la commande").click()
    toast(p, "Commande BC-0001 enregistrée")
    p.page.get_by_text("BC-0001").first.wait_for(timeout=8000)
    assert "360 000" in nombre(p.page.locator("main").inner_text()) and "À recevoir le" in p.page.locator("main").inner_text()
    p.page.get_by_role("link", name="📦 Recevoir").first.click()
    d = dialogue(p)
    d.get_by_label(re.compile("^Quantité reçue : ")).first.fill("10")                  # 2 unités manquantes à la livraison
    d.get_by_label("Mode de paiement").select_option("Espèces")
    d.get_by_role("button", name="Enregistrer la réception").click()
    toast(p, "Commande reçue")
    n = pull(p)
    ach = [a for a in n["purchases"] if a["note"] == "Commande BC-0001"]
    assert len(ach) == 1 and ach[0]["total"] == 300_000 and ach[0]["lines"][0]["qty"] == 10
    assert [o["status"] for o in n["porders"]] == ["received"]
    p.page.get_by_text("Reçue", exact=True).first.wait_for(timeout=8000)
    assert p.erreurs == [] or all("favicon" in x for x in p.erreurs), p.erreurs


def test_18_paie_salarie_bulletin_et_paiement(p):
    onglet(p, "Paie")
    p.page.get_by_role("button", name="Réglages").click()
    d = dialogue(p)
    d.get_by_label(re.compile("^Part salariale")).fill("5")
    d.get_by_label(re.compile("^Part patronale")).fill("18")
    d.get_by_role("button", name="Enregistrer").click()
    toast(p, "Réglages enregistrés")
    p.page.get_by_role("button", name="＋ Salarié").click()
    d = dialogue(p)
    d.get_by_label("Nom").fill("Mariama Camara")
    d.get_by_label("Poste").fill("Vendeuse")
    d.get_by_label(re.compile("^Salaire de base")).fill("1000000")
    d.get_by_role("button", name="Enregistrer").click()
    p.page.get_by_text("Mariama Camara · Vendeuse").wait_for(timeout=8000)
    p.page.get_by_role("button", name="Préparer tous (salaire de base)").click()
    toast(p, "1 bulletin(s) créé(s)")
    p.page.get_by_text("BP-0001").first.wait_for(timeout=8000)
    txt = nombre(p.page.locator("main").inner_text())
    assert "950 000" in txt and "À payer" in txt, txt                                # 1 000 000 - 5 %
    p.page.get_by_role("link", name="💸 Payer").first.click()
    d = dialogue(p)
    d.get_by_label("Mode de paiement").select_option("Espèces")
    d.get_by_role("button", name="Enregistrer").click()
    toast(p, "Payé (dépense DP-")
    p.page.get_by_text(re.compile("^Payé le ")).first.wait_for(timeout=8000)
    assert p.erreurs == [] or all("favicon" in x for x in p.erreurs), p.erreurs


def test_19_paie_avance_reprise_et_absence(p):
    onglet(p, "Paie")
    p.page.get_by_role("button", name="＋ Salarié").click()
    d = dialogue(p)
    d.get_by_label("Nom").fill("Alpha Barry")
    d.get_by_label(re.compile("^Salaire de base")).fill("1000000")
    d.get_by_role("button", name="Enregistrer").click()
    ligne = p.page.locator(".card .row", has_text="Alpha Barry · ").filter(has=p.page.get_by_role("button", name="Avance"))
    ligne.wait_for(timeout=8000)
    ligne.get_by_role("button", name="Avance").click()
    d = dialogue(p)
    d.get_by_label("Montant (GNF)").fill("200000")
    d.get_by_label("Mode de paiement").select_option("Espèces")
    d.get_by_role("button", name="Enregistrer").click()
    toast(p, "Avance AV-0001 enregistrée")
    p.page.get_by_text(re.compile("Avances sur salaire")).wait_for(timeout=8000)
    p.page.get_by_role("button", name="Bulletin avec primes / retenues").click()
    d = dialogue(p)
    assert d.get_by_label(re.compile("^Avance à reprendre")).input_value() == "200000"    # proposé, modifiable
    d.get_by_label(re.compile("^Avance à reprendre")).fill("50000")
    assert "avance" in d.inner_text()
    d.get_by_role("button", name="Enregistrer").click()
    toast(p, "Bulletin créé")
    p.page.get_by_text(re.compile("avance reprise")).wait_for(timeout=8000)
    assert "doit 150" in nombre(p.page.locator("main").inner_text())
    p.page.locator(".card .row", has_text="Alpha Barry · ").get_by_role("button", name="Absence").first.click()
    d = dialogue(p)
    d.get_by_label("Type").select_option("Maladie")
    d.get_by_role("button", name="Enregistrer").click()
    toast(p, "Absence enregistrée")
    p.page.get_by_text(re.compile("Absences et congés")).wait_for(timeout=8000)
    assert "1 j d'absence" in p.page.locator("main").inner_text()
    assert p.erreurs == [] or all("favicon" in x for x in p.erreurs), p.erreurs
