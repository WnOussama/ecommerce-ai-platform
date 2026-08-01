# Présentation de soutenance

`soutenance.html` est un diaporama HTML autonome (polices inlinées, aucune dépendance externe) — 15 diapositives.

## Utilisation

Ouvrir directement le fichier dans un navigateur :

```bash
open docs/presentation/soutenance.html
```

- **Navigation** : flèches gauche/droite du clavier, les boutons `‹` `›`, ou les points en bas
- **Export PDF** : `Fichier > Imprimer` dans le navigateur (Cmd/Ctrl+P) — la feuille de style d'impression empile automatiquement une diapositive par page
- **Thème** : suit `prefers-color-scheme` du système (clair/sombre)

## Mettre à jour le contenu

Le fichier est un unique `.html` — chaque diapositive est une balise `<section class="slide" data-slide>`. Éditer directement le texte des diapositives concernées ; la navigation (JS en bas de fichier) s'adapte automatiquement au nombre de diapositives présentes.
