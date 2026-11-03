"""Paie simple : salariés, bulletins mensuels, paiement, écritures comptables.

Ce module ne connaît AUCUN taux légal : les taux de cotisation (part salariale, part patronale) et le plafond sont des réglages du propriétaire, à fixer avec un expert-comptable
(CNSS Guinée). Tant qu'ils sont à zéro, les bulletins n'ont pas de cotisation et l'écran le signale. L'impôt sur les salaires (ITS) et les autres retenues se saisissent à la main
sur chaque bulletin ; les taux et montants sont FIGÉS sur le bulletin au moment de sa création (changer un taux ne modifie jamais un bulletin passé).

Un bulletin n'est pas un paiement : « Payer » crée une VRAIE dépense (catégorie réservée « Paie : salaires nets versés », compte 422), donc la caisse, la banque et les budgets suivent.
En comptabilité, le bulletin constate la charge (661 salaires bruts, 664 charges patronales) contre le personnel dû (422), la CNSS (431) et l'ITS retenu (4471) ; le paiement solde le 422.
Ce module travaille en ligne (données sensibles, pas de copie hors connexion).

Avance sur salaire : une VRAIE dépense (catégorie réservée, compte 421) ; elle reste due jusqu'à sa reprise sur un bulletin, qui diminue le net et crédite le 421. La reprise est toujours décidée par
une personne : « Préparer tous » n'en fait aucune. Registre des congés : un simple relevé de dates et de jours saisis ; aucun droit acquis, aucun solde, aucun effet sur les bulletins ni sur la comptabilité.
"""
from __future__ import annotations

import calendar
import json
import re
import sqlite3
import uuid
from datetime import date

from . import audit, period
from .ops import OpError, next_seq
from .permissions import can
from .sales_ops import _date, _int, _text, next_counter, today

PAY_CATEGORY = "Paie : salaires nets versés"
ADVANCE_CATEGORY = "Paie : avances sur salaire"
LEAVE_KINDS = ("Congé payé", "Maladie", "Sans solde", "Autre absence")
PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
SETTINGS_KEY = "payroll_settings"
DEFAULTS = {"employee_bp": 0, "employer_bp": 0, "ceiling": 0}        # points de base (1 % = 100) ; plafond mensuel de la base de cotisation (0 = aucun)


def period_end(p: str) -> str:
    y, m = int(p[:4]), int(p[5:])
    return date(y, m, calendar.monthrange(y, m)[1]).isoformat()


def get_settings(c: sqlite3.Connection) -> dict:
    r = c.execute("SELECT value FROM meta WHERE key=?", (SETTINGS_KEY,)).fetchone()
    d = json.loads(r["value"]) if r else {}
    return {k: int(d.get(k, v)) for k, v in DEFAULTS.items()}


def set_settings(c, actor, data: dict) -> dict:
    new = {"employee_bp": _int(data.get("employee_bp"), "La part salariale (points de base)", 0, 5000),
           "employer_bp": _int(data.get("employer_bp"), "La part patronale (points de base)", 0, 5000),
           "ceiling": _int(data.get("ceiling", 0), "Le plafond mensuel", 0, 10 ** 12)}
    old = get_settings(c)
    c.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (SETTINGS_KEY, json.dumps(new)))
    if new != old: audit.record(c, actor, "payroll.settings", "payroll", None, old, new)
    return new


def contributions(gross: int, st: dict) -> tuple[int, int]:
    base = min(gross, st["ceiling"]) if st["ceiling"] else gross
    return (base * st["employee_bp"] + 5000) // 10000, (base * st["employer_bp"] + 5000) // 10000


def entry_date(month: str, created_at: str) -> str:
    """Date de l'écriture : fin du mois, ou le jour de création si le mois n'est pas fini (jamais une date future)."""
    return min(period_end(month), created_at[:10])


# ---------------------------------------------------------------- vues
def employee_view(e) -> dict:
    d = {k: e[k] for k in ("id", "name", "position", "phone", "base_salary", "hire_date", "note", "created_at")}
    d["active"] = bool(e["active"]); return d


def advance_balance(c: sqlite3.Connection, employee_id: str) -> int:
    """Avances versées (dépense active) moins ce qui a été repris sur les bulletins actifs."""
    given = c.execute("SELECT COALESCE(SUM(a.amount),0) s FROM salary_advances a JOIN expenses x ON x.id=a.expense_id WHERE a.employee_id=? AND a.status='active' AND x.status='active'", (employee_id,)).fetchone()["s"]
    taken = c.execute("SELECT COALESCE(SUM(advance),0) s FROM payslips WHERE employee_id=? AND status='active'", (employee_id,)).fetchone()["s"]
    return given - taken


