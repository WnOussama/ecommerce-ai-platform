"""
Security Service - Service de sécurité partagé
Gère la sanitization, détection d'injection, et validation
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from enum import Enum
import re
import logging
import hashlib

logger = logging.getLogger(__name__)


# =============================================================================
# TYPES
# =============================================================================

class ThreatLevel(str, Enum):
    """Niveau de menace détecté"""
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ThreatType(str, Enum):
    """Type de menace détectée"""
    PROMPT_INJECTION = "prompt_injection"
    JAILBREAK_ATTEMPT = "jailbreak_attempt"
    DATA_EXTRACTION = "data_extraction"
    ROLEPLAY_ATTACK = "roleplay_attack"
    ENCODING_ATTACK = "encoding_attack"
    SQL_INJECTION = "sql_injection"
    XSS_ATTEMPT = "xss_attempt"


@dataclass
class SecurityCheckResult:
    """Résultat d'une vérification de sécurité"""
    is_safe: bool
    threat_level: ThreatLevel = ThreatLevel.NONE
    threats_detected: List[ThreatType] = field(default_factory=list)
    sanitized_input: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    blocked: bool = False
    block_reason: Optional[str] = None


@dataclass
class OutputValidationResult:
    """Résultat de validation d'output"""
    is_valid: bool
    filtered_output: str = ""
    violations: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


# =============================================================================
# SECURITY SERVICE
# =============================================================================

