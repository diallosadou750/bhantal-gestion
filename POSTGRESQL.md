# Utiliser PostgreSQL au lieu de SQLite

Par défaut, Gestion libre garde ses données dans un fichier SQLite (simple, chiffré, aucune installation). PostgreSQL est un **serveur de base de données** à part : utile si plusieurs ordinateurs ou une petite équipe doivent atteindre les mêmes données, ou si vous voulez les outils de sauvegarde et de réplication de PostgreSQL.

## Ce que cela change, et ce que cela ne change pas

| | SQLite (défaut) | PostgreSQL |
|---|---|---|
| Installation | aucune | un serveur PostgreSQL 14 ou plus à installer et à entretenir |
| Chiffrement de la base par l'application | oui (SQLCipher, voir `gestion/chiffrement.py`) | **non** : chiffrez le disque du serveur et utilisez TLS entre l'application et le serveur |
| Écritures simultanées | une à la fois | **une à la fois aussi** : l'application met les écritures en file (verrou global), car les numéros de facture, la séquence de synchronisation et le stock se calculent en lisant puis en écrivant |
| Sauvegardes | fichier chiffré copié chaque jour | `pg_dump` (format custom, restaurable avec `pg_restore`) |
| Mode démonstration | oui | toujours en SQLite (base jetable locale) |

Soyez honnête avec vous-même : pour une seule boutique, SQLite suffit largement. PostgreSQL apporte surtout l'accès depuis plusieurs machines et des outils d'exploitation éprouvés, pas de la vitesse d'écriture.

## Mise en route

1. Créez une base et un utilisateur dédiés (jamais le compte administrateur) :
   ```sql
   CREATE ROLE gestion LOGIN PASSWORD '…mot de passe fort…';
   CREATE DATABASE gestion OWNER gestion;
   ```
2. Installez le pilote : `pip install "psycopg[binary]"` (déjà dans `requirements-dev.txt`).
3. Indiquez l'adresse de la base avant de lancer l'application :
   ```bash
   set GESTION_DB_URL=postgresql://gestion:MOTDEPASSE@127.0.0.1:5432/gestion?sslmode=require
   python -m gestion.main
   ```
   Facultatif : `GESTION_DB_SCHEMA=prod` pour loger plusieurs installations dans la même base (un schéma chacune).
4. Les tables sont créées au premier démarrage. L'application refuse de démarrer si la base est injoignable (elle ne retombe jamais sur SQLite sans le dire).

Ne mettez jamais le mot de passe dans un fichier partagé ni dans l'historique d'un terminal : préférez une variable d'environnement du service Windows.

## Reprendre les données d'une base SQLite existante

```bash
.venv\Scripts\python.exe -m gestion.vers_postgres
```
(à lancer avec `GESTION_DB_URL` défini et le serveur Gestion libre **arrêté**). L'outil copie toutes les tables, vérifie les nombres de lignes, remet les compteurs d'identifiants et vérifie la chaîne d'empreintes du journal d'audit. La base SQLite d'origine n'est ni modifiée ni supprimée.

## Sauvegardes

Le bouton « Sauvegarder maintenant » et la sauvegarde quotidienne appellent `pg_dump` : il doit être installé sur la machine qui fait tourner l'application (sinon indiquez son chemin dans `GESTION_PG_DUMP`). Pour restaurer :
```bash
createdb gestion_restauree
pg_restore --no-owner --dbname gestion_restauree sauvegarde.dump
```
Une sauvegarde qui n'a jamais été restaurée n'est pas une sauvegarde : essayez une fois.

## Comment c'est testé

Toute la suite de tests (220 tests) tourne aussi sur PostgreSQL 16 : `set GESTION_TEST_PG=postgresql://…` puis `pytest tests` (un schéma neuf par test). Les sauvegardes `pg_dump` sont relues avec `pg_restore` dans une base neuve.

## Limites connues

- La traduction SQL est faite à la volée (`gestion/dbpg.py`) : un jour, une requête nouvelle écrite « à la SQLite » pourrait ne pas passer ; la suite de tests sur PostgreSQL est là pour le dire tout de suite.
- Pas de réplication ni de haute disponibilité configurées par l'application : c'est le rôle de votre serveur PostgreSQL.
