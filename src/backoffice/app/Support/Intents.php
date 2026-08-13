<?php

namespace App\Support;

/**
 * Intents the chatbot's classifier (_classify_intent in chat.py) can
 * actually produce - shared between the Rules page (condition builder)
 * and the intent distribution chart (human-readable legend), so both
 * stay in sync with the one real source of truth.
 */
class Intents
{
    public const LABELS = [
        'order_status' => 'Suivi de commande',
        'product_search' => 'Recherche produit',
        'price_inquiry' => 'Question prix',
        'shipping_info' => 'Livraison',
        'return_request' => 'Retour/remboursement',
        'coupon_request' => 'Demande de code promo',
        'recommendation' => 'Recommandation',
        'greeting' => 'Salutation',
        'general' => 'Général (non classé)',
    ];

    public static function label(string $code): string
    {
        return self::LABELS[$code] ?? $code;
    }
}
