# Rapport de stage — compilation

## Compiler

Le plus simple, sans installer de distribution LaTeX complète :

```bash
brew install tectonic
cd docs/rapport-de-stage
tectonic main.tex
```

Avec une distribution classique (MacTeX / TeX Live) :

```bash
cd docs/rapport-de-stage
pdflatex main.tex
makeglossaries main
pdflatex main.tex
pdflatex main.tex
```

Aucune dépendance à `biber` n'est nécessaire : la bibliographie (`annexes/bibliographie.tex`) est gérée manuellement plutôt que via `biblatex`, pour éviter les problèmes récurrents de compatibilité de version entre `biber` et `biblatex` selon les distributions. Les mêmes références restent disponibles au format `.bib` (`bibliographie.bib`) si vous préférez rebasculer dessus.

## À compléter avant soutenance

Quelques champs n'ont pas pu être remplis automatiquement — recherchez `[Nom à compléter]` / `[Section à compléter...]` :

- `chapitres/00_page_garde.tex` — nom de l'encadrant académique ESPRIT, nom de l'encadrant professionnel Prodexo, logos (si un template officiel ESPRIT est fourni, préférez-le à cette page de garde générique)
- `chapitres/01_remerciements.tex` — mêmes noms d'encadrants
- `chapitres/04_cadre_general.tex`, section 1.1 — présentation officielle de Prodexo (secteur, taille, positionnement)

## Captures d'écran du backoffice

Le chapitre 4 (Réalisation) décrit les pages du backoffice de façon narrative mais n'inclut pas de captures d'écran réelles — la stack Docker était indisponible au moment de la rédaction. Pour les ajouter :

1. Démarrer la stack (`docker compose -f infrastructure/docker/docker-compose.dev.yml up -d`)
2. Capturer les pages Dashboard, Agent IA Admin, Règles, Coûts & Messages, Conversations, Insights (1440×900 recommandé pour correspondre à la mise en page du document)
3. Les placer dans `docs/rapport-de-stage/images/`
4. Ajouter les `\includegraphics` correspondants dans `chapitres/07_realisation.tex`

## Diagrammes

Les diagrammes PlantUML utilisés par ce rapport vivent dans `docs/diagrams/` (partagés avec le README principal du projet) et ont été mis à jour pour refléter l'architecture réellement livrée, notamment le diagramme de classes et la séquence de génération de coupons en masse, qui référençaient encore l'ancienne architecture de l'agent d'administration avant ce stage.
