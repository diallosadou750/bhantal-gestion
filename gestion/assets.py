"""Immobilisations et amortissements (linéaire ou dégressif).

Le registre des immobilisations produit, comme le reste de la comptabilité, des écritures déduites des données :
  acquisition  : immobilisation (classe 2) contre le moyen de paiement, le patron, un fournisseur ou les soldes d'ouverture ;
  amortissement : chaque mois écoulé, dotation (681) contre amortissements cumulés (28x) ;
  cession ou mise au rebut : sortie de l'immobilisation et de ses amortissements, valeur comptable en charge HAO (811), prix de cession en produit HAO (821).
Mode de calcul (volontairement simple et expliqué) : linéaire, au mois entier, à partir du mois de mise en service ; la dotation du mois est comptabilisée en fin de mois ;
le dernier mois ajuste les arrondis pour que le total amorti soit exact. Durées proposées : indicatives, à faire valider (durées d'usage ou taux fiscaux en vigueur).
Amortissement dégressif (au choix, à partir de 3 ans) : chaque année d'utilisation (de 12 mois à partir du mois de mise en service), la dotation est le plus grand de
  - la valeur restante à amortir × (coefficient ÷ durée en années)   [dégressif],
  - la valeur restante ÷ le nombre d'années restantes                [linéaire sur le reste : la bascule se fait donc toute seule, la dernière année solde le bien],
répartie par mois entiers. Coefficients proposés (usage français, INDICATIFS : le régime fiscal de votre pays peut différer, à faire valider) : 1,25 pour 3 à 4 ans, 1,75 pour 5 à 6 ans, 2,25 au-delà.
Un bien dégressif est repéré par coeff > 0 (coefficient × 100) ; method reste « linear » (famille des biens amortissables).
"""
from __future__ import annotations

import calendar
import sqlite3
import uuid
from datetime import date, timedelta
from functools import lru_cache

from . import audit, period
from .ops import UUID_RE, OpError, _deny, next_seq
from .methods import valid_method
from .permissions import METHODS, can
from .sales_ops import _conflict, _date, _int, _text, next_counter, today

CATEGORIES = [
    {"key": "Logiciels et licences", "asset": "213", "dep": "281", "years": 3}, {"key": "Terrains", "asset": "221", "dep": None, "years": 0},
    {"key": "Bâtiments", "asset": "231", "dep": "283", "years": 20}, {"key": "Matériel et outillage", "asset": "241", "dep": "284", "years": 5},
    {"key": "Matériel informatique et de bureau", "asset": "244", "dep": "284", "years": 3}, {"key": "Mobilier de bureau", "asset": "244", "dep": "284", "years": 8},
    {"key": "Matériel de transport", "asset": "245", "dep": "284", "years": 5}, {"key": "Autres matériels", "asset": "248", "dep": "284", "years": 5},
]
CAT = {c["key"]: c for c in CATEGORIES}
PAY_MODES = list(METHODS) + ["owner", "credit"]


# ---------------------------------------------------------------- calcul du plan d'amortissement
def months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + b.month - a.month


def _add_months(d: date, k: int) -> tuple[int, int]:
    n = d.year * 12 + d.month - 1 + k
    return n // 12, n % 12 + 1


def default_coeff(years: int) -> int:
    """Coefficient dégressif indicatif × 100 selon la durée (usage français)."""
    return 125 if years <= 4 else 175 if years <= 6 else 225


def coeff_of(row) -> int:
    try: return int(row["coeff"] or 0)
    except (KeyError, IndexError): return 0


@lru_cache(maxsize=512)
def _curve(base: int, life: int, coeff: int) -> tuple[int, ...]:
    """Cumul amorti après k mois (k de 0 à life) en dégressif, sur toute la vie du bien. Chaque année : max(dégressif, linéaire sur le reste)."""
    years = life // 12; rate = coeff / 100 / years
    cum, done = [0], 0
    for j in range(1, years + 1):
        left, remaining = years - j + 1, base - done
        charge = remaining if left == 1 else min(remaining, round(max(remaining * rate, remaining / left)))
        cum += [done + round(charge * m / 12) for m in range(1, 13)]
        done += charge
    return tuple(cum)


