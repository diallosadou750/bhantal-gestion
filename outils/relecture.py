"""Feuille de relecture des traductions (à ouvrir dans Excel ou LibreOffice, remplie par un locuteur natif).

  .venv\\Scripts\\python.exe outils\\relecture.py export   -> outils/relecture_traductions.csv  (français | English | العربية | corrections)
  .venv\\Scripts\\python.exe outils\\relecture.py appliquer <fichier.csv>
  .venv\\Scripts\\python.exe outils\\relecture.py modele ff                    -> outils/modele_ff.csv  (feuille vierge pour une NOUVELLE langue : ff, man, sus…)
  .venv\\Scripts\\python.exe outils\\relecture.py construire ff "Pulaar (brouillon)" outils\\modele_ff.csv [rtl]

« appliquer » lit les colonnes de correction remplies, les écrit dans outils/trad/en_9_relecture.json et ar_9_relecture.json (elles passent après les autres
morceaux et les remplacent), puis reconstruit les deux langues. Une case de correction vide veut dire « traduction acceptée ».
Séparateur point-virgule, UTF-8 avec BOM (Excel).
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
I18N = RACINE / "gestion" / "static" / "i18n"
SORTIE = RACINE / "outils" / "relecture_traductions.csv"
NOMS = {"en": "English", "ar": "العربية"}


def charger(code: str) -> dict[str, str]:
    return json.loads((I18N / f"{code}.json").read_text(encoding="utf-8"))["textes"]


def exporter() -> None:
    en, ar = charger("en"), charger("ar")
    with SORTIE.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Français (source, ne pas modifier)", "English", "العربية", "Correction English", "Correction العربية", "Commentaire"])
        for k in sorted(set(en) | set(ar), key=str.lower): w.writerow([k, en.get(k, ""), ar.get(k, ""), "", "", ""])
    print(f"{len(set(en) | set(ar))} lignes -> {SORTIE.relative_to(RACINE)}")


def appliquer(fichier: str) -> None:
    corr: dict[str, dict[str, str]] = {"en": {}, "ar": {}}
    with open(fichier, encoding="utf-8-sig", newline="") as f:
        r = csv.reader(f, delimiter=";"); next(r, None)
        for ligne in r:
            if len(ligne) < 5: continue
            fr, ce, ca = ligne[0], ligne[3].strip(), ligne[4].strip()
            if ce: corr["en"][fr] = ce
            if ca: corr["ar"][fr] = ca
    for code, d in corr.items():
        dest = RACINE / "outils" / "trad" / f"{code}_9_relecture.json"
        ancien = json.loads(dest.read_text(encoding="utf-8")) if dest.exists() else {}
        ancien.update(d); dest.write_text(json.dumps(ancien, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{code} : {len(d)} correction(s) appliquée(s) ({len(ancien)} au total)")
        subprocess.run([sys.executable, str(RACINE / "outils" / "construire_langue.py"), code, NOMS[code]] + (["rtl"] if code == "ar" else []), check=True)


def modele(code: str) -> None:
    """Feuille vierge pour une nouvelle langue : les textes courts et fréquents (menus, boutons, libellés) d'abord, car ils suffisent à rendre l'application utilisable."""
    en = charger("en")
    cles = list(en)
    court = lambda k: 3 <= len(k.strip()) <= 20 and sum(c.isalpha() for c in k) >= 3 and not any(c.isdigit() for c in k) and "(" not in k
    cles.sort(key=lambda k: (not court(k), len(k), k.lower()))
    dest = RACINE / "outils" / f"modele_{code}.csv"
    with dest.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Français (source, ne pas modifier)", "English (aide)", f"Traduction {code}", "Commentaire", "Priorité (1 = à faire d'abord)"])
        for k in cles: w.writerow([k, en[k], "", "", 1 if court(k) else 2])
    print(f"{len(cles)} lignes ({sum(1 for k in cles if court(k))} prioritaires) -> {dest.relative_to(RACINE)}")


def construire(code: str, nom: str, fichier: str, rtl: bool) -> None:
    textes: dict[str, str] = {}
    with open(fichier, encoding="utf-8-sig", newline="") as f:
        r = csv.reader(f, delimiter=";"); tete = next(r, [])
        col = tete.index(f"Traduction {code}") if f"Traduction {code}" in tete else 2           # feuille à plusieurs langues : la colonne « Traduction <code> »
        for ligne in r:
            if len(ligne) > col and ligne[col].strip(): textes[ligne[0]] = ligne[col].strip()
    if not textes: sys.exit("Aucune traduction remplie dans la colonne « Traduction ».")
    (RACINE / "outils" / "trad" / f"{code}_1.json").write_text(json.dumps(textes, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{code} : {len(textes)} texte(s) traduit(s) sur {len(charger('en'))}")
    subprocess.run([sys.executable, str(RACINE / "outils" / "construire_langue.py"), code, nom] + (["rtl"] if rtl else []), check=True)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "modele": modele(sys.argv[2])
    elif len(sys.argv) >= 5 and sys.argv[1] == "construire": construire(sys.argv[2], sys.argv[3], sys.argv[4], len(sys.argv) > 5 and sys.argv[5] == "rtl")
    elif len(sys.argv) >= 2 and sys.argv[1] == "export": exporter()
    elif len(sys.argv) >= 3 and sys.argv[1] == "appliquer": appliquer(sys.argv[2])
    else: sys.exit(__doc__)
