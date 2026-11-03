"""Tests dans un vrai navigateur (Playwright + Edge ou Chrome déjà installés) : ils attrapent ce que les tests d'API ne voient pas
(un fichier JavaScript cassé, un onglet qui disparaît, une langue qui ne s'applique pas).

Lancer :  .venv\\Scripts\\python.exe -m pytest tests_navigateur -q
Ils démarrent leur propre serveur sur un port libre, avec une base jetable (aucun lien avec « donnees »).
"""
from __future__ import annotations

import uuid

import pytest
from conftest import H, PW, Page, aller


@pytest.fixture(scope="module")
def proprietaire(navigateur, serveur):
    """Compte propriétaire de test (valeurs jetables, base jetable) avec quelques données."""
    p = Page(navigateur, serveur)
    p.api("post", "/api/setup", data={"username": "patron", "full_name": "Patron Test", "password": PW})
    cl = p.push("client.create", {"id": str(uuid.uuid4()), "name": "Camara Test", "credit_limit": 5_000_000})["client"]
    pr = p.push("product.create", {"id": str(uuid.uuid4()), "name": "Ciment Test", "price": 100_000})["product"]
    vente = p.push("sale.create", {"client_id": cl["id"], "kind": "cash", "pay_method": "Espèces", "lines": [{"product_id": pr["id"], "qty": 2}]})["sale"]
    p.vente_id = vente["id"]
    yield p
    p.close()


def _ouvrir(p: Page, langue_stockee: str | None = None):
    p.page.goto("/")
    if langue_stockee:
        p.page.evaluate("l => localStorage.setItem('gestion_lang', l)", langue_stockee); p.page.reload()
    p.page.wait_for_selector("main h1, main h2", timeout=15000)


def test_ecran_de_connexion_sans_erreur(navigateur, serveur):
    p = Page(navigateur, serveur)
    p.page.goto("/"); p.page.wait_for_selector("form", timeout=15000)
    assert p.page.locator("input[type=password]").count() >= 1
    assert p.erreurs == []
    p.close()


def test_tous_les_onglets_s_ouvrent_sans_erreur(navigateur, serveur, proprietaire):
    p = proprietaire; p.erreurs.clear(); _ouvrir(p)
    rubriques = p.page.locator(".tabs .grp")
    assert rubriques.count() == 6, rubriques.count()                                    # Accueil, Commerce, Marchandises, Argent, Finances, Plus
    noms = []
    for i in range(rubriques.count()):
        rubriques.nth(i).click(); p.page.wait_for_timeout(300)
        sous = p.page.locator(".tabs .sous-select")
        options = sous.locator("option") if sous.count() else None
        for j in range(max(options.count() if options else 0, 1)):
            if options:
                noms.append(options.nth(j).inner_text().strip())
                sous.select_option(options.nth(j).get_attribute("value")); p.page.wait_for_timeout(400)
            assert p.page.locator("main").inner_text().strip(), f"onglet vide dans la rubrique {i}"
    assert len(noms) >= 14 and "Compta" in noms, noms                                   # régression déjà vue : un JS cassé faisait disparaître l'onglet Compta
    assert p.erreurs == [], p.erreurs


def test_tableau_de_bord_affiche_les_ventes(navigateur, serveur, proprietaire):
    p = proprietaire; _ouvrir(p)
    texte = p.page.locator("main").inner_text()
    assert "200 000" in texte.replace(" ", " ").replace(" ", " "), texte[:400]   # la vente de 2 × 100 000 GNF


def test_facture_imprimable_francais_anglais_arabe(navigateur, serveur, proprietaire):
    p = proprietaire; url = f"/api/invoice/{p.vente_id}"
    assert "FACTURE" in p.ctx.request.get(url, headers={"Accept-Language": "fr"}).text()
    assert "INVOICE" in p.ctx.request.get(url, headers={"Accept-Language": "en-US,en"}).text()
    ar = p.ctx.request.get(url, headers={"Accept-Language": "ar"}).text()
    assert 'dir="rtl"' in ar and "فاتورة" in ar
    pg = p.ctx.new_page(); pg.goto(url); assert pg.locator("h1").count() >= 1; pg.close()