def _cum_fn(row):
    """k -> montant amorti après k mois à partir de la reprise (linéaire ou dégressif)."""
    base, n = params(row); co = coeff_of(row)
    if co:
        curve = _curve(row["cost"] - row["residual"], row["life_months"], co); ma = row["months_already"]
        return lambda k: curve[ma + k] - curve[ma]
    return lambda k: round(base * k / n)


def params(row) -> tuple[int, int]:
    """(base à amortir à partir de la reprise, nombre de mois restants)."""
    return row["cost"] - row["residual"] - row["opening_accum"], row["life_months"] - row["months_already"]


def first_month(row) -> date:
    return date.fromisoformat(row["start_date"] if row["origin"] == "purchase" else row["acquisition_date"]).replace(day=1)


def schedule(row, upto: date) -> list[tuple[str, int]]:
    """Dotations (date, montant) jusqu'à la date upto incluse. Un mois n'est dotée qu'une fois terminé (ou à la date de cession pour le mois de sortie)."""
    if row["method"] != "linear": return []
    base, n = params(row)
    if base <= 0 or n <= 0: return []
    start = first_month(row); disp = date.fromisoformat(row["disposal_date"]) if row["disposal_date"] else None
    out, prev, cumf = [], 0, _cum_fn(row)
    for k in range(1, n + 1):
        y, m = _add_months(start, k - 1); first = date(y, m, 1)
        if disp and first > disp: break
        d = date(y, m, calendar.monthrange(y, m)[1])
        last_for_disposal = bool(disp) and (y, m) == (disp.year, disp.month)
        if last_for_disposal: d = disp
        if d > upto: break
        cum = cumf(k); amt = cum - prev; prev = cum
        if amt: out.append((d.isoformat(), amt))
        if last_for_disposal: break
    return out


def accumulated_at(row, d: date) -> int:
    return row["opening_accum"] + sum(a for dt, a in schedule(row, d))


def view(row, upto: date | None = None) -> dict:
    upto = upto or today()
    d = {k: row[k] for k in ("id", "number", "name", "category", "origin", "acquisition_date", "start_date", "cost", "residual", "life_months", "months_already", "opening_accum", "method", "pay_mode", "asset_account", "dep_account", "status",
                              "disposal_date", "disposal_price", "disposal_kind", "disposal_method", "cancel_reason", "note", "version", "seq", "created_at")}
    acc = accumulated_at(row, upto if row["status"] != "disposed" else date.fromisoformat(row["disposal_date"]))
    d["accumulated"] = acc; d["net_value"] = row["cost"] - acc
    base, n = params(row)
    co = coeff_of(row); d["dec_coeff"] = co / 100 if co else 0; d["scheme"] = "none" if row["method"] != "linear" else "degressive" if co else "linear"
    if row["method"] == "linear" and n > 0 and base > 0:
        if co and row["status"] == "active":                      # dégressif : la dotation change chaque année ; on montre celle du prochain mois
            k = len(schedule(row, upto)) + 1; f = _cum_fn(row); d["monthly"] = f(k) - f(k - 1) if k <= n else 0
        else: d["monthly"] = 0 if row["status"] == "disposed" and co else round(base / n)
    else: d["monthly"] = 0
    if row["method"] == "linear" and n > 0 and base > 0:
        y, m = _add_months(first_month(row), n - 1); d["fully_depreciated_on"] = date(y, m, calendar.monthrange(y, m)[1]).isoformat()
    else: d["fully_depreciated_on"] = None
    year = upto.year
    d["year_charge"] = sum(a for dt, a in schedule(row, upto) if dt.startswith(str(year)))
    if d["status"] == "disposed": d["gain_loss"] = row["disposal_price"] - (row["cost"] - acc)
    return d


def register(c: sqlite3.Connection, a: str | None, b: str) -> dict:
    rows = []
    up = date.fromisoformat(b)
    for r in c.execute("SELECT * FROM fixed_assets WHERE status<>'cancelled' ORDER BY acquisition_date, number"):
        if r["acquisition_date"] > b: continue
        sch = schedule(r, up); period_charge = sum(x for dt, x in sch if (a is None or dt >= a))
        disposed_before = r["status"] == "disposed" and r["disposal_date"] <= b
        acc = accumulated_at(r, date.fromisoformat(r["disposal_date"]) if disposed_before else up)
        rows.append({"id": r["id"], "number": r["number"], "name": r["name"], "category": r["category"], "acquisition_date": r["acquisition_date"], "cost": r["cost"], "status": "disposed" if disposed_before else "active",
                     "accumulated": acc, "net_value": 0 if disposed_before else r["cost"] - acc, "period_charge": period_charge})
    live = [x for x in rows if x["status"] == "active"]
    return {"from": a, "to": b, "rows": rows, "totals": {"cost": sum(x["cost"] for x in live), "accumulated": sum(x["accumulated"] for x in live), "net_value": sum(x["net_value"] for x in live), "period_charge": sum(x["period_charge"] for x in rows)}}


