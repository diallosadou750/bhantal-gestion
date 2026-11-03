"""Comptes, connexions, sessions et administration des utilisateurs. Chaque action sensible est journalisée."""
from __future__ import annotations

import re
import sqlite3

from . import audit, config, db, secretbox, security
from .audit import Actor
from .permissions import ROLES

USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,40}$")


class ServiceError(Exception):
    def __init__(self, status: int, message: str, code: str = "erreur"):
        super().__init__(message)
        self.status, self.message, self.code = status, message, code


def actor_of(user: sqlite3.Row | None, device: str = "", ip: str = "") -> Actor:
    if user is None:
        return Actor(None, None, None, device, ip)
    return Actor(user["id"], user["username"], user["role"], device, ip)


def public_user(u: sqlite3.Row) -> dict:
    return {"id": u["id"], "username": u["username"], "full_name": u["full_name"], "role": u["role"], "role_label": ROLES.get(u["role"], u["role"]),
            "active": bool(u["active"]), "must_change": bool(u["must_change"]), "created_at": u["created_at"], "totp_on": bool(u["totp_on"])}


def users_exist() -> bool:
    with db.read() as c:
        return c.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None


def _check_password(pw: str) -> None:
    problem = security.password_problem(pw, config.MIN_PASSWORD_LENGTH)
    if problem:
        raise ServiceError(422, problem, "mot_de_passe_faible")


def _check_identity(username: str, full_name: str) -> tuple[str, str]:
    username, full_name = (username or "").strip(), (full_name or "").strip()
    if not USERNAME_RE.match(username):
        raise ServiceError(422, "Identifiant : 3 à 40 caractères (lettres, chiffres, point, tiret, souligné).", "identifiant_invalide")
    if not (1 <= len(full_name) <= 80):
        raise ServiceError(422, "Le nom complet est obligatoire (80 caractères maximum).", "nom_invalide")
    return username, full_name


def setup_owner(username: str, full_name: str, password: str, device: str, ip: str) -> dict:
    username, full_name = _check_identity(username, full_name)
    _check_password(password)
    with db.transaction() as c:
        if c.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            raise ServiceError(409, "L'installation est déjà faite.", "deja_installe")
        cur = c.execute("INSERT INTO users(username,full_name,role,pw_hash,created_at) VALUES(?,?,?,?,?)",
                        (username, full_name, "owner", security.hash_password(password), security.iso()))
        u = c.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
        audit.record(c, actor_of(u, device, ip), "setup.owner_created", "user", str(u["id"]), None, {"username": username, "role": "owner"})
        return public_user(u)


def login(username: str, password: str, device: str, ip: str, code: str | None = None) -> tuple[str, dict]:
    username = (username or "").strip()
    generic = ServiceError(401, "Identifiant ou mot de passe incorrect.", "identifiants")
    with db.transaction() as c:
        u = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if u is None:
            security.burn_time(password or "")
            audit.record(c, Actor(None, username[:40], None, device, ip), "login.failed", note="identifiant inconnu")
            c.execute("COMMIT"); c.execute("BEGIN IMMEDIATE")
            raise generic
        if u["locked_until"] and security.parse_iso(u["locked_until"]) > security.now():
            audit.record(c, actor_of(u, device, ip), "login.blocked", note="compte verrouillé")
            c.execute("COMMIT"); c.execute("BEGIN IMMEDIATE")
            raise ServiceError(423, f"Compte verrouillé {config.LOCK_MINUTES} minutes après trop d'essais. Réessayez plus tard.", "verrouille")
        ok = security.verify_password(password or "", u["pw_hash"])
        if not ok or not u["active"]:
            failed = u["failed"] + 1
            locked = security.iso(security.now() + security.timedelta(minutes=config.LOCK_MINUTES)) if failed >= config.LOCK_AFTER_FAILURES else None
            c.execute("UPDATE users SET failed=?, locked_until=? WHERE id=?", (0 if locked else failed, locked, u["id"]))
            audit.record(c, actor_of(u, device, ip), "login.locked" if locked else "login.failed", note="compte désactivé" if ok else None)
            c.execute("COMMIT"); c.execute("BEGIN IMMEDIATE")
            raise generic
        if u["totp_on"]:
            if not (code or "").strip():
                audit.record(c, actor_of(u, device, ip), "login.2fa_required")
                c.execute("COMMIT"); c.execute("BEGIN IMMEDIATE")
                raise ServiceError(401, "Entrez le code à 6 chiffres de votre application d'authentification.", "code_requis")
            step = security.verify_totp(secretbox.ouvrir(u["totp_secret"]), code, u["totp_last"])
            if step is None:
                failed = u["failed"] + 1
                locked = security.iso(security.now() + security.timedelta(minutes=config.LOCK_MINUTES)) if failed >= config.LOCK_AFTER_FAILURES else None
                c.execute("UPDATE users SET failed=?, locked_until=? WHERE id=?", (0 if locked else failed, locked, u["id"]))
                audit.record(c, actor_of(u, device, ip), "login.locked" if locked else "login.failed", note="code de double authentification invalide")
                c.execute("COMMIT"); c.execute("BEGIN IMMEDIATE")
                raise ServiceError(401, "Code de vérification incorrect.", "code_invalide")
            c.execute("UPDATE users SET totp_last=? WHERE id=?", (step, u["id"]))
        c.execute("UPDATE users SET failed=0, locked_until=NULL WHERE id=?", (u["id"],))
        token = security.new_token()
        c.execute("INSERT INTO sessions(token_hash,user_id,created_at,expires_at,device,ip) VALUES(?,?,?,?,?,?)",
                  (security.token_hash(token), u["id"], security.iso(), security.iso(security.now() + security.hours(config.SESSION_HOURS)), device, ip))
        audit.record(c, actor_of(u, device, ip), "login.ok")
        return token, public_user(u)


