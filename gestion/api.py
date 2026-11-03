"""API HTTP (FastAPI) et service de l'interface web installable (PWA)."""
from __future__ import annotations

import csv
import io
import json
import re
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, accounting, audit, backup, config, db, invoice, ops, sales_ops, security, services
from . import db as _db
from .ops import OpError
from .permissions import ROLES, can
from .services import ServiceError, actor_of

COOKIE = "gestion_session"
STATIC = Path(__file__).resolve().parent / "static"
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

@asynccontextmanager
async def _lifespan(_: FastAPI):
    db.init_db()
    backup.start_scheduler()
    from . import notify
    notify.start_scheduler()
    yield


app = FastAPI(title="Gestion libre", version=__version__, docs_url=None, redoc_url=None, openapi_url=None, lifespan=_lifespan)


@app.middleware("http")
async def security_layer(request: Request, call_next):
    if request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.headers.get("x-requested-with") != "gestion":      # défense CSRF : un formulaire d'un autre site ne peut pas ajouter cet en-tête
            return JSONResponse({"error": "Requête refusée.", "code": "csrf"}, status_code=403)
    resp = await call_next(request)
    own_csp = resp.headers.get("content-security-policy")                    # une pièce jointe se sert avec sa propre politique, plus stricte (sandbox)
    resp.headers.update({"Content-Security-Policy": own_csp or CSP, "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
                         "X-Frame-Options": "DENY", "Permissions-Policy": "camera=(), microphone=(), geolocation=()"})
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.exception_handler(ServiceError)
async def _service_error(_: Request, e: ServiceError):
    return JSONResponse({"error": e.message, "code": e.code}, status_code=e.status)


def device_of(request: Request) -> str:
    dev = re.sub(r"[^A-Za-z0-9-]", "", request.headers.get("x-device-id", ""))[:40]
    ua = re.sub(r"[\r\n]", " ", request.headers.get("user-agent", ""))[:100]
    return f"{dev or 'inconnu'} | {ua}"


def ip_of(request: Request) -> str:
    return request.client.host if request.client else ""


class Ctx:
    def __init__(self, request: Request, user, token: str):
        self.request, self.user, self.token = request, user, token
        self.device, self.ip = device_of(request), ip_of(request)
        self.actor = actor_of(user, self.device, self.ip)


def current(request: Request) -> Ctx:
    token = request.cookies.get(COOKIE)
    user = services.authenticate(token)
    if user is None:
        raise ServiceError(401, "Connexion requise.", "non_connecte")
    if user["must_change"] and request.url.path not in ("/api/password", "/api/me", "/api/logout"):
        raise ServiceError(403, "Vous devez d'abord changer votre mot de passe.", "changer_mot_de_passe")
    return Ctx(request, user, token)


def need(perm: str):
    def dep(ctx: Ctx = Depends(current)) -> Ctx:
        if not can(ctx.user["role"], perm):
            with db.transaction() as c:
                audit.record(c, ctx.actor, "access.denied", note=f"{request_label(ctx)} : droit « {perm} » manquant")
            raise ServiceError(403, "Accès refusé.", "interdit")
        return ctx
    return dep


def request_label(ctx: Ctx) -> str:
    return f"{ctx.request.method} {ctx.request.url.path}"


class Setup(BaseModel):
    username: str = Field(max_length=60); full_name: str = Field(max_length=100); password: str = Field(max_length=200)


class Login(BaseModel):
    username: str = Field(max_length=60); password: str = Field(max_length=200); code: str | None = Field(default=None, max_length=12)


class PasswordChange(BaseModel):
    old: str = Field(max_length=200); new: str = Field(max_length=200)


class NewUser(BaseModel):
    username: str = Field(max_length=60); full_name: str = Field(max_length=100); role: str = Field(max_length=20); temp_password: str = Field(max_length=200)


class UserChanges(BaseModel):
    full_name: str | None = Field(default=None, max_length=100); role: str | None = Field(default=None, max_length=20)
    active: bool | None = None; reset_password: str | None = Field(default=None, max_length=200)


class Push(BaseModel):
    ops: list[dict] = Field(max_length=200)


def _set_cookie(resp: Response, token: str, request: Request) -> None:
    # Tailscale Serve terminates HTTPS and forwards the original scheme to the
    # loopback server. Keep local HTTP development usable, while securing the
    # session cookie whenever the public-facing connection uses HTTPS.
    https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower() == "https"
    resp.set_cookie(COOKIE, token, max_age=config.SESSION_HOURS * 3600, httponly=True, samesite="lax", secure=config.secure_cookies() or https, path="/")


@app.get("/api/state")
def state():
    return {"setup_required": not services.users_exist(), "version": __version__, "roles": ROLES, "demo": config.demo_mode()}


@app.post("/api/setup")
def setup(body: Setup, request: Request):
    u = services.setup_owner(body.username, body.full_name, body.password, device_of(request), ip_of(request))
    token, _ = services.login(body.username, body.password, device_of(request), ip_of(request))
    resp = JSONResponse({"user": u}); _set_cookie(resp, token, request); return resp


class DemoLogin(BaseModel):
    role: str = Field(max_length=20)


@app.post("/api/demo/login")
def demo_login(body: DemoLogin, request: Request):
    from . import demo
    if not config.demo_mode():
        raise ServiceError(404, "Introuvable.", "introuvable")
    token, u = demo.login_as(body.role, device_of(request), ip_of(request))
    resp = JSONResponse({"user": u}); _set_cookie(resp, token, request); return resp


@app.post("/api/login")
def login(body: Login, request: Request):
    token, u = services.login(body.username, body.password, device_of(request), ip_of(request), body.code)
    resp = JSONResponse({"user": u}); _set_cookie(resp, token, request); return resp


