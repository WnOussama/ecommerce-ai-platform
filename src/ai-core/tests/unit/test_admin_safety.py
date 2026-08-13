"""
Tests — AdminAISafetySystem

Covers:
- Risk level classification
- Action definitions and constraints
- Dry run simulation
- Pending action lifecycle
- DryRunResult validation
- Approval type mapping
"""

import pytest

from app.core.security.admin_safety import (
    ACTION_DEFINITIONS,
    ActionDefinition,
    ActionStatus,
    AdminAISafetySystem,
    ApprovalType,
    DryRunResult,
    PendingAction,
    RiskLevel,
)

# =============================================================================
# RISK LEVEL & ENUMS
# =============================================================================


class TestRiskLevel:
    """RiskLevel enum must cover all expected levels."""

    def test_all_levels_exist(self):
        assert RiskLevel.LOW == "low"
        assert RiskLevel.MEDIUM == "medium"
        assert RiskLevel.HIGH == "high"
        assert RiskLevel.CRITICAL == "critical"

    def test_is_string_enum(self):
        assert isinstance(RiskLevel.LOW, str)
        assert RiskLevel.LOW.value == "low"


class TestActionStatus:
    """ActionStatus covers the full lifecycle."""

    @pytest.mark.parametrize(
        "status",
        [
            "draft",
            "pending_confirmation",
            "pending_human_approval",
            "approved",
            "executing",
            "completed",
            "failed",
            "rolled_back",
            "rejected",
            "expired",
        ],
    )
    def test_all_statuses_exist(self, status):
        assert ActionStatus(status).value == status


class TestApprovalType:
    def test_none_for_readonly(self):
        assert ApprovalType.NONE == "none"

    def test_human_for_critical(self):
        assert ApprovalType.HUMAN == "human"


# =============================================================================
# ACTION DEFINITIONS
# =============================================================================


class TestActionDefinitions:
    """Verify all pre-configured actions have correct risk mapping."""

    def test_get_analytics_is_low_risk(self):
        defn = ACTION_DEFINITIONS["get_analytics"]
        assert defn.risk_level == RiskLevel.LOW
        assert defn.approval_type == ApprovalType.NONE
        assert defn.supports_rollback is False

    def test_generate_report_is_low_risk(self):
        defn = ACTION_DEFINITIONS["generate_report"]
        assert defn.risk_level == RiskLevel.LOW

    def test_bulk_coupons_is_high_risk(self):
        defn = ACTION_DEFINITIONS["generate_bulk_coupons"]
        assert defn.risk_level == RiskLevel.HIGH
        assert defn.approval_type == ApprovalType.DOUBLE
        assert defn.requires_reason is True
        assert defn.confirmation_delay_seconds > 0

    def test_delete_customer_data_is_critical(self):
        defn = ACTION_DEFINITIONS["delete_customer_data"]
        assert defn.risk_level == RiskLevel.CRITICAL
        assert defn.approval_type == ApprovalType.HUMAN
        assert defn.requires_reason is True
        assert defn.rollback_window_hours > 0

    def test_update_prices_has_value_limit(self):
        defn = ACTION_DEFINITIONS["update_product_prices"]
        assert abs(defn.max_value_change_percent - 25.0) < 0.01
        assert defn.max_affected_items == 100

    def test_all_definitions_have_required_fields(self):
        for name, defn in ACTION_DEFINITIONS.items():
            assert defn.name, f"{name} missing name"
            assert defn.description, f"{name} missing description"
            assert isinstance(defn.risk_level, RiskLevel), f"{name} bad risk_level"
            assert isinstance(defn.approval_type, ApprovalType), f"{name} bad approval_type"


class TestActionDefinitionDefaults:
    """Custom ActionDefinition with defaults."""

    def test_defaults(self):
        defn = ActionDefinition(
            name="test_action",
            description="A test action",
            risk_level=RiskLevel.LOW,
        )
        assert defn.max_affected_items == 100
        assert abs(defn.max_value_change_percent - 25.0) < 0.01
        assert defn.cooldown_seconds == 0
        assert defn.supports_rollback is True
        assert defn.rollback_window_hours == 24
        assert defn.requires_reason is False


# =============================================================================
# DRY RUN RESULT
# =============================================================================


