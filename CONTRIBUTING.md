# Contribuer

Les contributions sont les bienvenues (code, tests, traductions, relecture comptable).

1. Installez l'environnement : `.\installer.ps1`.
2. Lancez les tests : `.\.venv\Scripts\python.exe -m pytest tests`. Toute modification de comportement vient avec un test.
3. Règles du projet :
   - Les droits se vérifient **côté serveur** ; ne comptez jamais sur l'interface.
   - Toute écriture de données passe par `ops.apply_op` (ou un service équivalent) et **journalise dans la même transaction**.
   - Aucune donnée réelle (clients, montants, mots de passe) dans le dépôt, les tests ou les captures d'écran.
   - Pas d'insertion de données par `innerHTML` ; pas de script ni de style en ligne (la politique CSP les bloque).
   - Les règles comptables ou fiscales citées doivent indiquer leur source, ou être marquées « à confirmer ».
4. Licence : en contribuant, vous acceptez que votre code soit publié sous AGPL-3.0.