def plan(c: sqlite3.Connection, asset_id: str) -> dict:
    r = c.execute("SELECT * FROM fixed_assets WHERE id=?", (asset_id,)).fetchone()
    if r is None: raise OpError("Immobilisation introuvable.")
    full = dict(r)
    if r["disposal_date"] is None:                       # plan prévisionnel jusqu'à l'amortissement complet
        base, n = params(r); y, m = _add_months(first_month(r), max(n - 1, 0)); upto = date(y, m, calendar.monthrange(y, m)[1])
    else: upto = date.fromisoformat(r["disposal_date"])
    rows, cum = [], r["opening_accum"]
    by_year: dict[str, int] = {}
    for dt, a in schedule(full, upto):
        by_year[dt[:4]] = by_year.get(dt[:4], 0) + a
    for yy in sorted(by_year):
        cum += by_year[yy]; rows.append({"year": int(yy), "charge": by_year[yy], "accumulated": cum, "net_value": r["cost"] - cum})
    return {"asset": view(r), "years": rows}


# ---------------------------------------------------------------- écritures déduites
def entries(c: sqlite3.Connection, mp: dict, pay_account) -> list[dict]:
    """Écritures d'acquisition, d'amortissement et de cession. pay_account(méthode) -> (compte, journal)."""
    out: list[dict] = []

    def add(key, d, journal, ref, label, lines):
        lines = [(a, int(dr), int(cr)) for a, dr, cr in lines if dr or cr]
        if lines: out.append({"key": key, "date": d, "journal": journal, "ref": ref, "label": label, "lines": lines, "auto": True})
    for r in c.execute("SELECT * FROM fixed_assets WHERE status<>'cancelled' ORDER BY acquisition_date, number"):
        num, cost, aa, da = r["number"], r["cost"], r["asset_account"], r["dep_account"]
        if r["origin"] == "opening":
            add(f"asset:{r['id']}", r["acquisition_date"], "OD", num, f"Reprise de l'immobilisation {num} : {r['name']}", [(aa, cost, 0), (mp["opening_equity"], 0, cost), (mp["opening_equity"], r["opening_accum"], 0), (da, 0, r["opening_accum"])] if da else [(aa, cost, 0), (mp["opening_equity"], 0, cost)])
        else:
            if r["pay_mode"] == "owner": cp, j = mp["associates"], "OD"
            elif r["pay_mode"] == "credit": cp, j = mp["suppliers"], "AC"
            else: cp, j = pay_account(r["pay_mode"])
            add(f"asset:{r['id']}", r["acquisition_date"], j, num, f"Acquisition {num} : {r['name']}", [(aa, cost, 0), (cp, 0, cost)])
        upto = today() if r["status"] == "active" else date.fromisoformat(r["disposal_date"])
        for dt, amt in schedule(r, upto):
            add(f"dep:{r['id']}:{dt}", dt, "OD", f"AMORT-{dt[:7]}", f"Dotation aux amortissements {num} : {r['name']}", [(mp["depreciation_expense"], amt, 0), (da, 0, amt)])
        if r["status"] == "disposed":
            acc = accumulated_at(r, date.fromisoformat(r["disposal_date"])); vnc = cost - acc; price = r["disposal_price"]
            if price > 0:
                a, j = ("471", "OD") if r["disposal_method"] in ("", "credit") else pay_account(r["disposal_method"])
                add(f"assetsale:{r['id']}", r["disposal_date"], j, num, f"Cession de {num} : {r['name']}", [(a, price, 0), (mp["disposal_product"], 0, price)])
            add(f"assetout:{r['id']}", r["disposal_date"], "OD", num, f"Sortie de l'actif {num} : {r['name']} (valeur comptable)", [(da, acc, 0) if da else (aa, 0, 0), (mp["disposal_cost"], vnc, 0), (aa, 0, cost)])
    return out


