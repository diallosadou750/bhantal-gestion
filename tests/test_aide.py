from pathlib import Path

import pytest
from conftest import make_user

from gestion import aide
from gestion.permissions import ROLES

RACINE = Path(__file__).resolve().parent.parent


def test_aide_reservee_aux_personnes_connectees(client):
    assert client.get("/api/help").status_code == 401


@pytest.mark.parametrize("role", [r for r in ROLES if r != "owner"])
def test_aide_visible_par_tous_les_roles(owner, app_env, role):
    c, _ = make_user(owner, app_env, "u_" + role, role)
    r = c.get("/api/help")
    assert r.status_code == 200
    assert [s["id"] for s in r.json()["sections"]] == [s["id"] for s in aide.SECTIONS]


def test_contenu_complet_et_sans_donnees_d_entreprise(owner):
    secs = owner.get("/api/help").json()["sections"]
    ids = {s["id"] for s in secs}
    assert {"proprietaire", "equipe", "vente", "caisse", "banque", "sauvegarde", "langues", "demo", "limites", "probleme", "demarrage"} <= ids
    assert len(ids) == len(secs)                                                     # identifiants uniques (ancres du sommaire)
    texte = " ".join(s["intro"] + " ".join(s["items"]) + (s.get("note") or "") for s in secs)
    assert "CLÉ DE SECOURS" in texte and "liasse" in texte and "expert-comptable" in texte      # les mises en garde importantes y sont
    assert "afficher-cle --oui" in texte and "lancer_demo.bat" in texte
    assert all(s["title"] and s["intro"] and s["items"] for s in secs)


def test_regles_chiffrees_viennent_du_code():
    from gestion.permissions import CASHIER_EXPENSE_LIMIT, MAX_DISCOUNT_PCT
    texte = " ".join(" ".join(s["items"]) for s in aide.SECTIONS)
    assert f"{MAX_DISCOUNT_PCT['sales']} %" in texte and f"{CASHIER_EXPENSE_LIMIT:,}".replace(",", " ") in texte


def test_guide_markdown_a_jour():
    """GUIDE_UTILISATEUR.md est généré : s'il diffère, lancer .venv\\Scripts\\python.exe outils\\generer_guide.py."""
    f = RACINE / "GUIDE_UTILISATEUR.md"
    assert f.read_text(encoding="utf-8").replace("\r\n", "\n") == aide.guide_markdown()


def test_aucun_style_ni_script_en_ligne_dans_l_aide():
    js = (RACINE / "gestion" / "static" / "aide.js").read_text(encoding="utf-8")
    assert "style:" not in js and "setAttribute('style'" not in js and "innerHTML" not in js and "eval(" not in js
    assert '<script src="/aide.js"></script>' in (RACINE / "gestion" / "static" / "index.html").read_text(encoding="utf-8")
    assert "/aide.js" in (RACINE / "gestion" / "static" / "sw.js").read_text(encoding="utf-8")                   # disponible hors connexion