@app.post("/api/logout")
def logout(ctx: Ctx = Depends(current)):
    services.logout(ctx.token, ctx.user, ctx.device, ctx.ip)
    resp = JSONResponse({"ok": True}); resp.delete_cookie(COOKIE, path="/"); return resp


@app.get("/api/sessions")
def sessions_list(all: bool = False, ctx: Ctx = Depends(current)):
    if all and not can(ctx.user["role"], "users.manage"): raise ServiceError(403, "Accès refusé.", "interdit")
    return {"sessions": services.list_sessions(ctx.user, ctx.token, all)}


class RevokeIn(BaseModel):
    id: int | None = None
    others: bool = False


@app.post("/api/sessions/revoke")
def sessions_revoke(body: RevokeIn, ctx: Ctx = Depends(current)):
    if body.id is None and not body.others: raise ServiceError(422, "Choisissez une connexion.", "invalide")
    n = services.revoke_session(ctx.user, ctx.token, body.id, body.others, can(ctx.user["role"], "users.manage"), ctx.device, ctx.ip)
    return {"revoked": n}


@app.get("/api/me")
def me(ctx: Ctx = Depends(current)):
    from .permissions import PERMISSIONS
    perms = sorted(PERMISSIONS.get(ctx.user["role"], set()))
    return {"user": services.public_user(ctx.user), "permissions": perms}


class CodeIn(BaseModel):
    code: str = Field(max_length=12)


class DisableIn(BaseModel):
    password: str = Field(max_length=200); code: str = Field(max_length=12)


@app.post("/api/2fa/begin")
def twofa_begin(ctx: Ctx = Depends(current)):
    return services.totp_begin(ctx.user, ctx.device, ctx.ip)


@app.post("/api/2fa/enable")
def twofa_enable(body: CodeIn, ctx: Ctx = Depends(current)):
    services.totp_enable(ctx.user, body.code, ctx.device, ctx.ip); return {"ok": True}


@app.post("/api/2fa/disable")
def twofa_disable(body: DisableIn, ctx: Ctx = Depends(current)):
    services.totp_disable(ctx.user, body.password, body.code, ctx.device, ctx.ip); return {"ok": True}


@app.post("/api/users/{user_id}/2fa-reset")
def twofa_reset(user_id: int, ctx: Ctx = Depends(need("users.manage"))):
    services.totp_reset(ctx.user, user_id, ctx.device, ctx.ip); return {"ok": True}


@app.post("/api/password")
def password(body: PasswordChange, ctx: Ctx = Depends(current)):
    services.change_password(ctx.user, body.old, body.new, ctx.device, ctx.ip)
    return {"ok": True}


@app.get("/api/users")
def users(ctx: Ctx = Depends(need("users.manage"))):
    return {"users": services.list_users()}


@app.post("/api/users")
def users_create(body: NewUser, ctx: Ctx = Depends(need("users.manage"))):
    return {"user": services.create_user(ctx.user, body.username, body.full_name, body.role, body.temp_password, ctx.device, ctx.ip)}


@app.patch("/api/users/{user_id}")
def users_update(user_id: int, body: UserChanges, ctx: Ctx = Depends(need("users.manage"))):
    return {"user": services.update_user(ctx.user, user_id, body.model_dump(exclude_none=True), ctx.device, ctx.ip)}


@app.get("/api/clients")
def clients(since: int = 0, ctx: Ctx = Depends(current)):
    if not (can(ctx.user["role"], "clients.read_all") or can(ctx.user["role"], "clients.read_own")):
        raise ServiceError(403, "Accès refusé.", "interdit")
    with db.read() as c:
        rows, head = ops.list_clients(c, ctx.user, since)
        sales = [{"id": u["id"], "full_name": u["full_name"]} for u in c.execute("SELECT id, full_name FROM users WHERE active=1 AND role='sales' ORDER BY full_name")]
    return {"clients": rows, "cursor": head, "sales_people": sales}


@app.get("/api/sync/pull")
def sync_pull(since: int = 0, epoch: int | None = None, db: str | None = None, ctx: Ctx = Depends(current)):
    with _db.read() as c:
        return sales_ops.pull(c, ctx.user, max(0, since), epoch, db)


def _doc_lang(c, ctx, html: str) -> str:
    """Page imprimable dans la langue des documents (réglage de l'entreprise, sinon langue de l'appareil)."""
    from . import i18n
    lang = i18n.pick(ctx.request.headers.get("accept-language"), sales_ops.settings_get(c).get("doc_lang"))
    return i18n.tr_html(html, lang)


