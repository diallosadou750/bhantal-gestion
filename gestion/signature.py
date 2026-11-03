"""Signature du journal d'audit avec une clé gardée HORS du serveur (Ed25519, RFC 8032, Python pur).

Principe : la chaîne de hachage du journal détecte une ligne modifiée, mais quelqu'un qui contrôle le serveur pourrait recalculer toute la chaîne.
Le propriétaire garde sur une clé USB (ou son téléphone) une clé PRIVÉE ; le serveur ne connaît que la clé PUBLIQUE. De temps en temps, le propriétaire
signe « l'empreinte du journal à l'entrée n° X » : le serveur conserve cette signature (un « sceau ») et ne peut pas en fabriquer d'autres.
Toute réécriture de l'historique avant un sceau change l'empreinte et se voit : le sceau ne correspond plus.

Outil du propriétaire (hors serveur) :
    python -m gestion.signature generer [cle_signature.txt]     crée la clé privée (à garder hors de l'ordinateur) et affiche la clé publique
    python -m gestion.signature signer cle_signature.txt '<point à signer>'    affiche le sceau à coller dans l'application
    python -m gestion.signature verifier fichier_sceaux.json base.db             contrôle des sceaux exportés contre une copie de la base
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys

# ------------------------------------------------------------------ Ed25519 (RFC 8032, section 6 : implémentation de référence)
_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _inv(x: int) -> int:
    return pow(x, _P - 2, _P)


def _recover_x(y: int, sign: int) -> int | None:
    if y >= _P: return None
    x2 = (y * y - 1) * _inv(_D * y * y + 1) % _P
    if x2 == 0: return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P: x = x * _I % _P
    if (x * x - x2) % _P: return None
    return _P - x if (x & 1) != sign else x


_GY = 4 * _inv(5) % _P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)


def _add(p, q):
    a = (p[1] - p[0]) * (q[1] - q[0]) % _P; b = (p[1] + p[0]) * (q[1] + q[0]) % _P
    c = 2 * p[3] * q[3] * _D % _P; d = 2 * p[2] * q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(s: int, p):
    q = (0, 1, 1, 0)
    while s > 0:
        if s & 1: q = _add(q, p)
        p = _add(p, p); s >>= 1
    return q


def _eq(p, q) -> bool:
    return (p[0] * q[2] - q[0] * p[2]) % _P == 0 and (p[1] * q[2] - q[1] * p[2]) % _P == 0


def _enc(p) -> bytes:
    zi = _inv(p[2]); x, y = p[0] * zi % _P, p[1] * zi % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _dec(s: bytes):
    if len(s) != 32: return None
    y = int.from_bytes(s, "little"); sign = y >> 255; y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


def _h(m: bytes) -> int:
    return int.from_bytes(hashlib.sha512(m).digest(), "little")


def _expand(seed: bytes):
    h = hashlib.sha512(seed).digest(); a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8; a |= 1 << 254
    return a, h[32:]


def public_from_seed(seed: bytes) -> bytes:
    return _enc(_mul(_expand(seed)[0], _G))


def sign(seed: bytes, msg: bytes) -> bytes:
    a, prefix = _expand(seed); pub = _enc(_mul(a, _G))
    r = _h(prefix + msg) % _L; rs = _enc(_mul(r, _G))
    k = _h(rs + pub + msg) % _L
    return rs + int.to_bytes((r + k * a) % _L, 32, "little")


def verify(pub: bytes, msg: bytes, sig: bytes) -> bool:
    try:
        if len(pub) != 32 or len(sig) != 64: return False
        A = _dec(pub); R = _dec(sig[:32])
        if A is None or R is None: return False
        s = int.from_bytes(sig[32:], "little")
        if s >= _L: return False
        k = _h(sig[:32] + pub + msg) % _L
        return _eq(_mul(s, _G), _add(R, _mul(k, A)))
    except Exception:
        return False


# ------------------------------------------------------------------ sceaux du journal
def message(entry_id: int, entry_hash: str) -> bytes:
    return f"gestion-libre-sceau-v1|{int(entry_id)}|{entry_hash}".encode("ascii")


def fingerprint(pub_hex: str) -> str:
    return hashlib.sha256(bytes.fromhex(pub_hex)).hexdigest()[:16]


def valid_pub(pub_hex: str) -> bool:
    try: b = bytes.fromhex((pub_hex or "").strip())
    except ValueError: return False
    return len(b) == 32 and _dec(b) is not None


def seal_ok(pub_hex: str, entry_id: int, entry_hash: str, sig_hex: str) -> bool:
    try: return verify(bytes.fromhex(pub_hex), message(entry_id, entry_hash), bytes.fromhex(sig_hex))
    except (ValueError, TypeError): return False


# ------------------------------------------------------------------ outil en ligne de commande (sur l'ordinateur du propriétaire, pas sur le serveur)
def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    if cmd == "generer":
        f = argv[1] if len(argv) > 1 else "cle_signature.txt"
        if os.path.exists(f): print(f"{f} existe déjà : je n'écrase pas une clé."); return 2
        seed = secrets.token_bytes(32)
        with open(f, "w", encoding="ascii") as fh: fh.write(seed.hex())
        try: os.chmod(f, 0o600)
        except OSError: pass
        print(f"Clé PRIVÉE écrite dans {f} : copiez-la sur une clé USB ou un support hors de l'ordinateur du serveur, puis effacez-la d'ici. Ne la partagez avec personne ; sans elle, plus de nouveaux sceaux.")
        print("Clé PUBLIQUE (à coller dans l'application, Journal > Sceaux) :"); print(public_from_seed(seed).hex()); return 0
    if cmd == "signer" and len(argv) >= 3:
        seed = bytes.fromhex(open(argv[1], encoding="ascii").read().strip())
        try: pt = json.loads(argv[2] if not os.path.exists(argv[2]) else open(argv[2], encoding="utf-8").read())
        except ValueError: print("Point à signer illisible (collez le texte tel que l'application l'affiche)."); return 2
        sig = sign(seed, message(pt["id"], pt["hash"])).hex()
        print(json.dumps({"id": pt["id"], "hash": pt["hash"], "sig": sig})); return 0
    if cmd == "verifier" and len(argv) >= 3:
        import sqlite3
        d = json.load(open(argv[1], encoding="utf-8")); pub = d["public_key"]; bad = 0
        c = sqlite3.connect(argv[2])
        for s in d["seals"]:
            r = c.execute("SELECT hash FROM audit_log WHERE id=?", (s["id"],)).fetchone()
            ok = seal_ok(pub, s["id"], s["hash"], s["sig"]) and r is not None and r[0] == s["hash"]
            print(("OK      " if ok else "ALTÉRÉ  ") + f"entrée {s['id']}"); bad += not ok
        print("Tous les sceaux correspondent." if not bad else f"{bad} sceau(x) ne correspondent pas : l'historique a été modifié."); return 1 if bad else 0
    print(__doc__); return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
