"""Extrait les textes français de l'application pour préparer ou compléter une traduction.

Usage (depuis le dossier du projet) :
    .venv\\Scripts\\python.exe outils\\extraire_textes.py                 # liste des textes à traduire, au format JSON, dans outils\\textes_fr.json
    .venv\\Scripts\\python.exe outils\\extraire_textes.py --langue en     # ce qui manque encore dans gestion\\static\\i18n\\en.json (couverture)

Deux sources : les textes de l'interface (gestion/static/*.js) et les textes produits par le serveur (messages d'erreur, documents imprimés, PDF : gestion/*.py, lus par
l'analyseur Python : seules les vraies chaînes de caractères du programme sont retenues, pas les commentaires ni les descriptions techniques des modules).
L'extraction peut proposer quelques faux textes techniques : on ne traduit que ce qui s'affiche vraiment.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
STR = re.compile(r"""(?P<q>['"`])(?P<s>(?:\\.|(?!(?P=q)).){2,700}?)(?P=q)""")
ACCENT = re.compile(r"[àâäçéèêëîïôöùûüÿœÀÂÇÉÈÊËÎÏÔÖÙÛÜŒ]")
FR_MOTS = re.compile(r"\b(le|la|les|un|une|des|du|de|d'|l'|vous|votre|vos|pas|pour|sur|dans|avec|est|sont|ne|par|ou|et|à|aucun|aucune|tout|toute|ce|cette|ces|nouvelle?|nouveau)\b", re.I)
TECH = re.compile(r"^[\w\-./:#@%?&=+*()\[\]{}<>$,;|\\^~!]+$")
EXCLU_DEBUT = ("/api/", "http", "data:", "application/", "text/", "image/", "X-", "Content-", "gestion-", "#", ".")
CODE = re.compile(r"[{}`]|=>|\bconst\b|\breturn\b|\bfunction\b|</?[a-z]+[ >]|\bSELECT\b|\bINSERT\b|\bUPDATE\b|\bCREATE\b|\bFROM\b|\bWHERE\b|\\n")


def candidat(s: str) -> bool:
    s = s.strip()
    if len(s) < 2 or len(s) > 700 or s.startswith(EXCLU_DEBUT) or CODE.search(s): return False
    if s[0].isupper() and s.isalpha() and len(s) >= 3: return True                       # mots seuls (« Annuler », « Client »…)
    if TECH.match(s) and not ACCENT.search(s): return False
    if re.fullmatch(r"[\d\s.,:%/€$+\-–—…·()]+", s): return False
    return bool(ACCENT.search(s) or (" " in s and FR_MOTS.search(s)) or (s[0].isupper() and len(s) > 3 and s.isalpha()))


def textes_js() -> set[str]:
    out = set()
    for f in sorted((RACINE / "gestion" / "static").glob("*.js")):
        if f.name in ("sw.js", "print.js", "i18n.js"): continue
        src = re.sub(r"/\*.*?\*/", "", f.read_text(encoding="utf-8-sig"), flags=re.S)
        src = re.sub(r"(?m)^\s*//.*$", "", src)
        for m in STR.finditer(src):
            s = m.group("s").replace("\\'", "'").replace('\\"', '"')
            if "${" in s: continue                                             # gabarits dynamiques : voir les motifs (« motifs ») du fichier de langue
            if candidat(s): out.add(s.strip())
    return out


def textes_py() -> set[str]:
    out = set()
    for f in sorted((RACINE / "gestion").glob("*.py")):
        if f.name in ("demo.py", "importer.py", "i18n.py", "db.py", "config.py"): continue
        tree = ast.parse(f.read_text(encoding="utf-8-sig"))
        docs = {id(n.body[0].value) for n in ast.walk(tree) if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)) and n.body and isinstance(n.body[0], ast.Expr) and isinstance(getattr(n.body[0], "value", None), ast.Constant)}
        fstr = {id(v) for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) for v in n.values if isinstance(v, ast.Constant)}
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
                s = n.value.strip()
                if id(n) in fstr or "{" in s or "%" in s: continue
                if candidat(s): out.add(s)
    return out


def tous() -> list[str]:
    return sorted(textes_js() | textes_py())


def main() -> None:
    ap = argparse.ArgumentParser(); ap.add_argument("--langue"); a = ap.parse_args()
    tout = tous()
    if not a.langue:
        (RACINE / "outils" / "textes_fr.json").write_text(json.dumps({t: "" for t in tout}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{len(tout)} textes extraits dans outils/textes_fr.json"); return
    f = RACINE / "gestion" / "static" / "i18n" / f"{a.langue}.json"
    if not f.exists(): sys.exit(f"{f} n'existe pas : copiez outils/textes_fr.json comme point de départ.")
    d = json.loads(f.read_text(encoding="utf-8")); done = {k for k, v in d.get("textes", {}).items() if v}
    manque = [t for t in tout if t not in done]
    print(f"Couverture {a.langue} : {len(tout) - len(manque)} / {len(tout)} ({round(100 * (len(tout) - len(manque)) / max(1, len(tout)))} %)")
    (RACINE / "outils" / f"manque_{a.langue}.json").write_text(json.dumps({t: "" for t in manque}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