@app.get("/api/invoice/{sale_id}")
def invoice_page(sale_id: str, ctx: Ctx = Depends(current)):
    with db.transaction() as c:
        s = c.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
        if s is None or not sales_ops.sale_visible(c, ctx.user, s):
            raise ServiceError(404, "Facture introuvable.", "introuvable")
        audit.record(c, ctx.actor, "invoice.viewed", "sale", sale_id, note=s["number"])
        html = _doc_lang(c, ctx, invoice.invoice_html(c, s))
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/api/quote/{quote_id}")
def quote_page(quote_id: str, ctx: Ctx = Depends(current)):
    from . import quotes
    with db.transaction() as c:
        q = c.execute("SELECT * FROM quotes WHERE id=?", (quote_id,)).fetchone()
        cl = c.execute("SELECT * FROM clients WHERE id=?", (q["client_id"],)).fetchone() if q else None
        if q is None or cl is None or not ops.visible_to(ctx.user["role"], ctx.user["id"], cl) or not (can(ctx.user["role"], "sales.read_all") or can(ctx.user["role"], "sales.read_own")):
            raise ServiceError(404, "Devis introuvable.", "introuvable")
        audit.record(c, ctx.actor, "quote.viewed", "quote", quote_id, note=q["number"])
        html = _doc_lang(c, ctx, invoice.quote_html(c, q))
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/api/delivery/{note_id}")
def delivery_page(note_id: str, ctx: Ctx = Depends(current)):
    with db.transaction() as c:
        n = c.execute("SELECT * FROM delivery_notes WHERE id=?", (note_id,)).fetchone()
        s = c.execute("SELECT * FROM sales WHERE id=?", (n["sale_id"],)).fetchone() if n else None
        if n is None or not sales_ops.sale_visible(c, ctx.user, s):
            raise ServiceError(404, "Bon de livraison introuvable.", "introuvable")
        audit.record(c, ctx.actor, "delivery.viewed", "delivery", note_id, note=n["number"])
        html = _doc_lang(c, ctx, invoice.delivery_html(c, n))
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/api/porder/{order_id}")
def porder_page(order_id: str, ctx: Ctx = Depends(current)):
    with db.transaction() as c:
        o = c.execute("SELECT * FROM purchase_orders WHERE id=?", (order_id,)).fetchone()
        if o is None or not can(ctx.user["role"], "purchases.read"):
            raise ServiceError(404, "Bon de commande introuvable.", "introuvable")
        audit.record(c, ctx.actor, "porder.viewed", "porder", order_id, note=o["number"])
        html = _doc_lang(c, ctx, invoice.porder_html(c, o))
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/api/receipt/{payment_id}")
def receipt_page(payment_id: str, ctx: Ctx = Depends(current)):
    with db.transaction() as c:
        p = c.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
        s = c.execute("SELECT * FROM sales WHERE id=?", (p["sale_id"],)).fetchone() if p else None
        if p is None or not sales_ops.sale_visible(c, ctx.user, s):
            raise ServiceError(404, "Reçu introuvable.", "introuvable")
        audit.record(c, ctx.actor, "receipt.viewed", "payment", payment_id, note=s["number"])
        html = _doc_lang(c, ctx, invoice.receipt_html(c, p))
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.post("/api/sync/push")
def sync_push(body: Push, ctx: Ctx = Depends(current)):
    results = []
    for op in body.ops:
        with db.transaction() as c:
            results.append(ops.apply_op(c, ctx.user, ctx.actor, op))
    return {"results": results}


def _audit_rows(c, user, params: dict, limit: int, before_id: int | None):
    sql, args = "SELECT * FROM audit_log WHERE 1=1", []
    for col, key in (("username", "user"), ("action", "action"), ("entity", "entity"), ("entity_id", "entity_id")):
        if params.get(key):
            sql += f" AND {col} = ?"; args.append(params[key])
    if params.get("from"): sql += " AND ts >= ?"; args.append(params["from"])
    if params.get("to"): sql += " AND ts <= ?"; args.append(params["to"] + "Z" if len(params["to"]) == 10 else params["to"])
    if params.get("q"): sql += " AND (note LIKE ? OR before_json LIKE ? OR after_json LIKE ?)"; args += [f"%{params['q']}%"] * 3
    if before_id: sql += " AND id < ?"; args.append(before_id)
    sql += " ORDER BY id DESC LIMIT ?"; args.append(limit)
    return c.execute(sql, args).fetchall()


def _row(r) -> dict:
    return {"id": r["id"], "ts": r["ts"], "user": r["username"], "role": r["role"], "action": r["action"], "entity": r["entity"], "entity_id": r["entity_id"],
            "before": json.loads(r["before_json"]) if r["before_json"] else None, "after": json.loads(r["after_json"]) if r["after_json"] else None,
            "device": r["device"], "ip": r["ip"], "note": r["note"]}


def _acct_params(request: Request):
    q = request.query_params; today_ = date.today()
    d = lambda k, default: (q.get(k) or default)
    frm, to = d("from", f"{today_.year}-01-01"), d("to", today_.isoformat())
    for v in (frm, to):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v or ""): raise ServiceError(422, "Dates au format AAAA-MM-JJ.", "date_invalide")
    if frm > to: raise ServiceError(422, "La date de début est après la date de fin.", "periode_invalide")
    return frm, to, q


def _acct_data(c, kind: str, frm: str, to: str, q) -> dict:
    if kind == "journal": return accounting.journal(c, frm, to, q.get("journal") or None)
    if kind == "balance": return accounting.trial_balance(c, frm, to)
    if kind == "ledger":
        acc = q.get("account") or ""
        if not re.fullmatch(r"\d{3,8}", acc): raise ServiceError(422, "Choisissez un compte.", "compte_requis")
        return accounting.ledger(c, acc, frm, to)
    if kind == "statements": return accounting.statements(c, frm, to)
    if kind == "financials":
        from . import financials
        return financials.financial_statements(c, frm, to)
    if kind == "assets":
        from . import assets
        return assets.register(c, frm, to)
    if kind == "schedule":
        from . import assets
        aid = q.get("asset_id") or ""
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", aid): raise ServiceError(422, "Choisissez une immobilisation.", "immobilisation_requise")
        try: return assets.plan(c, aid)
        except OpError as e: raise ServiceError(404, str(e), "immobilisation_introuvable")
    raise ServiceError(422, "Type de rapport inconnu.", "rapport_inconnu")


@app.get("/api/accounting/report")
def accounting_report(request: Request, kind: str = "statements", ctx: Ctx = Depends(need("accounting.read"))):
    frm, to, q = _acct_params(request)
    with db.read() as c:
        return _acct_data(c, kind, frm, to, q)


@app.get("/api/accounting/closing")
def accounting_closing(year: int, ctx: Ctx = Depends(need("accounting.read"))):
    if not (2000 <= year <= 2100): raise ServiceError(422, "Exercice invalide.", "exercice_invalide")
    with db.read() as c:
        return accounting.closing_preview(c, year)


