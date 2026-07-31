"""
Guardrails System - Protection multicouche pour les interactions IA
Implémente 3 couches: Input, Context, Output
"""

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ============================================================================
# TYPES
# ============================================================================


class GuardrailResult(str, Enum):
    PASS = "pass"
    WARN = "warn"
    BLOCK = "block"


class GuardrailCategory(str, Enum):
    INJECTION = "injection"
    PII = "pii"
    TOXICITY = "toxicity"
    TOPIC_BOUNDARY = "topic_boundary"
    LENGTH = "length"
    ENCODING = "encoding"
    TENANT_ISOLATION = "tenant_isolation"
    HALLUCINATION = "hallucination"
    XSS = "xss"
    CONFIDENCE = "confidence"


@dataclass
class GuardrailCheck:
    """Résultat d'une vérification guardrail"""

    category: GuardrailCategory
    result: GuardrailResult
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    severity: int = 0  # 0-100


@dataclass
class GuardrailReport:
    """Rapport complet des guardrails"""

    passed: bool
    checks: List[GuardrailCheck]
    blocked_categories: List[GuardrailCategory]
    warnings: List[str]
    sanitized_content: Optional[str] = None

    @property
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0

    def get_block_reason(self) -> Optional[str]:
        if not self.passed and self.checks:
            blocked = [c for c in self.checks if c.result == GuardrailResult.BLOCK]
            if blocked:
                return blocked[0].message
        return None


# ============================================================================
# GUARDRAIL INTERFACE
# ============================================================================


class Guardrail(ABC):
    """Interface pour tous les guardrails"""

    @property
    @abstractmethod
    def category(self) -> GuardrailCategory:
        pass

    @abstractmethod
    async def check(self, content: str, context: Dict[str, Any]) -> GuardrailCheck:
        pass


# ============================================================================
# INPUT GUARDRAILS
# ============================================================================


class LengthGuardrail(Guardrail):
    """Vérifie la longueur du message"""

    def __init__(self, max_length: int = 2000, min_length: int = 1):
        self.max_length = max_length
        self.min_length = min_length

    @property
    def category(self) -> GuardrailCategory:
        return GuardrailCategory.LENGTH

    async def check(self, content: str, context: Dict[str, Any]) -> GuardrailCheck:
        length = len(content)

        if length < self.min_length:
            return GuardrailCheck(
                category=self.category,
                result=GuardrailResult.BLOCK,
                message="Message too short",
                details={"length": length, "min": self.min_length},
                severity=50,
            )

        if length > self.max_length:
            return GuardrailCheck(
                category=self.category,
                result=GuardrailResult.BLOCK,
                message=f"Message exceeds maximum length ({self.max_length} chars)",
                details={"length": length, "max": self.max_length},
                severity=60,
            )

        return GuardrailCheck(
            category=self.category,
            result=GuardrailResult.PASS,
            message="Length OK",
            details={"length": length},
        )


class EncodingGuardrail(Guardrail):
    """Vérifie l'encodage et les caractères suspects"""

    # Caractères unicode invisibles souvent utilisés pour injection
    SUSPICIOUS_CHARS = [
        "\u200b",  # Zero-width space
        "\u200c",  # Zero-width non-joiner
        "\u200d",  # Zero-width joiner
        "\u2060",  # Word joiner
        "\ufeff",  # BOM
        "\u00ad",  # Soft hyphen
    ]

    # Tags Unicode (souvent utilisés pour cacher des instructions)
    TAG_RANGE = range(0xE0000, 0xE007F + 1)

    @property
    def category(self) -> GuardrailCategory:
        return GuardrailCategory.ENCODING

    async def check(self, content: str, context: Dict[str, Any]) -> GuardrailCheck:
        suspicious_found = []

        # Vérifier caractères invisibles
        for char in self.SUSPICIOUS_CHARS:
            if char in content:
                suspicious_found.append(f"U+{ord(char):04X}")

        # Vérifier unicode tags
        for char in content:
            if ord(char) in self.TAG_RANGE:
                suspicious_found.append(f"TAG U+{ord(char):04X}")

        if suspicious_found:
            return GuardrailCheck(
                category=self.category,
                result=GuardrailResult.BLOCK,
                message="Suspicious characters detected",
                details={"chars": suspicious_found[:5]},  # Limiter pour log
                severity=90,
            )

        return GuardrailCheck(
            category=self.category, result=GuardrailResult.PASS, message="Encoding OK"
        )


