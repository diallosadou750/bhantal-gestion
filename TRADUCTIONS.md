# Langues de Gestion libre

Gestion libre est écrite en français ; le français est la **langue d'origine** : tous les textes de l'interface sont des phrases françaises dans le code. Une autre langue est un **dictionnaire** `gestion/static/i18n/<code>.json` qui associe chaque texte français à sa traduction. Un texte sans traduction reste en français (jamais de page cassée).

## Langues fournies

| Langue | Code | Sens | État |
|---|---|---|---|
| Français | `fr` | gauche → droite | langue d'origine |
| English | `en` | gauche → droite | environ 87 % des textes extraits ; le reste : fragments et données d'exemple |
| العربية | `ar` | droite → gauche | traduction automatique assistée, **à faire relire par un locuteur natif** avant usage professionnel |

Les langues locales de Guinée (pular `ff`, maninka `man`, soussou `sus`) **ne sont pas traduites** : je ne les maîtrise pas assez pour écrire des libellés comptables fiables, et une erreur dans un mot comme « avoir » ou « provision » induirait en erreur. Tout est prêt pour qu'un locuteur le fasse : les feuilles `outils/modele_ff.csv`, `modele_man.csv` et `modele_sus.csv` (colonne « Traduction » vide, priorité 1 = textes courts à faire d'abord ; l'anglais est donné en aide). Une traduction partielle est utilisable : tout texte non traduit reste en français. Pour commencer, la feuille courte `outils/top200_langues_locales.csv` réunit les 200 textes les plus visibles (Date, Annuler, Montant, Vente, Client, Stock…) avec trois colonnes : pular, maninka, soussou ; `construire` lit la colonne qui porte le code de la langue. Quand une feuille est remplie :

```bash
.venv\Scripts\python.exe outils\relecture.py construire ff "Pulaar (à relire)" outils\modele_ff.csv
```

## Comment l'application choisit la langue

1. Le choix fait dans **Mon compte → Langue** (mémorisé sur l'appareil).
2. Sinon, **automatiquement** : la langue de l'appareil (`navigator.languages`), si elle est dans `langues.json`.
3. Sinon le français.

L'option « Automatique » du sélecteur efface le choix mémorisé et revient au 2.

**Documents imprimés** (facture, reçu, devis, bon de livraison) : langue réglée dans *Mon compte → Informations de l'entreprise → Langue des documents* ; en mode automatique, la langue de l'appareil de la personne qui imprime.

**PDF des rapports** : français et anglais seulement. La police intégrée de l'outil PDF ne sait pas dessiner l'arabe (lettres liées, droite à gauche) ; en arabe, le PDF sort en français. Les pages HTML imprimables, elles, sortent bien en arabe.

## Ajouter une langue (exemple : l'espagnol, code `es`)

```bash
.venv\Scripts\python.exe outils\extraire_textes.py            # liste les textes français -> outils\textes_fr.json
```

1. Copiez `gestion/static/i18n/en.json` comme point de départ, ou partez de `outils/textes_fr.json`.
2. Traduisez les textes dans `outils/trad/es_1.json`, `es_2.json`… (un objet `{"texte français exact": "traduction"}` par fichier).
3. Les phrases à chiffres (« 12 livraison(s) en retard. ») se traduisent par des **motifs** dans `outils/trad/es_motifs.json` :
   `{"re": "^(\\d+) livraison\\(s\\) en retard\\.$", "to": "$1 entregas atrasadas."}` — `$1`, `$2`… reprennent les groupes capturés ; `{t:1}` traduit le groupe capturé. Les motifs précis passent avant les motifs généraux.
4. Construisez le dictionnaire (ajoute la langue à `langues.json`) :
   ```bash
   .venv\Scripts\python.exe outils\construire_langue.py es Español
   ```
   Pour une langue écrite de droite à gauche, ajoutez `rtl` à la fin.
5. Mesurez la couverture et listez ce qui manque :
   ```bash
   .venv\Scripts\python.exe outils\extraire_textes.py --langue es
   ```
   Le fichier `outils/manque_es.json` contient les textes encore non traduits.
6. Dans le navigateur, ouvrez l'application, choisissez la langue, parcourez les onglets ; `I18N.missing()` (console) liste les textes réellement affichés sans traduction.
7. Lancez `.venv\Scripts\python.exe -m pytest tests\test_i18n.py` : il vérifie que le JSON est valide, que les motifs compilent et n'oublient aucun groupe.

## Relecture par un locuteur natif

```bash
.venv\Scripts\python.exe outils\relecture.py export
```

Crée `outils/relecture_traductions.csv` (français, English, العربية, colonnes de correction ; séparateur point-virgule, UTF-8, s'ouvre dans Excel ou LibreOffice). Le relecteur ne remplit que les colonnes « Correction » ; une case vide = traduction acceptée. Ensuite :

```bash
.venv\Scripts\python.exe outils\relecture.py appliquer outils\relecture_traductions.csv
```

Les corrections sont enregistrées dans `outils/trad/en_9_relecture.json` et `ar_9_relecture.json` (elles remplacent les traductions d'origine) et les langues sont reconstruites. Ne pas oublier de redémarrer le serveur.

## Règles de traduction

- Ne traduisez pas les codes de pièces (FV-, DV-, BL-, AF-, DP-…), les sigles comptables (SYSCOHADA, CNSS, ITS) ni les noms de comptes à numéro.
- Gardez la ponctuation d'origine à la fin (« : », « … », « ? ») ; elle sert à retrouver le texte.
- Les termes comptables sont **indicatifs** : faites relire par un comptable de votre pays.
- Chiffres : l'application garde les chiffres occidentaux (0-9) dans toutes les langues.

## Limites connues

- La traduction se fait à l'affichage : les données saisies (noms de clients, de produits, notes) ne sont jamais traduites.
- Le journal d'audit conserve les textes d'origine ; il est traduit à l'affichage comme le reste.
- L'arabe et l'anglais sont issus d'un travail assisté par machine : relecture humaine indispensable pour tout document officiel.
