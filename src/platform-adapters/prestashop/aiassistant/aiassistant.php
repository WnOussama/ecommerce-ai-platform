<?php
/**
 * AI Assistant - Module PrestaShop
 *
 * Injecte un widget de chat IA sur le front-office et connecte la
 * boutique à l'AI Core (FastAPI) via une clé d'accès tenant, conformément
 * au cahier des charges ("Connexion de la boutique via une clé d'accès").
 *
 * Direction de l'intégration: ce module POUSSE le widget et l'identité de
 * la boutique vers l'AI Core. Le client Python
 * (src/ai-core/app/infrastructure/external/prestashop/client.py) TIRE le
 * catalogue depuis PrestaShop dans l'autre sens - deux directions, une
 * seule intégration.
 */

if (!defined('_PS_VERSION_')) {
    exit;
}

require_once dirname(__FILE__).'/classes/AiCoreClient.php';

class AiAssistant extends Module
{
    public function __construct()
    {
        $this->name = 'aiassistant';
        $this->tab = 'front_office_features';
        $this->version = '1.0.0';
        $this->author = 'Prodexo PFE';
        $this->need_instance = 0;
        $this->bootstrap = true;
        $this->ps_versions_compliancy = ['min' => '1.7', 'max' => _PS_VERSION_];

        parent::__construct();

        $this->displayName = $this->l('Assistant IA E-commerce');
        $this->description = $this->l(
            'Chatbot IA, recommandations et FAQ dynamique connectés à votre AI Core.'
        );

        $this->confirmUninstall = $this->l(
            'Êtes-vous sûr de vouloir désinstaller ce module ? La configuration de connexion sera perdue.'
        );
    }

    public function install()
    {
        return parent::install()
            && $this->registerHook('displayHeader')
            && $this->registerHook('displayFooter')
            && Configuration::updateValue('AIASSISTANT_API_URL', '')
            && Configuration::updateValue('AIASSISTANT_TENANT_ID', '')
            && Configuration::updateValue('AIASSISTANT_ENABLED', false)
            && Configuration::updateValue('AIASSISTANT_WELCOME_MESSAGE', $this->l('Bonjour ! Comment puis-je vous aider ?'));
    }

    public function uninstall()
    {
        return parent::uninstall()
            && Configuration::deleteByName('AIASSISTANT_API_URL')
            && Configuration::deleteByName('AIASSISTANT_TENANT_ID')
            && Configuration::deleteByName('AIASSISTANT_ENABLED')
            && Configuration::deleteByName('AIASSISTANT_WELCOME_MESSAGE');
    }

    /**
     * Page de configuration du module (BackOffice).
     * Utilise getContent() plutôt qu'un controller Symfony dédié pour
     * rester compatible avec le plus large éventail de versions PrestaShop.
     */
    public function getContent()
    {
        $output = '';

        if (Tools::isSubmit('submit_aiassistant')) {
            $output .= $this->processConfigurationForm();
        }

        return $output.$this->renderConfigurationForm();
    }

    protected function processConfigurationForm()
    {
        $apiUrl = (string) Tools::getValue('AIASSISTANT_API_URL');
        $tenantId = (string) Tools::getValue('AIASSISTANT_TENANT_ID');
        $enabled = (bool) Tools::getValue('AIASSISTANT_ENABLED');
        $welcomeMessage = (string) Tools::getValue('AIASSISTANT_WELCOME_MESSAGE');

        if ($enabled && ($apiUrl === '' || $tenantId === '')) {
            return $this->displayError(
                $this->l('L\'URL de l\'AI Core et l\'identifiant tenant sont requis pour activer le module.')
            );
        }

        Configuration::updateValue('AIASSISTANT_API_URL', $apiUrl);
        Configuration::updateValue('AIASSISTANT_TENANT_ID', $tenantId);
        Configuration::updateValue('AIASSISTANT_ENABLED', $enabled);
        Configuration::updateValue('AIASSISTANT_WELCOME_MESSAGE', $welcomeMessage);

        // Vérifie la connexion (clé d'accès) avant de confirmer, sans
        // bloquer la sauvegarde si l'AI Core est temporairement injoignable.
        if ($enabled) {
            $client = new AiCoreClient($apiUrl, $tenantId);
            $check = $client->checkConnection();

            if (!$check['ok']) {
                return $this->displayWarning(
                    $this->l('Configuration enregistrée, mais la connexion à l\'AI Core a échoué : ').$check['error']
                );
            }
        }

        return $this->displayConfirmation($this->l('Configuration mise à jour avec succès.'));
    }