class TestDryRunResult:
    """DryRunResult data structure validation."""

    def test_is_valid_when_no_errors(self):
        dr = DryRunResult(action_id="test", action_name="test_action")
        assert dr.is_valid is True

    def test_is_invalid_with_errors(self):
        dr = DryRunResult(
            action_id="test",
            action_name="test_action",
            validation_errors=["Something went wrong"],
        )
        assert dr.is_valid is False

    def test_to_dict_contains_required_keys(self):
        dr = DryRunResult(
            action_id="abc-123",
            action_name="generate_bulk_coupons",
            affected_items_count=50,
            risk_level=RiskLevel.HIGH,
        )
        d = dr.to_dict()
        assert d["action_id"] == "abc-123"
        assert d["action_name"] == "generate_bulk_coupons"
        assert d["affected_items_count"] == 50
        assert d["risk_level"] == "high"
        assert d["is_valid"] is True
        assert "created_at" in d

    def test_to_dict_truncates_preview(self):
        """Preview should be limited to 10 items."""
        dr = DryRunResult(
            action_id="test",
            action_name="test",
            affected_items_preview=[{"id": i} for i in range(20)],
        )
        d = dr.to_dict()
        assert len(d["affected_items_preview"]) == 10


# =============================================================================
# PENDING ACTION
# =============================================================================


class TestPendingAction:
    """PendingAction lifecycle data structure."""

    def test_default_status_is_draft(self):
        pa = PendingAction()
        assert pa.status == ActionStatus.DRAFT

    def test_has_auto_generated_id(self):
        pa = PendingAction()
        assert pa.id  # UUID is not empty
        assert len(pa.id) > 10

    def test_two_actions_have_different_ids(self):
        pa1 = PendingAction()
        pa2 = PendingAction()
        assert pa1.id != pa2.id

    def test_stores_tenant_id(self):
        pa = PendingAction(tenant_id="tenant_abc12345")
        assert pa.tenant_id == "tenant_abc12345"

    def test_supports_rollback_data(self):
        pa = PendingAction(rollback_data={"old_price": 100})
        assert pa.rollback_data["old_price"] == 100


# =============================================================================
# ADMIN AI SAFETY SYSTEM
# =============================================================================


class TestAdminAISafetySystem:
    """AdminAISafetySystem orchestration tests."""

    @pytest.fixture
    def safety(self):
        return AdminAISafetySystem()

    @pytest.mark.asyncio
    async def test_dry_run_unknown_action(self, safety):
        result = await safety.create_dry_run(
            tenant_id="tenant_abc12345",
            action_name="nonexistent_action",
            parameters={},
            initiated_by="admin_user",
        )
        assert result.is_valid is False
        assert any("Unknown" in e for e in result.validation_errors)

    @pytest.mark.asyncio
    async def test_dry_run_known_action(self, safety):
        result = await safety.create_dry_run(
            tenant_id="tenant_abc12345",
            action_name="get_analytics",
            parameters={},
            initiated_by="admin_user",
        )
        assert result.action_name == "get_analytics"
        assert result.action_id  # has an ID

    @pytest.mark.asyncio
    async def test_dry_run_bulk_coupons_validates_limits(self, safety):
        result = await safety.create_dry_run(
            tenant_id="tenant_abc12345",
            action_name="generate_bulk_coupons",
            parameters={"max_count": 5000, "discount_percent": 10},
            initiated_by="admin_user",
        )
        # Should have validation error because 5000 > max_affected_items
        assert result.is_valid is False or len(result.warnings) > 0

    @pytest.mark.asyncio
    async def test_dry_run_bulk_coupons_accepts_string_parameters(self, safety):
        """Regression: parameters arrive over HTTP as JSON, and some callers
        (e.g. the backoffice's KeyValue form field) only ever produce
        strings - comparing "2" > 1000 raised TypeError, found by actually
        driving the admin UI rather than by mocking the request."""
        result = await safety.create_dry_run(
            tenant_id="tenant_abc12345",
            action_name="generate_bulk_coupons",
            parameters={"max_count": "2", "discount_percent": "10"},
            initiated_by="admin_user",
        )
        assert result.is_valid is True
        assert result.affected_items_count == 2

    @pytest.mark.asyncio
    async def test_dry_run_update_prices_accepts_string_adjustment(self, safety):
        result = await safety.create_dry_run(
            tenant_id="tenant_abc12345",
            action_name="update_product_prices",
            parameters={"product_ids": ["p1"], "adjustment_percent": "10"},
            initiated_by="admin_user",
        )
        assert result.is_valid is True

    @pytest.mark.asyncio
    async def test_dry_run_sets_expiration(self, safety):
        result = await safety.create_dry_run(
            tenant_id="tenant_abc12345",
            action_name="get_analytics",
            parameters={},
            initiated_by="admin_user",
        )
        assert result.expires_at is not None
        # expires_at should be in the future (approx comparison)
        assert result.expires_at > result.created_at

    @pytest.mark.asyncio
    async def test_dry_run_returns_risk_level(self, safety):
        result = await safety.create_dry_run(
            tenant_id="tenant_abc12345",
            action_name="generate_bulk_coupons",
            parameters={"max_count": 10, "discount_percent": 5},
            initiated_by="admin_user",
        )
        assert isinstance(result.risk_level, RiskLevel)
