"""Chiffrement de la base de données au repos (SQLCipher : AES-256, une clé de 256 bits).

Ce que cela protège : quelqu'un qui copie le fichier de la base, une sauvegarde, un disque ou une clé USB volés, sans pouvoir ouvrir votre session Windows, ne lit RIEN
(clients, ventes, comptes, pièces jointes, journal : tout est dans la base, tout est chiffré).
Ce que cela ne protège pas : une personne qui utilise votre ordinateur avec votre session ouverte, un virus qui tourne sous votre compte, ni les exports CSV que vous créez
vous-même (ils sont en clair, c'est leur but). Ce n'est pas un remplaçant du mot de passe Windows ni de BitLocker : c'est une couche en plus.

Où est la clé (par ordre de priorité) :
  1. « phrase »  : variable d'environnement GESTION_CLE (une phrase de passe) ; la clé en est dérivée (scrypt) avec un sel public (chiffrement.sel). Rien n'est écrit sur le disque ;
                   sans la phrase, la base est irrécupérable. Recommandé pour un serveur.
  2. « dpapi »   : sous Windows, clé aléatoire protégée par Windows (DPAPI) pour VOTRE compte utilisateur : cle.dpapi n'est lisible que par ce compte sur cet ordinateur.
  3. « fichier » : ailleurs (Linux, Mac), clé aléatoire dans cle.key (droits 600). Protège des copies de la seule base ; gardez la clé à part.
Le mode choisi est noté dans chiffrement.json. PERDRE LA CLÉ = PERDRE LES DONNÉES : imprimez la clé de secours (python -m gestion.chiffrement afficher-cle --oui) et rangez-la en lieu sûr.

Usage :  python -m gestion.chiffrement statut | migrer | afficher-cle --oui
Au démarrage, une base existante non chiffrée est migrée automatiquement ; l'original n'est JAMAIS supprimé : il est déplacé dans « clair_a_supprimer » (à supprimer par vous,
quand vous aurez vérifié que tout fonctionne).
Désactiver (déconseillé) : GESTION_CHIFFREMENT=0. Le mode démonstration n'est jamais chiffré.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3 as _plain
import sys
from datetime import datetime
from functools import lru_cache
from pathlib import Path

try:
    from sqlcipher3 import dbapi2 as _cipher
except ImportError:                                     # bibliothèque absente : l'application fonctionne, mais SANS chiffrement (signalé dans « Mon compte »)
    _cipher = None

from . import config

MAGIC = b"SQLite format 3\x00"
META = "chiffrement.json"
CLEAR_DIR = "clair_a_supprimer"


class CipherError(RuntimeError):
    """Problème de clé ou de chiffrement : l'application refuse de démarrer plutôt que de créer une base vide ou en clair par erreur."""


def disponible() -> bool:
    return _cipher is not None


def actif() -> bool:
    if os.environ.get("GESTION_CHIFFREMENT", "1") in ("0", "off", "non"): return False
    if config.demo_mode(): return False
    return _cipher is not None


def sqlite():
    """Module de base de données à utiliser (sqlcipher3 si chiffrement actif, sinon sqlite3) ; mêmes classes, mêmes erreurs."""
    return _cipher if actif() else _plain


def est_en_clair(path: Path) -> bool:
    try:
        with open(path, "rb") as f: return f.read(16) == MAGIC
    except OSError:
        return False


# ---------------------------------------------------------------- clé

def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
    buf = ctypes.create_string_buffer(data, len(data))
    inp = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))); out = Blob()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    args = (ctypes.byref(inp), "Gestion libre", None, None, None, 0, ctypes.byref(out)) if protect else (ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out))
    if not fn(*args): raise CipherError("Windows refuse de déchiffrer la clé : elle appartient à un autre compte ou à un autre ordinateur.")
    try: return ctypes.string_at(out.pbData, out.cbData)
    finally: ctypes.windll.kernel32.LocalFree(out.pbData)


def _meta_path(d: Path) -> Path:
    return d / META


