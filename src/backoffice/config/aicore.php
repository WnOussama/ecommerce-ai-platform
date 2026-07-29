<?php

return [

    /*
    |--------------------------------------------------------------------------
    | AI Core API
    |--------------------------------------------------------------------------
    |
    | The backoffice never touches the AI Core's database directly - it is
    | a pure REST client, per the platform's architecture rule that FastAPI
    | is the exclusive owner of that database. All admin data comes from
    | these HTTP endpoints.
    |
    | Auth: in production, set AICORE_API_KEY (sent as "Authorization:
    | Bearer <key>"). AICORE_TENANT_ID sends the dev-only "X-Tenant-ID"
    | header that TenantContextMiddleware accepts when ENVIRONMENT=development
    | - tenant creation / real API key issuance is not yet implemented on
    | the AI Core side (see docs/deployment/docker.md), so this is the only
    | working auth path today.
    |
    */

    'base_url' => env('AICORE_API_URL', 'http://localhost:8000/api/v1'),

    // Root URL (no /api/v1 suffix) - used only for the public /health check.
    'root_url' => env('AICORE_ROOT_URL', 'http://localhost:8000'),

    'tenant_id' => env('AICORE_TENANT_ID'),

    'api_key' => env('AICORE_API_KEY'),

    'timeout' => env('AICORE_TIMEOUT', 10),

];
