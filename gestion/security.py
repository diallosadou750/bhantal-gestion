"""Mots de passe (scrypt), jetons de session, dates."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

_N, _R, _P = 2 ** 14, 8, 1


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or now()).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_iso(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return "scrypt${}${}${}${}${}".format(_N, _R, _P, base64.b64encode(salt).decode(), base64.b64encode(dk).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, dk = stored.split("$")
        calc = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(calc, base64.b64decode(dk))
    except Exception:
        return False


# Faux hachage pour que la durée de réponse ne révèle pas si un identifiant existe.
_DUMMY = hash_password("mot-de-passe-factice-0123456789")


def burn_time(password: str) -> None:
    verify_password(password, _DUMMY)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def password_problem(password: str, min_len: int) -> str | None:
    if len(password) < min_len:
        return f"Le mot de passe doit contenir au moins {min_len} caractères."
    if password.lower() == password or not any(ch.isdigit() for ch in password):
        return "Le mot de passe doit mélanger majuscules, minuscules et au moins un chiffre."
    return None


def hours(h: int) -> timedelta:
    return timedelta(hours=h)


# ---------------------------------------------------------------- double authentification (TOTP, RFC 6238)
def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_code(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    mac = hmac.new(key, step.to_bytes(8, "big"), hashlib.sha1).digest()
    o = mac[-1] & 0x0F
    return f"{(int.from_bytes(mac[o:o + 4], 'big') & 0x7FFFFFFF) % 1_000_000:06d}"


def verify_totp(secret: str, code: str, last_step: int | None = None, at: float | None = None) -> int | None:
    """Pas valide (tolérance ±1 pas de 30 s) ou None. Un pas déjà utilisé est refusé (un code ne sert qu'une fois)."""
    import time
    code = (code or "").strip().replace(" ", "")
    if not (code.isdigit() and len(code) == 6): return None
    now_step = int((time.time() if at is None else at) // 30)
    for step in (now_step, now_step - 1, now_step + 1):
        if last_step is not None and step <= last_step: continue
        if hmac.compare_digest(totp_code(secret, step), code): return step
    return None