def _read_meta(d: Path) -> dict | None:
    p = _meta_path(d)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _phrase_key(d: Path, phrase: str) -> str:
    sel = d / "chiffrement.sel"
    if not sel.exists(): sel.write_bytes(secrets.token_bytes(16))
    return hashlib.scrypt(phrase.encode("utf-8"), salt=sel.read_bytes(), n=2 ** 15, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024).hex()


@lru_cache(maxsize=8)
def _cle(dossier: str, phrase: str | None) -> str:
    d = Path(dossier); meta = _read_meta(d)
    mode = (meta or {}).get("mode") or ("phrase" if phrase else "dpapi" if os.name == "nt" else "fichier")
    if mode == "phrase":
        if not phrase: raise CipherError("La base est protégée par une phrase de passe : définissez la variable d'environnement GESTION_CLE avant de lancer l'application.")
        key = _phrase_key(d, phrase)
    elif mode == "dpapi":
        f = d / "cle.dpapi"
        if f.exists(): key = _dpapi(f.read_bytes(), False).decode("ascii")
        elif meta: raise CipherError("Fichier cle.dpapi introuvable : sans lui (ou sans la clé de secours imprimée) la base ne peut pas être ouverte.")
        else:
            key = secrets.token_hex(32); f.write_bytes(_dpapi(key.encode("ascii"), True))
    else:
        f = d / "cle.key"
        if f.exists(): key = f.read_text(encoding="ascii").strip()
        elif meta: raise CipherError("Fichier cle.key introuvable : sans lui (ou sans la clé de secours imprimée) la base ne peut pas être ouverte.")
        else:
            key = secrets.token_hex(32); f.write_text(key, encoding="ascii")
            try: os.chmod(f, 0o600)
            except OSError: pass
    if not meta: _meta_path(d).write_text(json.dumps({"mode": mode, "algorithme": "SQLCipher 4, AES-256", "cree": datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")
    return key


def cle_hex() -> str:
    return _cle(str(config.data_dir()), os.environ.get("GESTION_CLE") or None)


def appliquer_cle(conn, key: str | None = None) -> None:
    """À appeler en tout premier sur une connexion SQLCipher."""
    conn.execute(f"PRAGMA key=\"x'{key or cle_hex()}'\"")


def ouvrir(path: Path, **kw):
    """Connexion chiffrée (ou ordinaire si le chiffrement est désactivé)."""
    mod = sqlite()
    conn = mod.connect(path, **kw)
    if mod is _cipher: appliquer_cle(conn)
    return conn


def verifier_demarrage() -> None:
    """Refuse les situations dangereuses : clé perdue, base chiffrée sans bibliothèque, base en clair non migrée."""
    d = config.data_dir(); db = config.db_path(); meta = _read_meta(d)
    if config.demo_mode() or os.environ.get("GESTION_CHIFFREMENT", "1") in ("0", "off", "non"): return
    if _cipher is None:
        if meta: raise CipherError("La base est chiffrée mais la bibliothèque sqlcipher3 est absente : pip install sqlcipher3-wheels")
        return                                            # pas de bibliothèque : base en clair, signalé dans l'interface
    if db.exists() and est_en_clair(db) and db.stat().st_size > 0:
        print("Chiffrement de la base existante (l'original est conservé dans « clair_a_supprimer »)…", flush=True)
        rapport = migrer()
        print(rapport["resume"], flush=True)
    elif db.exists() and not est_en_clair(db):
        _cle(str(d), os.environ.get("GESTION_CLE") or None)   # lève une erreur claire si la clé manque
        if not meta: raise CipherError("Base chiffrée mais chiffrement.json manquant : restaurez ce fichier ou utilisez la clé de secours.")


def statut() -> dict:
    d = config.data_dir(); db = config.db_path(); meta = _read_meta(d) or {}
    return {"postgresql": bool(config.db_url()), "bibliotheque": disponible(), "actif": actif(), "mode": meta.get("mode"), "fichier_chiffre": (not config.db_url()) and db.exists() and not est_en_clair(db),
            "originaux_a_supprimer": sorted(p.name for p in (d / CLEAR_DIR).glob("*")) if (d / CLEAR_DIR).exists() else []}


# ---------------------------------------------------------------- migration

def _tables(conn) -> list[str]:
    return [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]


def _chiffrer_fichier(src: Path, dest: Path, key: str) -> dict[str, int]:
    """Copie la base en clair src vers dest chiffrée, puis vérifie table par table que tout y est."""
    if dest.exists(): dest.unlink()
    c = _cipher.connect(src, isolation_level=None, timeout=5)
    try:
        try: c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except _cipher.Error: pass
        c.execute("BEGIN EXCLUSIVE"); c.execute("ROLLBACK")      # échoue si un serveur utilise encore la base
        c.execute(f"ATTACH DATABASE '{dest}' AS chiffre KEY \"x'{key}'\"")
        c.execute("SELECT sqlcipher_export('chiffre')")
        uv = c.execute("PRAGMA main.user_version").fetchone()[0]
        c.execute(f"PRAGMA chiffre.user_version={int(uv)}")
        n_src = {t: c.execute(f'SELECT COUNT(*) FROM main."{t}"').fetchone()[0] for t in _tables(c)}
        c.execute("DETACH DATABASE chiffre")
    except _cipher.OperationalError as e:
        raise CipherError(f"Impossible de verrouiller la base (le serveur tourne-t-il encore ? arrêtez-le d'abord) : {e}") from e
    finally:
        c.close()
    v = _cipher.connect(dest, isolation_level=None); v.execute(f"PRAGMA key=\"x'{key}'\"")
    try:
        n_dst = {t: v.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in _tables(v)}
        if v.execute("PRAGMA integrity_check").fetchone()[0] != "ok": raise CipherError("Contrôle d'intégrité de la copie chiffrée échoué : rien n'a été remplacé.")
    finally:
        v.close()
    if n_src != n_dst: raise CipherError(f"La copie chiffrée diffère de l'original ({n_src} ≠ {n_dst}) : rien n'a été remplacé.")
    return n_dst


def migrer() -> dict:
    """Chiffre la base et les sauvegardes existantes. Les originaux en clair sont déplacés (jamais supprimés) dans clair_a_supprimer."""
    if _cipher is None: raise CipherError("sqlcipher3 absent : pip install sqlcipher3-wheels")
    d = config.data_dir(); db = config.db_path(); key = cle_hex()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S"); clair = d / CLEAR_DIR; fait: list[str] = []; comptes: dict[str, int] = {}
    cibles = [db] if db.exists() and est_en_clair(db) else []
    if (d / "sauvegardes").exists(): cibles += sorted(p for p in (d / "sauvegardes").glob("gestion_*.sqlite3") if est_en_clair(p))     # les sauvegardes en clair seraient la fuite
    for f in cibles:
        tmp = f.with_name(f.name + ".chiffre")
        n = _chiffrer_fichier(f, tmp, key)
        if f == db: comptes = n
        clair.mkdir(exist_ok=True)
        for suffixe in ("", "-wal", "-shm"):
            s = f.with_name(f.name + suffixe)
            if s.exists(): shutil.move(str(s), str(clair / f"{s.name}.{stamp}"))
        os.replace(tmp, f); fait.append(f.name)
    resume = f"{len(fait)} fichier(s) chiffré(s) ({', '.join(fait) or 'rien à faire'}). Originaux en clair conservés dans {clair.name}\\ : supprimez-les quand vous aurez vérifié." if fait else "Rien à migrer : tout est déjà chiffré."
    return {"fichiers": fait, "lignes_par_table": comptes, "resume": resume}


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "statut"
    try:
        if cmd == "statut":
            print(json.dumps(statut(), ensure_ascii=False, indent=1))
        elif cmd == "migrer":
            print(migrer()["resume"])
        elif cmd == "afficher-cle":
            if "--oui" not in argv: print("Cette commande affiche la clé de secours EN CLAIR. Relancez avec --oui, seul devant l'écran, puis imprimez-la et rangez-la en lieu sûr."); return 2
            print(cle_hex())
        else:
            print(__doc__); return 2
    except CipherError as e:
        print("ERREUR :", e); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
