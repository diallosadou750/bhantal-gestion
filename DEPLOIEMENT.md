# Déploiement

Trois façons de rendre l'application accessible depuis les téléphones, de la plus simple à la plus ouverte.

## 1. Sur le réseau de l'entreprise (HTTP, réseau de confiance)

`lancer_reseau.bat` écoute sur le réseau local et affiche l'adresse (par exemple `http://192.168.1.20:8090`). Pare-feu Windows : autorisez Python sur les réseaux privés lorsqu'il le demande.

- Simple, gratuit, les données restent dans vos murs.
- **Pas d'installation comme application, pas d'ouverture hors connexion** (le navigateur exige HTTPS), et le Wi-Fi n'est pas chiffré pour cette application.
- L'ordinateur qui héberge doit rester allumé.

## 2. Accès privé chez soi ou au bureau depuis n'importe où (tunnel)

Tailscale donne à chaque téléphone de l'équipe un accès chiffré à l'ordinateur-serveur, sans ouvrir de port sur Internet, et fournit du HTTPS. Dans cette installation Bhantal, le serveur reste lié à `127.0.0.1:8090` ; seul le relais HTTPS privé Tailscale est publié.

1. Laissez Tailscale connecté sur l'ordinateur qui héberge les données et démarrez Gestion libre.
2. Exécutez `activer_acces_multiappareils.ps1` avec PowerShell. Le script affiche l'adresse HTTPS à utiliser.
3. Installez Tailscale sur chaque téléphone ou ordinateur, connectez-le au même réseau privé, puis ouvrez l'adresse affichée.
4. Créez un compte distinct pour chaque membre dans Plus > Équipe et attribuez-lui le rôle nécessaire.

L'ordinateur serveur doit rester allumé. Après une première ouverture et synchronisation, l'application garde les données consultées sur l'appareil ; les opérations faites pendant une coupure réseau sont envoyées au retour de la connexion. Pour éviter les conflits, gardez l'application ouverte pendant le travail hors ligne et reconnectez Tailscale avant de fermer la session. Le compte Gestion libre reste obligatoire, même pour les appareils autorisés sur le réseau privé.

## 3. Serveur sur Internet (HTTPS)

1. Louer un petit serveur (VPS) et un nom de domaine.
2. Installer Python, ce dépôt, et lancer l'application derrière un serveur HTTPS (par exemple Caddy, qui obtient le certificat tout seul) :

```
gestion.exemple.com {
    reverse_proxy 127.0.0.1:8090
}
```

3. Variables d'environnement : `GESTION_SECURE_COOKIES=1`, `GESTION_DATA_DIR=/var/lib/gestion`.
4. Sauvegardes chiffrées et copiées ailleurs ; mises à jour régulières ; mots de passe forts ; comptes personnels.

Vous devenez responsable de la sécurité de ce serveur et des données de vos clients. En cas de doute, faites-vous aider.
