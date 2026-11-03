"""Provisions et dépréciations : créances douteuses, stocks, litiges et autres risques.

Une provision constate une perte PROBABLE sans qu'elle soit encore certaine : dotation en charge (691) contre un compte de dépréciation ou de provision (491, 391, 191, 198).
Quand le risque disparaît ou diminue (client qui paie, litige gagné), on fait une reprise (791). Les écritures sont déduites du registre, comme le reste de la comptabilité.
Le montant d'une provision est un JUGEMENT : l'application ne le décide jamais. Les suggestions sur créances en retard ne sont que des repères indicatifs,
à valider avec l'expert-comptable (et à distinguer de la règle fiscale de déductibilité, non traitée ici).
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import date

from . import audit, period
from .ops import UUID_RE, OpError, _deny, next_seq
from .permissions import can
from .sales_ops import _conflict, _date, _int, _text, next_counter, sale_totals, today

KINDS = {
    "receivable": {"label": "Créance client douteuse", "account": "491"},
    "stock": {"label": "Dépréciation de stock", "account": "391"},
    "litigation": {"label": "Litige ou contentieux", "account": "191"},
    "other": {"label": "Autre risque ou charge", "account": "198"},
}
SUGGEST_DAYS, SUGGEST_FULL_DAYS = 90, 180


def remaining(c: sqlite3.Connection, p) -> int:
    rel = c.execute("SELECT COALESCE(SUM(amount),0) s FROM provision_releases WHERE provision_id=? AND status='active'", (p["id"],)).fetchone()["s"]
    return p["amount"] - rel


def view(c: sqlite3.Connection, p) -> dict:
    d = {k: p[k] for k in ("id", "number", "kind", "label", "sale_id", "client_id", "amount", "prov_date", "status", "cancel_reason", "note", "version", "seq", "created_at")}
    rels = [{"id": r["id"], "date": r["rel_date"], "amount": r["amount"], "reason": r["reason"], "status": r["status"]} for r in c.execute("SELECT * FROM provision_releases WHERE provision_id=? ORDER BY rel_date, id", (p["id"],))]
    d["releases"] = rels; d["released"] = sum(r["amount"] for r in rels if r["status"] == "active"); d["remaining"] = p["amount"] - d["released"]
    d["account"] = KINDS[p["kind"]]["account"]; d["kind_label"] = KINDS[p["kind"]]["label"]
    s = c.execute("SELECT s.number, cl.name FROM sales s JOIN clients cl ON cl.id=s.client_id WHERE s.id=?", (p["sale_id"],)).fetchone() if p["sale_id"] else None
    d["sale_number"] = s["number"] if s else None; d["client_name"] = s["name"] if s else None
    return d


def suggestions(c: sqlite3.Connection) -> list[dict]:
    """Créances échues depuis longtemps et sans provision en cours : repères indicatifs (50 % dès 90 jours, 100 % dès 180)."""
    out, t = [], today()
    covered = {r["sale_id"] for r in c.execute("SELECT * FROM provisions WHERE status='active' AND sale_id IS NOT NULL") if remaining(c, r) > 0}
    for s in c.execute("SELECT s.*, cl.name cname FROM sales s JOIN clients cl ON cl.id=s.client_id WHERE s.status='active' AND s.due_date IS NOT NULL ORDER BY s.due_date"):
        if s["id"] in covered: continue
        bal = sale_totals(c, s)["balance"]
        if bal <= 0: continue
        days = (t - date.fromisoformat(s["due_date"])).days
        if days < SUGGEST_DAYS: continue
        out.append({"sale_id": s["id"], "number": s["number"], "client_id": s["client_id"], "client_name": s["cname"], "due_date": s["due_date"], "days_late": days, "balance": bal,
                    "suggested": bal if days >= SUGGEST_FULL_DAYS else round(bal / 2)})
    return out


def entries(c: sqlite3.Connection, mp: dict) -> list[dict]:
    out = []
    for p in c.execute("SELECT * FROM provisions WHERE status='active' ORDER BY prov_date, number"):
        acct = KINDS[p["kind"]]["account"]
        out.append({"key": f"prov:{p['id']}", "date": p["prov_date"], "journal": "OD", "ref": p["number"], "label": f"Dotation à la provision {p['number']} : {p['label']}", "lines": [(mp["provision_expense"], p["amount"], 0), (acct, 0, p["amount"])], "auto": True})
        for r in c.execute("SELECT * FROM provision_releases WHERE provision_id=? AND status='active' ORDER BY rel_date, id", (p["id"],)):
            out.append({"key": f"provrel:{r['id']}", "date": r["rel_date"], "journal": "OD", "ref": p["number"], "label": f"Reprise de provision {p['number']} : {p['label']}", "lines": [(acct, r["amount"], 0), (mp["provision_reversal"], 0, r["amount"])], "auto": True})
    return out


def _create(c, user, actor, data, role, now):
    if not can(role, "accounting.write"): return _deny(c, actor, "provision.create", None, "droit manquant")
    pid = str(data.get("id") or uuid.uuid4())
    if not UUID_RE.match(pid): raise OpError("Identifiant de provision invalide.")
    if c.execute("SELECT 1 FROM provisions WHERE id=?", (pid,)).fetchone(): raise OpError("Cette provision existe déjà.")
    kind = data.get("kind")
    if kind not in KINDS: raise OpError("Nature de provision inconnue.")
    label = _text(data.get("label"), "Le libellé", 160, True)
    if len(label) < 3: raise OpError("Le libellé est obligatoire.")
    amount = _int(data.get("amount"), "Le montant", 1, 10 ** 13)
    d = _date(data.get("prov_date") or today().isoformat(), "La date")
    if date.fromisoformat(d) > today(): raise OpError("La date ne peut pas être dans le futur.")
    period.ensure_open(c, d)
    sale_id = client_id = None
    if kind == "receivable":
        s = c.execute("SELECT * FROM sales WHERE id=? AND status='active'", (str(data.get("sale_id") or ""),)).fetchone()
        if s is None: raise OpError("Choisissez la facture à crédit concernée.")
        bal = sale_totals(c, s)["balance"]
        already = sum(remaining(c, r) for r in c.execute("SELECT * FROM provisions WHERE status='active' AND sale_id=?", (s["id"],)))
        if amount + already > bal: raise OpError(f"La provision dépasse le reste dû sur cette facture ({bal}, dont {already} déjà provisionné).")
        sale_id, client_id = s["id"], s["client_id"]
    number = f"PV-{next_counter(c, 'provision_no'):04d}"
    c.execute("INSERT INTO provisions(id,number,kind,label,sale_id,client_id,amount,prov_date,status,note,version,seq,created_at,updated_at,created_by,updated_by) VALUES(?,?,?,?,?,?,?,?,'active',?,1,?,?,?,?,?)",
              (pid, number, kind, label, sale_id, client_id, amount, d, _text(data.get("note"), "La note", 200), next_seq(c), now, now, user["id"], user["id"]))
    audit.record(c, actor, "provision.create", "provision", pid, None, {"numéro": number, "nature": KINDS[kind]["label"], "libellé": label, "montant": amount, "date": d, "facture": sale_id})
    return {"status": "ok", "provision": view(c, c.execute("SELECT * FROM provisions WHERE id=?", (pid,)).fetchone())}


def _release(c, user, actor, data, role, now):
    if not can(role, "accounting.write"): return _deny(c, actor, "provision.release", str(data.get("id") or ""), "droit manquant")
    p = c.execute("SELECT * FROM provisions WHERE id=? AND status='active'", (str(data.get("id") or ""),)).fetchone()
    if p is None: return {"status": "invalid", "message": "Provision introuvable ou annulée."}
    if int(data.get("base_version") or 0) != p["version"]: return _conflict(c, actor, "provision", p["id"], view(c, p), "reprise : provision modifiée entre-temps")
    left = remaining(c, p)
    amount = _int(data.get("amount") or left, "Le montant repris", 1, 10 ** 13)
    if amount > left: raise OpError(f"On ne peut pas reprendre plus que le solde de la provision ({left}).")
    d = _date(data.get("date") or today().isoformat(), "La date de la reprise")
    if date.fromisoformat(d) > today(): raise OpError("La date ne peut pas être dans le futur.")
    if d < p["prov_date"]: raise OpError("La reprise ne peut pas précéder la provision.")
    period.ensure_open(c, d)
    reason = _text(data.get("reason"), "Le motif", 200, True)
    if len(reason) < 3: raise OpError("Le motif de la reprise est obligatoire (client payé, litige gagné…).")
    rid = str(uuid.uuid4())
    c.execute("INSERT INTO provision_releases(id,provision_id,rel_date,amount,reason,status,created_at,created_by) VALUES(?,?,?,?,?,'active',?,?)", (rid, p["id"], d, amount, reason, now, user["id"]))
    c.execute("UPDATE provisions SET version=version+1, seq=?, updated_at=?, updated_by=? WHERE id=?", (next_seq(c), now, user["id"], p["id"]))
    audit.record(c, actor, "provision.release", "provision", p["id"], {"solde": left}, {"solde": left - amount, "montant repris": amount, "date": d, "motif": reason}, note=p["number"])
    return {"status": "ok", "provision": view(c, c.execute("SELECT * FROM provisions WHERE id=?", (p["id"],)).fetchone())}


def _unrelease(c, user, actor, data, role, now):
    if not can(role, "accounting.write"): return _deny(c, actor, "provision.unrelease", str(data.get("id") or ""), "droit manquant")
    p = c.execute("SELECT * FROM provisions WHERE id=? AND status='active'", (str(data.get("id") or ""),)).fetchone()
    if p is None: return {"status": "invalid", "message": "Provision introuvable ou annulée."}
    r = c.execute("SELECT * FROM provision_releases WHERE id=? AND provision_id=? AND status='active'", (str(data.get("release_id") or ""), p["id"])).fetchone()
    if r is None: return {"status": "invalid", "message": "Reprise introuvable."}
    if int(data.get("base_version") or 0) != p["version"]: return _conflict(c, actor, "provision", p["id"], view(c, p), "annulation de reprise : provision modifiée entre-temps")
    period.ensure_open(c, r["rel_date"])
    reason = _text(data.get("reason"), "Le motif", 200, True)
    if len(reason) < 3: raise OpError("Le motif d'annulation est obligatoire.")
    c.execute("UPDATE provision_releases SET status='cancelled' WHERE id=?", (r["id"],))
    c.execute("UPDATE provisions SET version=version+1, seq=?, updated_at=?, updated_by=? WHERE id=?", (next_seq(c), now, user["id"], p["id"]))
    audit.record(c, actor, "provision.unrelease", "provision", p["id"], {"reprise": r["amount"], "date": r["rel_date"]}, {"statut": "annulée", "motif": reason}, note=p["number"])
    return {"status": "ok"}


def _cancel(c, user, actor, data, role, now):
    if not can(role, "accounting.write"): return _deny(c, actor, "provision.cancel", str(data.get("id") or ""), "droit manquant")
    p = c.execute("SELECT * FROM provisions WHERE id=? AND status='active'", (str(data.get("id") or ""),)).fetchone()
    if p is None: return {"status": "invalid", "message": "Provision introuvable ou déjà annulée."}
    if c.execute("SELECT 1 FROM provision_releases WHERE provision_id=? AND status='active'", (p["id"],)).fetchone(): raise OpError("Des reprises existent : annulez-les d'abord, ou reprenez le solde.")
    if int(data.get("base_version") or 0) != p["version"]: return _conflict(c, actor, "provision", p["id"], view(c, p), "annulation : provision modifiée entre-temps")
    period.ensure_open(c, p["prov_date"])
    reason = _text(data.get("reason"), "Le motif", 200, True)
    if len(reason) < 3: raise OpError("Le motif d'annulation est obligatoire.")
    c.execute("UPDATE provisions SET status='cancelled', cancel_reason=?, version=version+1, seq=?, updated_at=?, updated_by=? WHERE id=?", (reason, next_seq(c), now, user["id"], p["id"]))
    audit.record(c, actor, "provision.cancel", "provision", p["id"], {"numéro": p["number"], "montant": p["amount"]}, {"statut": "annulée", "motif": reason})
    return {"status": "ok"}


HANDLERS = {"provision.create": _create, "provision.release": _release, "provision.unrelease": _unrelease, "provision.cancel": _cancel}