@app.get("/api/accounting/export.csv")
def accounting_export(request: Request, kind: str = "journal", ctx: Ctx = Depends(need("accounting.read"))):
    frm, to, q = _acct_params(request)
    if kind not in ("journal", "balance", "ledger"): raise ServiceError(422, "Export possible : journal, balance, grand livre.", "rapport_inconnu")
    with db.transaction() as c:
        data = _acct_data(c, kind, frm, to, q)
        audit.record(c, ctx.actor, "accounting.exported", "accounting", kind, note=f"{kind} du {frm} au {to}")
    return Response(accounting.export_csv(kind, data), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f"attachment; filename=compta_{kind}_{frm}_{to}.csv"})


class NotifIn(BaseModel):
    enabled: bool | None = None
    host: str | None = None
    port: int | str | None = None
    security: str | None = None
    user: str | None = None
    password: str | None = None
    sender: str | None = None
    to: list[str] | str | None = None
    hour: str | None = None
    names: bool | None = None
    always: bool | None = None


@app.get("/api/notifications")
def notif_get(ctx: Ctx = Depends(need("settings.manage"))):
    from . import notify
    with db.read() as c:
        s = notify.load(c); subj, body, d = notify.build(c, s)
    return {"settings": notify.public(s), "preview": {"subject": subj, "text": body}}


@app.post("/api/notifications")
def notif_set(body: NotifIn, ctx: Ctx = Depends(need("settings.manage"))):
    from . import notify
    with db.transaction() as c:
        before = notify.load(c)
        try: new = notify.validate(body.model_dump(exclude_unset=True), before)
        except notify.NotifyError as e: raise ServiceError(422, str(e), "rappels_invalides")
        notify.save(c, new)
        b, a = notify.public(before), notify.public(new)
        diff = [k for k in a if a[k] != b.get(k) and k not in ("last_status", "last_sent", "last_try", "tries")]
        audit.record(c, ctx.actor, "notify.settings", "notify", None, {k: b[k] for k in diff if k != "has_password"}, {k: a[k] for k in diff if k != "has_password"},
                     note="rappels par e-mail : " + ("activés" if new["enabled"] else "désactivés") + ("; mot de passe modifié" if "has_password" in diff else ""))
    return {"status": "ok", "settings": notify.public(new)}


@app.post("/api/notifications/test")
def notif_test(ctx: Ctx = Depends(need("settings.manage"))):
    from . import notify
    return notify.send_now(test=True)


@app.get("/api/digest")
def digest_get(names: bool = False, ctx: Ctx = Depends(need("reports.read"))):
    from . import digest
    with db.read() as c:
        d = digest.compute(c, names=names)
    return {"digest": d, "text": digest.to_text(d, names=names)}


@app.get("/api/audit")
def audit_list(request: Request, limit: int = 100, before_id: int | None = None, ctx: Ctx = Depends(need("audit.read"))):
    limit = max(1, min(limit, 500))
    with db.read() as c:
        rows = [_row(r) for r in _audit_rows(c, ctx.user, dict(request.query_params), limit, before_id)]
        actions = [r["action"] for r in c.execute("SELECT DISTINCT action FROM audit_log ORDER BY action")]
        users_ = [r["username"] for r in c.execute("SELECT DISTINCT username FROM audit_log WHERE username IS NOT NULL ORDER BY username")]
    return {"entries": rows, "actions": actions, "users": users_}


@app.get("/api/backup/list")
def backup_list(ctx: Ctx = Depends(need("backup.run"))):
    from . import chiffrement
    return {"backups": backup.list_backups(), "dir": str(backup.backup_dir()), "encryption": chiffrement.statut(), "extra": {"dir": str(backup.extra_dir() or ""), "status": backup.extra_status()}}


class ExtraIn(BaseModel):
    dir: str


@app.post("/api/backup/extra")
def backup_extra_set(body: ExtraIn, ctx: Ctx = Depends(need("backup.run"))):
    try: d = backup.check_extra_dir(body.dir)
    except ValueError as e: raise ServiceError(422, str(e), "dossier_invalide")
    backup._meta_set(backup.EXTRA_DIR_KEY, str(d))
    path = backup.run_backup()                             # première copie tout de suite : on voit si cela marche
    with db.transaction() as c:
        audit.record(c, ctx.actor, "backup.extra_set", "backup", None, None, {"dossier": str(d)}, note="copie de secours hors de l'ordinateur activée")
        audit.record(c, ctx.actor, "backup.created", "backup", path.name, note="sauvegarde faite en activant la copie hors de l'ordinateur")
    return {"status": "ok", "dir": str(d), "copy": backup.extra_status()}


@app.delete("/api/backup/extra")
def backup_extra_clear(ctx: Ctx = Depends(need("backup.run"))):
    old = backup.extra_dir()
    backup._meta_set(backup.EXTRA_DIR_KEY, None); backup._meta_set(backup.EXTRA_STATUS_KEY, None)
    with db.transaction() as c:
        audit.record(c, ctx.actor, "backup.extra_cleared", "backup", None, {"dossier": str(old or "")}, None, note="copie de secours hors de l'ordinateur désactivée")
    return {"status": "ok"}


@app.post("/api/backup/run")
def backup_run(ctx: Ctx = Depends(need("backup.run"))):
    path = backup.run_backup()
    with db.transaction() as c:
        audit.record(c, ctx.actor, "backup.created", "backup", path.name, note="sauvegarde manuelle sur le serveur")
    return {"status": "ok", "name": path.name}


