# Assistant IA E-commerce — Module PrestaShop

Injecte un widget de chat IA sur le front-office d'une boutique PrestaShop
et la connecte à l'AI Core (FastAPI) via un identifiant tenant, conformément
au cahier des charges ("Connexion de la boutique via une clé d'accès").

## Direction de l'intégration

Ce module **pousse** le widget et l'identité de la boutique vers l'AI Core.
Le client Python
(`src/ai-core/app/infrastructure/external/prestashop/client.py`, 799
lignes) **tire** le catalogue depuis PrestaShop dans l'autre sens — deux
directions, une seule intégration entre les deux plateformes.

## Architecture de sécurité

Le widget JS (`views/js/widget.js`) n'a **jamais accès** à l'URL de l'AI
Core ni à l'identifiant tenant — il appelle uniquement le contrôleur front
local du module (`controllers/front/chat.php`, même origine que la
boutique), qui relaie la requête côté serveur (PHP, `classes/AiCoreClient.php`)
avec les identifiants configurés en back-office. Un visiteur ne peut donc
jamais extraire les identifiants tenant depuis le code source de la page.

## Installation (développement)

```bash
# Copier le module dans une instance PrestaShop
cp -r aiassistant /path/to/prestashop/modules/

# Installer via la console PrestaShop
php bin/console prestashop:module install aiassistant
```

Puis configurer depuis **Modules > Assistant IA E-commerce > Configurer** :
URL de l'API AI Core, identifiant tenant, message d'accueil, activation.

## Tests

```bash
composer install
vendor/bin/phpunit
```

`tests/AiCoreClientTest.php` exécute de vrais appels HTTP contre l'AI Core
(pas de mock) — les tests s'ignorent proprement si aucune instance n'est
joignable sur `http://localhost:8000`.

## Vérification effectuée

Module installé et testé dans une véritable instance PrestaShop 8.2.7
(conteneurs Docker temporaires, `prestashop/prestashop:8-apache` +
`mysql:8.0`, réseau séparé du reste du projet) :

- Installation réelle via `php bin/console prestashop:module install aiassistant`
- Widget injecté et fonctionnel sur le front-office (capture d'écran de la
  conversation réelle disponible dans l'historique de développement)
- Configuration sauvegardée via le formulaire back-office réel, avec
  vérification de connexion à l'AI Core au moment de l'enregistrement
- Un message envoyé depuis le widget traverse réellement :
  navigateur → contrôleur front PrestaShop → `AiCoreClient` (PHP, serveur) →
  AI Core (conteneur Docker séparé, réseau différent, via `host.docker.internal`) →
  réponse RAG avec les produits de démonstration réellement indexés

Ces conteneurs de test PrestaShop ne font pas partie de la stack Docker du
projet (`infrastructure/docker/`) - PrestaShop est la plateforme externe
que ce module cible, pas un service de ce dépôt.
