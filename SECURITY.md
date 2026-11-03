# Sécurité

## Ce que l'application garantit

- Mots de passe stockés avec scrypt (sel aléatoire), jamais en clair ; messages d'erreur identiques pour un identifiant inconnu et un mauvais mot de passe ; temps de réponse égalisé.
- Verrouillage de 15 minutes après 5 échecs consécutifs.
- Sessions : jeton aléatoire, stocké haché côté serveur, cookie `HttpOnly` + `SameSite=Lax` (+ `Secure` avec `GESTION_SECURE_COOKIES=1`). Désactiver un compte, changer son rôle ou réinitialiser son mot de passe coupe ses sessions.
- Protection CSRF : toute requête d'écriture doit porter l'en-tête `X-Requested-With: gestion`.
- En-têtes : `Content-Security-Policy` stricte (aucun script ni style en ligne), `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer`.
- Droits vérifiés **côté serveur** à chaque opération (le masquage dans l'interface n'est qu'un confort). Les refus sont journalisés.
- Export CSV protégé contre l'injection de formules (`=`, `+`, `-`, `@` en début de cellule).

## Le journal d'audit : ce qu'il garantit et ce qu'il ne garantit pas

- **Ajout seul** : des déclencheurs SQLite refusent toute modification ou suppression de ses lignes.
- **Chaîné** : chaque ligne contient l'empreinte SHA-256 de la précédente. Modifier, retirer ou insérer une ligne casse la chaîne ; « Vérifier l'intégrité » la recalcule entièrement.
- **Limite** : quelqu'un qui a accès au fichier de base *et* sait recalculer la chaîne peut la réécrire entièrement, et la suppression des toutes dernières lignes n'est pas détectable sans point de repère externe. **Bonne pratique** : notez régulièrement (par exemple chaque semaine) l'empreinte affichée par « Vérifier l'intégrité » et conservez-la ailleurs (e-mail, carnet, autre ordinateur). Si plus tard le journal compte moins d'entrées ou une autre empreinte pour la même période, il y a eu altération.
- Les heures sont en UTC (affichées dans le fuseau de l'appareil). L'horloge vient du serveur, pas de l'appareil de l'utilisateur.

## Bonnes pratiques d'exploitation

- Servez l'application en **HTTPS** dès qu'elle quitte un réseau de confiance (voir DEPLOIEMENT.md) et activez `GESTION_SECURE_COOKIES=1`.
- Sauvegardez `donnees/gestion.sqlite3` (copie hors de la machine) ; chiffrez le disque du serveur.
- Un compte = une personne. Ne partagez pas les mots de passe : le journal ne prouve « qui » que si les comptes sont personnels.
- Retirez tout de suite les comptes des personnes qui quittent l'équipe (Équipe → Désactiver).
- Gardez Python et les dépendances à jour (`pip install -U -r requirements.txt`).

## Signaler une faille

Ouvrez une alerte privée auprès du mainteneur du dépôt avant toute divulgation publique. N'incluez jamais de données réelles dans un signalement.