class SecurityService:
    """
    Service de sécurité partagé entre tous les agents.

    Responsabilités:
    - Input sanitization
    - Prompt injection detection
    - Output validation/filtering
    - Rate limit checking
    - Audit logging
    """

    # =========================================================================
    # INJECTION DETECTION PATTERNS
    # =========================================================================

    # Patterns de prompt injection (CRITIQUE)
    INJECTION_PATTERNS = [
        # Override instructions
        (r"ignore\s+(all\s+)?(previous|prior|above|earlier)\s+instructions?", ThreatType.PROMPT_INJECTION),
        (r"disregard\s+(all\s+)?(previous|prior|your)\s+(instructions?|rules?|guidelines?)", ThreatType.PROMPT_INJECTION),
        (r"forget\s+(everything|all|what)\s+(you|i)\s+(told|said|instructed)", ThreatType.PROMPT_INJECTION),
        (r"new\s+instructions?:\s*", ThreatType.PROMPT_INJECTION),
        (r"override\s+(previous\s+)?instructions?", ThreatType.PROMPT_INJECTION),

        # System prompt extraction
        (r"(show|reveal|display|print|output)\s+(me\s+)?(your|the)\s+system\s+prompt", ThreatType.DATA_EXTRACTION),
        (r"what\s+(are|is)\s+your\s+(initial\s+)?instructions?", ThreatType.DATA_EXTRACTION),
        (r"repeat\s+(your\s+)?(system\s+)?(prompt|instructions?)", ThreatType.DATA_EXTRACTION),
        (r"tell\s+me\s+(your|the)\s+(system\s+)?(prompt|instructions?)", ThreatType.DATA_EXTRACTION),

        # Jailbreak attempts
        (r"(DAN|STAN|DUDE)\s*(mode)?", ThreatType.JAILBREAK_ATTEMPT),
        (r"jailbreak(ed)?", ThreatType.JAILBREAK_ATTEMPT),
        (r"developer\s+mode", ThreatType.JAILBREAK_ATTEMPT),
        (r"god\s+mode", ThreatType.JAILBREAK_ATTEMPT),
        (r"no\s+(rules?|restrictions?|limitations?)", ThreatType.JAILBREAK_ATTEMPT),

        # Roleplay attacks
        (r"(you\s+are|act\s+as|pretend\s+(to\s+be|you\'?re)|roleplay\s+as)\s+[a-z]+", ThreatType.ROLEPLAY_ATTACK),
        (r"from\s+now\s+on\s+(you|your)", ThreatType.ROLEPLAY_ATTACK),
        (r"let\'?s\s+play\s+a\s+game", ThreatType.ROLEPLAY_ATTACK),

        # Encoding attacks
        (r"base64[\s:]+[A-Za-z0-9+/=]{20,}", ThreatType.ENCODING_ATTACK),
        (r"\\x[0-9a-fA-F]{2}", ThreatType.ENCODING_ATTACK),
        (r"\\u[0-9a-fA-F]{4}", ThreatType.ENCODING_ATTACK),
    ]

    # Patterns pour SQL injection
    SQL_PATTERNS = [
        r"(\b(SELECT|INSERT|UPDATE|DELETE|DROP|UNION|ALTER)\b)",
        r"(--)|(\/\*)",
        r"(\bOR\b\s+\d+\s*=\s*\d+)",
    ]

    # Patterns pour XSS
    XSS_PATTERNS = [
        r"<script[^>]*>",
        r"javascript:",
        r"on(load|error|click|mouse)",
    ]

    # =========================================================================
    # OUTPUT GUARDRAILS (Pour Client Agent)
    # =========================================================================

    # Patterns à filtrer dans les outputs
    OUTPUT_FORBIDDEN_PATTERNS = [
        # Données sensibles
        (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "email"),  # Emails
        (r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b", "credit_card"),  # Cartes
        (r"\b(mot\s+de\s+passe|password)[\s:]+\S+", "password"),  # Passwords

        # System info leakage
        (r"(system|initial)\s+prompt", "system_info"),
        (r"(my|the)\s+instructions?\s+(are|is)", "system_info"),

        # Comportement inapproprié
        (r"\b(idiot|stupide|imbécile)\b", "insult"),
    ]

    # Limites de longueur
    MAX_INPUT_LENGTH = 2000
    MAX_OUTPUT_LENGTH = 4000

    def __init__(self):
        # Compiler les regex pour performance
        self._injection_patterns = [
            (re.compile(pattern, re.IGNORECASE), threat_type)
            for pattern, threat_type in self.INJECTION_PATTERNS
        ]

        self._sql_patterns = [
            re.compile(pattern, re.IGNORECASE)
            for pattern in self.SQL_PATTERNS
        ]

        self._xss_patterns = [
            re.compile(pattern, re.IGNORECASE)
            for pattern in self.XSS_PATTERNS
        ]

        self._output_patterns = [
            (re.compile(pattern, re.IGNORECASE), name)
            for pattern, name in self.OUTPUT_FORBIDDEN_PATTERNS
        ]

    # =========================================================================
    # INPUT VALIDATION
    # =========================================================================

    def check_input(
        self,
        text: str,
        tenant_id: Optional[str] = None,
        strict_mode: bool = False,
    ) -> SecurityCheckResult:
        """
        Vérifie et sanitize un input utilisateur.

        Args:
            text: Texte à vérifier
            tenant_id: ID du tenant pour logging
            strict_mode: Si True, bloque au moindre soupçon

        Returns:
            SecurityCheckResult avec statut et input sanitizé
        """
        threats_detected = []
        details = {}

        # 1. Vérification longueur
        if len(text) > self.MAX_INPUT_LENGTH:
            text = text[:self.MAX_INPUT_LENGTH]
            details["truncated"] = True

        # 2. Normalisation (enlever caractères de contrôle)
        sanitized = self._normalize_text(text)

        # 3. Détection d'injection
        for pattern, threat_type in self._injection_patterns:
            if pattern.search(sanitized):
                threats_detected.append(threat_type)
                details[f"{threat_type.value}_match"] = True

        # 4. Détection SQL injection
        for pattern in self._sql_patterns:
            if pattern.search(sanitized):
                threats_detected.append(ThreatType.SQL_INJECTION)
                details["sql_pattern_match"] = True
                break

        # 5. Détection XSS
        for pattern in self._xss_patterns:
            if pattern.search(sanitized):
                threats_detected.append(ThreatType.XSS_ATTEMPT)
                # Sanitize HTML
                sanitized = self._strip_html(sanitized)
                details["xss_pattern_match"] = True
                break

        # Déterminer le niveau de menace
        threat_level = self._calculate_threat_level(threats_detected)

        # Décider si on bloque
        blocked = False
        block_reason = None

        if threat_level == ThreatLevel.CRITICAL:
            blocked = True
            block_reason = "Critical security threat detected"
        elif threat_level == ThreatLevel.HIGH and strict_mode:
            blocked = True
            block_reason = "High security threat in strict mode"

        # Logging sécurité
        if threats_detected:
            self._log_security_event(
                event_type="input_threat_detected",
                tenant_id=tenant_id,
                threat_level=threat_level,
                threats=threats_detected,
                blocked=blocked,
            )

        return SecurityCheckResult(
            is_safe=len(threats_detected) == 0,
            threat_level=threat_level,
            threats_detected=threats_detected,
            sanitized_input=sanitized,
            details=details,
            blocked=blocked,
            block_reason=block_reason,
        )

    def _normalize_text(self, text: str) -> str:
        """Normalise le texte (supprime caractères dangereux)"""
        # Supprimer caractères de contrôle (sauf newline, tab)
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)

        # Normaliser les espaces
        text = re.sub(r'\s+', ' ', text)

        return text.strip()

    def _strip_html(self, text: str) -> str:
        """Supprime les balises HTML"""
        return re.sub(r'<[^>]+>', '', text)

    def _calculate_threat_level(self, threats: List[ThreatType]) -> ThreatLevel:
        """Calcule le niveau de menace global"""
        if not threats:
            return ThreatLevel.NONE

        # Menaces critiques
        critical_threats = {
            ThreatType.PROMPT_INJECTION,
            ThreatType.JAILBREAK_ATTEMPT,
        }

        high_threats = {
            ThreatType.DATA_EXTRACTION,
            ThreatType.ROLEPLAY_ATTACK,
            ThreatType.SQL_INJECTION,
        }

        if any(t in critical_threats for t in threats):
            return ThreatLevel.CRITICAL
        elif any(t in high_threats for t in threats):
            return ThreatLevel.HIGH
        elif len(threats) > 1:
            return ThreatLevel.MEDIUM
        else:
            return ThreatLevel.LOW

    # =========================================================================
    # OUTPUT VALIDATION (GUARDRAILS)
    # =========================================================================

    def validate_output(
        self,
        output: str,
        agent_type: str = "client",
        tenant_id: Optional[str] = None,
    ) -> OutputValidationResult:
        """
        Valide et filtre l'output de l'IA.
        Différents niveaux selon le type d'agent.

        Args:
            output: Texte généré par l'IA
            agent_type: "client" (strict) ou "admin" (moins strict)
            tenant_id: Pour logging

        Returns:
            OutputValidationResult avec output filtré
        """
        violations = []
        warnings = []
        filtered = output

        # 1. Vérification longueur
        if len(output) > self.MAX_OUTPUT_LENGTH:
            filtered = output[:self.MAX_OUTPUT_LENGTH] + "..."
            warnings.append("output_truncated")

        # 2. Vérification patterns interdits
        for pattern, pattern_name in self._output_patterns:
            if pattern.search(filtered):
                # Client: filtrer strictement
                if agent_type == "client":
                    filtered = pattern.sub("[FILTERED]", filtered)
                    violations.append(f"filtered_{pattern_name}")
                else:
                    # Admin: juste avertir
                    warnings.append(f"contains_{pattern_name}")

        # 3. Pour Client Agent: vérifier le ton
        if agent_type == "client":
            tone_check = self._check_conversational_tone(filtered)
            if not tone_check["is_appropriate"]:
                warnings.extend(tone_check["issues"])

        # Logging si violations
        if violations:
            self._log_security_event(
                event_type="output_filtered",
                tenant_id=tenant_id,
                agent_type=agent_type,
                violations=violations,
            )

        return OutputValidationResult(
            is_valid=len(violations) == 0,
            filtered_output=filtered,
            violations=violations,
            warnings=warnings,
        )

    def _check_conversational_tone(self, text: str) -> Dict[str, Any]:
        """Vérifie que le ton est conversationnel et approprié"""
        issues = []

        # Vérifier qu'on n'a pas de JSON brut dans une réponse client
        if text.strip().startswith("{") and text.strip().endswith("}"):
            issues.append("raw_json_in_client_response")

        # Vérifier le vouvoiement (FR)
        if re.search(r"\b(tu |ton |ta |tes )\b", text, re.IGNORECASE):
            issues.append("informal_tone")

        return {
            "is_appropriate": len(issues) == 0,
            "issues": issues
        }

    # =========================================================================
    # JSON VALIDATION (Pour Admin Agent)
    # =========================================================================

    def validate_json_output(
        self,
        output: str,
        expected_schema: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Optional[Dict[str, Any]], List[str]]:
        """
        Valide que l'output est du JSON valide et conforme au schéma.
        Utilisé par l'Admin Agent qui doit TOUJOURS retourner du JSON.

        Returns:
            (is_valid, parsed_json, errors)
        """
        import json

        errors = []

        # 1. Parser le JSON
        try:
            parsed = json.loads(output)
        except json.JSONDecodeError as e:
            errors.append(f"invalid_json: {str(e)}")
            return False, None, errors

        # 2. Valider le schéma si fourni
        if expected_schema:
            schema_errors = self._validate_schema(parsed, expected_schema)
            errors.extend(schema_errors)

        # 3. Vérifier les champs obligatoires pour Admin
        required_fields = ["action_type", "status"]
        for field in required_fields:
            if field not in parsed:
                errors.append(f"missing_required_field: {field}")

        return len(errors) == 0, parsed, errors

    def _validate_schema(
        self,
        data: Dict[str, Any],
        schema: Dict[str, Any],
    ) -> List[str]:
        """Validation simple de schéma"""
        errors = []

        for field, field_type in schema.items():
            if field not in data:
                if not field.startswith("?"):  # ? = optionnel
                    errors.append(f"missing_field: {field}")
            elif not isinstance(data[field], field_type):
                errors.append(f"wrong_type: {field} should be {field_type.__name__}")

        return errors

    # =========================================================================
    # LOGGING & AUDIT
    # =========================================================================

    def _log_security_event(
        self,
        event_type: str,
        tenant_id: Optional[str] = None,
        **kwargs,
    ):
        """Log un événement de sécurité"""
        logger.warning(
            f"Security event: {event_type}",
            extra={
                "event_type": event_type,
                "tenant_id": tenant_id,
                "timestamp": datetime.utcnow().isoformat(),
                **kwargs,
            }
        )

    # =========================================================================
    # API KEY VALIDATION
    # =========================================================================

    @staticmethod
    def hash_api_key(api_key: str) -> str:
        """Hash une API key pour stockage sécurisé"""
        return hashlib.sha256(api_key.encode()).hexdigest()

    @staticmethod
    def validate_api_key_format(api_key: str) -> bool:
        """Valide le format d'une API key"""
        # Format: sk_live_xxx ou sk_test_xxx
        pattern = r'^sk_(live|test)_[A-Za-z0-9]{32,}$'
        return bool(re.match(pattern, api_key))