    protected function renderConfigurationForm()
    {
        $fieldsForm = [
            'form' => [
                'legend' => [
                    'title' => $this->l('Connexion à l\'AI Core'),
                    'icon' => 'icon-cogs',
                ],
                'input' => [
                    [
                        'type' => 'text',
                        'label' => $this->l('URL de l\'API AI Core'),
                        'name' => 'AIASSISTANT_API_URL',
                        'desc' => $this->l('Ex: https://ai.exemple.com/api/v1'),
                        'required' => true,
                    ],
                    [
                        'type' => 'text',
                        'label' => $this->l('Identifiant Tenant'),
                        'name' => 'AIASSISTANT_TENANT_ID',
                        'desc' => $this->l('Fourni lors de la création de votre boutique sur l\'AI Core'),
                        'required' => true,
                    ],
                    [
                        'type' => 'text',
                        'label' => $this->l('Message d\'accueil'),
                        'name' => 'AIASSISTANT_WELCOME_MESSAGE',
                    ],
                    [
                        'type' => 'switch',
                        'label' => $this->l('Activer le widget de chat'),
                        'name' => 'AIASSISTANT_ENABLED',
                        'values' => [
                            ['id' => 'active_on', 'value' => 1, 'label' => $this->l('Oui')],
                            ['id' => 'active_off', 'value' => 0, 'label' => $this->l('Non')],
                        ],
                    ],
                ],
                'submit' => [
                    'title' => $this->l('Enregistrer'),
                ],
            ],
        ];

        $helper = new HelperForm();
        $helper->show_toolbar = false;
        $helper->table = $this->table;
        $helper->module = $this;
        $helper->default_form_language = (int) Context::getContext()->language->id;
        $helper->identifier = $this->identifier;
        $helper->submit_action = 'submit_aiassistant';
        $helper->currentIndex = AdminController::$currentIndex.'&configure='.$this->name;
        $helper->token = Tools::getAdminTokenLite('AdminModules');

        $helper->fields_value = [
            'AIASSISTANT_API_URL' => Configuration::get('AIASSISTANT_API_URL'),
            'AIASSISTANT_TENANT_ID' => Configuration::get('AIASSISTANT_TENANT_ID'),
            'AIASSISTANT_WELCOME_MESSAGE' => Configuration::get('AIASSISTANT_WELCOME_MESSAGE'),
            'AIASSISTANT_ENABLED' => Configuration::get('AIASSISTANT_ENABLED'),
        ];

        return $helper->generateForm([$fieldsForm]);
    }

    public function hookDisplayHeader()
    {
        if (!Configuration::get('AIASSISTANT_ENABLED')) {
            return '';
        }

        $this->context->controller->addCSS($this->_path.'views/css/widget.css');
        $this->context->controller->addJS($this->_path.'views/js/widget.js');

        Media::addJsDef([
            'aiAssistantConfig' => [
                'chatUrl' => $this->context->link->getModuleLink($this->name, 'chat'),
                'welcomeMessage' => Configuration::get('AIASSISTANT_WELCOME_MESSAGE'),
            ],
        ]);

        return '';
    }

    /**
     * Injecte le widget en pied de page. Le widget ne connaît jamais
     * l'URL ni l'identifiant tenant de l'AI Core - ceux-ci restent côté
     * serveur (voir controllers/front/chat.php), seule
     * l'URL du contrôleur front local ("chatUrl" ci-dessus) est exposée.
     */
    public function hookDisplayFooter()
    {
        if (!Configuration::get('AIASSISTANT_ENABLED')) {
            return '';
        }

        return $this->display(__FILE__, 'views/templates/hook/widget.tpl');
    }
}