# ---------------------------------------------------------------- opérations
def _asset_create(c, user, actor, data, role, now):
    if not can(role, "accounting.write"): return _deny(c, actor, "asset.create", None, "droit manquant")
    aid = str(data.get("id") or uuid.uuid4())
    if not UUID_RE.match(aid): raise OpError("Identifiant d'immobilisation invalide.")
    if c.execute("SELECT 1 FROM fixed_assets WHERE id=?", (aid,)).fetchone(): raise OpError("Cette immobilisation existe déjà.")
    name = _text(data.get("name"), "La désignation", 120, True)
    cat = CAT.get(data.get("category"))
    if cat is None: raise OpError("Catégorie d'immobilisation inconnue.")
    origin = data.get("origin")
    if origin not in ("purchase", "opening"): raise OpError("Origine : achat ou reprise d'un bien déjà possédé.")
    acq = _date(data.get("acquisition_date") or today().isoformat(), "Date d'acquisition ou de reprise")
    if date.fromisoformat(acq) > today() + timedelta(days=1): raise OpError("La date ne peut pas être dans le futur.")
    period.ensure_open(c, acq)
    start = _date(data.get("start_date") or acq, "Date de mise en service")
    if origin == "opening" and start > acq: raise OpError("Pour un bien déjà possédé, la mise en service d'origine précède la date de reprise.")
    if origin == "purchase" and start < acq: raise OpError("La mise en service ne peut pas précéder l'achat.")
    cost = _int(data.get("cost"), "Le coût d'acquisition", 1, 10 ** 13)
    residual = _int(data.get("residual", 0) or 0, "La valeur résiduelle", 0, cost)
    method = "none" if cat["dep"] is None else "linear"
    years = _int(data.get("life_years") or cat["years"] or 1, "La durée en années", 1, 50)
    life = years * 12 if method == "linear" else 0
    scheme = data.get("scheme") or "linear"
    if scheme not in ("linear", "degressive"): raise OpError("Méthode d'amortissement : linéaire ou dégressif.")
    coeff = 0
    if scheme == "degressive":
        if method != "linear": raise OpError("Ce type de bien (terrain) ne s'amortit pas.")
        if years < 3: raise OpError("L'amortissement dégressif demande une durée d'au moins 3 ans.")
        try: coeff = round(float(str(data.get("coeff") if data.get("coeff") not in (None, "") else default_coeff(years) / 100).replace(",", ".")) * 100)
        except ValueError: raise OpError("Le coefficient dégressif doit être un nombre (par ex. 1,75).")
        if not 100 <= coeff <= 300: raise OpError("Le coefficient dégressif doit être compris entre 1 et 3.")
    pay = data.get("pay_mode") or ("opening" if origin == "opening" else None)
    if origin == "purchase" and not (valid_method(c, pay) or pay in ("owner", "credit")): raise OpError("Mode de règlement : un moyen de paiement, « owner » (payé par le patron) ou « credit » (à payer).")
    if origin == "opening": pay = "opening"
    for code in (cat["asset"], cat["dep"]):
        if code and not c.execute("SELECT 1 FROM accounts WHERE code=?", (code,)).fetchone(): raise OpError(f"Le compte {code} n'existe pas dans le plan comptable.")
    months_already = accum = 0
    if origin == "opening" and method == "linear":
        months_already = max(0, min(life, months_between(date.fromisoformat(start).replace(day=1), date.fromisoformat(acq).replace(day=1))))
        accum = (_curve(cost - residual, life, coeff)[months_already] if coeff else round((cost - residual) * months_already / life)) if life else 0
    number = f"IM-{next_counter(c, 'asset_no'):04d}"
    c.execute("""INSERT INTO fixed_assets(id,number,name,category,origin,acquisition_date,start_date,cost,residual,life_months,months_already,opening_accum,coeff,method,pay_mode,asset_account,dep_account,status,note,version,seq,created_at,updated_at,created_by,updated_by)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'active',?,1,?,?,?,?,?)""",
              (aid, number, name, cat["key"], origin, acq, start, cost, residual, life, months_already, accum, coeff, method, pay, cat["asset"], cat["dep"], _text(data.get("note"), "La note", 200), next_seq(c), now, now, user["id"], user["id"]))
    r = c.execute("SELECT * FROM fixed_assets WHERE id=?", (aid,)).fetchone()
    audit.record(c, actor, "asset.create", "asset", aid, None, {"numéro": number, "désignation": name, "catégorie": cat["key"], "coût": cost, "origine": origin, "durée (mois)": life, "règlement": pay, "amortissements antérieurs": accum, "méthode": "dégressif" if coeff else "linéaire", "coefficient": coeff / 100 if coeff else None})
    return {"status": "ok", "asset": view(r)}