def advance_view(c: sqlite3.Connection, a) -> dict:
    d = {k: a[k] for k in ("id", "number", "employee_id", "amount", "advance_date", "note", "status", "cancel_reason", "created_at")}
    d["employee"] = c.execute("SELECT name FROM employees WHERE id=?", (a["employee_id"],)).fetchone()["name"]
    x = c.execute("SELECT number, method, status FROM expenses WHERE id=?", (a["expense_id"],)).fetchone()
    d["paid"] = x["status"] == "active"; d["pay_method"] = x["method"]; d["expense_number"] = x["number"]
    return d


def leave_view(c: sqlite3.Connection, l) -> dict:
    d = {k: l[k] for k in ("id", "employee_id", "kind", "start_date", "end_date", "days", "note", "status", "cancel_reason", "created_at")}
    d["employee"] = c.execute("SELECT name FROM employees WHERE id=?", (l["employee_id"],)).fetchone()["name"]
    return d


def slip_view(c: sqlite3.Connection, s) -> dict:
    d = {k: s[k] for k in ("id", "number", "employee_id", "period", "base", "bonus", "gross", "employee_bp", "employer_bp", "ceiling", "social_employee", "social_employer", "its", "advance", "net", "note", "status",
                           "cancel_reason", "expense_id", "created_at")}
    e = c.execute("SELECT name FROM employees WHERE id=?", (s["employee_id"],)).fetchone()
    d["employee"] = e["name"] if e else "?"
    d["employer_cost"] = s["gross"] + s["social_employer"]
    x = c.execute("SELECT number, expense_date, method FROM expenses WHERE id=? AND status='active'", (s["expense_id"],)).fetchone() if s["expense_id"] else None
    d["paid"] = x is not None; d["paid_date"] = x["expense_date"] if x else None; d["pay_method"] = x["method"] if x else None; d["expense_number"] = x["number"] if x else None
    return d


def overview(c: sqlite3.Connection, month: str) -> dict:
    if not PERIOD_RE.match(month): raise OpError("Mois invalide (AAAA-MM).")
    emps = [employee_view(e) for e in c.execute("SELECT * FROM employees ORDER BY active DESC, name")]
    slips = [slip_view(c, s) for s in c.execute("SELECT * FROM payslips WHERE period=? ORDER BY number", (month,))]
    live = [s for s in slips if s["status"] == "active"]
    todo = [e for e in emps if e["active"] and not any(s["employee_id"] == e["id"] for s in live)]
    tot = {"gross": sum(s["gross"] for s in live), "social_employee": sum(s["social_employee"] for s in live), "social_employer": sum(s["social_employer"] for s in live),
           "its": sum(s["its"] for s in live), "advance": sum(s["advance"] for s in live), "net": sum(s["net"] for s in live), "employer_cost": sum(s["employer_cost"] for s in live),
           "unpaid_net": sum(s["net"] for s in live if not s["paid"]), "count": len(live)}
    st = get_settings(c); year = month[:4]
    leaves = [leave_view(c, l) for l in c.execute("SELECT * FROM employee_leaves WHERE start_date LIKE ? ORDER BY start_date DESC, created_at DESC", (year + "%",)).fetchall()]
    for e in emps:
        e["advance_balance"] = advance_balance(c, e["id"])
        e["leave_days"] = sum(l["days"] for l in leaves if l["employee_id"] == e["id"] and l["status"] == "active")
    advances = [advance_view(c, a) for a in c.execute("SELECT * FROM salary_advances WHERE advance_date LIKE ? ORDER BY advance_date DESC, number DESC", (year + "%",)).fetchall()]
    tot["advances_open"] = sum(e["advance_balance"] for e in emps)
    return {"month": month, "settings": st, "rates_missing": st["employee_bp"] == 0 and st["employer_bp"] == 0, "employees": emps, "slips": slips, "to_generate": [e["id"] for e in todo], "totals": tot,
            "advances": advances, "leaves": leaves, "leave_kinds": list(LEAVE_KINDS)}


