"""PostgreSQL : adaptateur qui donne à psycopg l'allure du module sqlite3 utilisé par toute l'application.

Pourquoi un adaptateur : l'application parle SQL « à la SQLite » (points d'interrogation, lignes lisibles par nom ET par numéro, INSERT OR IGNORE, rowid, lastrowid…). Plutôt que de réécrire
les ~570 requêtes, on traduit à la volée les quelques différences de dialecte, de façon simple et vérifiable :
  - ?                      -> %s ;  LIKE -> ILIKE (SQLite ignore la casse) ;  noms comparés sans tenir compte de la casse (login, dépôts) -> lower(x)=lower(?) + index unique sur lower(x) ;
  - INSERT OR IGNORE       -> INSERT … ON CONFLICT DO NOTHING ;  lastrowid -> RETURNING id ;  PRAGMA -> sans objet (PRAGMA table_info lit information_schema) ;
  - BEGIN IMMEDIATE        -> BEGIN + verrou d'écriture global (pg_advisory_xact_lock) : comme SQLite, UN SEUL écrivain à la fois. Les compteurs (numéros de facture, séquence de
                              synchronisation, stock) lisent puis écrivent : sans cette sérialisation, deux ventes simultanées pourraient recevoir le même numéro ;
  - schéma                 : INTEGER -> BIGINT (les montants dépassent 2 milliards), AUTOINCREMENT -> identité, BLOB -> BYTEA, déclencheurs réécrits en PL/pgSQL.
  - nombres                : SUM() de PostgreSQL rend un NUMERIC : il est relu comme un entier ; les entiers sont envoyés « sans type » pour que le serveur choisisse (texte ou nombre),
                              comme le faisait la typographie souple de SQLite.
Ce que PostgreSQL apporte ici : un serveur à part (plusieurs ordinateurs, accès réseau), des outils de sauvegarde éprouvés (pg_dump), la réplication. Ce qu'il n'apporte PAS :
des écritures parallèles (le verrou ci-dessus les met en file, comme avant) ni le chiffrement de la base par l'application (utilisez le chiffrement du disque et TLS vers le serveur).
Configuration : GESTION_DB_URL=postgresql://utilisateur:motdepasse@hote:5432/gestion  (+ GESTION_DB_SCHEMA, facultatif, pour isoler plusieurs installations dans une base).
"""
from __future__ import annotations

import re
import threading
from functools import lru_cache

import psycopg
from psycopg import errors as pgerrors
from psycopg.adapt import Dumper, Loader
from psycopg.pq import Format

WRITE_LOCK_KEY = 7_770_001            # clé du verrou d'écriture global (arbitraire, propre à cette application)
Error = psycopg.Error
IntegrityError = psycopg.IntegrityError


# ---------------------------------------------------------------- types
class _Num(Loader):
    """NUMERIC (résultat de SUM, par exemple) -> int quand c'est un entier, sinon float : l'application ne manipule que des entiers et des flottants."""

    def load(self, data):
        s = bytes(data).decode("ascii")
        return int(s) if re.fullmatch(r"-?\d+", s) else float(s)


class _IntUnknown(Dumper):
    """Entiers envoyés sans type : le serveur les lit comme texte ou comme nombre selon la colonne (SQLite était aussi souple)."""
    format = Format.TEXT
    oid = 0

    def dump(self, obj):
        return str(int(obj)).encode("ascii")


psycopg.adapters.register_loader("numeric", _Num)


class Row(tuple):
    """Ligne lisible par numéro (r[0]), par nom (r["total"], sans tenir compte de la casse) et convertible en dict, comme sqlite3.Row."""
    def __new__(cls, values, names, idx):
        o = super().__new__(cls, values); o._names = names; o._idx = idx
        return o

    def __getitem__(self, k):
        if isinstance(k, str): return tuple.__getitem__(self, self._idx[k.lower()])
        return tuple.__getitem__(self, k)

    def keys(self):
        return list(self._names)


def _row_factory(cur):
    names = [d.name for d in cur.description] if cur.description else []
    idx: dict[str, int] = {}
    for i, n in enumerate(names): idx.setdefault(n.lower(), i)
    return lambda values: Row(values, names, idx)


# ---------------------------------------------------------------- traduction SQL
def _outside_quotes(sql: str, fn) -> str:
    """Applique fn aux morceaux hors guillemets simples (les textes entre '…' ne sont jamais touchés)."""
    out, buf, i, n = [], [], 0, len(sql)
    while i < n:
        if sql[i] == "'":
            out.append(fn("".join(buf))); buf = []
            j = i + 1
            while j < n:
                if sql[j] == "'" and j + 1 < n and sql[j + 1] == "'": j += 2; continue
                if sql[j] == "'": break
                j += 1
            out.append(sql[i:j + 1]); i = j + 1
        else:
            buf.append(sql[i]); i += 1
    out.append(fn("".join(buf)))
    return "".join(out)


