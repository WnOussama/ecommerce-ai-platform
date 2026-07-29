<?php

namespace App\Exceptions;

use Exception;

class AiCoreException extends Exception
{
    public static function fromStatus(int $status, string $body): self
    {
        return new self("AI Core API request failed with status {$status}: {$body}");
    }

    public static function connectionFailed(string $reason): self
    {
        return new self("Could not reach the AI Core API: {$reason}");
    }
}