# ---------------------------------------------------------------- salariés
def employee_save(c, user, actor, data: dict, eid: str | None) -> dict:
    ch = {}
    if eid is None or "name" in data: ch["name"] = _text(data.get("name"), "Le nom", 120, True)
    if eid is None or "position" in data: ch["position"] = _text(data.get("position"), "Le poste", 80)
    if eid is None or "phone" in data: ch["phone"] = _text(data.get("phone"), "Le téléphone", 30)
    if eid is None or "base_salary" in data: ch["base_salary"] = _int(data.get("base_salary"), "Le salaire de base", 0, 10 ** 11)
    if eid is None or "hire_date" in data: ch["hire_date"] = _date(data.get("hire_date") or None, "La date d'embauche", required=False)
    if eid is None or "note" in data: ch["note"] = _text(data.get("note"), "La note", 200)
    if "active" in data: ch["active"] = 1 if data["active"] else 0
    now = security_now()
    if eid is None:
        eid = str(uuid.uuid4())
        c.execute("INSERT INTO employees(id,name,position,phone,base_salary,hire_date,note,active,created_at,updated_at,created_by) VALUES(?,?,?,?,?,?,?,1,?,?,?)",
                  (eid, ch["name"], ch["position"], ch["phone"], ch["base_salary"], ch["hire_date"], ch["note"], now, now, user["id"]))
        audit.record(c, actor, "employee.create", "employee", eid, None, {"nom": ch["name"], "poste": ch["position"], "salaire de base": ch["base_salary"]})
    else:
        old = c.execute("SELECT * FROM employees WHERE id=?", (eid,)).fetchone()
        if old is None: raise OpError("Salarié introuvable.")
        ch = {k: v for k, v in ch.items() if old[k] != v}
        if ch:
            c.execute(f"UPDATE employees SET {', '.join(k + '=?' for k in ch)}, updated_at=? WHERE id=?", (*ch.values(), now, eid))
            audit.record(c, actor, "employee.update", "employee", eid, {k: old[k] for k in ch}, ch, note=old["name"])
    return employee_view(c.execute("SELECT * FROM employees WHERE id=?", (eid,)).fetchone())


def security_now() -> str:
    from . import security
    return security.iso()


# ---------------------------------------------------------------- bulletins
def slip_create(c, user, actor, employee_id: str, month: str, bonus: int = 0, its: int = 0, note: str = "", advance: int = 0) -> dict:
    if not PERIOD_RE.match(month or ""): raise OpError("Mois invalide (AAAA-MM).")
    e = c.execute("SELECT * FROM employees WHERE id=?", (employee_id,)).fetchone()
    if e is None or not e["active"]: raise OpError("Salarié introuvable ou inactif.")
    if month > today().isoformat()[:7]: raise OpError("On ne prépare pas un bulletin pour un mois qui n'a pas commencé.")
    period.ensure_open(c, min(period_end(month), today().isoformat()))
    if c.execute("SELECT 1 FROM payslips WHERE employee_id=? AND period=? AND status='active'", (employee_id, month)).fetchone():
        raise OpError(f"{e['name']} a déjà un bulletin pour {month} : annulez-le d'abord pour le refaire.")
    bonus = _int(bonus, "Les primes", 0, 10 ** 11); its = _int(its, "L'ITS et autres retenues", 0, 10 ** 11)
    note = _text(note, "La note", 200)
    advance = _int(advance, "L'avance reprise", 0, 10 ** 11); balance = advance_balance(c, employee_id)
    if advance > balance: raise OpError(f"L'avance reprise dépasse ce que {e['name']} doit encore ({balance}).")
    gross = e["base_salary"] + bonus; st = get_settings(c); se, sr = contributions(gross, st)
    net = gross - se - its - advance
    if net < 0: raise OpError("Les retenues dépassent le salaire brut.")
    sid = str(uuid.uuid4()); number = f"BP-{next_counter(c, 'payslip_no'):04d}"; now = security_now()
    c.execute("INSERT INTO payslips(id,number,employee_id,period,base,bonus,gross,employee_bp,employer_bp,ceiling,social_employee,social_employer,its,advance,net,note,status,seq,created_at,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'active',?,?,?)",
              (sid, number, employee_id, month, e["base_salary"], bonus, gross, st["employee_bp"], st["employer_bp"], st["ceiling"], se, sr, its, advance, net, note, next_seq(c), now, user["id"]))
    audit.record(c, actor, "payslip.create", "payslip", sid, None, {"numéro": number, "salarié": e["name"], "mois": month, "brut": gross, "cotisations salariales": se, "cotisations patronales": sr, "ITS/retenues": its, "avance reprise": advance, "net": net})
    return slip_view(c, c.execute("SELECT * FROM payslips WHERE id=?", (sid,)).fetchone())