class PromptInjectionGuardrail(Guardrail):
    """Détection avancée des tentatives de prompt injection"""

    # Patterns niveau 1: Mots-clés directs (haute confiance)
    DIRECT_PATTERNS = [
        r"ignore\s+(all\s+)?(previous|above|prior)\s+(instructions?|rules?|prompts?)",
        r"disregard\s+(all\s+)?(previous|above|prior)",
        r"forget\s+(everything|all|your)\s+(instructions?|rules?|training)",
        r"you\s+are\s+now\s+(a|an|the|DAN|STAN|DUDE)",
        r"pretend\s+(to\s+be|you\s+are)",
        r"act\s+as\s+(if|a|an)",
        r"roleplay\s+as",
        r"new\s+instructions?:",
        r"system\s*:\s*",
        r"\[system\]",
        r"<\s*system\s*>",
        r"```\s*system",  # Markdown code block with system
        r"---\s*\n\s*system",  # Horizontal rule followed by system
        r"jailbreak",
        r"\bDAN\b",  # DAN word boundary
        r"DAN\s+mode",
        r"\bDAN\b",  # DAN alone (Do Anything Now)
        r"do\s+anything\s+now",
        r"developer\s+mode",
        r"bypass\s+(safety|filter|rules?)",
        r"```\s*system",  # Markdown code block injection
    ]

    # Patterns niveau 2: Manipulation indirecte
    INDIRECT_PATTERNS = [
        r"what\s+(is|are)\s+your\s+(instructions?|rules?|prompt|system)",
        r"reveal\s+your\s+(prompt|instructions?|system)",
        r"show\s+me\s+your\s+(prompt|instructions?|system)",
        r"print\s+(your\s+)?(\w+\s+)?(prompt|instructions?|system|initial)",
        r"repeat\s+(back\s+)?(your\s+)?(instructions?|prompt)",
        r"tell\s+me\s+(your|the)\s+(rules?|instructions?|prompt)",
        r"output\s+your\s+(prompt|instructions?|system)",
        r"display\s+your\s+(prompt|instructions?|system)",
        r"your\s+initial\s+instructions",
        r"(the\s+)?rules?\s+you\s+follow",
        r"repeat\s+.{0,20}verbatim",
    ]

    # Patterns niveau 3: Encodage/Obfuscation
    OBFUSCATION_PATTERNS = [
        r"base64\s*:",
        r"decode\s+this",
        r"rot13",
        r"hex\s*:",
        r"\\x[0-9a-f]{2}",  # Hex escape sequences
        r"&#\d+;",  # HTML entities
        r"\\u[0-9a-f]{4}",  # Unicode escapes
    ]

    def __init__(self, sensitivity: str = "high"):
        self.sensitivity = sensitivity
        self._compile_patterns()

    def _compile_patterns(self):
        flags = re.IGNORECASE | re.MULTILINE
        self.direct_regex = [re.compile(p, flags) for p in self.DIRECT_PATTERNS]
        self.indirect_regex = [re.compile(p, flags) for p in self.INDIRECT_PATTERNS]
        self.obfuscation_regex = [re.compile(p, flags) for p in self.OBFUSCATION_PATTERNS]

    @property
    def category(self) -> GuardrailCategory:
        return GuardrailCategory.INJECTION

    async def check(self, content: str, context: Dict[str, Any]) -> GuardrailCheck:
        matches = []
        max_severity = 0

        # Niveau 1: Patterns directs (block immédiat)
        for regex in self.direct_regex:
            if regex.search(content):
                matches.append(("direct", regex.pattern[:30]))
                max_severity = max(max_severity, 100)

        # Si pattern direct trouvé, bloquer immédiatement
        if max_severity >= 100:
            logger.warning(
                "Prompt injection blocked",
                extra={"patterns": matches, "content_preview": content[:100]},
            )
            return GuardrailCheck(
                category=self.category,
                result=GuardrailResult.BLOCK,
                message="Potential prompt injection detected",
                details={"patterns": matches},
                severity=100,
            )

        # Niveau 2: Patterns indirects
        for regex in self.indirect_regex:
            if regex.search(content):
                matches.append(("indirect", regex.pattern[:30]))
                max_severity = max(max_severity, 70)

        # Niveau 3: Obfuscation
        for regex in self.obfuscation_regex:
            if regex.search(content):
                matches.append(("obfuscation", regex.pattern[:30]))
                max_severity = max(max_severity, 80)

        # Décision basée sur sensibilité
        if self.sensitivity == "high" and max_severity >= 70:
            return GuardrailCheck(
                category=self.category,
                result=GuardrailResult.BLOCK,
                message="Suspicious content detected",
                details={"patterns": matches, "severity": max_severity},
                severity=max_severity,
            )
        elif max_severity >= 80:
            return GuardrailCheck(
                category=self.category,
                result=GuardrailResult.BLOCK,
                message="Suspicious content detected",
                details={"patterns": matches},
                severity=max_severity,
            )
        elif max_severity >= 50:
            return GuardrailCheck(
                category=self.category,
                result=GuardrailResult.WARN,
                message="Content flagged for review",
                details={"patterns": matches},
                severity=max_severity,
            )

        return GuardrailCheck(
            category=self.category,
            result=GuardrailResult.PASS,
            message="No injection patterns detected",
        )


