"""Langues des documents produits par le serveur (factures, reçus, devis, bons de livraison, PDF).

Même principe que l'interface : le français est la langue d'origine, la traduction vient des fichiers gestion/static/i18n/<code>.json (textes exacts + motifs à chiffres) ;
un texte sans traduction reste en français. La langue d'un document est : celle réglée pour l'entreprise (« Langues des documents »), ou, en mode automatique, celle de
l'appareil de la personne qui l'imprime (en-tête Accept-Language). Les PDF n'existent qu'en français et en anglais (la police intégrée ne sait pas dessiner l'arabe).
Cet algorithme reproduit celui de static/i18n.js ; les deux doivent rester d'accord (voir tests/test_i18n.py).
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from html import escape, unescape
from pathlib import Path

DIR = Path(__file__).resolve().parent / "static" / "i18n"
UNITS = re.compile(r"^(GNF|j|Ko|Mo|kg|L|mm|cm|m|h|x|[A-Z]{1,3}-)*$")
LETTERS = re.compile(r"[^\W\d_]+", re.U)


@lru_cache(maxsize=16)
def load(code: str) -> dict | None:
    f = DIR / f"{code}.json"
    if code == "fr" or not re.fullmatch(r"[a-z]{2,3}(-[A-Za-z]+)?", code) or not f.exists(): return None
    d = json.loads(f.read_text(encoding="utf-8"))
    d["_motifs"] = [(re.compile(m["re"]), m["to"]) for m in d.get("motifs", [])]
    return d


def languages() -> list[dict]:
    f = DIR / "langues.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else [{"code": "fr", "name": "Français"}]


def _expand(d: dict, m: re.Match, to: str, depth: int) -> str:
    def rep(x: re.Match) -> str:
        if x.group(1): return m.group(int(x.group(1))) or ""
        g = m.group(int(x.group(2)))
        return "" if g is None else (lookup(d, g.strip(), depth + 1) or g)
    return re.sub(r"\$(\d)|\{t:(\d)\}", rep, to)


def lookup(d: dict, k: str, depth: int = 0) -> str | None:
    t = d["textes"]
    if t.get(k): return t[k]
    for re_, to in d["_motifs"]:
        m = re_.search(k)
        if m: return _expand(d, m, to, depth)
    if depth > 2: return None
    if " · " in k:
        orig = k.split(" · "); parts = [lookup(d, p.strip(), depth + 1) for p in orig]
        if any(p is not None for p in parts): return " · ".join(p if p is not None else o for p, o in zip(parts, orig))
    i = k.find(" : ")
    if i > 0:
        left, right = k[:i], k[i + 3:]
        tl = t.get(left) or (t.get(left + " :") or "").rstrip(": ").rstrip() or None
        if tl: return f"{tl}: {lookup(d, right.strip(), depth + 1) or right}"
    return None


def tr(text: str, lang: str) -> str:
    """Traduit un texte (espaces de début et de fin conservés). Sans traduction : le texte d'origine."""
    d = load(lang)
    if d is None or not text: return text
    k = text.strip()
    if not k or not LETTERS.search(k): return text
    words = LETTERS.findall(k)
    if all(UNITS.match(w) for w in words):                                            # « 12 j », « 1 200 GNF » : seuls les motifs peuvent les réécrire
        for re_, to in d["_motifs"]:
            m = re_.search(k)
            if m and "{t:" not in to: return text.replace(k, re.sub(r"\$(\d)", lambda x: m.group(int(x.group(1))) or "", to), 1)
        return text
    r = lookup(d, k)
    return text.replace(k, r, 1) if r is not None else text


def tr_html(html: str, lang: str) -> str:
    """Traduit les textes d'une page HTML produite par le serveur (contenu entre balises, titre, attributs alt/title) ; règle lang et dir."""
    d = load(lang)
    if d is None: return html
    skip = {"script": 0, "style": 0}

    def text(m: re.Match) -> str:
        raw = m.group(1)
        if not raw.strip(): return m.group(0)
        return ">" + escape(tr(unescape(raw), lang), quote=False) + "<"
    out = re.sub(r">([^<>]+)<", text, html)
    out = re.sub(r'(<title>)([^<]*)(</title>)', lambda m: m.group(1) + escape(tr(unescape(m.group(2)), lang), quote=False) + m.group(3), out)
    out = out.replace('<html lang="fr">', f'<html lang="{lang}"' + (' dir="rtl"' if d.get("dir") == "rtl" else "") + ">", 1)
    return out


def pick(accept_language: str | None, doc_lang: str | None, allowed: set[str] | None = None) -> str:
    """Langue d'un document : réglage de l'entreprise, sinon langue de l'appareil, sinon français."""
    codes = {l["code"] for l in languages()}
    if allowed is not None: codes &= allowed | {"fr"}
    if doc_lang and doc_lang != "auto" and doc_lang in codes: return doc_lang
    for part in (accept_language or "").split(","):
        base = part.split(";")[0].strip().lower().split("-")[0]
        if base in codes: return base
    return "fr"