@app.get("/api/backup/download")
def backup_download(ctx: Ctx = Depends(need("backup.run"))):
    import tempfile
    tmp = Path(tempfile.mkdtemp()) / "gestion_sauvegarde.sqlite3"
    backup.snapshot(tmp)
    data = tmp.read_bytes()
    try: tmp.unlink(); tmp.parent.rmdir()
    except OSError: pass
    with db.transaction() as c:
        audit.record(c, ctx.actor, "backup.downloaded", "backup", None, note=f"{len(data)} octets")
    return Response(data, media_type="application/octet-stream", headers={"Content-Disposition": f"attachment; filename=gestion_sauvegarde_{date.today().isoformat()}.sqlite3"})


@app.get("/api/export/all.zip")
def export_all(ctx: Ctx = Depends(need("accounting.read"))):
    with db.transaction() as c:
        data = backup.export_zip(c)
        audit.record(c, ctx.actor, "export.all", "export", None, note="export complet en CSV")
    return Response(data, media_type="application/zip", headers={"Content-Disposition": f"attachment; filename=gestion_export_{date.today().isoformat()}.zip"})


@app.get("/api/reports/insights")
def reports_insights(ctx: Ctx = Depends(need("reports.read"))):
    from . import insights
    with db.read() as c:
        return {"reliability": insights.reliability(c), "forecast": insights.forecast(c)}


class LogoIn(BaseModel):
    image: str = Field(max_length=400_000)                                  # base64 d'un PNG ou JPEG de 250 Ko au plus