@pytest.mark.parametrize("langue,attendu,sens", [("en", "Today's sales", "ltr"), ("ar", "مبيعات اليوم", "rtl")])
def test_langue_choisie_s_applique(navigateur, serveur, proprietaire, langue, attendu, sens):
    p = proprietaire; p.erreurs.clear(); _ouvrir(p, langue)
    assert attendu in p.page.locator("main").inner_text()
    assert p.page.evaluate("document.documentElement.lang") == langue
    assert (p.page.evaluate("document.documentElement.dir") or "ltr") == sens
    assert p.erreurs == [], p.erreurs
    p.page.evaluate("localStorage.removeItem('gestion_lang')")


def test_langue_automatique_suit_la_machine(navigateur, serveur, proprietaire):
    ctx = navigateur.new_context(locale="en-GB", base_url=serveur, service_workers="block")
    page = ctx.new_page()
    ctx.request.post("/api/login", headers=H, data={"username": "patron", "password": PW})
    page.goto("/"); page.wait_for_selector("main h1, main h2", timeout=15000)
    assert page.evaluate("document.documentElement.lang") == "en" and "Hello" in page.locator("main").inner_text()
    ctx.close()


def test_changement_de_langue_depuis_mon_compte(navigateur, serveur, proprietaire):
    p = proprietaire; _ouvrir(p)
    aller(p.page, "Mon compte")
    sel = p.page.locator("select").first
    sel.select_option("en"); p.page.wait_for_load_state("load"); p.page.wait_for_selector("main h1", timeout=15000)
    assert p.page.evaluate("document.documentElement.lang") == "en"
    p.page.evaluate("localStorage.removeItem('gestion_lang')")


PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


def test_facture_avec_logo_sans_violation_de_securite(navigateur, serveur, proprietaire):
    """La politique de sécurité interdit les attributs style : le logo et la case de signature doivent être mis en forme par des classes."""
    p = proprietaire; p.api("post", "/api/logo", data={"image": PNG})
    pg = p.ctx.new_page(); erreurs = []
    pg.on("console", lambda m: erreurs.append(m.text) if m.type == "error" else None)
    pg.goto(f"/api/invoice/{p.vente_id}")
    assert pg.evaluate("getComputedStyle(document.querySelector('img.doclogo')).maxHeight") == "70px"
    assert erreurs == [], erreurs
    pg.close(); p.api("delete", "/api/logo")


def test_mon_compte_sans_logo_n_appelle_pas_le_serveur(navigateur, serveur, proprietaire):
    p = proprietaire; demandes = []
    p.page.on("request", lambda r: demandes.append(r.url) if r.url.endswith("/api/logo") else None)
    _ouvrir(p); aller(p.page, "Mon compte"); p.page.wait_for_timeout(600)
    assert demandes == [] and "Aucun logo" in p.page.locator("main").inner_text()


def test_creer_une_immobilisation_degressive_depuis_le_formulaire(navigateur, serveur, proprietaire):
    """Remplit le vrai formulaire : méthode dégressive, aperçu de la première année, puis enregistrement et fiche."""
    p = proprietaire; p.erreurs.clear(); _ouvrir(p)
    aller(p.page, "Compta")
    p.page.get_by_role("button", name="Immobilisations").click()
    p.page.get_by_role("button", name="Nouvelle immobilisation").click()
    dlg = p.page.locator("dialog[open]")
    dlg.get_by_label("Désignation").fill("Camion test dégressif")
    dlg.get_by_label("Catégorie").select_option("Matériel de transport")
    dlg.get_by_label("Coût d'acquisition (hors taxe récupérable)").fill("10000000")
    assert not dlg.get_by_label("Coefficient dégressif", exact=False).is_visible()          # masqué tant que la méthode est linéaire
    dlg.get_by_label("Méthode d'amortissement").select_option("degressive")
    assert dlg.get_by_label("Coefficient dégressif", exact=False).input_value() == "1.75"    # 5 ans -> 1,75
    texte = dlg.inner_text().replace("\u202f", " ").replace("\u00a0", " ")
    assert "Dégressif (coefficient 1.75)" in texte and "3 500 000" in texte                  # 35 % de 10 000 000 la première année
    dlg.get_by_role("button", name="Enregistrer").click()
    p.page.wait_for_selector("text=Camion test dégressif", timeout=10000)
    p.page.get_by_role("link", name="IM-0001").click()
    fiche = p.page.locator("dialog[open]").inner_text()
    assert "dégressif (coefficient 1.75)" in fiche
    assert p.erreurs == [], p.erreurs


