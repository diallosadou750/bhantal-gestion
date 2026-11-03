# Feuille de route

Chaque étape reprend le même socle : droits vérifiés par le serveur, journal d'audit dans la même transaction, synchronisation par opérations rejouables et versions.

## Fait (0.1)
- Comptes, rôles, sessions, verrouillage, journal d'audit chaîné, vérification, export CSV.
- Clients : création, modification, suppression logique, synchronisation hors connexion, conflits.
- Interface mobile installable (PWA).

- Catalogue, ventes comptant et crédit, paiements, avoirs, créances, relances WhatsApp, factures et reçus imprimables, tableau de bord (0.2).
- Stocks, fournisseurs, achats, règlements fournisseurs, ajustements, inventaire, alertes de prix d'achat, coût moyen et marges (0.3).
- Dépenses par catégorie, budgets, seuil de rentabilité, caisse espèces avec clôture et mouvements, import des dépenses du classeur Excel (0.4).
- Rapprochement bancaire et mobile money : import de relevé, pointage manuel et automatique, état de rapprochement (0.5).
- Comptabilité en partie double déduite des documents : journal, balance, grand livre, résultat, bilan, TVA, écritures manuelles, plan comptable, verrou de période (0.6).
- Clôture d'exercice avec report des soldes, affectation du résultat, verrou automatique et réouverture contrôlée (0.7).
- Immobilisations : registre, amortissement linéaire mensuel, cessions, plan d'amortissement (0.8).
- Provisions et dépréciations : créances douteuses, stocks, litiges, dotations et reprises (0.10).
- Charges et produits constatés d'avance : étalement au prorata des jours (0.11).
- Devis convertibles en vente, rôle auditeur en lecture seule, double authentification TOTP, sauvegardes automatiques et export complet (0.12).
- Bons de livraison partiels ou complets, imprimables (0.13).
- Comptes bancaires et mobile money multiples (0.14).
- Virements entre comptes, dépenses récurrentes, fiabilité des clients, prévision de trésorerie, avoirs fournisseurs, logo, journal des connexions (0.15).
- Pièces jointes (photo du reçu, facture fournisseur, bon signé) (0.16).
- TVA par ligne : taux par produit, taux figé sur la vente, ventilation par taux en comptabilité, sur la facture et dans le récapitulatif (0.17).
- Dépôts, transferts de stock, lots et dates de péremption (0.18).
- Catégories de dépenses personnalisées (0.19).
- Marges par produit et par client, exports CSV (0.20).
- Rapports de ventes par vendeur et par mois (0.21).
- Export PDF des rapports (marges, ventes, états financiers) (0.22).
- Aide intégrée, guide d'utilisation et démarrage automatique Windows (0.28).
- Import Excel : fournisseurs, entrées de stock, stock initial, seuils et factures fournisseurs (0.33).
- Rappels et notifications : résumé par e-mail, WhatsApp à la demande, notification sur l'appareil (0.32).
- Retours en stock liés aux avoirs clients, par dépôt et par lot (0.31).
- Export PDF du journal, de la balance et du grand livre (0.30).
- Copie de secours vérifiée hors de l'ordinateur (0.29).
- PostgreSQL en option (0.27).
- Amortissement dégressif (0.26).
- Tests dans un vrai navigateur (Playwright) (0.25).
- Chiffrement de la base et des sauvegardes (SQLCipher, clé Windows DPAPI ou phrase de passe) (0.24).
- Langues : anglais et arabe (RTL), langue automatique de l'appareil, documents multilingues, guide TRADUCTIONS.md (0.23). Reste : relecture native, PDF arabe (police), langues locales.
- États financiers de synthèse : bilan, résultat par soldes intermédiaires, flux de trésorerie, impression (0.9).

## Prochaines étapes
2. **Stocks (suite)** : retours en stock liés aux avoirs clients, lots suivis par dépôt, import des entrées de stock et fournisseurs du classeur Excel.
3. **Banque et dépenses (suite)** : lecture souple des relevés CSV faite (0.38 : en-têtes de banque, formats de dates et de nombres, choix manuel des colonnes). Reste : formats propres à chaque banque guinéenne et fichiers Excel/PDF directs (il faut des exemples réels).
4. **Comptabilité (suite)** : amortissement dégressif et distinction comptable/fiscal, liasse officielle (rubriques, codes et notes annexes), déclaration de TVA — *toutes les règles doivent être validées par un expert-comptable pour la Guinée (SYSCOHADA)*.
5. ~~Rapports (suite)~~ : ventes par catégorie de produit, objectifs de vente par vendeur, export PDF du journal et de la balance (faits, 0.34).
6. **Import** depuis le classeur Excel actuel et **export** complet des données.
7. **Sécurité (suite)** : ~~chiffrement de la base, clé de double authentification chiffrée, copie des sauvegardes hors du serveur~~ (faits, 0.35 pour la clé 2FA et le mot de passe d'envoi des e-mails).
8. **Technique** : PostgreSQL en option, tests de bout en bout dans un navigateur, traductions (français, anglais, langues locales).

- Bons de commande fournisseurs : commande, suivi des retards, réception avec écarts qui crée l'achat et le stock, impression du bon (faits, 0.40 et 0.41).
- Paie simple : salariés, bulletins mensuels, taux de cotisation saisis par l'entreprise (aucun taux légal codé), paiement en dépense, écritures 661/664/422/431/4471 (0.42). Avances sur salaire (dépense au 421, reprise décidée sur le bulletin) et registre des absences sans calcul légal (0.43). Reste : barème d'ITS, droits à congés, heures supplémentaires, déclaration CNSS (règles à valider par un expert).

## Idées à discuter
- Notifications (retards, stock bas) par WhatsApp ou e-mail.
- ~~Signature des entrées du journal avec une clé conservée hors du serveur~~ (faite, 0.36 : sceaux Ed25519).