def demo_login(username: str, device: str, ip: str) -> tuple[str, dict]:
    """Session sans mot de passe, réservée au mode démonstration (comptes fictifs, base jetable)."""
    if not config.demo_mode():
        raise ServiceError(404, "Introuvable.", "introuvable")
    with db.transaction() as c:
        u = c.execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()
        if u is None:
            raise ServiceError(404, "Compte de démonstration introuvable.", "introuvable")
        token = security.new_token()
        c.execute("INSERT INTO sessions(token_hash,user_id,created_at,expires_at,device,ip) VALUES(?,?,?,?,?,?)",
                  (security.token_hash(token), u["id"], security.iso(), security.iso(security.now() + security.hours(config.SESSION_HOURS)), device, ip))
        audit.record(c, actor_of(u, device, ip), "login.demo", note="entrée en mode démonstration")
        return token, public_user(u)


def authenticate(token: str | None) -> sqlite3.Row | None:
    if not token:
        return None
    with db.read() as c:
        row = c.execute("""SELECT u.*, s.expires_at AS s_exp FROM sessions s JOIN users u ON u.id=s.user_id
                           WHERE s.token_hash=? AND s.revoked=0""", (security.token_hash(token),)).fetchone()
    if row is None or not row["active"] or security.parse_iso(row["s_exp"]) <= security.now():
        return None
    return row


def logout(token: str, user: sqlite3.Row, device: str, ip: str) -> None:
    with db.transaction() as c:
        c.execute("UPDATE sessions SET revoked=1 WHERE token_hash=?", (security.token_hash(token),))
        audit.record(c, actor_of(user, device, ip), "logout")


def change_password(user: sqlite3.Row, old: str, new: str, device: str, ip: str) -> None:
    _check_password(new)
    with db.transaction() as c:
        u = c.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
        if not security.verify_password(old or "", u["pw_hash"]):
            audit.record(c, actor_of(u, device, ip), "password.change_failed")
            c.execute("COMMIT"); c.execute("BEGIN IMMEDIATE")
            raise ServiceError(403, "Ancien mot de passe incorrect.", "ancien_incorrect")
        if old == new:
            raise ServiceError(422, "Le nouveau mot de passe doit être différent de l'ancien.", "identique")
        c.execute("UPDATE users SET pw_hash=?, must_change=0 WHERE id=?", (security.hash_password(new), u["id"]))
        audit.record(c, actor_of(u, device, ip), "password.changed", "user", str(u["id"]))


