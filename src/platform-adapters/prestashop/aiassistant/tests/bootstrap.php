<?php
/**
 * AiCoreClient est la seule classe de ce module testable hors du
 * bootstrap complet PrestaShop (elle n'a aucune dépendance sur Module,
 * Configuration, Context, etc.) - on définit juste la constante que son
 * garde de sécurité vérifie.
 */

if (!defined('_PS_VERSION_')) {
    define('_PS_VERSION_', '8.0.0');
}

require_once __DIR__.'/../classes/AiCoreClient.php';
