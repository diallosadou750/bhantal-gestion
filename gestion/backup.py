"""Sauvegarde et export des données.

- Sauvegarde cohérente de la base (API de sauvegarde de SQLite : pas de copie de fichier en cours d'écriture), téléchargeable par le propriétaire.
- Sauvegarde automatique quotidienne dans le dossier « sauvegardes » à côté de la base (les 30 dernières sont gardées). Une copie sur le MÊME disque ne protège pas d'une panne
  du disque : copiez régulièrement ce dossier ailleurs (clé USB, autre ordinateur, nuage chiffré).
- Export complet en fichiers CSV (un par table utile) dans une archive ZIP, pour sortir ses données à tout moment. Les mots de passe et sessions n'y figurent jamais.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import chiffrement, config, db

KEEP = 30
EXPORT_TABLES = ["clients", "products", "sales", "sale_lines", "payments", "credit_notes", "credit_note_lines", "suppliers", "purchases", "purchase_lines", "supplier_payments", "stock_adjustments", "expenses", "cash_movements", "cash_closings",
                 "pay_accounts", "bank_accounts", "bank_lines", "bank_matches", "bank_reconciliations", "accounts", "journal_entries", "journal_lines", "fiscal_years", "fiscal_lines", "fixed_assets", "provisions", "provision_releases", "deferrals", "quotes", "quote_lines", "purchase_orders", "purchase_order_lines", "employees", "payslips", "salary_advances", "employee_leaves", "delivery_notes", "delivery_lines", "transfers", "recurring_expenses", "supplier_notes", "supplier_note_lines", "depots", "stock_transfers", "expense_categories", "lots", "sale_line_lots", "audit_log"]
_NAME = re.compile(r"^gestion_\d{8}_\d{6}\.(sqlite3|dump)$")


def backup_dir() -> Path:
    d = config.data_dir() / "sauvegardes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _pg_snapshot(dest: Path) -> Path:
    """PostgreSQL : sauvegarde avec pg_dump (format « custom », restaurable avec pg_restore). Le mot de passe n'est jamais écrit dans un journal."""
    exe = os.environ.get("GESTION_PG_DUMP") or shutil.which("pg_dump")
    if not exe: raise RuntimeError("pg_dump est introuvable : installez les outils clients PostgreSQL ou indiquez son chemin dans GESTION_PG_DUMP.")
    cmd = [exe, "--format=custom", "--no-owner", "--file", str(dest)] + (["--schema", config.db_schema()] if config.db_schema() else []) + [config.db_url()]
    r = subprocess.run(cmd, capture_output=True, timeout=600)
    if r.returncode != 0: raise RuntimeError("pg_dump a échoué : " + r.stderr.decode("utf-8", "replace")[-300:].replace(config.db_url(), "***"))
    return dest


def snapshot(dest: Path) -> Path:
    """Copie cohérente de la base vers dest."""
    if config.db_url(): return _pg_snapshot(dest)
    src = chiffrement.ouvrir(config.db_path(), timeout=30)
    out = chiffrement.ouvrir(dest)                         # la sauvegarde est chiffrée avec la même clé
    try:
        src.backup(out)
    finally:
        out.close(); src.close()
    return dest


EXTRA_DIR_KEY = "backup_extra_dir"
EXTRA_STATUS_KEY = "backup_extra_status"


def _meta_get(key: str):
    with db.read() as c:
        r = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r["value"] if r else None


def _meta_set(key: str, value: str | None) -> None:
    with db.transaction() as c:
        if value is None: c.execute("DELETE FROM meta WHERE key=?", (key,))
        else: c.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def extra_dir() -> Path | None:
    v = _meta_get(EXTRA_DIR_KEY)
    return Path(v) if v else None


def extra_status() -> dict | None:
    v = _meta_get(EXTRA_STATUS_KEY)
    return json.loads(v) if v else None