def test_page_aide_sections_et_navigation(navigateur, serveur, proprietaire):
    p = proprietaire; p.erreurs.clear(); _ouvrir(p)
    aller(p.page, "Aide")
    p.page.wait_for_selector("details.help-section", timeout=8000)
    sections = p.page.locator("details.help-section")
    assert sections.count() >= 15
    assert sections.nth(0).get_attribute("open") is not None and sections.nth(5).get_attribute("open") is None      # seule la première est ouverte
    sections.nth(5).locator("summary").click()
    assert sections.nth(5).get_attribute("open") is not None
    p.page.get_by_role("link", name="Ce que l'application ne fait pas").click()                                      # le sommaire ouvre la section
    assert p.page.locator("#aide-limites").get_attribute("open") is not None
    assert "liasse" in p.page.locator("#aide-limites").inner_text()
    p.page.get_by_role("button", name="Tout ouvrir").click()
    assert p.page.locator("details.help-section:not([open])").count() == 0
    p.page.get_by_role("button", name="Tout fermer").click()
    assert p.page.locator("details.help-section[open]").count() == 0
    assert p.erreurs == [], p.erreurs                                                                                  # aucune violation de la politique de sécurité


def test_aide_lisible_hors_connexion_apres_une_premiere_lecture(navigateur, serveur, proprietaire):
    p = proprietaire; _ouvrir(p)
    aller(p.page, "Aide"); p.page.wait_for_selector("details.help-section")
    aller(p.page, "Mon compte")
    p.ctx.set_offline(True)
    try:
        aller(p.page, "Aide")
        p.page.wait_for_selector("details.help-section", timeout=8000)
        assert "hors connexion" in p.page.locator("main").inner_text()
    finally:
        p.ctx.set_offline(False)


def test_aide_visible_pour_un_caissier_et_en_anglais(navigateur, serveur, proprietaire):
    p = proprietaire
    p.api("post", "/api/users", data={"username": "caissier1", "full_name": "Caissier Test", "role": "cashier", "temp_password": "Temporaire2026ab"})
    ctx = navigateur.new_context(base_url=serveur, locale="en-GB", service_workers="block")
    from conftest import H
    assert ctx.request.post("/api/login", headers=H, data={"username": "caissier1", "password": "Temporaire2026ab"}).ok
    assert ctx.request.post("/api/password", headers=H, data={"old": "Temporaire2026ab", "new": PW}).ok
    page = ctx.new_page(); page.set_default_timeout(8000); erreurs = []
    page.on("console", lambda m: erreurs.append(m.text) if m.type == "error" else None)
    page.goto("/"); page.wait_for_selector("[role=tab]")
    noms = "".join(page.locator(".tabs .grp").evaluate_all("l => l.map(e => e.dataset.noms)"))
    assert "|Aide|" in noms and "Équipe" not in noms                                          # le caissier n'a pas l'équipe, mais a l'aide
    page.locator('[data-noms*="|Aide|"]').click(); page.wait_for_timeout(200)
    page.get_by_role("tab", name="Help", exact=True).click(); page.wait_for_selector("details.help-section")    # « Aide » traduit
    assert page.get_by_role("button", name="Expand all").is_visible()
    assert erreurs == [], erreurs
    ctx.close()
