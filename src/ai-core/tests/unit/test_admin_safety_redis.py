"""
Tests pour le stockage Redis de AdminAISafetySystem.

Régression: AdminAISafetySystem.__init__ acceptait déjà un cache_client
mais ne s'en servait jamais - request_confirmation/confirm_action/
approve_action/execute_action/rollback_action lisaient et écrivaient
uniquement le dict self._pending_actions en mémoire. Toute action pending
était donc perdue au redémarrage du process ou invisible aux autres
workers, malgré le paramètre laissant croire le contraire.

Ces tests utilisent un faux client Redis async minimal (dict + listes en
mémoire, mêmes méthodes que redis.asyncio) et prouvent que l'état
survit à la création d'une toute nouvelle instance de
AdminAISafetySystem partageant le même cache - ce qu'un simple dict
d'instance ne pourrait jamais faire.
"""

import pytest

from app.core.security.admin_safety import ActionStatus, AdminAISafetySystem, ApprovalType


class FakeAsyncRedis:
    """Reproduit juste assez de l'API redis.asyncio pour ces tests."""

    def __init__(self):
        self._store: dict[str, str] = {}
        self._lists: dict[str, list[str]] = {}

    async def set(self, key, value, ex=None):
        self._store[key] = value

    async def get(self, key):
        return self._store.get(key)

    async def delete(self, key):
        self._store.pop(key, None)

    async def lpush(self, key, value):
        self._lists.setdefault(key, []).insert(0, value)

    async def lrem(self, key, count, value):
        if key in self._lists:
            self._lists[key] = [v for v in self._lists[key] if v != value]

    async def lrange(self, key, start, end):
        values = self._lists.get(key, [])
        if end == -1:
            return values[start:]
        return values[start : end + 1]


