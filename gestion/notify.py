"""Rappels par e-mail : le résumé du jour (voir digest.py) part chaque matin à l'heure choisie vers les adresses indiquées.

Principes :
  - RIEN ne part tant que le propriétaire n'a pas rempli ET activé la configuration (« Mon compte → Rappels ») ; le bouton « Envoyer un essai » n'envoie qu'à ses adresses.
  - Les identifiants de la boîte d'envoi sont ceux de LEUR propriétaire, saisis par lui. Pour Gmail ou Outlook, utilisez un « mot de passe d'application », jamais le vrai mot de passe.
    Le mot de passe est conservé dans la base (chiffrée avec elle quand le chiffrement est actif) et n'est JAMAIS renvoyé à l'écran ni noté dans le journal ; la variable
    d'environnement GESTION_SMTP_PASSWORD le remplace si vous préférez ne rien stocker.
  - Un seul message par jour, et seulement s'il y a quelque chose à signaler (sauf si « toujours envoyer » est coché). En cas d'échec : nouvel essai toutes les 30 minutes, 4 fois au plus.
  - Les messages ne contiennent que des nombres et des montants, sauf si le propriétaire autorise les noms de clients et de produits.
"""
from __future__ import annotations

import json
import os
import re
import smtplib
import ssl
import threading
import time
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from . import audit, config, db, digest, secretbox

KEY = "notif"
EMAIL = re.compile(r"^[^@\s<>,;\"']+@[^@\s<>,;\"']+\.[A-Za-z]{2,}$")
HOST = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
TIMEOUT = 20
RETRY_MIN = 30
MAX_TRIES = 4
DEFAULTS = {"enabled": False, "host": "", "port": 587, "security": "starttls", "user": "", "password": "", "sender": "", "to": [], "hour": "07:30", "names": False, "always": False,
            "last_sent": "", "last_try": "", "tries": 0, "last_status": None}


class NotifyError(ValueError):
    """Erreur lisible par le propriétaire (jamais de mot de passe dedans)."""


def load(c) -> dict:
    r = c.execute("SELECT value FROM meta WHERE key=?", (KEY,)).fetchone()
    d = dict(DEFAULTS)
    if r: d.update(json.loads(r["value"]))
    d["password"] = secretbox.ouvrir(d.get("password"))
    return d


def public(s: dict) -> dict:
    """Réglages sans le mot de passe (on dit seulement s'il existe)."""
    d = {k: v for k, v in s.items() if k != "password"}
    d["has_password"] = bool(s.get("password") or os.environ.get("GESTION_SMTP_PASSWORD"))
    d["password_from_env"] = bool(os.environ.get("GESTION_SMTP_PASSWORD"))
    return d


def save(c, s: dict) -> None:
    c.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (KEY, json.dumps({**s, "password": secretbox.sceller(s.get("password"))}, ensure_ascii=False)))


def validate(data: dict, before: dict) -> dict:
    """Contrôle et fusionne la configuration reçue de l'écran. Lève NotifyError."""
    s = dict(before)
    if "enabled" in data: s["enabled"] = bool(data["enabled"])
    for k in ("names", "always"):
        if k in data: s[k] = bool(data[k])
    if "host" in data:
        h = str(data["host"] or "").strip()
        if h and not HOST.match(h): raise NotifyError("Serveur d'envoi invalide (ex. smtp.gmail.com).")
        s["host"] = h
    if "port" in data:
        try: p = int(data["port"])
        except (TypeError, ValueError): raise NotifyError("Port invalide (ex. 587 ou 465).")
        if not 1 <= p <= 65535: raise NotifyError("Port invalide (1 à 65535).")
        s["port"] = p
    if "security" in data:
        if data["security"] not in ("starttls", "ssl", "none"): raise NotifyError("Sécurité : STARTTLS, SSL ou aucune.")
        s["security"] = data["security"]
    if "user" in data: s["user"] = str(data["user"] or "").strip()[:200]
    if "password" in data and data["password"] is not None: s["password"] = str(data["password"])[:200]          # None : inchangé ; chaîne vide : effacé
    if "sender" in data:
        v = str(data["sender"] or "").strip()
        if v and not EMAIL.match(v): raise NotifyError("Adresse d'expédition invalide.")
        s["sender"] = v
    if "to" in data:
        to = [x.strip() for x in (data["to"] if isinstance(data["to"], list) else re.split(r"[,;\s]+", str(data["to"] or ""))) if x and x.strip()]
        if len(to) > 5: raise NotifyError("Cinq adresses au plus.")
        for a in to:
            if not EMAIL.match(a): raise NotifyError(f"Adresse invalide : {a}")
        s["to"] = to
    if "hour" in data:
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(data["hour"] or "")): raise NotifyError("Heure invalide (HH:MM, ex. 07:30).")
        s["hour"] = data["hour"]
    if s["enabled"]:
        if not s["host"] or not s["to"]: raise NotifyError("Pour activer les rappels, indiquez le serveur d'envoi et au moins une adresse de destination.")
        if bool(s["user"]) != bool(s["password"] or os.environ.get("GESTION_SMTP_PASSWORD")): raise NotifyError("Identifiant et mot de passe vont ensemble : remplissez les deux, ou aucun si le serveur n'en demande pas.")
    return s


