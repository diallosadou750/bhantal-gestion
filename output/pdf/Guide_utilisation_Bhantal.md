# Guide d'utilisation - Bhantal Gestion

Guide pratique pour démarrer, enregistrer les opérations courantes et protéger les données de l'entreprise.

## 1. Démarrer sur cet ordinateur

1. La première fois, lancez `installer.ps1` depuis le dossier de l'application.
2. Ouvrez `lancer.bat`. La page s'ouvre dans le navigateur à l'adresse `http://127.0.0.1:8090`.
3. Gardez la fenêtre du serveur ouverte pendant l'utilisation.
4. À la première ouverture, créez le compte propriétaire et conservez son mot de passe en lieu sûr.

**À savoir :** le dépôt GitHub public contient le code source, mais n'héberge pas l'application. L'accès Internet public nécessite encore un serveur HTTPS et un nom de domaine.

## 2. Les onglets principaux

| Onglet | Pour quoi faire |
| --- | --- |
| Tableau | Résumé du jour, alertes, indicateurs et bouton Nouvelle vente. |
| Commerce | Clients, ventes, créances, devis et relances. |
| Marchandises | Produits, stocks, fournisseurs et achats. |
| Argent | Caisse, dépenses, banque et paiements. |
| Finances | Rapports et comptabilité, selon le rôle. |
| Plus | Équipe, réglages, mon compte, historique et aide. |

## 3. Configurer Bhantal

Dans **Plus > Mon compte** :

- Renseignez le nom de l'entreprise, l'adresse et les téléphones à afficher sur les factures.
- Ajoutez le logo et la signature. La signature apparaît sur les factures et rapports PDF.
- Activez la double authentification et gardez les codes de secours en lieu sûr.
- Créez un compte distinct pour chaque membre dans **Plus > Équipe**.

Le propriétaire gère tous les réglages. Le comptable accède aux opérations financières. Le commercial gère les clients et ventes qui lui sont attribués. Le caissier s'occupe des opérations quotidiennes de caisse. L'auditeur consulte les informations comptables en lecture seule.

Les comptes sont créés par le propriétaire et utilisent l'identifiant et le mot de passe de l'application. La connexion Google/Gmail n'est pas activée dans cette version.

## 4. Ajouter des clients et produits

- Dans **Commerce > Clients**, créez le client avec au minimum son nom. Ajoutez son téléphone et sa limite de crédit si nécessaire.
- Dans **Marchandises > Produits**, ajoutez le nom, l'unité, le prix de vente et le seuil d'alerte.
- Pour un service, désactivez le suivi de stock. Pour une marchandise, saisissez le stock initial avec une entrée ou un inventaire justifié.
- Vérifiez prix, quantités et unités avant les premières ventes.

## 5. Enregistrer une vente et un paiement

1. Depuis **Tableau** ou **Commerce > Ventes**, choisissez **Nouvelle vente**.
2. Sélectionnez le client, ajoutez les produits et quantités, puis vérifiez le total.
3. Pour une vente au comptant, choisissez le mode de paiement. Pour une vente à crédit, indiquez l'échéance et l'acompte éventuel.
4. Ouvrez la facture après l'enregistrement pour l'imprimer ou l'enregistrer en PDF.
5. Pour encaisser une créance, ouvrez **Commerce > Créances**, choisissez **Encaisser**, puis saisissez le montant, la date, le mode et la référence.

Un paiement erroné s'annule avec un motif. Pour un retour ou une erreur sur une vente enregistrée, utilisez un avoir au lieu de modifier la vente.

### Ajouter une preuve

Ouvrez le détail d'un produit puis **Photos / preuves**, ou le détail d'un paiement puis **Preuve**. Ajoutez une photo ou un PDF de 5 Mo maximum. Cette action nécessite une connexion.

## 6. Achats, stock et caisse

- Créez le fournisseur dans **Marchandises > Fournisseurs**, puis enregistrez l'achat reçu : le stock augmente.
- Un achat à crédit crée une dette fournisseur. Enregistrez chaque règlement.
- Dans **Marchandises > Stocks**, comparez le stock affiché au stock réel et expliquez tout écart d'inventaire.
- Chaque jour, comparez l'argent réel en caisse au montant attendu, puis choisissez **Clôturer la caisse**. Expliquez tout écart.
- Virements et paiements mobiles ne sont pas des espèces et ne doivent pas être comptés dans le tiroir-caisse.

## 7. Rapports et historique

- Consultez chaque jour les retards clients, livraisons, produits à recommander et dettes à suivre sur le tableau de bord.
- Dans **Finances**, utilisez les rapports et exportez-les en PDF ou CSV si nécessaire.
- Le journal d'audit indique qui a créé, modifié, annulé ou exporté une opération et à quelle date.
- Faites vérifier les estimations et écritures comptables par votre comptable. L'application ne remplace pas les déclarations fiscales officielles.

## 8. Plusieurs appareils et travail hors connexion

- Pour l'accès privé à distance, gardez le serveur allumé et connecté. Les autres appareils doivent rejoindre le même réseau privé Tailscale et ouvrir l'adresse HTTPS fournie par le script d'accès multiappareils.
- Chaque personne se connecte avec son propre compte.
- Après une première connexion et synchronisation, certaines saisies restent possibles hors connexion et sont envoyées au retour du réseau.
- Reconnectez-vous régulièrement. Si un conflit est signalé, relisez les deux versions avant de choisir.

L'accès Tailscale est privé et ne rend pas l'application accessible à tous sur Internet. Un site public exige un hébergement HTTPS, un domaine, des sauvegardes distantes et une configuration sécurisée.

## 9. Sauvegardes et sécurité

- Dans **Plus > Mon compte**, lancez une sauvegarde et configurez une copie sur un support externe ou conservé ailleurs. Vérifiez régulièrement que la sauvegarde peut être retrouvée.
- Ne partagez pas le dossier `donnees`, les exports CSV, la clé de chiffrement ni les codes de secours.
- Utilisez un mot de passe fort, activez la double authentification et désactivez les comptes inutilisés.
- Sur un ordinateur partagé, déconnectez-vous après utilisation.

### En cas de problème

- Page inaccessible : vérifiez que le serveur est lancé avec `lancer.bat`.
- Mot de passe oublié : demandez une réinitialisation au propriétaire.
- Saisie en attente : reconnectez l'appareil et attendez la synchronisation.
- Pour plus d'aide, ouvrez l'onglet **Aide** ou consultez [GUIDE_UTILISATEUR.md](../../GUIDE_UTILISATEUR.md) dans le dépôt.

**Avant les vraies saisies :** vérifiez les comptes, les prix, le stock initial, les coordonnées sur facture, l'accès du propriétaire et la sauvegarde hors du PC.
