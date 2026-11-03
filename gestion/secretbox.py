"""Secrets stockés dans la base (clé de double authentification, mot de passe d'envoi des e-mails) : chiffrés avec une clé gardée À CÔTÉ de la base.

Une copie de la base seule (sauvegarde volée, fichier envoyé par erreur, export) ne révèle donc plus ces secrets.
La clé de 32 octets vit dans le dossier des données : protégée par Windows (DPAPI, liée au compte) ou fichier à droits 600 ailleurs ;
la variable GESTION_SECRET_KEY (64 caractères hexadécimaux) la remplace. Les sauvegardes de l'application ne contiennent PAS cette clé.
Chiffrement : flux dérivé par HMAC-SHA256 (mode compteur) + étiquette HMAC-SHA256 (chiffrer puis authentifier) ; bibliothèque standard seulement.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from functools import lru_cache
from pathlib import Path

from . import config

PREFIX = "gl1:"


@lru_cache(maxsize=8)
def _key(dossier: str, env: str | None) -> bytes:
    if env:
        try: k = bytes.fromhex(env.strip())
        except ValueError: k = b""
        if len(k) != 32: raise RuntimeError("GESTION_SECRET_KEY doit contenir 64 caractères hexadécimaux.")
        return k
    d = Path(dossier)
    if os.name == "nt":
        from .chiffrement import _dpapi
        f = d / "secrets.dpapi"
        if f.exists(): return _dpapi(f.read_bytes(), False)
        k = secrets.token_bytes(32); f.write_bytes(_dpapi(k, True)); return k
    f = d / "secrets.key"
    if f.exists(): return bytes.fromhex(f.read_text(encoding="ascii").strip())
    k = secrets.token_bytes(32); f.write_text(k.hex(), encoding="ascii")
    try: os.chmod(f, 0o600)
    except OSError: pass
    return k


def _cle() -> bytes:
    return _key(str(config.data_dir()), os.environ.get("GESTION_SECRET_KEY") or None)


def _flux(key: bytes, nonce: bytes, n: int) -> bytes:
    out = b""; i = 0
    while len(out) < n:
        out += hmac.new(key, b"flux" + nonce + i.to_bytes(4, "big"), hashlib.sha256).digest(); i += 1
    return out[:n]


def est_scelle(valeur: str | None) -> bool:
    return bool(valeur) and valeur.startswith(PREFIX)


def sceller(clair: str | None) -> str | None:
    """Texte chiffré (préfixe gl1:) ; None et chaîne vide restent tels quels ; déjà scellé : inchangé."""
    if not clair or est_scelle(clair): return clair
    key = _cle(); nonce = secrets.token_bytes(16); data = clair.encode("utf-8")
    ct = bytes(a ^ b for a, b in zip(data, _flux(key, nonce, len(data))))
    tag = hmac.new(key, b"tag" + nonce + ct, hashlib.sha256).digest()
    return PREFIX + base64.urlsafe_b64encode(nonce + ct + tag).decode("ascii")


def ouvrir(valeur: str | None) -> str:
    """Texte en clair. Une valeur sans préfixe (ancienne base) est rendue telle quelle ; une valeur altérée ou chiffrée avec une autre clé donne '' (jamais d'exception)."""
    if not valeur: return ""
    if not est_scelle(valeur): return valeur
    try:
        raw = base64.urlsafe_b64decode(valeur[len(PREFIX):]); nonce, ct, tag = raw[:16], raw[16:-32], raw[-32:]
        key = _cle()
        if not hmac.compare_digest(tag, hmac.new(key, b"tag" + nonce + ct, hashlib.sha256).digest()): return ""
        return bytes(a ^ b for a, b in zip(ct, _flux(key, nonce, len(ct)))).decode("utf-8")
    except Exception:
        return ""


def sceller_existants(conn) -> int:
    """Au démarrage : chiffre les secrets encore en clair (bases créées avant cette version). Renvoie le nombre de valeurs converties."""
    n = 0
    for r in conn.execute("SELECT id, totp_secret FROM users WHERE totp_secret IS NOT NULL AND totp_secret<>''").fetchall():
        if not est_scelle(r["totp_secret"]):
            conn.execute("UPDATE users SET totp_secret=? WHERE id=?", (sceller(r["totp_secret"]), r["id"])); n += 1
    return n