def list_sessions(user: sqlite3.Row, token: str, everyone: bool) -> list[dict]:
    """Connexions ouvertes : les siennes, ou (propriétaire) celles de tout le monde."""
    th = security.token_hash(token)
    with db.read() as c:
        sql = "SELECT s.rowid id, s.*, u.username, u.full_name FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.revoked=0 AND s.expires_at>?" + ("" if everyone else " AND s.user_id=?")
        rows = c.execute(sql, (security.iso(),) if everyone else (security.iso(), user["id"])).fetchall()
    return [{"id": r["id"], "user": r["username"], "full_name": r["full_name"], "created_at": r["created_at"], "expires_at": r["expires_at"], "device": r["device"], "ip": r["ip"], "current": r["token_hash"] == th}
            for r in sorted(rows, key=lambda r: r["created_at"], reverse=True)]


def revoke_session(user: sqlite3.Row, token: str, session_id, others: bool, can_all: bool, device: str, ip: str) -> int:
    th = security.token_hash(token)
    with db.transaction() as c:
        if others:
            rows = c.execute("SELECT rowid id, * FROM sessions WHERE user_id=? AND revoked=0 AND token_hash<>?", (user["id"], th)).fetchall()
        else:
            r = c.execute("SELECT rowid id, * FROM sessions WHERE rowid=? AND revoked=0", (session_id,)).fetchone()
            if r is None or (r["user_id"] != user["id"] and not can_all): raise ServiceError(404, "Connexion introuvable.", "introuvable")
            rows = [r]
        for r in rows:
            c.execute("UPDATE sessions SET revoked=1 WHERE rowid=?", (r["id"],))
            owner = c.execute("SELECT username FROM users WHERE id=?", (r["user_id"],)).fetchone()["username"]
            audit.record(c, actor_of(user, device, ip), "session.revoked", "session", str(r["id"]), None, {"compte": owner, "appareil": (r["device"] or "")[:80], "adresse": r["ip"]},
                         note="fermée depuis une autre connexion" + (" (tout le monde peut la voir ici)" if r["user_id"] != user["id"] else ""))
        return len(rows)


def totp_begin(user: sqlite3.Row, device: str, ip: str) -> dict:
    """Prépare la double authentification : génère une clé (pas encore active tant qu'un code correct n'est pas saisi)."""
    with db.transaction() as c:
        u = c.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
        if u["totp_on"]: raise ServiceError(409, "La double authentification est déjà active.", "deja_active")
        secret = security.new_totp_secret()
        c.execute("UPDATE users SET totp_secret=?, totp_last=NULL WHERE id=?", (secretbox.sceller(secret), u["id"]))
        audit.record(c, actor_of(u, device, ip), "2fa.begin", "user", str(u["id"]))
    return {"secret": secret, "uri": f"otpauth://totp/Gestion%20libre:{u['username']}?secret={secret}&issuer=Gestion%20libre&digits=6&period=30"}


def totp_enable(user: sqlite3.Row, code: str, device: str, ip: str) -> None:
    with db.transaction() as c:
        u = c.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
        if u["totp_on"] or not u["totp_secret"]: raise ServiceError(409, "Commencez par demander une clé.", "pas_de_cle")
        step = security.verify_totp(secretbox.ouvrir(u["totp_secret"]), code)
        if step is None:
            audit.record(c, actor_of(u, device, ip), "2fa.enable_failed")
            c.execute("COMMIT"); c.execute("BEGIN IMMEDIATE")
            raise ServiceError(422, "Code incorrect : vérifiez l'heure du téléphone et réessayez.", "code_invalide")
        c.execute("UPDATE users SET totp_on=1, totp_last=? WHERE id=?", (step, u["id"]))
        audit.record(c, actor_of(u, device, ip), "2fa.enabled", "user", str(u["id"]))


def totp_disable(user: sqlite3.Row, password: str, code: str, device: str, ip: str) -> None:
    with db.transaction() as c:
        u = c.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
        if not u["totp_on"]: raise ServiceError(409, "La double authentification n'est pas active.", "pas_active")
        if not security.verify_password(password or "", u["pw_hash"]) or security.verify_totp(secretbox.ouvrir(u["totp_secret"]), code, u["totp_last"]) is None:
            audit.record(c, actor_of(u, device, ip), "2fa.disable_failed")
            c.execute("COMMIT"); c.execute("BEGIN IMMEDIATE")
            raise ServiceError(403, "Mot de passe ou code incorrect.", "refuse")
        c.execute("UPDATE users SET totp_on=0, totp_secret=NULL, totp_last=NULL WHERE id=?", (u["id"],))
        audit.record(c, actor_of(u, device, ip), "2fa.disabled", "user", str(u["id"]))