def check_extra_dir(raw: str) -> Path:
    """Dossier de copie hors de l'ordinateur : chemin complet d'un dossier existant, hors du dossier des données, où l'on peut écrire (essai d'écriture, de relecture et d'effacement)."""
    raw = (raw or "").strip().strip('"')
    if not raw: raise ValueError("Indiquez le dossier de copie.")
    p = Path(raw)
    if not p.is_absolute(): raise ValueError("Indiquez le chemin complet du dossier (par exemple E:\\Sauvegardes).")
    if not p.is_dir(): raise ValueError("Ce dossier n'existe pas ou n'est pas joignable (clé USB branchée ? disque réseau connecté ?).")
    data = config.data_dir().resolve(); cible = p.resolve()
    if cible == data or data in cible.parents:
        raise ValueError("Ce dossier est sur les données elles-mêmes : choisissez un autre disque, une clé USB ou un dossier synchronisé.")
    probe = p / (".essai_gestion_" + os.urandom(4).hex())
    try:
        probe.write_bytes(b"ok")
        if probe.read_bytes() != b"ok": raise OSError("relecture différente")
        probe.unlink()
    except OSError as e:
        raise ValueError(f"Impossible d'écrire dans ce dossier ({e.strerror or e}).")
    return p


def _sha256(f: Path) -> str:
    h = hashlib.sha256()
    with open(f, "rb") as fh:
        for bloc in iter(lambda: fh.read(1 << 20), b""): h.update(bloc)
    return h.hexdigest()


def copy_out(source: Path) -> dict | None:
    """Copie la sauvegarde dans le dossier hors de l'ordinateur, vérifie qu'elle est identique (empreinte), garde les 30 dernières. Ne lève jamais d'erreur : le résultat est noté pour l'affichage."""
    d = extra_dir()
    if d is None: return None
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        d = check_extra_dir(str(d))
        tmp = d / (source.name + ".tmp"); final = d / source.name
        shutil.copyfile(source, tmp)
        if _sha256(tmp) != _sha256(source): tmp.unlink(); raise OSError("la copie n'est pas identique à l'original")
        os.replace(tmp, final)
        for old in sorted(p for p in d.iterdir() if _NAME.match(p.name))[:-KEEP]:
            try: old.unlink()
            except OSError: pass
        st = {"ts": now, "ok": True, "name": source.name, "message": "copie vérifiée"}
    except (OSError, ValueError) as e:
        st = {"ts": now, "ok": False, "name": source.name, "message": str(e)[:200]}
    try: _meta_set(EXTRA_STATUS_KEY, json.dumps(st, ensure_ascii=False))
    except Exception: pass
    return st


def run_backup() -> Path:
    name = "gestion_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + (".dump" if config.db_url() else ".sqlite3")
    path = snapshot(backup_dir() / name)
    old = sorted(p for p in backup_dir().iterdir() if _NAME.match(p.name))
    for p in old[:-KEEP]:
        try: p.unlink()
        except OSError: pass
    copy_out(path)                                         # copie vérifiée hors de l'ordinateur si le propriétaire l'a demandée
    return path


def list_backups() -> list[dict]:
    return [{"name": p.name, "size": p.stat().st_size} for p in sorted(backup_dir().iterdir(), reverse=True) if _NAME.match(p.name)]


def _cell(v) -> str:
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") and not re.fullmatch(r"-?\d+(\.\d+)?", s) else s


def export_zip(c: sqlite3.Connection) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for t in EXPORT_TABLES:
            cur = c.execute(f"SELECT * FROM {t}")
            cols = [d[0] for d in cur.description]
            s = io.StringIO(); w = csv.writer(s, delimiter=";"); w.writerow(cols)
            for r in cur: w.writerow([_cell(r[k]) for k in cols])
            z.writestr(f"{t}.csv", "﻿" + s.getvalue())
        z.writestr("LISEZMOI.txt", "Export complet de Gestion libre : un fichier CSV par table (séparateur point-virgule, UTF-8). Les mots de passe et sessions ne sont jamais exportés.\n")
    return buf.getvalue()


_started = False


def start_scheduler(interval_s: int = 24 * 3600) -> None:
    """Sauvegarde au démarrage (si la dernière date de plus de 20 h) puis une fois par jour. Jamais en mode démonstration."""
    global _started
    if _started or config.demo_mode() or os.environ.get("GESTION_NO_AUTOBACKUP"): return
    _started = True

    def loop():
        while True:
            try:
                last = max((p.stat().st_mtime for p in backup_dir().iterdir() if _NAME.match(p.name)), default=0)
                if time.time() - last > 20 * 3600 and (config.db_url() or config.db_path().exists()): run_backup()
            except Exception:                      # une sauvegarde ratée ne doit jamais arrêter l'application ; le propriétaire voit l'âge de la dernière dans « Mon compte »
                pass
            time.sleep(interval_s if interval_s < 3600 else 3600)
    threading.Thread(target=loop, daemon=True, name="sauvegarde-auto").start()