def generate(c, user, actor, month: str) -> dict:
    made, blocked, owing = [], [], []
    for e in c.execute("SELECT * FROM employees WHERE active=1 ORDER BY name").fetchall():
        if c.execute("SELECT 1 FROM payslips WHERE employee_id=? AND period=? AND status='active'", (e["id"], month)).fetchone(): continue
        try:
            made.append(slip_create(c, user, actor, e["id"], month)["number"])
            if advance_balance(c, e["id"]) > 0: owing.append(e["name"])         # aucune reprise automatique : on le signale, la personne décide
        except OpError as x: blocked.append({"employee": e["name"], "message": x.message})
    return {"created": made, "blocked": blocked, "with_advance": owing}


def slip_pay(c, user, actor, slip_id: str, method: str, pay_date: str | None) -> dict:
    from .cash_ops import _expense_create
    s = c.execute("SELECT * FROM payslips WHERE id=?", (slip_id,)).fetchone()
    if s is None or s["status"] != "active": raise OpError("Bulletin introuvable ou annulé.")
    if slip_view(c, s)["paid"]: raise OpError("Ce bulletin est déjà payé.")
    if s["net"] <= 0: raise OpError("Net à payer nul : rien à verser.")
    emp = c.execute("SELECT name FROM employees WHERE id=?", (s["employee_id"],)).fetchone()["name"]
    d = _date(pay_date or today().isoformat(), "Date du paiement")
    res = _expense_create(c, user, actor, {"category": PAY_CATEGORY, "amount": s["net"], "method": method, "payee": emp, "expense_date": d,
                                           "description": f"Salaire {s['period']} ({s['number']})"}, user["role"], security_now())
    if res.get("status") != "ok": raise OpError(res.get("message", "Paiement refusé."))
    c.execute("UPDATE payslips SET expense_id=?, seq=? WHERE id=?", (res["expense"]["id"], next_seq(c), slip_id))
    audit.record(c, actor, "payslip.pay", "payslip", slip_id, None, {"net": s["net"], "mode": method, "date": d, "dépense": res["expense"]["number"]}, note=s["number"])
    return slip_view(c, c.execute("SELECT * FROM payslips WHERE id=?", (slip_id,)).fetchone())


def slip_cancel(c, user, actor, slip_id: str, reason: str) -> dict:
    from .cash_ops import _expense_cancel
    s = c.execute("SELECT * FROM payslips WHERE id=?", (slip_id,)).fetchone()
    if s is None or s["status"] != "active": raise OpError("Bulletin introuvable ou déjà annulé.")
    reason = _text(reason, "Le motif", 200, True)
    if len(reason) < 3: raise OpError("Le motif d'annulation est obligatoire.")
    period.ensure_open(c, entry_date(s["period"], s["created_at"]))
    if slip_view(c, s)["paid"]:
        x = c.execute("SELECT version FROM expenses WHERE id=?", (s["expense_id"],)).fetchone()
        res = _expense_cancel(c, user, actor, {"id": s["expense_id"], "base_version": x["version"], "reason": f"Annulation du bulletin {s['number']} : {reason}"}, user["role"], security_now())
        if res.get("status") != "ok": raise OpError(res.get("message", "Annulation du paiement refusée."))
    c.execute("UPDATE payslips SET status='cancelled', cancel_reason=?, seq=? WHERE id=?", (reason, next_seq(c), slip_id))
    audit.record(c, actor, "payslip.cancel", "payslip", slip_id, {"statut": "actif", "net": s["net"]}, {"statut": "annulé", "motif": reason}, note=s["number"])
    return slip_view(c, c.execute("SELECT * FROM payslips WHERE id=?", (slip_id,)).fetchone())