def _image_type(b: bytes) -> str | None:
    return "image/png" if b[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg" if b[:3] == b"\xff\xd8\xff" else None


@app.get("/api/help")
def help_get(ctx: Ctx = Depends(current)):
    """Page « Aide » : texte français commun à tous les rôles (aucune donnée de l'entreprise)."""
    from . import aide
    return {"sections": aide.SECTIONS}


@app.get("/api/logo")
def logo_get(ctx: Ctx = Depends(current)):
    import base64
    with db.read() as c:
        r = c.execute("SELECT value FROM meta WHERE key='logo'").fetchone()
    if r is None: raise ServiceError(404, "Pas de logo.", "introuvable")
    raw = base64.b64decode(r["value"])
    return Response(raw, media_type=_image_type(raw) or "application/octet-stream", headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"})


@app.post("/api/logo")
def logo_set(body: LogoIn, ctx: Ctx = Depends(need("settings.manage"))):
    import base64, binascii
    try: raw = base64.b64decode(body.image, validate=True)
    except (binascii.Error, ValueError): raise ServiceError(422, "Image illisible.", "image_invalide")
    if len(raw) > 250_000: raise ServiceError(422, "Image trop lourde (250 Ko au plus).", "image_trop_lourde")
    if _image_type(raw) is None: raise ServiceError(422, "Format accepté : PNG ou JPEG (pas de SVG).", "image_invalide")
    with db.transaction() as c:
        c.execute("INSERT INTO meta(key,value) VALUES('logo',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (body.image,))
        audit.record(c, ctx.actor, "logo.updated", "settings", "logo", None, {"taille (octets)": len(raw)})
    return {"ok": True}


@app.delete("/api/logo")
def logo_delete(ctx: Ctx = Depends(need("settings.manage"))):
    with db.transaction() as c:
        if c.execute("DELETE FROM meta WHERE key='logo'").rowcount: audit.record(c, ctx.actor, "logo.removed", "settings", "logo")
    return {"ok": True}


@app.get("/api/signature")
def signature_get(ctx: Ctx = Depends(current)):
    import base64
    with db.read() as c:
        r = c.execute("SELECT value FROM meta WHERE key='signature'").fetchone()
    if r is None: raise ServiceError(404, "Pas de signature.", "introuvable")
    raw = base64.b64decode(r["value"])
    return Response(raw, media_type=_image_type(raw) or "application/octet-stream", headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"})


@app.post("/api/signature")
def signature_set(body: LogoIn, ctx: Ctx = Depends(need("settings.manage"))):
    import base64, binascii
    try: raw = base64.b64decode(body.image, validate=True)
    except (binascii.Error, ValueError): raise ServiceError(422, "Image illisible.", "image_invalide")
    if len(raw) > 250_000: raise ServiceError(422, "Image trop lourde (250 Ko au plus).", "image_trop_lourde")
    if _image_type(raw) is None: raise ServiceError(422, "Format accepté : PNG ou JPEG (pas de SVG).", "image_invalide")
    with db.transaction() as c:
        c.execute("INSERT INTO meta(key,value) VALUES('signature',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (body.image,))
        audit.record(c, ctx.actor, "signature.updated", "settings", "signature", None, {"taille (octets)": len(raw)})
    return {"ok": True}


@app.delete("/api/signature")
def signature_delete(ctx: Ctx = Depends(need("settings.manage"))):
    with db.transaction() as c:
        if c.execute("DELETE FROM meta WHERE key='signature'").rowcount: audit.record(c, ctx.actor, "signature.removed", "settings", "signature")
    return {"ok": True}


def _att_args(entity: str, eid: str):
    from . import attachments
    if entity not in attachments.ENTITIES or not (8 <= len(eid) <= 64): raise ServiceError(422, "Document invalide.", "invalide")


@app.get("/api/attachments")
def attachments_list(entity: str, id: str, ctx: Ctx = Depends(current)):
    from . import attachments
    _att_args(entity, id)
    with db.read() as c:
        if not attachments.access(c, ctx.user, entity, id, False): raise ServiceError(404, "Document introuvable.", "introuvable")
        return {"attachments": attachments.list_for(c, entity, id)}


@app.post("/api/attachments")
async def attachments_add(request: Request, entity: str, id: str, name: str = "piece", ctx: Ctx = Depends(current)):
    from . import attachments
    _att_args(entity, id)
    try: declared = int(request.headers.get("content-length") or 0)
    except ValueError: declared = 0
    if declared > attachments.MAX_BYTES + 1024: raise ServiceError(413, "Fichier trop lourd (5 Mo au plus).", "trop_lourd")
    data = await request.body()
    with db.transaction() as c:
        if not attachments.access(c, ctx.user, entity, id, True):
            audit.record(c, ctx.actor, "access.denied", note=f"pièce jointe refusée ({entity})")
            c.execute("COMMIT"); c.execute("BEGIN IMMEDIATE")
            raise ServiceError(403, "Accès refusé.", "interdit")
        try: m = attachments.add(c, ctx.user, ctx.actor, entity, id, name, data, security.iso())
        except OpError as e: raise ServiceError(422, e.message, "pièce_refusée")
    return {"attachment": m}


@app.get("/api/attachments/{att_id}")
def attachments_get(att_id: str, ctx: Ctx = Depends(current)):
    from . import attachments
    with db.transaction() as c:
        r = c.execute("SELECT * FROM attachments WHERE id=? AND status='active'", (att_id,)).fetchone()
        if r is None or not attachments.access(c, ctx.user, r["entity"], r["entity_id"], False): raise ServiceError(404, "Pièce introuvable.", "introuvable")
        audit.record(c, ctx.actor, "attachment.viewed", r["entity"], r["entity_id"], note=r["name"])
        data, mime, name = bytes(r["data"]), r["mime"], r["name"]
    return Response(data, media_type=mime, headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(name)}", "Cache-Control": "private, no-store",
                                                   "Content-Security-Policy": "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox", "X-Content-Type-Options": "nosniff"})


class RemoveIn(BaseModel):
    reason: str = Field(max_length=200)


@app.post("/api/attachments/{att_id}/remove")
def attachments_remove(att_id: str, body: RemoveIn, ctx: Ctx = Depends(current)):
    from . import attachments
    with db.transaction() as c:
        try: attachments.remove(c, ctx.user, ctx.actor, att_id, body.reason)
        except OpError as e: raise ServiceError(422, e.message, "retrait_refusé")
    return {"ok": True}


def _margin_period(request: Request):
    from . import margins
    try: return margins.parse_period(request.query_params.get("from"), request.query_params.get("to"))
    except ValueError as e: raise ServiceError(422, str(e), "periode_invalide")


@app.get("/api/reports/margins")
def reports_margins(request: Request, ctx: Ctx = Depends(need("costs.read"))):
    from . import margins
    a, b = _margin_period(request)
    with db.read() as c:
        return margins.compute(c, a, b)


@app.get("/api/reports/margins.csv")
def reports_margins_csv(request: Request, kind: str = "product", ctx: Ctx = Depends(need("costs.read"))):
    from . import margins
    if kind not in ("product", "client"): raise ServiceError(422, "Export possible : produit ou client.", "rapport_inconnu")
    a, b = _margin_period(request)
    with db.transaction() as c:
        data = margins.compute(c, a, b)
        audit.record(c, ctx.actor, "report.exported", "report", f"marges_{kind}", note=f"marges par {kind} du {a} au {b}")
    return Response(margins.to_csv(data, kind), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f"attachment; filename=marges_{kind}_{a}_{b}.csv"})


@app.get("/api/reports/sales")
def reports_sales(request: Request, ctx: Ctx = Depends(current)):
    from . import margins, sales_reports
    if sales_reports.scope_of(ctx.user) is None:
        with db.transaction() as c: audit.record(c, ctx.actor, "access.denied", note="rapport de ventes : droit manquant")
        raise ServiceError(403, "Accès refusé.", "interdit")
    a, b = _margin_period(request)
    with db.read() as c:
        return sales_reports.compute(c, ctx.user, a, b)


@app.get("/api/reports/sales.csv")
def reports_sales_csv(request: Request, kind: str = "seller", ctx: Ctx = Depends(current)):
    from . import sales_reports
    if kind not in ("seller", "month", "category"): raise ServiceError(422, "Export possible : vendeur, mois ou catégorie.", "rapport_inconnu")
    if sales_reports.scope_of(ctx.user) is None: raise ServiceError(403, "Accès refusé.", "interdit")
    a, b = _margin_period(request)
    with db.transaction() as c:
        data = sales_reports.compute(c, ctx.user, a, b)
        audit.record(c, ctx.actor, "report.exported", "report", f"ventes_{kind}", note="ventes par " + {"seller": "vendeur", "month": "mois", "category": "catégorie"}[kind] + f" du {a} au {b}")
    return Response(sales_reports.to_csv(data, kind), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f"attachment; filename=ventes_{kind}_{a}_{b}.csv"})


class TargetsIn(BaseModel):
    targets: dict[str, int | str | None]


@app.get("/api/reports/targets")
def targets_get(ctx: Ctx = Depends(need("budgets.set"))):
    from . import sales_reports
    with db.read() as c:
        return {"sellers": sales_reports.sellers_list(c)}


@app.post("/api/reports/targets")
def targets_set(body: TargetsIn, ctx: Ctx = Depends(need("budgets.set"))):
    from . import sales_reports
    with db.transaction() as c:
        before = sales_reports.get_targets(c)
        try: new = sales_reports.set_targets(c, body.targets)
        except ValueError as e: raise ServiceError(422, str(e), "objectif_invalide")
        names = {u["id"]: u["full_name"] for u in c.execute("SELECT id, full_name FROM users")}
        label = lambda d: {names.get(k, str(k)): v for k, v in d.items()}
        audit.record(c, ctx.actor, "targets.set", "targets", None, label(before), label(new), note="objectifs de ventes mensuels par vendeur")
        return {"status": "ok", "sellers": sales_reports.sellers_list(c)}


@app.get("/api/reports/pdf")
def reports_pdf(request: Request, report: str, ctx: Ctx = Depends(current)):
    from . import pdf_reports, sales_reports
    if report not in pdf_reports.REPORTS: raise ServiceError(422, "Rapport inconnu.", "rapport_inconnu")
    perm, family = pdf_reports.REPORTS[report]
    allowed = can(ctx.user["role"], perm) if perm else sales_reports.scope_of(ctx.user) is not None
    if not allowed:
        with db.transaction() as c: audit.record(c, ctx.actor, "access.denied", note=f"export PDF « {report} » : droit manquant")
        raise ServiceError(403, "Accès refusé.", "interdit")
    a, b = _margin_period(request)
    extra = {}
    if report == "ledger":
        extra["account"] = request.query_params.get("account") or ""
        if not re.fullmatch(r"\d{3,8}", extra["account"]): raise ServiceError(422, "Choisissez un compte.", "compte_requis")
    if report == "journal":
        extra["journal"] = request.query_params.get("journal") or ""
        if extra["journal"] and not re.fullmatch(r"[A-Z]{2}", extra["journal"]): raise ServiceError(422, "Journal inconnu.", "journal_inconnu")
    with db.transaction() as c:
        from . import i18n
        lang = i18n.pick(request.headers.get("accept-language"), sales_ops.settings_get(c).get("doc_lang"), allowed={"en"})          # les PDF n'existent qu'en français et en anglais
        pdf, name = pdf_reports.build(c, ctx.user, report, a, b, lang, extra)
        audit.record(c, ctx.actor, "report.exported", "report", report, note=f"PDF « {report} » du {a} au {b}")
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f"inline; filename={name}", "Cache-Control": "private, no-store"})


