"""Démarre un serveur PostgreSQL JETABLE pour les tests (paquet pgserver, aucune installation) et affiche l'adresse à mettre dans GESTION_TEST_PG.
  .venv\Scripts\python.exe outils\serveur_pg_test.py     (laisser la fenêtre ouverte, Ctrl+C pour arrêter)
  set GESTION_TEST_PG=<adresse affichée> & .venv\Scripts\python.exe -m pytest tests
"""
import pathlib
import time

import pgserver

d = pathlib.Path(__file__).resolve().parent.parent / ".pgtest"
srv = pgserver.get_server(d, cleanup_mode="stop")
print("GESTION_TEST_PG=" + srv.get_uri(), flush=True)
try:
    while True: time.sleep(60)
except KeyboardInterrupt:
    pass