@pytest.mark.asyncio
class TestAdminAISafetyRedisBacking:
    async def test_request_confirmation_persists_to_cache(self):
        cache = FakeAsyncRedis()
        safety = AdminAISafetySystem(cache_client=cache)

        dry_run = await safety.create_dry_run(
            tenant_id="t1",
            action_name="generate_bulk_coupons",
            parameters={"max_count": 10, "discount_percent": 10},
            initiated_by="admin_1",
        )
        pending = await safety.request_confirmation(
            dry_run=dry_run,
            tenant_id="t1",
            parameters={"max_count": 10, "discount_percent": 10},
            initiated_by="admin_1",
            reason="test",
        )

        assert cache._store  # something was actually written to the fake redis
        assert f"admin_pending_action:{pending.id}" in cache._store

    async def test_state_survives_a_new_instance_sharing_the_cache(self):
        """The real regression test: a second, independent AdminAISafetySystem
        instance (simulating a new process/worker) must see the same
        pending action - proving state isn't trapped in a per-instance dict."""
        cache = FakeAsyncRedis()
        safety_a = AdminAISafetySystem(cache_client=cache)

        dry_run = await safety_a.create_dry_run(
            tenant_id="t1",
            action_name="suggest_marketing_strategy",
            parameters={},
            initiated_by="admin_1",
        )
        pending = await safety_a.request_confirmation(
            dry_run=dry_run,
            tenant_id="t1",
            parameters={},
            initiated_by="admin_1",
        )

        safety_b = AdminAISafetySystem(cache_client=cache)
        confirmed, error = await safety_b.confirm_action(
            action_id=pending.id,
            confirmation_token=pending.confirmation_token,
            confirmed_by="admin_1",
            tenant_id="t1",
        )

        assert error is None
        assert confirmed.status == ActionStatus.APPROVED

    async def test_medium_risk_full_flow_confirm_then_execute(self):
        cache = FakeAsyncRedis()
        safety = AdminAISafetySystem(cache_client=cache)

        dry_run = await safety.create_dry_run(
            tenant_id="t1",
            action_name="suggest_marketing_strategy",
            parameters={},
            initiated_by="admin_1",
        )
        pending = await safety.request_confirmation(
            dry_run=dry_run, tenant_id="t1", parameters={}, initiated_by="admin_1"
        )
        assert pending.approval_type == ApprovalType.SIMPLE

        confirmed, error = await safety.confirm_action(
            pending.id, pending.confirmation_token, "admin_1", tenant_id="t1"
        )
        assert error is None
        assert confirmed.status == ActionStatus.APPROVED

        async def executor(action):
            return {"executed": True, "action_name": action.action_name}

        result, exec_error = await safety.execute_action(pending.id, executor, tenant_id="t1")
        assert exec_error is None
        assert result == {"executed": True, "action_name": "suggest_marketing_strategy"}

        final = await safety._get_pending(pending.id)
        assert final.status == ActionStatus.COMPLETED

    async def test_high_risk_requires_two_confirmations(self):
        cache = FakeAsyncRedis()
        safety = AdminAISafetySystem(cache_client=cache)

        dry_run = await safety.create_dry_run(
            tenant_id="t1",
            action_name="generate_bulk_coupons",
            parameters={"max_count": 10, "discount_percent": 10},
            initiated_by="admin_1",
        )
        assert dry_run.is_valid
        pending = await safety.request_confirmation(
            dry_run=dry_run,
            tenant_id="t1",
            parameters={"max_count": 10, "discount_percent": 10},
            initiated_by="admin_1",
            reason="required for HIGH risk",
        )
        assert pending.approval_type == ApprovalType.DOUBLE

        after_first, error = await safety.confirm_action(
            pending.id, pending.confirmation_token, "admin_1", tenant_id="t1"
        )
        assert error is None
        assert after_first.status == ActionStatus.PENDING_CONFIRMATION  # still waiting

        # confirmation_delay_seconds is 60 for this action, so the second
        # confirmation must report the wait rather than approve immediately.
        after_second, error = await safety.confirm_action(
            pending.id, pending.confirmation_token, "admin_1", tenant_id="t1"
        )
        assert error is not None
        assert "wait" in error.lower()

    async def test_human_approval_queue_uses_redis_list(self):
        cache = FakeAsyncRedis()
        safety = AdminAISafetySystem(cache_client=cache)

        dry_run = await safety.create_dry_run(
            tenant_id="t1",
            action_name="delete_customer_data",
            parameters={},
            initiated_by="admin_1",
        )
        pending = await safety.request_confirmation(
            dry_run=dry_run,
            tenant_id="t1",
            parameters={},
            initiated_by="admin_1",
            reason="GDPR request",
        )
        assert pending.status == ActionStatus.PENDING_HUMAN_APPROVAL
        assert cache._lists["admin_human_queue:t1"] == [pending.id]

        queue = await safety.get_pending_approvals("t1")
        assert [p.id for p in queue] == [pending.id]

        approved, error = await safety.approve_action(
            pending.id, "admin_2", "looks fine", tenant_id="t1"
        )
        assert error is None
        assert approved.status == ActionStatus.APPROVED
        assert cache._lists["admin_human_queue:t1"] == []

    async def test_approver_must_differ_from_initiator(self):
        cache = FakeAsyncRedis()
        safety = AdminAISafetySystem(cache_client=cache)

        dry_run = await safety.create_dry_run(
            tenant_id="t1",
            action_name="delete_customer_data",
            parameters={},
            initiated_by="admin_1",
        )
        pending = await safety.request_confirmation(
            dry_run=dry_run,
            tenant_id="t1",
            parameters={},
            initiated_by="admin_1",
            reason="GDPR request",
        )

        _, error = await safety.approve_action(
            pending.id, "admin_1", "self-approval attempt", tenant_id="t1"
        )
        assert error == "Approver must be different from initiator"

    async def test_falls_back_to_in_memory_dict_without_cache_client(self):
        """No cache_client => behaves exactly as before this change."""
        safety = AdminAISafetySystem()

        dry_run = await safety.create_dry_run(
            tenant_id="t1",
            action_name="suggest_marketing_strategy",
            parameters={},
            initiated_by="a",
        )
        pending = await safety.request_confirmation(
            dry_run=dry_run, tenant_id="t1", parameters={}, initiated_by="a"
        )

        assert pending.id in safety._pending_actions
        confirmed, error = await safety.confirm_action(
            pending.id, pending.confirmation_token, "a", tenant_id="t1"
        )
        assert error is None
        assert confirmed.status == ActionStatus.APPROVED

    async def _pending_for(self, tenant_id, action_name="suggest_marketing_strategy"):
        cache = FakeAsyncRedis()
        safety = AdminAISafetySystem(cache_client=cache)
        dry_run = await safety.create_dry_run(
            tenant_id=tenant_id, action_name=action_name, parameters={}, initiated_by="admin_1"
        )
        pending = await safety.request_confirmation(
            dry_run=dry_run, tenant_id=tenant_id, parameters={}, initiated_by="admin_1", reason="r"
        )
        return safety, pending

    async def test_another_tenant_cannot_confirm_even_with_the_valid_token(self):
        safety, pending = await self._pending_for("t1")

        confirmed, error = await safety.confirm_action(
            pending.id, pending.confirmation_token, "attacker", tenant_id="t2"
        )

        assert confirmed is None
        assert error == "Action not found"  # same answer as a missing action: no oracle
        # untouched for its real owner
        assert (await safety._get_pending(pending.id)).status == ActionStatus.PENDING_CONFIRMATION

    async def test_another_tenant_cannot_reject_a_pending_action(self):
        safety, pending = await self._pending_for("t1")

        rejected, error = await safety.reject_action(pending.id, "attacker", "nope", tenant_id="t2")

        assert rejected is None
        assert error == "Action not found"
        assert (await safety._get_pending(pending.id)).status == ActionStatus.PENDING_CONFIRMATION

    async def test_another_tenant_cannot_approve_a_critical_action(self):
        safety, pending = await self._pending_for("t1", "delete_customer_data")
        assert pending.status == ActionStatus.PENDING_HUMAN_APPROVAL

        approved, error = await safety.approve_action(
            pending.id, "attacker", "approved", tenant_id="t2"
        )

        assert approved is None
        assert error == "Action not found"
        assert (await safety._get_pending(pending.id)).status == ActionStatus.PENDING_HUMAN_APPROVAL

    async def test_another_tenant_cannot_execute_or_roll_back(self):
        safety, pending = await self._pending_for("t1")
        await safety.confirm_action(pending.id, pending.confirmation_token, "a", tenant_id="t1")

        async def executor(action):
            raise AssertionError("must never run for another tenant")

        result, error = await safety.execute_action(pending.id, executor, tenant_id="t2")
        assert (result, error) == (None, "Action not found")

        ok, error = await safety.rollback_action(pending.id, "attacker", "r", tenant_id="t2")
        assert (ok, error) == (False, "Action not found")

    async def test_the_owner_can_still_reject(self):
        safety, pending = await self._pending_for("t1")

        rejected, error = await safety.reject_action(
            pending.id, "admin_1", "changed mind", tenant_id="t1"
        )

        assert error is None
        assert rejected.status == ActionStatus.REJECTED