@app.get("/api/audit/verify")
def audit_verify(ctx: Ctx = Depends(need("audit.read"))):
    with db.read() as c:
        return audit.verify(c)


class DunningIn(BaseModel):
    sale_id: str
    channel: str
    note: str = ""
    promise: str | None = None


@app.get("/api/dunning")
def dunning_list(only_due: bool = False, ctx: Ctx = Depends(need("sales.read_all"))):
    from . import dunning
    with db.read() as c:
        return dunning.compute(c, only_due)


@app.post("/api/dunning")
def dunning_log(body: DunningIn, ctx: Ctx = Depends(need("sales.create"))):
    from . import dunning
    with db.transaction() as c:
        try: r = dunning.log(c, ctx.actor, body.sale_id, body.channel, body.note, body.promise)
        except dunning.DunningError as e: raise ServiceError(422, str(e), "relance_refusee")
        return {"status": "ok", **r}


class PayrollSettingsIn(BaseModel):
    employee_bp: int
    employer_bp: int
    ceiling: int = 0


class EmployeeIn(BaseModel):
    name: str | None = None
    position: str | None = None
    phone: str | None = None
    base_salary: int | None = None
    hire_date: str | None = None
    note: str | None = None
    active: bool | None = None


class SlipIn(BaseModel):
    employee_id: str
    period: str
    bonus: int = 0
    its: int = 0
    advance: int = 0
    note: str = ""


class SlipPayIn(BaseModel):
    method: str
    date: str | None = None


class AdvanceIn(BaseModel):
    employee_id: str
    amount: int
    method: str
    date: str | None = None
    note: str = ""


class LeaveIn(BaseModel):
    employee_id: str
    kind: str
    start: str
    end: str
    days: int
    note: str = ""


class ReasonIn(BaseModel):
    reason: str


def _payroll_call(fn):
    from . import payroll
    with db.transaction() as c:
        try: return {"status": "ok", **fn(c, payroll)}
        except OpError as e: raise ServiceError(422, e.message, "paie_refusee")


@app.get("/api/payroll")
def payroll_overview(month: str = "", ctx: Ctx = Depends(need("payroll.read"))):
    from . import payroll, sales_ops as so
    with db.read() as c:
        try: return payroll.overview(c, month or so.today().isoformat()[:7])
        except OpError as e: raise ServiceError(422, e.message, "paie_refusee")


@app.post("/api/payroll/settings")
def payroll_settings(body: PayrollSettingsIn, ctx: Ctx = Depends(need("payroll.manage"))):
    return _payroll_call(lambda c, p: {"settings": p.set_settings(c, ctx.actor, body.model_dump())})


@app.post("/api/payroll/employees")
def payroll_employee_create(body: EmployeeIn, ctx: Ctx = Depends(need("payroll.manage"))):
    return _payroll_call(lambda c, p: {"employee": p.employee_save(c, ctx.user, ctx.actor, body.model_dump(exclude_none=True), None)})


@app.post("/api/payroll/employees/{eid}")
def payroll_employee_update(eid: str, body: EmployeeIn, ctx: Ctx = Depends(need("payroll.manage"))):
    return _payroll_call(lambda c, p: {"employee": p.employee_save(c, ctx.user, ctx.actor, body.model_dump(exclude_none=True), eid)})


@app.post("/api/payroll/slips")
def payroll_slip_create(body: SlipIn, ctx: Ctx = Depends(need("payroll.manage"))):
    return _payroll_call(lambda c, p: {"slip": p.slip_create(c, ctx.user, ctx.actor, body.employee_id, body.period, body.bonus, body.its, body.note, body.advance)})


@app.post("/api/payroll/generate")
def payroll_generate(body: dict, ctx: Ctx = Depends(need("payroll.manage"))):
    return _payroll_call(lambda c, p: p.generate(c, ctx.user, ctx.actor, str(body.get("period") or "")))


@app.post("/api/payroll/slips/{sid}/pay")
def payroll_slip_pay(sid: str, body: SlipPayIn, ctx: Ctx = Depends(need("payroll.manage"))):
    return _payroll_call(lambda c, p: {"slip": p.slip_pay(c, ctx.user, ctx.actor, sid, body.method, body.date)})