# ---------------------------------------------------------------- avances sur salaire
def advance_create(c, user, actor, employee_id: str, amount: int, method: str, pay_date: str | None, note: str = "") -> dict:
    from .cash_ops import _expense_create
    e = c.execute("SELECT * FROM employees WHERE id=?", (employee_id,)).fetchone()
    if e is None or not e["active"]: raise OpError("Salarié introuvable ou inactif.")
    amount = _int(amount, "Le montant de l'avance", 1, 10 ** 11); note = _text(note, "La note", 200)
    d = _date(pay_date or today().isoformat(), "Date de l'avance")
    aid = str(uuid.uuid4()); number = f"AV-{next_counter(c, 'advance_no'):04d}"
    res = _expense_create(c, user, actor, {"category": ADVANCE_CATEGORY, "amount": amount, "method": method, "payee": e["name"], "expense_date": d, "description": f"Avance sur salaire ({number})"}, user["role"], security_now())
    if res.get("status") != "ok": raise OpError(res.get("message", "Paiement refusé."))
    c.execute("INSERT INTO salary_advances(id,number,employee_id,amount,advance_date,note,status,expense_id,seq,created_at,created_by) VALUES(?,?,?,?,?,?,'active',?,?,?,?)",
              (aid, number, employee_id, amount, d, note, res["expense"]["id"], next_seq(c), security_now(), user["id"]))
    audit.record(c, actor, "advance.create", "salary_advance", aid, None, {"numéro": number, "salarié": e["name"], "montant": amount, "mode": method, "date": d, "dépense": res["expense"]["number"]})
    return advance_view(c, c.execute("SELECT * FROM salary_advances WHERE id=?", (aid,)).fetchone())


def _reject_if_already_taken(c, a) -> None:
    if advance_balance(c, a["employee_id"]) < a["amount"]:
        raise OpError(f"L'avance {a['number']} a déjà été reprise sur un bulletin : annulez d'abord ce bulletin.")


def check_advance_expense_cancel(c, expense_id: str) -> None:
    """Refuse d'annuler à part la dépense d'une avance déjà reprise : le salarié aurait remboursé une avance jamais versée."""
    a = c.execute("SELECT * FROM salary_advances WHERE expense_id=? AND status='active'", (expense_id,)).fetchone()
    if a is not None: _reject_if_already_taken(c, a)


def advance_cancel(c, user, actor, advance_id: str, reason: str) -> dict:
    from .cash_ops import _expense_cancel
    a = c.execute("SELECT * FROM salary_advances WHERE id=?", (advance_id,)).fetchone()
    if a is None or a["status"] != "active": raise OpError("Avance introuvable ou déjà annulée.")
    reason = _text(reason, "Le motif", 200, True)
    if len(reason) < 3: raise OpError("Le motif d'annulation est obligatoire.")
    x = c.execute("SELECT version, status FROM expenses WHERE id=?", (a["expense_id"],)).fetchone()
    if x["status"] == "active":
        res = _expense_cancel(c, user, actor, {"id": a["expense_id"], "base_version": x["version"], "reason": f"Annulation de l'avance {a['number']} : {reason}"}, user["role"], security_now())
        if res.get("status") != "ok": raise OpError(res.get("message", "Annulation du paiement refusée."))
    c.execute("UPDATE salary_advances SET status='cancelled', cancel_reason=?, seq=? WHERE id=?", (reason, next_seq(c), advance_id))
    audit.record(c, actor, "advance.cancel", "salary_advance", advance_id, {"statut": "actif", "montant": a["amount"]}, {"statut": "annulé", "motif": reason}, note=a["number"])
    return advance_view(c, c.execute("SELECT * FROM salary_advances WHERE id=?", (advance_id,)).fetchone())


# ---------------------------------------------------------------- registre des congés
def leave_create(c, user, actor, employee_id: str, kind: str, start: str, end: str, days: int, note: str = "") -> dict:
    e = c.execute("SELECT * FROM employees WHERE id=?", (employee_id,)).fetchone()
    if e is None: raise OpError("Salarié introuvable.")
    if kind not in LEAVE_KINDS: raise OpError("Type d'absence inconnu.")
    start = _date(start, "La date de début"); end = _date(end, "La date de fin")
    if end < start: raise OpError("La date de fin précède la date de début.")
    span = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    days = _int(days, "Le nombre de jours", 1, span); note = _text(note, "La note", 200)
    if c.execute("SELECT 1 FROM employee_leaves WHERE employee_id=? AND status='active' AND start_date<=? AND end_date>=?", (employee_id, end, start)).fetchone():
        raise OpError(f"{e['name']} a déjà une absence enregistrée sur ces dates.")
    lid = str(uuid.uuid4())
    c.execute("INSERT INTO employee_leaves(id,employee_id,kind,start_date,end_date,days,note,status,seq,created_at,created_by) VALUES(?,?,?,?,?,?,?,'active',?,?,?)",
              (lid, employee_id, kind, start, end, days, note, next_seq(c), security_now(), user["id"]))
    audit.record(c, actor, "leave.create", "employee_leave", lid, None, {"salarié": e["name"], "type": kind, "du": start, "au": end, "jours": days})
    return leave_view(c, c.execute("SELECT * FROM employee_leaves WHERE id=?", (lid,)).fetchone())