IDENTITY_TABLES: set[str] = set()      # tables à clé « id » auto-incrémentée (remplie à la création du schéma) : on y ajoute RETURNING id pour lastrowid


@lru_cache(maxsize=4096)
def translate(sql: str, with_params: bool) -> tuple[str, str | None]:
    """(requête PostgreSQL, table de l'INSERT dont il faut relire l'identifiant ou None)."""
    s = sql.strip().rstrip(";")
    up = s.upper()
    if up.startswith("PRAGMA"):
        m = re.match(r"PRAGMA\s+table_info\(\s*['\"]?(\w+)['\"]?\s*\)", s, re.I)
        if m: return (f"SELECT column_name AS name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='{m.group(1).lower()}' ORDER BY ordinal_position", None)
        return ("SELECT 1 WHERE FALSE", None)
    if up in ("BEGIN IMMEDIATE", "BEGIN EXCLUSIVE", "BEGIN DEFERRED"): return ("BEGIN", "__lock__")
    insert_ignore = bool(re.match(r"INSERT\s+OR\s+IGNORE\s+INTO", s, re.I))
    if re.match(r"INSERT\s+OR\s+REPLACE", s, re.I): raise NotImplementedError("INSERT OR REPLACE n'est pas traduit : utiliser INSERT … ON CONFLICT … DO UPDATE")

    def fix(part: str) -> str:
        part = re.sub(r"\bLIKE\b", "ILIKE", part, flags=re.I)
        part = re.sub(r"\b((?:\w+\.)?\w+)\s*=\s*\?\s+COLLATE\s+NOCASE\b", r"lower(\1)=lower(?)", part, flags=re.I)         # name=? COLLATE NOCASE
        part = re.sub(r"\b((?:\w+\.)?username)\s*=\s*\?", r"lower(\1)=lower(?)", part, flags=re.I)                              # identifiants : la casse ne compte pas
        part = re.sub(r"\s+COLLATE\s+NOCASE\b", "", part, flags=re.I)
        if with_params: part = part.replace("%", "%%").replace("?", "%s")
        return part
    s = _outside_quotes(s, fix)
    if insert_ignore:
        s = re.sub(r"^INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", s, flags=re.I) + " ON CONFLICT DO NOTHING"
    table = None
    m = re.match(r"INSERT\s+INTO\s+(\w+)", s, re.I)
    if m and m.group(1).lower() in IDENTITY_TABLES and "RETURNING" not in s.upper() and "ON CONFLICT" not in s.upper():
        s += " RETURNING id"; table = m.group(1).lower()
    if re.match(r"ALTER\s+TABLE", s, re.I): s = re.sub(r"\bINTEGER\b", "BIGINT", s, flags=re.I)
    return (s, table)


# ---------------------------------------------------------------- connexion
class Cursor:
    def __init__(self, cur, lastrowid=None):
        self._c = cur; self.lastrowid = lastrowid

    @property
    def rowcount(self): return self._c.rowcount

    @property
    def description(self): return self._c.description

    def fetchone(self): return self._c.fetchone() if self._c.description else None

    def fetchall(self): return self._c.fetchall() if self._c.description else []

    def __iter__(self): return iter(self.fetchall())


class Connection:
    """Une connexion PostgreSQL qui se comporte comme une connexion sqlite3 (execute, executemany, row_factory ignoré, close rend la connexion au « réservoir »)."""

    def __init__(self, raw, key):
        self._raw = raw; self._key = key; self.row_factory = None; self._closed = False

    def execute(self, sql: str, params=()):
        q, table = translate(sql, bool(params))
        cur = self._raw.cursor(row_factory=_row_factory)
        args = tuple(int(p) if isinstance(p, bool) else p for p in params) if params else None
        if table == "__lock__":
            cur.execute("BEGIN"); cur.execute("SELECT pg_advisory_xact_lock(%s)", (WRITE_LOCK_KEY,)); return Cursor(cur)
        cur.execute(q, args)
        if table:
            row = cur.fetchone()
            return Cursor(cur, lastrowid=row[0] if row else None)
        return Cursor(cur)

    def executemany(self, sql: str, seq):
        q, _ = translate(sql, True)
        cur = self._raw.cursor(); cur.executemany(q, [tuple(int(p) if isinstance(p, bool) else p for p in row) for row in seq]); return Cursor(cur)

    def commit(self): pass                       # la connexion est en mode automatique : chaque transaction est ouverte et fermée par l'application (BEGIN … COMMIT)

    def rollback(self): pass

    def close(self):
        if self._closed: return
        self._closed = True
        _release(self._key, self._raw)

    def __enter__(self): return self

    def __exit__(self, *exc): self.close(); return False


_pool: dict[tuple, list] = {}
_pool_lock = threading.Lock()
MAX_IDLE = 8