def send_mail(s: dict, subject: str, body: str) -> None:
    """Envoie un message avec la configuration s. Lève NotifyError avec un texte clair, sans jamais citer le mot de passe."""
    pwd = os.environ.get("GESTION_SMTP_PASSWORD") or s.get("password") or ""
    sender = s.get("sender") or s.get("user") or ""
    if not EMAIL.match(sender): raise NotifyError("Indiquez l'adresse d'expédition.")
    msg = EmailMessage()
    msg["Subject"] = subject; msg["From"] = sender; msg["To"] = ", ".join(s["to"]); msg["Date"] = formatdate(localtime=True); msg["Message-ID"] = make_msgid(domain="gestion-libre.local")
    msg.set_content(body)
    try:
        ctx = ssl.create_default_context()
        if s["security"] == "ssl": cli = smtplib.SMTP_SSL(s["host"], s["port"], timeout=TIMEOUT, context=ctx)
        else: cli = smtplib.SMTP(s["host"], s["port"], timeout=TIMEOUT)
        with cli:
            cli.ehlo()
            if s["security"] == "starttls":
                cli.starttls(context=ctx); cli.ehlo()
            if s.get("user"): cli.login(s["user"], pwd)
            cli.send_message(msg)
    except smtplib.SMTPAuthenticationError: raise NotifyError("Le serveur refuse l'identifiant ou le mot de passe (pour Gmail ou Outlook : mot de passe d'application).")
    except smtplib.SMTPRecipientsRefused: raise NotifyError("Le serveur refuse l'adresse de destination.")
    except smtplib.SMTPSenderRefused: raise NotifyError("Le serveur refuse l'adresse d'expédition.")
    except (smtplib.SMTPException, ssl.SSLError) as e: raise NotifyError(f"Envoi impossible : {str(e)[:150]}".replace(pwd, "***") if pwd else f"Envoi impossible : {str(e)[:150]}")
    except (OSError, TimeoutError) as e: raise NotifyError(f"Serveur injoignable ({getattr(e, 'strerror', None) or e}) : vérifiez le nom du serveur, le port et la connexion Internet.")


def build(c, s: dict) -> tuple[str, str, dict]:
    d = digest.compute(c, names=s.get("names", False))
    return digest.subject(d), digest.to_text(d, names=s.get("names", False)), d


def send_now(test: bool = False, now: datetime | None = None) -> dict:
    """Envoie le résumé maintenant (essai manuel ou envoi planifié). Renvoie {ok, message, sent}. Note le résultat ; ne lève jamais d'erreur."""
    now = now or datetime.now()
    with db.read() as c:
        s = load(c); subj, body, d = build(c, s)
    res = {"ok": False, "message": "", "sent": False, "ts": now.isoformat(timespec="seconds")}
    try:
        if not s["host"] or not s["to"]: raise NotifyError("Configuration incomplète : indiquez le serveur d'envoi et au moins une adresse de destination.")
        if not test and d["empty"] and not s["always"]:
            res.update(ok=True, message="Rien à signaler : aucun message envoyé.", sent=False)
        else:
            send_mail(s, ("[ESSAI] " if test else "") + subj, ("Ceci est un message d'essai.\n\n" if test else "") + body)
            res.update(ok=True, message=f"Message envoyé à {', '.join(s['to'])}.", sent=True)
    except NotifyError as e:
        res["message"] = str(e)
    with db.transaction() as c:
        s = load(c)
        s["last_status"] = res
        if not test:
            s["last_try"] = now.isoformat(timespec="seconds")
            if res["ok"]: s["last_sent"] = now.date().isoformat(); s["tries"] = 0
            else: s["tries"] = s.get("tries", 0) + 1
        save(c, s)
        audit.record(c, audit.Actor(None, "système", "system", "serveur", ""), "notify.sent" if res["sent"] else "notify.failed" if not res["ok"] else "notify.skipped", "notify", None,
                     note=("essai : " if test else "résumé du jour : ") + res["message"])
    return res


def due(s: dict, now: datetime) -> bool:
    """Faut-il envoyer maintenant ? Configuration active, heure passée, pas déjà envoyé aujourd'hui, et si échec : 30 minutes entre deux essais, 4 essais au plus."""
    if not s["enabled"] or not s["host"] or not s["to"]: return False
    if s["last_sent"] == now.date().isoformat(): return False
    h, m = (int(x) for x in s["hour"].split(":"))
    if now < now.replace(hour=h, minute=m, second=0, microsecond=0): return False
    if s.get("tries", 0) >= MAX_TRIES and s.get("last_try", "")[:10] == now.date().isoformat(): return False
    if s.get("last_try"):
        last = datetime.fromisoformat(s["last_try"])
        if s.get("tries", 0) and now - last < timedelta(minutes=RETRY_MIN): return False
    return True


def tick(now: datetime | None = None) -> dict | None:
    now = now or datetime.now()
    with db.read() as c: s = load(c)
    if not due(s, now): return None
    if s.get("tries", 0) >= MAX_TRIES and s.get("last_try", "")[:10] != now.date().isoformat():
        with db.transaction() as c:                                              # nouveau jour : on repart de zéro
            s = load(c); s["tries"] = 0; save(c, s)
    return send_now(now=now)


_started = False


def start_scheduler(interval_s: int = 60) -> None:
    global _started
    if _started or config.demo_mode() or os.environ.get("GESTION_NO_AUTOBACKUP") or os.environ.get("GESTION_NO_NOTIFY"): return
    _started = True

    def loop():
        while True:
            try: tick()
            except Exception: pass                                            # un rappel raté ne doit jamais arrêter l'application
            time.sleep(interval_s)
    threading.Thread(target=loop, daemon=True, name="rappels").start()
