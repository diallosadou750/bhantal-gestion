"""Modes de paiement : ceux de base (permissions.METHODS) plus un mode par compte bancaire ou mobile money supplémentaire (voir payaccts)."""
from __future__ import annotations

import sqlite3

from .permissions import METHODS


def extra_labels(c: sqlite3.Connection, active_only: bool = True) -> list[str]:
    sql = "SELECT label FROM pay_accounts" + (" WHERE active=1" if active_only else "") + " ORDER BY created_at, key"
    return [r["label"] for r in c.execute(sql)]


def all_methods(c: sqlite3.Connection) -> list[str]:
    return list(METHODS) + extra_labels(c)


def valid_method(c: sqlite3.Connection, method) -> bool:
    return method in METHODS or (isinstance(method, str) and method in extra_labels(c))
