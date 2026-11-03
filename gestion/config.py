"""Configuration par variables d'environnement (aucun secret dans le code)."""
from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    """Dossier des données (base SQLite). Par défaut : ./donnees à côté du projet."""
    p = Path(os.environ.get("GESTION_DATA_DIR") or Path(__file__).resolve().parent.parent / "donnees")
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return data_dir() / "gestion.sqlite3"


def db_url() -> str | None:
    """PostgreSQL si GESTION_DB_URL est défini (postgresql://…) ; sinon SQLite. Jamais en mode démonstration (base jetable locale)."""
    u = os.environ.get("GESTION_DB_URL", "").strip()
    return u if u.startswith(("postgresql://", "postgres://")) and not demo_mode() else None


def db_schema() -> str | None:
    """Schéma PostgreSQL à utiliser (facultatif) : permet de loger plusieurs installations dans une même base."""
    return os.environ.get("GESTION_DB_SCHEMA") or None


def secure_cookies() -> bool:
    """À activer (GESTION_SECURE_COOKIES=1) dès que l'application est servie en HTTPS."""
    return os.environ.get("GESTION_SECURE_COOKIES", "0") == "1"


def demo_mode() -> bool:
    """Mode démonstration (--demo) : jamais actif sur les vraies données."""
    return os.environ.get("GESTION_DEMO", "0") == "1" and (data_dir() / ".demo").exists()      # exige le marqueur du dossier de démonstration


SESSION_HOURS = int(os.environ.get("GESTION_SESSION_HOURS", "12"))
LOCK_AFTER_FAILURES = 5
LOCK_MINUTES = 15
MIN_PASSWORD_LENGTH = 10
