"""Copie les données d'une base SQLite existante vers PostgreSQL.

  set GESTION_DB_URL=postgresql://utilisateur:motdepasse@hote:5432/gestion
  python -m gestion.vers_postgres [--force]

À lancer avec le serveur Gestion libre ARRÊTÉ. La base SQLite d'origine n'est ni modifiée ni supprimée. Si la base PostgreSQL contient déjà des données (au-delà des réglages
d'origine), l'outil refuse, sauf --force (qui remplace alors les lignes de même clé, sans jamais supprimer). Après la copie, l'outil vérifie le nombre de lignes de chaque table,
remet les compteurs d'identifiants et revérifie la chaîne d'empreintes du journal d'audit : un seul écart et il le dit.
"""
from __future__ import annotations

import sys

from . import audit, chiffrement, config, db, dbpg


def _fk_order(src, tables: list[str]) -> list[str]:
    """Tables classées de façon que les tables référencées passent avant celles qui les référencent."""
    deps = {t: {r[2] for r in src.execute(f'PRAGMA foreign_key_list("{t}")') if r[2] != t and r[2] in tables} for t in tables}
    order: list[str] = []
    while deps:
        ready = sorted(t for t, d in deps.items() if d <= set(order))
        if not ready: ready = sorted(deps)[:1]                         # cycle improbable : on avance quand même
        for t in ready: order.append(t); deps.pop(t)
    return order


def migrer(force: bool = False) -> dict:
    if not config.db_url(): raise SystemExit("Définissez d'abord GESTION_DB_URL (postgresql://…).")
    f = config.db_path()
    if not f.exists(): raise SystemExit(f"Aucune base SQLite à copier : {f} n'existe pas.")
    mod = chiffrement.sqlite()
    src = mod.connect(f, isolation_level=None)
    if mod is not chiffrement._plain: chiffrement.appliquer_cle(src)
    try:
        tables = [r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        db.init_db()                                                   # crée le schéma PostgreSQL (et les réglages d'origine)
        dst = db.connect()
        try:
            n_users = dst.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            if n_users and not force: raise SystemExit("La base PostgreSQL contient déjà des utilisateurs : rien n'a été copié (utilisez --force pour remplacer les lignes de même clé).")
            report: dict[str, int] = {}
            raw = dst._raw
            raw.execute("BEGIN"); raw.execute("SELECT pg_advisory_xact_lock(%s)", (dbpg.WRITE_LOCK_KEY,))
            try:
                for t in _fk_order(src, tables):
                    cols = [r[1] for r in src.execute(f'PRAGMA table_info("{t}")')]
                    pk = [r[0] for r in raw.execute("""SELECT a.attname FROM pg_index i JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=ANY(i.indkey)
                                                      WHERE i.indrelid=%s::regclass AND i.indisprimary""", (t,))]
                    sets = ", ".join(f'"{c}"=excluded."{c}"' for c in cols if c not in pk)
                    conflict = f' ON CONFLICT ({", ".join(chr(34) + c + chr(34) for c in pk)}) DO ' + (f"UPDATE SET {sets}" if sets else "NOTHING") if pk else ""
                    sql = f'INSERT INTO "{t}" ({", ".join(chr(34) + c + chr(34) for c in cols)}) VALUES ({", ".join(["%s"] * len(cols))}){conflict}'
                    rows = [tuple(bytes(v) if isinstance(v, (bytes, memoryview)) else v for v in r) for r in src.execute(f'SELECT {", ".join(chr(34) + c + chr(34) for c in cols)} FROM "{t}"')]
                    if rows: raw.cursor().executemany(sql, rows)
                    report[t] = len(rows)
                for t in sorted(dbpg.IDENTITY_TABLES) + ["sessions"]:                  # les compteurs reprennent après le plus grand identifiant copié
                    col = "rowid" if t == "sessions" else "id"
                    seq = raw.execute("SELECT pg_get_serial_sequence(%s, %s)", (t, col)).fetchone()[0]
                    if seq: raw.execute(f'SELECT setval(%s, COALESCE((SELECT MAX("{col}") FROM "{t}"), 0) + 1, false)', (seq,))
                for t, n in report.items():
                    got = raw.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                    if got < n: raise SystemExit(f"Écart sur la table {t} : {n} lignes copiées, {got} présentes.")
                raw.execute("COMMIT")
            except BaseException:
                raw.execute("ROLLBACK"); raise
            with db.read() as c:
                chain = audit.verify(c)
            if not chain.get("ok"): raise SystemExit(f"La chaîne du journal d'audit ne se vérifie pas après la copie : {chain}")
            return {"tables": report, "lignes": sum(report.values()), "journal": chain}
        finally:
            dst.close()
    finally:
        src.close()


def main(argv: list[str]) -> int:
    r = migrer("--force" in argv)
    print(f"Copie terminée : {len(r['tables'])} tables, {r['lignes']} lignes, journal d'audit vérifié ({r['journal'].get('entries')} entrées).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