def leave_cancel(c, user, actor, leave_id: str, reason: str) -> dict:
    l = c.execute("SELECT * FROM employee_leaves WHERE id=?", (leave_id,)).fetchone()
    if l is None or l["status"] != "active": raise OpError("Absence introuvable ou déjà annulée.")
    reason = _text(reason, "Le motif", 200, True)
    if len(reason) < 3: raise OpError("Le motif d'annulation est obligatoire.")
    c.execute("UPDATE employee_leaves SET status='cancelled', cancel_reason=?, seq=? WHERE id=?", (reason, next_seq(c), leave_id))
    audit.record(c, actor, "leave.cancel", "employee_leave", leave_id, {"statut": "actif", "jours": l["days"]}, {"statut": "annulé", "motif": reason})
    return leave_view(c, c.execute("SELECT * FROM employee_leaves WHERE id=?", (leave_id,)).fetchone())


# ---------------------------------------------------------------- comptabilité
def entries(c: sqlite3.Connection) -> list[dict]:
    out = []
    for s in c.execute("SELECT s.*, e.name ename FROM payslips s JOIN employees e ON e.id=s.employee_id WHERE s.status='active'"):
        lines = [("661", s["gross"], 0), ("664", s["social_employer"], 0), ("422", 0, s["net"]), ("421", 0, s["advance"]), ("431", 0, s["social_employee"] + s["social_employer"]), ("4471", 0, s["its"])]
        lines = [l for l in lines if l[1] or l[2]]
        if lines: out.append({"key": f"payslip:{s['id']}", "date": entry_date(s["period"], s["created_at"]), "journal": "OD", "ref": s["number"], "label": f"Paie {s['period']} : {s['ename']}", "lines": lines, "auto": True})
    return out


# ---------------------------------------------------------------- bulletin imprimable
def slip_html(c: sqlite3.Connection, s) -> str:
    from html import escape
    from .invoice import _d, _header, _n, _page, _st
    v = slip_view(c, s); e = c.execute("SELECT * FROM employees WHERE id=?", (s["employee_id"],)).fetchone()
    stamp = '<p class="stamp">ANNULÉ</p>' if s["status"] == "cancelled" else ""
    def row(label, val, strong=False): return f'<tr><td>{label}</td><td class="num">{"<strong>" if strong else ""}{_n(val)}{"</strong>" if strong else ""}</td></tr>'
    rows = (row("Salaire de base", s["base"]) + (row("Primes", s["bonus"]) if s["bonus"] else "") + row("Salaire brut", s["gross"], True)
            + (row(f"Cotisations salariales ({s['employee_bp'] / 100:g} %)", -s["social_employee"]) if s["social_employee"] else "")
            + (row("ITS et autres retenues", -s["its"]) if s["its"] else "") + (row("Avance sur salaire reprise", -s["advance"]) if s["advance"] else "") + row("Net à payer", s["net"], True))
    body = (_header(_st(c), "BULLETIN DE PAIE", s["number"], _d(period_end(s["period"]))) + stamp +
            f'<section class="client"><h3>Salarié</h3><p><strong>{escape(e["name"])}</strong>{" · " + escape(e["position"]) if e["position"] else ""}<br>Période : {escape(s["period"])}</p></section>'
            f'<table><thead><tr><th>Rubrique</th><th>Montant (GNF)</th></tr></thead><tbody>{rows}</tbody></table>'
            + (f'<p class="muted">Part patronale (information) : {_n(s["social_employer"])} GNF · coût total employeur : {_n(v["employer_cost"])} GNF</p>' if s["social_employer"] else "")
            + (f'<p>{escape(s["note"])}</p>' if s["note"] else "")
            + (f'<p>Payé le {_d(v["paid_date"])} ({escape(v["pay_method"])}).</p>' if v["paid"] else '<p>Rien à verser.</p>' if s["net"] == 0 else '<p>Non encore payé.</p>')
            + '<table class="small"><tbody><tr><td class="sign">Signature du salarié</td><td>Signature de l\'employeur</td></tr></tbody></table>'
            '<footer><p class="muted">Bulletin simplifié : les taux et retenues sont ceux saisis par l\'entreprise.</p></footer>')
    return _page(f"Bulletin {s['number']}", body)
