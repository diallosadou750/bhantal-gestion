"""Scripts de démarrage automatique Windows : vérifications statiques (la création réelle d'une tâche planifiée se teste à la main, voir README)."""
import os
import re
import subprocess
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
SCRIPTS = ["demarrer_serveur.ps1", "installer_demarrage_auto.ps1", "desinstaller_demarrage_auto.ps1"]


def lire(n: str) -> str:
    return (RACINE / n).read_bytes().decode("utf-8-sig")


@pytest.mark.parametrize("nom", SCRIPTS)
def test_script_present_avec_bom_pour_les_accents(nom):
    assert (RACINE / nom).read_bytes()[:3] == b"\xef\xbb\xbf"                  # PowerShell 5.1 lit sinon l'UTF-8 comme de l'ANSI : accents abîmés


@pytest.mark.skipif(os.name != "nt", reason="PowerShell Windows")
@pytest.mark.parametrize("nom", SCRIPTS)
def test_syntaxe_powershell(nom):
    cmd = f"$e=$null;$t=$null;[void][System.Management.Automation.Language.Parser]::ParseFile('{RACINE / nom}',[ref]$t,[ref]$e); if($e.Count){{ $e | % {{ $_.Message }}; exit 1 }}"
    r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_le_serveur_n_est_jamais_ouvert_au_reseau_ni_en_demo():
    code = re.sub(r"^\s*#.*$", "", lire("demarrer_serveur.ps1"), flags=re.M)             # on ignore les commentaires
    assert "--lan" not in code and "--demo" not in code
    assert "Remove-Item Env:GESTION_DEMO" in code                                        # une variable de démonstration héritée est écartée
    assert "gestion.main --port" in code and "[int]$Port" in code


def test_tache_sans_droit_admin_ni_mot_de_passe():
    inst = lire("installer_demarrage_auto.ps1")
    assert "-RunLevel Limited" in inst and "-LogonType Interactive" in inst and "-AtLogOn" in inst
    assert not re.search(r"-Password|-Credential|ConvertTo-SecureString|RunLevel Highest", inst, re.I)
    assert "GestionLibre-Serveur" in inst and "journal_serveur.log" in lire("demarrer_serveur.ps1")


def test_desinstallation_ne_touche_pas_aux_donnees_ni_aux_autres_programmes():
    des = lire("desinstaller_demarrage_auto.ps1")
    assert "Remove-Item" not in des and "donnees" not in des.replace("Vos données", "")                # jamais de suppression de données
    assert "gestion\\.main" in des                                                                 # n'arrête qu'un serveur Gestion libre


def test_lancer_bat_ne_double_pas_le_serveur():
    bat = (RACINE / "lancer.bat").read_text(encoding="utf-8", errors="replace")
    assert "Get-NetTCPConnection -LocalPort 8090" in bat and "--lan" not in bat and "--demo" not in bat