class PIIDetectionGuardrail(Guardrail):
    """Détection des données personnelles sensibles"""

    PII_PATTERNS = {
        "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
        "phone_fr": r"\b(?:(?:\+|00)33|0)\s*[1-9](?:[\s.-]*\d{2}){4}\b",
        "phone_intl": r"\b\+?[1-9]\d{1,14}\b",
        "credit_card": r"\b(?:\d{4}[-\s]?){3}\d{4}\b",
        "ssn_fr": r"\b[12]\d{2}(?:0[1-9]|1[0-2])\d{2}\d{3}\d{3}\d{2}\b",
        "iban": r"\b[A-Z]{2}\d{2}[A-Z0-9]{4,30}\b",
    }

    def __init__(self, allow_email: bool = True):
        self.allow_email = allow_email
        self._compile_patterns()

    def _compile_patterns(self):
        self.regexes = {
            name: re.compile(pattern, re.IGNORECASE) for name, pattern in self.PII_PATTERNS.items()
        }

    @property
    def category(self) -> GuardrailCategory:
        return GuardrailCategory.PII

    async def check(self, content: str, context: Dict[str, Any]) -> GuardrailCheck:
        found_pii = []

        for pii_type, regex in self.regexes.items():
            if pii_type == "email" and self.allow_email:
                continue

            matches = regex.findall(content)
            if matches:
                found_pii.append({"type": pii_type, "count": len(matches)})

        if found_pii:
            # PII sensible (carte, SSN) = block
            sensitive_types = ["credit_card", "ssn_fr", "iban"]
            has_sensitive = any(p["type"] in sensitive_types for p in found_pii)

            if has_sensitive:
                return GuardrailCheck(
                    category=self.category,
                    result=GuardrailResult.BLOCK,
                    message="Sensitive PII detected",
                    details={"pii_found": found_pii},
                    severity=95,
                )

            return GuardrailCheck(
                category=self.category,
                result=GuardrailResult.WARN,
                message="PII detected in message",
                details={"pii_found": found_pii},
                severity=50,
            )

        return GuardrailCheck(
            category=self.category, result=GuardrailResult.PASS, message="No PII detected"
        )


# ============================================================================
# CONTEXT GUARDRAILS
# ============================================================================


