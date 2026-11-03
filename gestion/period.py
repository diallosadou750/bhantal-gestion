"""Verrou de période comptable : une fois les comptes arrêtés jusqu'à une date, plus aucune opération datée de cette période ne peut être créée ni annulée."""
from __future__ import annotations

import sqlite3

from .ops import OpError


def locked_through(c: sqlite3.Connection) -> str | None:
    r = c.execute("SELECT value FROM meta WHERE key='acct_lock'").fetchone()
    return r["value"] or None if r else None


def ensure_open(c: sqlite3.Connection, d: str) -> None:
    lock = locked_through(c)
    if lock and d <= lock:
        raise OpError(f"La comptabilité est verrouillée jusqu'au {lock} : on ne peut plus créer ni annuler une opération datée du {d}. "
                      "Passez une écriture d'ajustement datée d'après le verrou, ou demandez au propriétaire de le lever.")
