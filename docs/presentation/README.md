# Présentations

Deux diaporamas HTML autonomes (polices et images inlinées, aucune dépendance externe), pour deux publics différents.

| Fichier | Public | Contenu |
|---|---|---|
| `restitution.html` | Revue technique interne (encadrant / expert Prodexo) | 17 slides : stack, diagrammes UML complets (cas d'utilisation, C4, classes, séquences, activités, déploiement — cliquables pour zoomer), extraits de code réels, tests, points à discuter |
| `soutenance.html` | Soutenance finale (jury ESPRIT) | 15 slides : pitch condensé — contexte, méthodologie, architecture simplifiée, fonctionnalités, bilan |

`discours_restitution.md` est le script oral (français) pour `restitution.html`, slide par slide.

## Utilisation

```bash
open docs/presentation/restitution.html   # ou soutenance.html
```

- **Navigation** : flèches gauche/droite du clavier, boutons `‹` `›`, ou les points en bas
- **Diagrammes** (restitution uniquement) : cliquer une image l'ouvre en plein écran à sa résolution native — Échap ou un clic pour fermer
- **Export PDF** : `Fichier > Imprimer` dans le navigateur (Cmd/Ctrl+P) — la feuille de style d'impression empile une diapositive par page
- **Thème** : suit `prefers-color-scheme` du système (clair/sombre)

## Mettre à jour le contenu

Chaque fichier est un unique `.html` — chaque diapositive est une balise `<section class="slide" data-slide>`. Éditer directement le texte concerné ; la navigation (JS en bas de fichier) s'adapte automatiquement au nombre de diapositives présentes. Les diagrammes de `restitution.html` sont des images PNG de `docs/diagrams/` encodées en base64 — pour les mettre à jour, régénérer le `.png` avec `plantuml`, réencoder (`base64 -i fichier.png`) et remplacer la chaîne dans le `<img src="data:image/png;base64,...">` correspondant.
