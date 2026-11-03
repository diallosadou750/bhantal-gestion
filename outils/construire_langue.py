"""Assemble le fichier de langue gestion/static/i18n/<code>.json à partir des morceaux de outils/trad/<code>_*.json (textes) et <code>_motifs.json (phrases à chiffres).

Usage :  .venv\\Scripts\\python.exe outils\\construire_langue.py en English
Un morceau de texte = un objet JSON {"texte français exact": "traduction"}. Un motif = {"re": "expression régulière sur le texte français", "to": "traduction avec $1, $2… et {t:1} pour traduire un morceau capturé"}.
Les motifs sont essayés dans l'ordre : mettre les plus précis avant les plus généraux. Voir TRADUCTIONS.md.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent


def main() -> None:
    if len(sys.argv) < 3: sys.exit(__doc__)
    code, name = sys.argv[1], sys.argv[2]; rtl = len(sys.argv) > 3 and sys.argv[3] == "rtl"
    textes: dict[str, str] = {}
    for f in sorted((RACINE / "outils" / "trad").glob(f"{code}_[0-9]*.json")): textes.update(json.loads(f.read_text(encoding="utf-8")))
    mf = RACINE / "outils" / "trad" / f"{code}_motifs.json"
    motifs = json.loads(mf.read_text(encoding="utf-8")) if mf.exists() else []
    for m in motifs: re.compile(m["re"])                                                  # une expression invalide ferait échouer l'interface : on s'en aperçoit ici
    out = {"code": code, "name": name, "dir": "rtl" if rtl else "ltr", "textes": textes, "motifs": motifs}
    dest = RACINE / "gestion" / "static" / "i18n" / f"{code}.json"; dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    lf = dest.parent / "langues.json"
    langs = json.loads(lf.read_text(encoding="utf-8")) if lf.exists() else [{"code": "fr", "name": "Français"}]
    if not any(l["code"] == code for l in langs): langs.append({"code": code, "name": name})
    lf.write_text(json.dumps(langs, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{code} : {len(textes)} textes, {len(motifs)} motifs → {dest.relative_to(RACINE)}")


if __name__ == "__main__":
    main()