class TenantIsolationGuardrail(Guardrail):
    """Vérifie l'isolation des données entre tenants"""

    @property
    def category(self) -> GuardrailCategory:
        return GuardrailCategory.TENANT_ISOLATION

    async def check(self, content: str, context: Dict[str, Any]) -> GuardrailCheck:
        tenant_id = context.get("tenant_id")
        retrieved_docs = context.get("retrieved_documents", [])

        if not tenant_id:
            return GuardrailCheck(
                category=self.category,
                result=GuardrailResult.BLOCK,
                message="Missing tenant context",
                severity=100,
            )

        # Vérifier que tous les documents récupérés appartiennent au bon tenant
        foreign_docs = []
        for doc in retrieved_docs:
            doc_tenant = doc.get("metadata", {}).get("tenant_id")
            if doc_tenant and str(doc_tenant) != str(tenant_id):
                foreign_docs.append(doc.get("id", "unknown"))

        if foreign_docs:
            logger.error(
                "Tenant isolation violation detected",
                extra={"tenant_id": tenant_id, "foreign_docs": foreign_docs},
            )
            return GuardrailCheck(
                category=self.category,
                result=GuardrailResult.BLOCK,
                message="Tenant isolation violation",
                details={"foreign_documents": len(foreign_docs)},
                severity=100,
            )

        return GuardrailCheck(
            category=self.category, result=GuardrailResult.PASS, message="Tenant isolation verified"
        )


class TopicBoundaryGuardrail(Guardrail):
    """Vérifie que la conversation reste dans le domaine e-commerce"""

    # Topics autorisés
    ALLOWED_TOPICS = [
        "product",
        "order",
        "shipping",
        "delivery",
        "return",
        "refund",
        "payment",
        "price",
        "discount",
        "coupon",
        "stock",
        "availability",
        "size",
        "color",
        "recommendation",
        "review",
        "warranty",
        "support",
    ]

    # Topics interdits
    FORBIDDEN_TOPICS = [
        r"(?:\b[eé]lections?\b|\bvote[rz]?\b|\bpr[eé]sident\b|\bgouvernement\b|\bparti\s+politiq)",
        r"\b(religion|dieu|allah|jesus|bouddha)\b",
        r"\b(drogue|cannabis|cocaine|hero[ïi]ne)\b",
        r"\b(arme|fusil|pistolet|bombe)\b",
        r"\b(hack|pirater|virus|malware)\b",
        r"\b(suicide|se\s+tuer|mourir)\b",
        r"\b(sexe|porn|xxx|adult)\b",
    ]

    def __init__(self):
        self.forbidden_regex = [re.compile(p, re.IGNORECASE) for p in self.FORBIDDEN_TOPICS]

    @property
    def category(self) -> GuardrailCategory:
        return GuardrailCategory.TOPIC_BOUNDARY

    async def check(self, content: str, context: Dict[str, Any]) -> GuardrailCheck:
        # Vérifier topics interdits
        for regex in self.forbidden_regex:
            if regex.search(content):
                return GuardrailCheck(
                    category=self.category,
                    result=GuardrailResult.BLOCK,
                    message="Topic outside allowed domain",
                    details={"reason": "forbidden_topic"},
                    severity=80,
                )

        return GuardrailCheck(
            category=self.category, result=GuardrailResult.PASS, message="Topic within bounds"
        )


# ============================================================================
# OUTPUT GUARDRAILS
# ============================================================================


class OutputPIIMaskingGuardrail(Guardrail):
    """Masque les PII dans la sortie LLM"""

    @property
    def category(self) -> GuardrailCategory:
        return GuardrailCategory.PII

    async def check(self, content: str, context: Dict[str, Any]) -> GuardrailCheck:
        # Réutilise la logique de détection
        pii_guard = PIIDetectionGuardrail(allow_email=False)
        result = await pii_guard.check(content, context)

        if result.result != GuardrailResult.PASS:
            # Masquer les PII détectés
            masked_content = self._mask_pii(content)
            return GuardrailCheck(
                category=self.category,
                result=GuardrailResult.WARN,
                message="PII masked in output",
                details={"masked": True, "masked_content": masked_content},
            )

        return result

    def _mask_pii(self, content: str) -> str:
        """Masque les PII détectés"""
        patterns = {
            "email": (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "[EMAIL]"),
            "phone": (
                r"\b(?:\+|00)?\d{1,3}[\s.-]?\d{2,4}[\s.-]?\d{2,4}[\s.-]?\d{2,4}\b",
                "[PHONE]",
            ),
            "card": (r"\b(?:\d{4}[-\s]?){3}\d{4}\b", "[CARD]"),
        }

        masked = content
        for name, (pattern, replacement) in patterns.items():
            masked = re.sub(pattern, replacement, masked, flags=re.IGNORECASE)

        return masked