def _asset_dispose(c, user, actor, data, role, now):
    if not can(role, "accounting.write"): return _deny(c, actor, "asset.dispose", str(data.get("id") or ""), "droit manquant")
    r = c.execute("SELECT * FROM fixed_assets WHERE id=? AND status='active'", (str(data.get("id") or ""),)).fetchone()
    if r is None: return {"status": "invalid", "message": "Immobilisation introuvable ou déjà sortie."}
    d = _date(data.get("date") or today().isoformat(), "Date de la cession")
    if date.fromisoformat(d) > today(): raise OpError("La date de cession ne peut pas être dans le futur.")
    if d < r["acquisition_date"]: raise OpError("La cession ne peut pas précéder l'acquisition.")
    period.ensure_open(c, d)
    kind = data.get("kind")
    if kind not in ("sold", "scrapped"): raise OpError("Cession ou mise au rebut ?")
    price = _int(data.get("price", 0) or 0, "Le prix de cession", 0, 10 ** 13) if kind == "sold" else 0
    method = _text(data.get("method"), "Le mode d'encaissement", 30) if price > 0 else ""
    if method and not valid_method(c, method) and method != "credit": raise OpError("Mode d'encaissement inconnu.")
    if int(data.get("base_version") or 0) != r["version"]: return _conflict(c, actor, "asset", r["id"], view(r), "sortie : immobilisation modifiée entre-temps")
    c.execute("UPDATE fixed_assets SET status='disposed', disposal_date=?, disposal_price=?, disposal_kind=?, disposal_method=?, version=version+1, seq=?, updated_at=?, updated_by=? WHERE id=?", (d, price, kind, method, next_seq(c), now, user["id"], r["id"]))
    r2 = c.execute("SELECT * FROM fixed_assets WHERE id=?", (r["id"],)).fetchone(); v = view(r2)
    audit.record(c, actor, "asset.dispose", "asset", r["id"], {"statut": "en service", "valeur nette": r["cost"] - accumulated_at(r, date.fromisoformat(d))}, {"statut": "cédée" if kind == "sold" else "mise au rebut", "prix": price, "plus ou moins-value": v["gain_loss"], "date": d}, note=r["number"])
    return {"status": "ok", "asset": v}


def _asset_cancel(c, user, actor, data, role, now):
    if not can(role, "accounting.write"): return _deny(c, actor, "asset.cancel", str(data.get("id") or ""), "droit manquant")
    r = c.execute("SELECT * FROM fixed_assets WHERE id=? AND status='active'", (str(data.get("id") or ""),)).fetchone()
    if r is None: return {"status": "invalid", "message": "Immobilisation introuvable, déjà sortie ou annulée."}
    period.ensure_open(c, r["acquisition_date"])
    reason = _text(data.get("reason"), "Le motif", 200, True)
    if len(reason) < 3: raise OpError("Le motif d'annulation est obligatoire.")
    if int(data.get("base_version") or 0) != r["version"]: return _conflict(c, actor, "asset", r["id"], view(r), "annulation : immobilisation modifiée entre-temps")
    c.execute("UPDATE fixed_assets SET status='cancelled', cancel_reason=?, version=version+1, seq=?, updated_at=?, updated_by=? WHERE id=?", (reason, next_seq(c), now, user["id"], r["id"]))
    audit.record(c, actor, "asset.cancel", "asset", r["id"], {"statut": "en service", "numéro": r["number"], "coût": r["cost"]}, {"statut": "annulée", "motif": reason})
    return {"status": "ok"}


HANDLERS = {"asset.create": _asset_create, "asset.dispose": _asset_dispose, "asset.cancel": _asset_cancel}