def totp_reset(admin: sqlite3.Row, user_id: int, device: str, ip: str) -> None:
    """Le propriétaire réinitialise la double authentification d'un membre qui a perdu son téléphone."""
    with db.transaction() as c:
        u = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if u is None: raise ServiceError(404, "Utilisateur introuvable.", "introuvable")
        c.execute("UPDATE users SET totp_on=0, totp_secret=NULL, totp_last=NULL WHERE id=?", (u["id"],))
        c.execute("UPDATE sessions SET revoked=1 WHERE user_id=?", (u["id"],))
        audit.record(c, actor_of(admin, device, ip), "2fa.reset", "user", str(u["id"]), {"double authentification": bool(u["totp_on"])}, {"double authentification": False}, note=f"réinitialisée pour {u['username']} (sessions fermées)")


def list_users() -> list[dict]:
    with db.read() as c:
        return [public_user(u) for u in c.execute("SELECT * FROM users ORDER BY username")]


def _owners_left(c: sqlite3.Connection, excluding: int) -> int:
    return c.execute("SELECT COUNT(*) n FROM users WHERE role='owner' AND active=1 AND id<>?", (excluding,)).fetchone()["n"]


def create_user(admin: sqlite3.Row, username: str, full_name: str, role: str, temp_password: str, device: str, ip: str) -> dict:
    username, full_name = _check_identity(username, full_name)
    if role not in ROLES:
        raise ServiceError(422, "Rôle inconnu.", "role_invalide")
    _check_password(temp_password)
    with db.transaction() as c:
        if c.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            raise ServiceError(409, "Cet identifiant existe déjà.", "identifiant_pris")
        cur = c.execute("INSERT INTO users(username,full_name,role,pw_hash,must_change,created_at) VALUES(?,?,?,?,1,?)",
                        (username, full_name, role, security.hash_password(temp_password), security.iso()))
        u = c.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
        audit.record(c, actor_of(admin, device, ip), "user.created", "user", str(u["id"]), None, {"username": username, "full_name": full_name, "role": role})
        return public_user(u)


def update_user(admin: sqlite3.Row, user_id: int, changes: dict, device: str, ip: str) -> dict:
    with db.transaction() as c:
        u = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if u is None:
            raise ServiceError(404, "Utilisateur introuvable.", "introuvable")
        before, after = {}, {}
        new_role = changes.get("role", u["role"]); new_active = int(bool(changes.get("active", u["active"])))
        if new_role not in ROLES:
            raise ServiceError(422, "Rôle inconnu.", "role_invalide")
        if u["role"] == "owner" and (new_role != "owner" or not new_active) and _owners_left(c, u["id"]) == 0:
            raise ServiceError(409, "Il doit rester au moins un propriétaire actif.", "dernier_proprietaire")
        if "full_name" in changes:
            fn = (changes["full_name"] or "").strip()
            if not (1 <= len(fn) <= 80):
                raise ServiceError(422, "Le nom complet est obligatoire (80 caractères maximum).", "nom_invalide")
            if fn != u["full_name"]: before["full_name"], after["full_name"] = u["full_name"], fn; c.execute("UPDATE users SET full_name=? WHERE id=?", (fn, u["id"]))
        if new_role != u["role"]:
            before["role"], after["role"] = u["role"], new_role; c.execute("UPDATE users SET role=? WHERE id=?", (new_role, u["id"]))
        if new_active != u["active"]:
            before["active"], after["active"] = bool(u["active"]), bool(new_active); c.execute("UPDATE users SET active=? WHERE id=?", (new_active, u["id"]))
            if not new_active:
                c.execute("UPDATE sessions SET revoked=1 WHERE user_id=?", (u["id"],))
        if changes.get("reset_password"):
            _check_password(changes["reset_password"])
            c.execute("UPDATE users SET pw_hash=?, must_change=1, failed=0, locked_until=NULL WHERE id=?", (security.hash_password(changes["reset_password"]), u["id"]))
            c.execute("UPDATE sessions SET revoked=1 WHERE user_id=?", (u["id"],))
            after["password"] = "réinitialisé"
        if new_role != u["role"]:
            c.execute("UPDATE sessions SET revoked=1 WHERE user_id=?", (u["id"],))
            c.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='epoch'")
        if after:
            audit.record(c, actor_of(admin, device, ip), "user.updated", "user", str(u["id"]), before, after)
        return public_user(c.execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone())