class XSSSanitizationGuardrail(Guardrail):
    """Protège contre les injections XSS dans la sortie"""

    XSS_PATTERNS = [
        r"<script[^>]*>.*?</script>",
        r"javascript:",
        r"on\w+\s*=",
        r"<iframe[^>]*>",
        r"<object[^>]*>",
        r"<embed[^>]*>",
    ]

    def __init__(self):
        self.patterns = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in self.XSS_PATTERNS]

    @property
    def category(self) -> GuardrailCategory:
        return GuardrailCategory.XSS

    async def check(self, content: str, context: Dict[str, Any]) -> GuardrailCheck:
        for pattern in self.patterns:
            if pattern.search(content):
                # Sanitize
                sanitized = self._sanitize(content)
                return GuardrailCheck(
                    category=self.category,
                    result=GuardrailResult.WARN,
                    message="XSS patterns sanitized",
                    details={"sanitized_content": sanitized},
                )

        return GuardrailCheck(
            category=self.category, result=GuardrailResult.PASS, message="No XSS patterns detected"
        )

    def _sanitize(self, content: str) -> str:
        """Sanitize le contenu HTML"""
        import html

        # Escape HTML entities
        sanitized = html.escape(content)
        return sanitized


class HallucinationDetectionGuardrail(Guardrail):
    """Détecte les potentielles hallucinations en vérifiant le grounding"""

    # Phrases indiquant une affirmation sans source
    CONFIDENCE_MARKERS = [
        r"je suis certain",
        r"il est évident",
        r"tout le monde sait",
        r"c'est un fait",
        r"sans aucun doute",
        r"définitivement",
        r"absolument",
    ]

    @property
    def category(self) -> GuardrailCategory:
        return GuardrailCategory.HALLUCINATION

    async def check(self, content: str, context: Dict[str, Any]) -> GuardrailCheck:
        retrieved_docs = context.get("retrieved_documents", [])
        content_lower = content.lower()

        warnings = []

        # Check 1: Réponse sans documents de contexte
        if not retrieved_docs and len(content) > 200:
            warnings.append("Long response without source documents")

        # Check 2: Affirmations trop confiantes
        for marker in self.CONFIDENCE_MARKERS:
            if re.search(marker, content_lower, re.IGNORECASE):
                warnings.append(f"Overconfident assertion: {marker}")
                break

        # Check 3: Mentions de prix/stock sans source
        price_pattern = r"\d+[.,]\d{2}\s*€"
        if re.search(price_pattern, content):
            # Vérifier si le prix est dans les documents
            price_in_docs = any(
                re.search(price_pattern, doc.get("content", "")) for doc in retrieved_docs
            )
            if not price_in_docs:
                warnings.append("Price mentioned without source document")

        if warnings:
            return GuardrailCheck(
                category=self.category,
                result=GuardrailResult.WARN,
                message="Potential hallucination detected",
                details={"warnings": warnings},
                severity=60,
            )

        return GuardrailCheck(
            category=self.category, result=GuardrailResult.PASS, message="Response appears grounded"
        )


class ConfidenceCheckGuardrail(Guardrail):
    """Vérifie la confiance du modèle et ajoute des disclaimers si nécessaire"""

    UNCERTAINTY_MARKERS = [
        "je ne suis pas sûr",
        "je ne sais pas",
        "je n'ai pas cette information",
        "il faudrait vérifier",
        "peut-être",
        "possiblement",
        "il se peut que",
    ]

    @property
    def category(self) -> GuardrailCategory:
        return GuardrailCategory.CONFIDENCE

    async def check(self, content: str, context: Dict[str, Any]) -> GuardrailCheck:
        content_lower = content.lower()

        uncertainty_detected = any(marker in content_lower for marker in self.UNCERTAINTY_MARKERS)

        intent_confidence = context.get("intent_confidence", 1.0)

        if intent_confidence < 0.5 or uncertainty_detected:
            return GuardrailCheck(
                category=self.category,
                result=GuardrailResult.WARN,
                message="Low confidence response",
                details={
                    "intent_confidence": intent_confidence,
                    "uncertainty_markers": uncertainty_detected,
                    "suggestion": "Add disclaimer or offer human handoff",
                },
                severity=40,
            )

        return GuardrailCheck(
            category=self.category, result=GuardrailResult.PASS, message="Confidence acceptable"
        )