def _release(key, raw) -> None:
    try:
        if raw.closed: return
        if raw.info.transaction_status != psycopg.pq.TransactionStatus.IDLE: raw.execute("ROLLBACK")    # une transaction oubliée ne doit jamais fuiter vers l'appelant suivant
        with _pool_lock:
            idle = _pool.setdefault(key, [])
            if len(idle) < MAX_IDLE: idle.append(raw); return
        raw.close()
    except psycopg.Error:
        try: raw.close()
        except psycopg.Error: pass


def connect(url: str, schema: str | None = None) -> Connection:
    key = (url, schema or "")
    raw = None
    with _pool_lock:
        idle = _pool.get(key, [])
        while idle:
            cand = idle.pop()
            if not cand.closed: raw = cand; break
    if raw is not None:
        try: raw.execute("SELECT 1")
        except psycopg.Error: raw = None
    if raw is None:
        raw = psycopg.connect(url, autocommit=True, prepare_threshold=None)
        raw.adapters.register_dumper(int, _IntUnknown)
        if schema: raw.execute(f'SET search_path TO "{schema}", public')
    return Connection(raw, key)


def reset_pool() -> None:
    with _pool_lock:
        for lst in _pool.values():
            for r in lst:
                try: r.close()
                except psycopg.Error: pass
        _pool.clear()


# ---------------------------------------------------------------- schéma
_TRIGGER = re.compile(r"CREATE TRIGGER[^;]*?BEGIN.*?END;", re.S | re.I)

AUDIT_TRIGGERS = """
CREATE OR REPLACE FUNCTION audit_log_immutable() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'audit_log est en ajout seul : modification ou suppression interdite'; END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS audit_no_change ON audit_log;
CREATE TRIGGER audit_no_change BEFORE UPDATE OR DELETE ON audit_log FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();
DROP TRIGGER IF EXISTS audit_no_truncate ON audit_log;
CREATE TRIGGER audit_no_truncate BEFORE TRUNCATE ON audit_log FOR EACH STATEMENT EXECUTE FUNCTION audit_log_immutable();
"""


def translate_schema(schema_sql: str) -> list[str]:
    """Schéma SQLite -> liste d'instructions PostgreSQL (les déclencheurs sont ajoutés à part)."""
    s = _TRIGGER.sub("", schema_sql)
    s = re.sub(r"INTEGER PRIMARY KEY AUTOINCREMENT", "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY", s)
    s = re.sub(r"\bINTEGER\b", "BIGINT", s)
    s = re.sub(r"\bBLOB\b", "BYTEA", s)
    s = s.replace("lower(hex(randomblob(16)))", "md5(random()::text || clock_timestamp()::text)")          # identifiant aléatoire de la base
    s = re.sub(r"\bREAL\b", "DOUBLE PRECISION", s)
    s = re.sub(r"\s+COLLATE NOCASE", "", s)
    s = s.replace("CREATE TABLE IF NOT EXISTS sessions(", "CREATE TABLE IF NOT EXISTS sessions(\n    rowid BIGINT GENERATED ALWAYS AS IDENTITY,")        # SQLite numérote les lignes tout seul (rowid) ; la liste des connexions s'en sert
    stmts = [x.strip() for x in s.split(";") if x.strip()]
    stmts += ["CREATE UNIQUE INDEX IF NOT EXISTS users_username_ci ON users(lower(username))", "CREATE UNIQUE INDEX IF NOT EXISTS depots_name_ci ON depots(lower(name))",
              "CREATE UNIQUE INDEX IF NOT EXISTS pay_accounts_label_ci ON pay_accounts(lower(label))"]        # unicité sans tenir compte de la casse (COLLATE NOCASE de SQLite)
    IDENTITY_TABLES.update(m.lower() for m in re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)\([^;]*?BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY", ";".join(stmts)))
    return stmts


def create_schema(conn: Connection, schema_sql: str) -> None:
    """Crée le schéma une seule fois, même si deux processus démarrent ensemble. Les tables qui en référencent d'autres sont recréées jusqu'à ce que tout passe (SQLite acceptait les références avancées)."""
    stmts = translate_schema(schema_sql)
    conn.execute("SELECT pg_advisory_lock(%s)" % (WRITE_LOCK_KEY + 1))
    try:
        pending = stmts
        for _ in range(12):
            failed = []
            for st in pending:
                q, _ = translate(st, False)
                try: conn._raw.execute(q)
                except (pgerrors.UndefinedTable, pgerrors.UndefinedColumn): failed.append(st)
            if not failed: break
            if len(failed) == len(pending): raise RuntimeError("Schéma PostgreSQL : instructions impossibles :\n" + "\n".join(f[:120] for f in failed[:5]))
            pending = failed
        conn._raw.execute(AUDIT_TRIGGERS)
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)" % (WRITE_LOCK_KEY + 1))
