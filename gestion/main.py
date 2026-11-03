"""Point d'entrée : python -m gestion.main  [--lan] [--port 8090]"""
from __future__ import annotations

import argparse
import socket
from pathlib import Path

import uvicorn

from . import config


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1)); return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main() -> None:
    p = argparse.ArgumentParser(description="Gestion libre : serveur local")
    p.add_argument("--lan", action="store_true", help="accepter les connexions des autres appareils du réseau local (téléphones)")
    p.add_argument("--port", type=int, default=8090)
    p.add_argument("--demo", action="store_true", help="mode démonstration : base jetable remplie de données fictives, entrée sans identifiant (jamais en réseau)")
    a = p.parse_args()
    if a.demo:
        if a.lan: raise SystemExit("Le mode démonstration (sans identifiant) est refusé en réseau : il ne tourne que sur cet ordinateur.")
        from . import demo
        demo.prepare(Path(__file__).resolve().parent.parent / "donnees_demo")
        print("MODE DÉMONSTRATION : données fictives, entrée sans identifiant, base jetable (dossier donnees_demo).")
    host = "0.0.0.0" if a.lan else "127.0.0.1"
    print(f"Données : {config.db_path()}")
    if a.lan:
        print(f"Adresse à ouvrir sur les téléphones (même Wi-Fi) : http://{lan_ip()}:{a.port}")
        print("ATTENTION : en HTTP simple, le Wi-Fi n'est pas chiffré. Réseau de confiance uniquement ; pour Internet, utilisez HTTPS (voir SECURITY.md).")
    else:
        print(f"Adresse : http://127.0.0.1:{a.port}")
    uvicorn.run("gestion.api:app", host=host, port=a.port, log_level="info")


if __name__ == "__main__":
    main()
