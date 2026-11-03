"""Relances des factures impayées : quoi relancer, avec quel ton, et qui a déjà relancé qui.

Chaque relance faite (WhatsApp, appel, SMS, e-mail, visite) est notée dans le journal d'audit (action « dunning.logged ») : on sait qui a relancé, quand, par quel canal,
et la date de paiement promise par le client. Rien n'est envoyé automatiquement : l'application prépare le message, la personne l'envoie puis le note.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta

from . import audit, sales_ops
from .sales_ops import settings_get, today

CHANNELS = {"whatsapp": "WhatsApp", "appel": "Appel", "sms": "SMS", "mail": "E-mail", "visite": "Visite"}
# (jours de retard à partir de, libellé, délai avant la relance suivante)
LEVELS = [(1, "Rappel amical", 3), (8, "Relance", 7), (22, "Relance ferme", 10), (46, "Dernier avertissement", 14)]
MAX_PROMISE_DAYS = 90


def level_of(days_late: int) -> int:
    lv = 1
    for i, (start, _, _) in enumerate(LEVELS, 1):
        if days_late >= start: lv = i
    return lv


def _money(v: int) -> str:
    return f"{int(v):,}".replace(",", " ") + " GNF"


def _fd(iso: str) -> str:
    return "/".join(reversed(iso.split("-")))


def message(level: int, client: str, number: str, balance: int, due: str, days_late: int, company: str, promise: str | None = None) -> str:
    c = company or "notre entreprise"
    if promise:
        return f"Bonjour {client}, nous vous remercions de nous avoir indiqué le {_fd(promise)} pour le règlement de la facture {number} ({_money(balance)} restant dû). Nous comptons sur ce paiement. {c}"
    if level == 1:
        return f"Bonjour {client}, petit rappel amical : la facture {number} ({_money(balance)}) était à régler le {_fd(due)}. Si c'est déjà fait, merci d'ignorer ce message. {c}"
    if level == 2:
        return f"Bonjour {client}, sauf erreur de notre part, la facture {number} reste impayée : {_money(balance)}, échue depuis {days_late} jours. Pouvez-vous nous indiquer la date de votre paiement ? Merci. {c}"
    if level == 3:
        return f"Bonjour {client}, malgré nos précédents rappels, la facture {number} ({_money(balance)}) n'est toujours pas réglée, {days_late} jours après l'échéance. Merci de procéder au paiement cette semaine ou de nous contacter pour convenir d'un échéancier. {c}"
    return f"Bonjour {client}, la facture {number} ({_money(balance)}) est impayée depuis {days_late} jours. Faute de règlement ou d'accord de votre part sous 7 jours, nous serons contraints d'engager les démarches de recouvrement prévues dans nos conditions de vente. {c}"


def _history(c: sqlite3.Connection, ids: list[str]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    if not ids: return out
    q = ",".join("?" * len(ids))
    for r in c.execute(f"SELECT ts, username, entity_id, after_json FROM audit_log WHERE action='dunning.logged' AND entity='sale' AND entity_id IN ({q}) ORDER BY id", ids):
        a = json.loads(r["after_json"] or "{}")
        out.setdefault(r["entity_id"], []).append({"ts": r["ts"], "user": r["username"], "channel": a.get("canal"), "level": a.get("niveau"), "note": a.get("note") or "", "promise": a.get("promesse")})
    return out


def compute(c: sqlite3.Connection, only_due: bool = False) -> dict:
    t = today(); ts = t.isoformat(); company = settings_get(c).get("company_name") or ""
    sales = [s for s in c.execute("SELECT * FROM sales WHERE status='active' AND kind='credit' AND due_date IS NOT NULL AND due_date < ? ORDER BY due_date", (ts,))]
    hist = _history(c, [s["id"] for s in sales]); cl = {r["id"]: r for r in c.execute("SELECT id, name, phone FROM clients")}
    rows = []
    for s in sales:
        bal = sales_ops.sale_totals(c, s)["balance"]
        if bal <= 0: continue
        days = (t - date.fromisoformat(s["due_date"])).days; lv = level_of(days); h = hist.get(s["id"], [])
        last = h[-1] if h else None
        promise = next((x["promise"] for x in reversed(h) if x["promise"]), None); promise_active = bool(promise and promise >= ts)
        gap = LEVELS[lv - 1][2]
        days_since = (t - date.fromisoformat(last["ts"][:10])).days if last else None
        if promise_active: state = "promesse"
        elif last is None or days_since >= gap: state = "a_relancer"
        else: state = "relance"
        if only_due and state != "a_relancer": continue
        c_ = cl.get(s["client_id"]); name = c_["name"] if c_ else "—"
        rows.append({"sale_id": s["id"], "number": s["number"], "client_id": s["client_id"], "client": name, "phone": (c_["phone"] if c_ else "") or "", "due_date": s["due_date"], "days_late": days, "balance": bal,
                     "level": lv, "level_label": LEVELS[lv - 1][1], "state": state, "last": last, "promise": promise, "promise_active": promise_active, "attempts": len(h),
                     "message": message(lv, name, s["number"], bal, s["due_date"], days, company, promise if promise_active else None)})
    order = {"a_relancer": 0, "relance": 1, "promesse": 2}
    rows.sort(key=lambda r: (order[r["state"]], -r["level"], -r["balance"]))
    return {"items": rows, "totals": {"count": len(rows), "amount": sum(r["balance"] for r in rows), "to_do": sum(1 for r in rows if r["state"] == "a_relancer"),
                                       "to_do_amount": sum(r["balance"] for r in rows if r["state"] == "a_relancer"), "promised_amount": sum(r["balance"] for r in rows if r["state"] == "promesse")},
            "levels": [{"level": i, "label": l, "from_days": s} for i, (s, l, _) in enumerate(LEVELS, 1)], "channels": CHANNELS}


class DunningError(ValueError):
    pass


def log(c: sqlite3.Connection, actor, sale_id: str, channel: str, note: str = "", promise: str | None = None) -> dict:
    """Note une relance faite. Refuse une facture qui n'est pas en retard, un canal inconnu, une date promise passée ou lointaine."""
    if channel not in CHANNELS: raise DunningError("Canal inconnu (WhatsApp, appel, SMS, e-mail ou visite).")
    s = c.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
    if s is None or s["status"] != "active" or s["kind"] != "credit" or not s["due_date"] or s["due_date"] >= today().isoformat() or sales_ops.sale_totals(c, s)["balance"] <= 0:
        raise DunningError("Cette facture n'est pas en retard de paiement : rien à relancer.")
    if promise:
        try: d = date.fromisoformat(promise)
        except ValueError: raise DunningError("Date promise invalide.")
        if d < today() or d > today() + timedelta(days=MAX_PROMISE_DAYS): raise DunningError(f"La date promise doit être comprise entre aujourd'hui et {MAX_PROMISE_DAYS} jours.")
    note = (note or "").strip()[:300]
    days = (today() - date.fromisoformat(s["due_date"])).days; lv = level_of(days)
    audit.record(c, actor, "dunning.logged", "sale", sale_id, None, {"canal": channel, "niveau": lv, "note": note, "promesse": promise or None},
                 note=f"relance {CHANNELS[channel]} de la facture {s['number']} (niveau {lv})" + (f", paiement promis le {_fd(promise)}" if promise else ""))
    return {"level": lv}