@app.post("/api/payroll/slips/{sid}/cancel")
def payroll_slip_cancel(sid: str, body: ReasonIn, ctx: Ctx = Depends(need("payroll.manage"))):
    return _payroll_call(lambda c, p: {"slip": p.slip_cancel(c, ctx.user, ctx.actor, sid, body.reason)})


@app.post("/api/payroll/advances")
def payroll_advance_create(body: AdvanceIn, ctx: Ctx = Depends(need("payroll.manage"))):
    return _payroll_call(lambda c, p: {"advance": p.advance_create(c, ctx.user, ctx.actor, body.employee_id, body.amount, body.method, body.date, body.note)})


@app.post("/api/payroll/advances/{aid}/cancel")
def payroll_advance_cancel(aid: str, body: ReasonIn, ctx: Ctx = Depends(need("payroll.manage"))):
    return _payroll_call(lambda c, p: {"advance": p.advance_cancel(c, ctx.user, ctx.actor, aid, body.reason)})


@app.post("/api/payroll/leaves")
def payroll_leave_create(body: LeaveIn, ctx: Ctx = Depends(need("payroll.manage"))):
    return _payroll_call(lambda c, p: {"leave": p.leave_create(c, ctx.user, ctx.actor, body.employee_id, body.kind, body.start, body.end, body.days, body.note)})


@app.post("/api/payroll/leaves/{lid}/cancel")
def payroll_leave_cancel(lid: str, body: ReasonIn, ctx: Ctx = Depends(need("payroll.manage"))):
    return _payroll_call(lambda c, p: {"leave": p.leave_cancel(c, ctx.user, ctx.actor, lid, body.reason)})


@app.get("/api/payslip/{sid}")
def payslip_page(sid: str, ctx: Ctx = Depends(need("payroll.read"))):
    from . import payroll
    with db.transaction() as c:
        s = c.execute("SELECT * FROM payslips WHERE id=?", (sid,)).fetchone()
        if s is None: raise ServiceError(404, "Bulletin introuvable.", "introuvable")
        audit.record(c, ctx.actor, "payslip.viewed", "payslip", sid, note=s["number"])
        html = _doc_lang(c, ctx, payroll.slip_html(c, s))
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/api/readiness")
def readiness(request: Request, ctx: Ctx = Depends(need("audit.seal"))):
    from . import readiness as rd
    with db.read() as c:
        return rd.compute(c, request.headers.get("host", ""), request.url.scheme)


class SealKeyIn(BaseModel):
    public_key: str
    replace: bool = False


class SealIn(BaseModel):
    id: int
    hash: str
    sig: str


@app.get("/api/audit/seal")
def seal_status(ctx: Ctx = Depends(need("audit.read"))):
    with db.read() as c:
        return {**audit.seal_check(c), "checkpoint": audit.seal_checkpoint(c), "seals": audit.seal_state(c)["seals"][-20:]}


@app.post("/api/audit/seal/key")
def seal_key(body: SealKeyIn, ctx: Ctx = Depends(need("audit.seal"))):
    from . import signature
    pub = body.public_key.strip().lower()
    if not signature.valid_pub(pub): raise ServiceError(422, "Clé publique invalide : 64 caractères hexadécimaux, affichés par « python -m gestion.signature generer ».", "cle_invalide")
    with db.transaction() as c:
        old = audit.seal_state(c)["public_key"]
        if old and old != pub and not body.replace: raise ServiceError(409, "Une clé est déjà enregistrée. Les anciens sceaux restent valables avec leur clé ; pour la remplacer, confirmez.", "cle_existante")
        if old == pub: return {"status": "ok", "fingerprint": signature.fingerprint(pub)}
        audit._meta_set(c, audit.SEAL_PUB, pub)
        audit.record(c, ctx.actor, "audit.seal_key", "audit", None, {"empreinte de la clé": signature.fingerprint(old) if old else None}, {"empreinte de la clé": signature.fingerprint(pub)}, note="clé publique de signature du journal enregistrée" if not old else "clé publique de signature du journal REMPLACÉE")
    return {"status": "ok", "fingerprint": signature.fingerprint(pub)}


@app.post("/api/audit/seal")
def seal_add(body: SealIn, ctx: Ctx = Depends(need("audit.seal"))):
    from . import security
    with db.transaction() as c:
        try: s = audit.seal_add(c, body.id, body.hash.strip().lower(), body.sig.strip(), security.iso())
        except ValueError as e: raise ServiceError(422, str(e), "sceau_refuse")
        audit.record(c, ctx.actor, "audit.sealed", "audit", str(s["id"]), None, {"entrée": s["id"], "empreinte": s["hash"][:16]}, note=f"journal scellé jusqu'à l'entrée {s['id']}")
        return {"status": "ok", **audit.seal_check(c)}


def _safe_cell(v) -> str:
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s       # évite l'injection de formules à l'ouverture dans Excel


@app.get("/api/audit/export.csv")
def audit_export(request: Request, ctx: Ctx = Depends(need("audit.read"))):
    with db.transaction() as c:
        rows = _audit_rows(c, ctx.user, dict(request.query_params), 100000, None)
        audit.record(c, ctx.actor, "audit.exported", note=f"{len(rows)} lignes")
    buf = io.StringIO(); w = csv.writer(buf, delimiter=";")
    w.writerow(["n°", "date (UTC)", "utilisateur", "rôle", "action", "objet", "id objet", "avant", "après", "appareil", "adresse", "note"])
    for r in rows:
        w.writerow([r["id"], r["ts"], _safe_cell(r["username"]), r["role"], r["action"], r["entity"], r["entity_id"], _safe_cell(r["before_json"]), _safe_cell(r["after_json"]), _safe_cell(r["device"]), r["ip"], _safe_cell(r["note"])])
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=journal_audit.csv"})


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