# ============================================================================
# GUARDRAILS ORCHESTRATOR
# ============================================================================


class GuardrailsOrchestrator:
    """
    Orchestrateur des guardrails avec exécution en pipeline.
    Applique les guardrails dans l'ordre: Input → Context → Output
    """

    def __init__(self):
        # Guardrails d'entrée (appliqués au message utilisateur)
        self.input_guardrails: List[Guardrail] = [
            LengthGuardrail(),
            EncodingGuardrail(),
            PromptInjectionGuardrail(sensitivity="high"),
            PIIDetectionGuardrail(allow_email=True),
        ]

        # Guardrails de contexte (appliqués après retrieval)
        self.context_guardrails: List[Guardrail] = [
            TenantIsolationGuardrail(),
            TopicBoundaryGuardrail(),
        ]

        # Guardrails de sortie (appliqués à la réponse LLM)
        self.output_guardrails: List[Guardrail] = [
            OutputPIIMaskingGuardrail(),
            XSSSanitizationGuardrail(),
            HallucinationDetectionGuardrail(),
            ConfidenceCheckGuardrail(),
        ]

    async def check_input(self, content: str, context: Dict[str, Any] = None) -> GuardrailReport:
        """Vérifie les guardrails d'entrée"""
        context = context or {}
        return await self._run_guardrails(self.input_guardrails, content, context)

    async def check_context(self, content: str, context: Dict[str, Any]) -> GuardrailReport:
        """Vérifie les guardrails de contexte"""
        return await self._run_guardrails(self.context_guardrails, content, context)

    async def check_output(self, content: str, context: Dict[str, Any]) -> GuardrailReport:
        """Vérifie les guardrails de sortie"""
        return await self._run_guardrails(self.output_guardrails, content, context)

    async def _run_guardrails(
        self, guardrails: List[Guardrail], content: str, context: Dict[str, Any]
    ) -> GuardrailReport:
        """Exécute une liste de guardrails"""
        checks = []
        blocked_categories = []
        warnings = []
        sanitized_content = content

        for guardrail in guardrails:
            try:
                check = await guardrail.check(content, context)
                checks.append(check)

                if check.result == GuardrailResult.BLOCK:
                    blocked_categories.append(check.category)
                    logger.warning(
                        f"Guardrail blocked: {check.category.value}",
                        extra={"guardrail_message": check.message, "details": check.details},
                    )
                elif check.result == GuardrailResult.WARN:
                    warnings.append(check.message)
                    # Appliquer la sanitization si fournie
                    if "sanitized_content" in check.details:
                        sanitized_content = check.details["sanitized_content"]
                    elif "masked_content" in check.details:
                        sanitized_content = check.details["masked_content"]

            except Exception as e:
                logger.error(f"Guardrail {guardrail.category.value} failed: {e}")
                # En cas d'erreur, on continue mais on warn
                warnings.append(f"Guardrail check failed: {guardrail.category.value}")

        passed = len(blocked_categories) == 0

        return GuardrailReport(
            passed=passed,
            checks=checks,
            blocked_categories=blocked_categories,
            warnings=warnings,
            sanitized_content=sanitized_content if sanitized_content != content else None,
        )

    async def full_check(
        self, input_content: str, output_content: str, context: Dict[str, Any]
    ) -> Tuple[GuardrailReport, GuardrailReport, GuardrailReport]:
        """Exécute les 3 couches de guardrails"""
        input_report = await self.check_input(input_content, context)
        context_report = await self.check_context(input_content, context)
        output_report = await self.check_output(output_content, context)

        return input_report, context_report, output_report


# Singleton pour accès global
guardrails = GuardrailsOrchestrator()
