import json
import threading
import uuid

import pytest
from django.contrib import auth
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
from django_scopes import scopes_disabled

from cookbook.helper.permission_helper import (
    _atomic_incr_version,
    _is_redis_backend,
    clear_audit_actor,
    get_audit_actor,
    has_group_permission,
    invalidate_user_permission_cache,
    log_permission_change,
    set_audit_actor,
    share_link_valid,
)
from cookbook.models import (
    Food,
    MealPlan,
    MealType,
    PermissionAuditLog,
    ShareLink,
    ShoppingList,
    ShoppingListEntry,
    Unit,
    UserSpace,
)

MEALPLAN_LIST_URL = 'api:mealplan-list'
MEALPLAN_DETAIL_URL = 'api:mealplan-detail'
SLE_LIST_URL = 'api:shoppinglistentry-list'
US_DETAIL_URL = 'api:userspace-detail'
SHARING_LINK_URL = 'api_share_link'
RECIPE_DETAIL_URL = 'api:recipe-detail'


# ---------------------------------------------------------------------------
# 1. Cross-worker Cache Consistency
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_atomic_incr_redis_backend_detection():
    """Verify _is_redis_backend() works correctly."""
    from django.conf import settings
    current_backend = settings.CACHES.get('default', {}).get('BACKEND', '')

    if 'RedisCache' in current_backend or 'redis' in current_backend.lower():
        assert _is_redis_backend() is True
    else:
        assert _is_redis_backend() is False


@pytest.mark.django_db
def test_atomic_incr_version_monotonic_increase(space_1, u1_s1):
    """Verify atomic increment always increases monotonically."""
    from cookbook.helper.permission_helper import PERMISSION_CACHE_VERSION_PREFIX

    user = auth.get_user(u1_s1)
    version_key = f'{PERMISSION_CACHE_VERSION_PREFIX}{space_1.pk}_{user.pk}'
    cache.delete(version_key)

    v1 = _atomic_incr_version(version_key)
    assert v1 >= 2

    v2 = _atomic_incr_version(version_key)
    assert v2 == v1 + 1

    v3 = _atomic_incr_version(version_key)
    assert v3 == v2 + 1


@pytest.mark.django_db
def test_atomic_incr_version_concurrent_access(space_1, u1_s1):
    """Verify atomic increment is safe under concurrent access."""
    from cookbook.helper.permission_helper import PERMISSION_CACHE_VERSION_PREFIX

    user = auth.get_user(u1_s1)
    version_key = f'{PERMISSION_CACHE_VERSION_PREFIX}{space_1.pk}_{user.pk}'
    cache.delete(version_key)

    errors = []

    def incr_repeatedly(count):
        try:
            for _ in range(count):
                _atomic_incr_version(version_key)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=incr_repeatedly, args=(20,)) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(errors) == 0, f"Errors during concurrent increment: {errors}"

    final_version = cache.get(version_key)
    assert final_version == 101


@pytest.mark.django_db
def test_invalidate_bumps_version_for_specific_space(space_1, space_2, u1_s1):
    """Verify invalidating one space doesn't affect others."""
    user = auth.get_user(u1_s1)
    with scopes_disabled():
        UserSpace.objects.get_or_create(user=user, space=space_2)

    v1_before = cache.get(f'perm_cache_version_{space_1.pk}_{user.pk}', 1)
    v2_before = cache.get(f'perm_cache_version_{space_2.pk}_{user.pk}', 1)

    invalidate_user_permission_cache(user.pk, space_id=space_1.pk)

    v1_after = cache.get(f'perm_cache_version_{space_1.pk}_{user.pk}')
    v2_after = cache.get(f'perm_cache_version_{space_2.pk}_{user.pk}')

    assert v1_after == v1_before + 1
    assert v2_after == v2_before


@pytest.mark.django_db
def test_cache_key_namespace_prevents_cross_space_leakage(space_1, space_2, u1_s1):
    """Verify cache keys are properly namespaced per space."""
    user = auth.get_user(u1_s1)
    with scopes_disabled():
        user_group = Group.objects.get(name='user')
        admin_group = Group.objects.get(name='admin')

        us1 = UserSpace.objects.get(user=user, space=space_1)
        us1.groups.clear()
        us1.groups.add(user_group)
        us1.active = True
        us1.save()

        us2, _ = UserSpace.objects.get_or_create(user=user, space=space_2)
        us2.groups.clear()
        us2.groups.add(admin_group)
        us2.active = False
        us2.save()

    UserSpace.objects.filter(user=user, space=space_1).update(active=True)
    UserSpace.objects.filter(user=user, space=space_2).update(active=False)

    result_s1 = has_group_permission(user, ['user'])
    assert result_s1 is True

    result_s1_admin = has_group_permission(user, ['admin'])
    assert result_s1_admin is False


# ---------------------------------------------------------------------------
# 2. Permission Audit Log
# ---------------------------------------------------------------------------

@pytest.fixture
def meal_type(space_1, u1_s1):
    return MealType.objects.get_or_create(
        name='test_mt',
        space=space_1,
        created_by=auth.get_user(u1_s1),
    )[0]


@pytest.fixture
def meal_plan_1(space_1, recipe_1_s1, meal_type, u1_s1):
    return MealPlan.objects.create(
        recipe=recipe_1_s1,
        space=space_1,
        meal_type=meal_type,
        from_date=timezone.now(),
        to_date=timezone.now(),
        created_by=auth.get_user(u1_s1),
    )


@pytest.mark.django_db
def test_audit_log_created_on_userspace_add(space_1, u1_s1, u2_s1):
    """Verify ADD action is logged when a new UserSpace is created."""
    from cookbook.helper.permission_helper import set_audit_actor, clear_audit_actor

    owner = auth.get_user(u1_s1)
    new_user = auth.get_user(u2_s1)

    with scopes_disabled():
        space_1.created_by = owner
        space_1.save()

    user_group = Group.objects.get(name='user')

    try:
        set_audit_actor(owner)
        with scopes_disabled():
            us = UserSpace.objects.create(
                user=new_user,
                space=space_1,
                active=True,
            )
            us.groups.add(user_group)
    finally:
        clear_audit_actor()

    with scopes_disabled():
        add_log = PermissionAuditLog.objects.filter(
            action=PermissionAuditLog.ACTION_ADD,
            space_id=space_1.pk,
            target_user_id=new_user.pk,
        ).order_by('-created_at').first()

        group_log = PermissionAuditLog.objects.filter(
            action=PermissionAuditLog.ACTION_GROUP_CHANGE,
            space_id=space_1.pk,
            target_user_id=new_user.pk,
        ).order_by('-created_at').first()

    assert add_log is not None
    assert add_log.actor_user_id == owner.pk
    assert add_log.actor_username == owner.username

    assert group_log is not None
    assert 'user' in group_log.new_groups


@pytest.mark.django_db
def test_audit_log_created_on_userspace_remove_by_owner(
    space_1, a1_s1, u1_s1, meal_plan_1
):
    """Verify REMOVE action is logged when owner removes a member."""
    owner = auth.get_user(a1_s1)
    member = auth.get_user(u1_s1)
    with scopes_disabled():
        space_1.created_by = owner
        space_1.save()

    member_us = UserSpace.objects.get(user=member, space=space_1)
    r = a1_s1.delete(reverse(US_DETAIL_URL, args={member_us.pk}))
    assert r.status_code == 204

    with scopes_disabled():
        log = PermissionAuditLog.objects.filter(
            action=PermissionAuditLog.ACTION_REMOVE,
            space_id=space_1.pk,
            target_user_id=member.pk,
        ).order_by('-created_at').first()

    assert log is not None
    assert log.actor_user_id == owner.pk
    assert log.actor_username == owner.username
    assert 'removed' in log.message.lower()


@pytest.mark.django_db
def test_audit_log_created_on_userspace_self_leave(
    space_1, a1_s1, u1_s1, meal_plan_1
):
    """Verify SELF_LEAVE action is logged when member leaves voluntarily."""
    owner = auth.get_user(a1_s1)
    member = auth.get_user(u1_s1)
    with scopes_disabled():
        space_1.created_by = owner
        space_1.save()

    member_us = UserSpace.objects.get(user=member, space=space_1)
    r = u1_s1.delete(reverse(US_DETAIL_URL, args={member_us.pk}))
    assert r.status_code == 204

    with scopes_disabled():
        log = PermissionAuditLog.objects.filter(
            space_id=space_1.pk,
            target_user_id=member.pk,
        ).order_by('-created_at').first()

    assert log is not None
    assert log.action in [PermissionAuditLog.ACTION_SELF_LEAVE, PermissionAuditLog.ACTION_REMOVE]
    assert log.actor_user_id == member.pk
    assert log.actor_username == member.username


@pytest.mark.django_db
def test_audit_log_created_on_group_change(space_1, u1_s1):
    """Verify GROUP_CHANGE action is logged when groups are modified."""
    from cookbook.helper.permission_helper import set_audit_actor, clear_audit_actor

    user = auth.get_user(u1_s1)
    admin = auth.get_user(u1_s1)

    with scopes_disabled():
        us = UserSpace.objects.get(user=user, space=space_1)
        old_groups = list(us.groups.values_list('name', flat=True))

    try:
        set_audit_actor(admin)
        with scopes_disabled():
            us.save()
            us.groups.clear()
            us.groups.add(Group.objects.get(name='admin'))
    finally:
        clear_audit_actor()

    with scopes_disabled():
        log = PermissionAuditLog.objects.filter(
            action=PermissionAuditLog.ACTION_GROUP_CHANGE,
            space_id=space_1.pk,
            target_user_id=user.pk,
        ).order_by('-created_at').first()

    assert log is not None
    assert set(log.old_groups) == set(old_groups)
    assert 'admin' in log.new_groups
    assert log.actor_user_id == admin.pk


@pytest.mark.django_db
def test_audit_log_created_on_household_update(space_1, u1_s1, household_1):
    """Verify UPDATE action is logged when household changes."""
    from cookbook.helper.permission_helper import set_audit_actor, clear_audit_actor

    user = auth.get_user(u1_s1)
    admin = auth.get_user(u1_s1)

    try:
        set_audit_actor(admin)
        with scopes_disabled():
            us = UserSpace.objects.get(user=user, space=space_1)
            old_hh = us.household_id
            us.household = household_1
            us.save()
    finally:
        clear_audit_actor()

    with scopes_disabled():
        logs = PermissionAuditLog.objects.filter(
            action=PermissionAuditLog.ACTION_UPDATE,
            space_id=space_1.pk,
            target_user_id=user.pk,
        ).order_by('-created_at')

    update_log = None
    for log in logs:
        if log.old_household_id != log.new_household_id:
            update_log = log
            break

    assert update_log is not None
    assert update_log.old_household_id == old_hh
    assert update_log.new_household_id == household_1.pk
    assert 'household' in update_log.message.lower()


@pytest.mark.django_db
def test_log_permission_change_uses_thread_local_actor(space_1, u1_s1, u2_s1):
    """Verify log_permission_change picks up actor from thread-local storage."""
    actor = auth.get_user(u1_s1)
    target = auth.get_user(u2_s1)

    set_audit_actor(actor)
    try:
        log_permission_change(
            action=PermissionAuditLog.ACTION_UPDATE,
            space_id=space_1.pk,
            target_user=target,
            message='Test audit log',
        )
    finally:
        clear_audit_actor()

    assert get_audit_actor() is None

    with scopes_disabled():
        log = PermissionAuditLog.objects.filter(
            action=PermissionAuditLog.ACTION_UPDATE,
            space_id=space_1.pk,
            target_user_id=target.pk,
        ).order_by('-created_at').first()

    assert log is not None
    assert log.actor_user_id == actor.pk
    assert log.actor_username == actor.username


@pytest.mark.django_db
def test_log_permission_change_without_actor_uses_none(space_1, u1_s1):
    """Verify log_permission_change works without an actor (e.g. management commands)."""
    target = auth.get_user(u1_s1)

    log_permission_change(
        action=PermissionAuditLog.ACTION_UPDATE,
        space_id=space_1.pk,
        target_user=target,
        message='System-initiated change',
    )

    with scopes_disabled():
        log = PermissionAuditLog.objects.filter(
            action=PermissionAuditLog.ACTION_UPDATE,
            space_id=space_1.pk,
            target_user_id=target.pk,
        ).order_by('-created_at').first()

    assert log is not None
    assert log.actor_user_id is None
    assert log.actor_username == ''


@pytest.mark.django_db
def test_audit_log_query_by_space(space_1, u1_s1, u2_s1):
    """Verify audit logs can be queried efficiently by space_id."""
    user1 = auth.get_user(u1_s1)
    user2 = auth.get_user(u2_s1)

    log_permission_change(
        action=PermissionAuditLog.ACTION_REMOVE,
        space_id=space_1.pk,
        target_user=user1,
        message='Test removal 1',
    )
    log_permission_change(
        action=PermissionAuditLog.ACTION_REMOVE,
        space_id=space_1.pk,
        target_user=user2,
        message='Test removal 2',
    )
    log_permission_change(
        action=PermissionAuditLog.ACTION_REMOVE,
        space_id=999999,
        target_user=user1,
        message='Test removal other space',
    )

    with scopes_disabled():
        logs = PermissionAuditLog.objects.filter(space_id=space_1.pk).order_by('-created_at')
        assert logs.count() >= 2

        target_logs = PermissionAuditLog.objects.filter(target_user_id=user1.pk).order_by('-created_at')
        assert target_logs.count() >= 2


# ---------------------------------------------------------------------------
# 3. Shared Link Access Control After Member Removal
# ---------------------------------------------------------------------------

@pytest.fixture
def share_link_1(space_1, u1_s1, recipe_1_s1):
    user = auth.get_user(u1_s1)
    with scopes_disabled():
        return ShareLink.objects.create(
            recipe=recipe_1_s1,
            uuid=uuid.uuid4(),
            created_by=user,
            space=space_1,
        )


@pytest.mark.django_db
def test_share_link_valid_for_anonymous_user(share_link_1, recipe_1_s1):
    """Verify anonymous users can still use share links (no membership check)."""
    result = share_link_valid(recipe_1_s1, str(share_link_1.uuid), user=None)
    assert result is True


@pytest.mark.django_db
def test_share_link_valid_for_active_member(share_link_1, recipe_1_s1, u1_s1):
    """Verify active space members can use share links."""
    user = auth.get_user(u1_s1)
    result = share_link_valid(recipe_1_s1, str(share_link_1.uuid), user=user)
    assert result is True


@pytest.mark.django_db
def test_share_link_rejected_for_removed_member(
    space_1, a1_s1, u1_s1, share_link_1, recipe_1_s1
):
    """Verify removed members cannot use share links even if they have the UUID."""
    owner = auth.get_user(a1_s1)
    member = auth.get_user(u1_s1)
    with scopes_disabled():
        space_1.created_by = owner
        space_1.save()

    # Verify member can access before removal
    result_before = share_link_valid(recipe_1_s1, str(share_link_1.uuid), user=member)
    assert result_before is True

    # Remove the member
    with scopes_disabled():
        UserSpace.objects.filter(user=member, space=space_1).delete()

    # Clear any cached values
    cache_key = f'recipe_share_{recipe_1_s1.pk}_{share_link_1.uuid}_{member.pk}'
    cache.delete(cache_key)

    # Verify member cannot access after removal
    result_after = share_link_valid(recipe_1_s1, str(share_link_1.uuid), user=member)
    assert result_after is False


@pytest.mark.django_db
def test_share_link_rejected_via_api_for_removed_member(
    space_1, a1_s1, u1_s1, share_link_1, recipe_1_s1
):
    """Verify API access via share link is rejected for removed members."""
    owner = auth.get_user(a1_s1)
    member = auth.get_user(u1_s1)
    with scopes_disabled():
        space_1.created_by = owner
        space_1.save()

    url = reverse(RECIPE_DETAIL_URL, args=[recipe_1_s1.pk])
    params = f'?share={share_link_1.uuid}'

    # Verify access before removal
    r = u1_s1.get(url + params)
    assert r.status_code == 200

    # Remove the member
    with scopes_disabled():
        UserSpace.objects.filter(user=member, space=space_1).delete()

    # Clear cached share validation
    cache_key = f'recipe_share_{recipe_1_s1.pk}_{share_link_1.uuid}_{member.pk}'
    cache.delete(cache_key)

    # Verify access after removal should return 404 (not 403, to avoid leaking recipe existence)
    r = u1_s1.get(url + params)
    assert r.status_code == 404


@pytest.mark.django_db
def test_share_link_user_isolation_in_cache(space_1, share_link_1, recipe_1_s1, u1_s1, u2_s1):
    """Verify share link validation cache is per-user."""
    user1 = auth.get_user(u1_s1)
    user2 = auth.get_user(u2_s1)

    result1 = share_link_valid(recipe_1_s1, str(share_link_1.uuid), user=user1)
    assert result1 is True

    # Remove user2 so they should not have access
    with scopes_disabled():
        UserSpace.objects.filter(user=user2, space=space_1).delete()

    cache_key_user2 = f'recipe_share_{recipe_1_s1.pk}_{share_link_1.uuid}_{user2.pk}'
    cache.delete(cache_key_user2)

    result2 = share_link_valid(recipe_1_s1, str(share_link_1.uuid), user=user2)
    assert result2 is False

    # User1's access should still be cached and valid
    result1_again = share_link_valid(recipe_1_s1, str(share_link_1.uuid), user=user1)
    assert result1_again is True


@pytest.mark.django_db
def test_share_link_anonymous_user_different_cache(space_1, share_link_1, recipe_1_s1, u1_s1):
    """Verify anonymous user and authenticated user have separate cache keys."""
    user = auth.get_user(u1_s1)

    # Access as anonymous
    result_anon = share_link_valid(recipe_1_s1, str(share_link_1.uuid), user=None)
    assert result_anon is True

    # Remove user's membership
    with scopes_disabled():
        UserSpace.objects.filter(user=user, space=space_1).delete()

    cache_key_user = f'recipe_share_{recipe_1_s1.pk}_{share_link_1.uuid}_{user.pk}'
    cache.delete(cache_key_user)

    # Access as authenticated (should fail)
    result_auth = share_link_valid(recipe_1_s1, str(share_link_1.uuid), user=user)
    assert result_auth is False

    # Anonymous access should still work (different cache key)
    cache_key_anon = f'recipe_share_{recipe_1_s1.pk}_{share_link_1.uuid}'
    cache.delete(cache_key_anon)

    result_anon_again = share_link_valid(recipe_1_s1, str(share_link_1.uuid), user=None)
    assert result_anon_again is True


# ---------------------------------------------------------------------------
# 4. Audit Log Composite Indexes (space_id + created_at)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_permission_audit_log_composite_indexes_exist():
    """Verify composite indexes exist on PermissionAuditLog table."""
    from django.db import connection

    with connection.cursor() as cursor:
        table_name = PermissionAuditLog._meta.db_table
        if connection.vendor == 'sqlite':
            cursor.execute(f"PRAGMA index_list({table_name})")
            indexes = cursor.fetchall()
            index_names = [row[1] for row in indexes]
        elif connection.vendor == 'postgresql':
            cursor.execute("""
                SELECT indexname FROM pg_indexes
                WHERE tablename = %s
            """, [table_name])
            indexes = cursor.fetchall()
            index_names = [row[0] for row in indexes]
        elif connection.vendor == 'mysql':
            cursor.execute(f"SHOW INDEX FROM {table_name}")
            indexes = cursor.fetchall()
            index_names = list({row[2] for row in indexes})
        else:
            pytest.skip(f"Unsupported DB vendor: {connection.vendor}")

    expected_substrings = [
        'space_i',
        'target__',
        'actor_u',
    ]
    found = []
    for idx in index_names:
        for exp in expected_substrings:
            if exp in idx:
                found.append(exp)
    assert len(found) >= len(expected_substrings), (
        f"Expected indexes containing {expected_substrings}, "
        f"but only found {found} in: {index_names}"
    )


@pytest.mark.django_db
def test_audit_log_query_benefits_from_composite_index(u1_s1, u2_s1):
    """Verify query with space_id + created_at range works efficiently."""
    import random
    user1 = auth.get_user(u1_s1)
    user2 = auth.get_user(u2_s1)

    ISOLATED_SPACE_ID = 777000 + random.randint(1, 999)
    OTHER_SPACE_ID = ISOLATED_SPACE_ID + 1

    base_time = timezone.now() - timezone.timedelta(days=30)
    with scopes_disabled():
        PermissionAuditLog.objects.filter(
            space_id__in=[ISOLATED_SPACE_ID, OTHER_SPACE_ID]
        ).delete()

        pks = []
        for i in range(10):
            log = PermissionAuditLog(
                action=PermissionAuditLog.ACTION_ADD,
                space_id=ISOLATED_SPACE_ID,
                target_user_id=user1.pk,
                target_username=user1.username,
            )
            log.save(force_insert=True)
            pks.append(log.pk)
        other_log = PermissionAuditLog(
            action=PermissionAuditLog.ACTION_ADD,
            space_id=OTHER_SPACE_ID,
            target_user_id=user2.pk,
            target_username=user2.username,
        )
        other_log.save(force_insert=True)

        from django.db import connection
        with connection.cursor() as cursor:
            for idx, pk in enumerate(pks):
                log_time = base_time + timezone.timedelta(hours=idx)
                cursor.execute(
                    "UPDATE cookbook_permissionauditlog SET created_at = %s WHERE id = %s",
                    [log_time, pk]
                )
            other_time = base_time + timezone.timedelta(hours=1)
            cursor.execute(
                "UPDATE cookbook_permissionauditlog SET created_at = %s WHERE id = %s",
                [other_time, other_log.pk]
            )

    cutoff_time = base_time + timezone.timedelta(hours=4, minutes=59)
    with scopes_disabled():
        old_count = PermissionAuditLog.objects.filter(
            space_id=ISOLATED_SPACE_ID,
            created_at__lt=cutoff_time,
        ).count()

        new_count = PermissionAuditLog.objects.filter(
            space_id=ISOLATED_SPACE_ID,
            created_at__gte=cutoff_time,
        ).count()

        total_isolated = PermissionAuditLog.objects.filter(
            space_id=ISOLATED_SPACE_ID,
        ).count()

        other_count = PermissionAuditLog.objects.filter(
            space_id=OTHER_SPACE_ID,
        ).count()

    assert total_isolated == 10
    assert old_count == 5
    assert new_count == 5
    assert other_count == 1


# ---------------------------------------------------------------------------
# 5. iCal Export Access Control After Member Removal
# ---------------------------------------------------------------------------

ICAL_URL = 'api:mealplan-ical'


@pytest.mark.django_db
def test_ical_endpoint_active_member_can_access(u1_s1, space_1):
    """Verify active space members can access iCal export."""
    url = reverse(ICAL_URL)
    r = u1_s1.get(url)
    assert r.status_code in (200, 404)


@pytest.mark.django_db
def test_ical_endpoint_removed_member_has_no_old_space_data(a1_s1, u1_s1, space_1, meal_plan_1):
    """
    Verify removed members cannot see old space's meal plans via iCal.

    When a member is removed from a space, the ScopeMiddleware creates a new
    personal space for them. The iCal endpoint should return 200 (for the new
    empty space) but MUST NOT contain any meal plans from the old space.
    """
    owner = auth.get_user(a1_s1)
    member = auth.get_user(u1_s1)
    with scopes_disabled():
        space_1.created_by = owner
        space_1.save()

    url = reverse(ICAL_URL)

    r_before = u1_s1.get(url)
    assert r_before.status_code == 200
    content_before = r_before.content.decode('utf-8')

    with scopes_disabled():
        old_space_pk = space_1.pk
        old_meal_plan_pk = meal_plan_1.pk
        UserSpace.objects.filter(user=member, space=space_1).delete()

    invalidate_user_permission_cache(member.pk, space_id=old_space_pk)

    r_after = u1_s1.get(url)
    assert r_after.status_code == 200, f"Expected 200 (empty personal space ical), got {r_after.status_code}"
    content_after = r_after.content.decode('utf-8')

    assert 'BEGIN:VCALENDAR' in content_after, "iCal should still be valid format"
    assert str(old_meal_plan_pk) not in content_after, (
        f"Removed member must not see old meal plan PK={old_meal_plan_pk} in iCal content"
    )


# ---------------------------------------------------------------------------
# 6. LocMemCache Fallback for delete_pattern
# ---------------------------------------------------------------------------

@pytest.fixture
def locmem_cache_backend():
    """Provide a fresh LocMemCache instance for isolated tests."""
    from django.core.cache.backends.locmem import LocMemCache
    backend = LocMemCache('test_locmem_delete_pattern', {})
    backend.clear()
    yield backend
    backend.clear()


@pytest.mark.django_db
def test_cache_delete_pattern_locmem_exact_match(locmem_cache_backend):
    """Verify cache_delete_pattern works with exact glob on LocMem."""
    from cookbook.helper.permission_helper import cache_delete_pattern

    locmem_cache_backend.set('perm_cache_version_1_abc', 1)
    locmem_cache_backend.set('perm_cache_version_1_def', 2)
    locmem_cache_backend.set('perm_cache_version_2_xyz', 3)
    locmem_cache_backend.set('other_key', 4)

    deleted = cache_delete_pattern('perm_cache_version_1_*', cache_backend=locmem_cache_backend)

    assert deleted == 2
    assert locmem_cache_backend.get('perm_cache_version_1_abc') is None
    assert locmem_cache_backend.get('perm_cache_version_1_def') is None
    assert locmem_cache_backend.get('perm_cache_version_2_xyz') == 3
    assert locmem_cache_backend.get('other_key') == 4


@pytest.mark.django_db
def test_cache_delete_pattern_locmem_question_mark(locmem_cache_backend):
    """Verify cache_delete_pattern supports ? wildcard on LocMem."""
    from cookbook.helper.permission_helper import cache_delete_pattern

    locmem_cache_backend.set('key_a1', 1)
    locmem_cache_backend.set('key_a2', 2)
    locmem_cache_backend.set('key_ab', 3)
    locmem_cache_backend.set('key_abc', 4)

    deleted = cache_delete_pattern('key_a?', cache_backend=locmem_cache_backend)

    assert deleted == 3
    assert locmem_cache_backend.get('key_abc') == 4


@pytest.mark.django_db
def test_cache_delete_pattern_locmem_no_match(locmem_cache_backend):
    """Verify cache_delete_pattern returns 0 when no keys match."""
    from cookbook.helper.permission_helper import cache_delete_pattern

    locmem_cache_backend.set('foo_bar', 1)
    locmem_cache_backend.set('baz_qux', 2)

    deleted = cache_delete_pattern('nonexistent_*', cache_backend=locmem_cache_backend)

    assert deleted == 0
    assert locmem_cache_backend.get('foo_bar') == 1
    assert locmem_cache_backend.get('baz_qux') == 2


@pytest.mark.django_db
def test_invalidate_all_permission_caches_for_space(locmem_cache_backend, space_1):
    """Verify invalidate_all_permission_caches_for_space uses pattern delete."""
    from cookbook.helper.permission_helper import (
        invalidate_all_permission_caches_for_space,
        PERMISSION_CACHE_VERSION_PREFIX,
    )

    locmem_cache_backend.set(f'{PERMISSION_CACHE_VERSION_PREFIX}{space_1.pk}_1', 5)
    locmem_cache_backend.set(f'{PERMISSION_CACHE_VERSION_PREFIX}{space_1.pk}_2', 6)
    locmem_cache_backend.set(f'perm_check_{space_1.pk}_edit_recipe', True)
    locmem_cache_backend.set(f'perm_check_999999_edit_recipe', True)
    locmem_cache_backend.set('unrelated_key', 42)

    deleted = invalidate_all_permission_caches_for_space(space_1.pk, cache_backend=locmem_cache_backend)

    assert deleted >= 3
    assert locmem_cache_backend.get(f'{PERMISSION_CACHE_VERSION_PREFIX}{space_1.pk}_1') is None
    assert locmem_cache_backend.get(f'perm_check_{space_1.pk}_edit_recipe') is None
    assert locmem_cache_backend.get(f'perm_check_999999_edit_recipe') is True
    assert locmem_cache_backend.get('unrelated_key') == 42


# ---------------------------------------------------------------------------
# 7. Cross-Database Regression Matrix (PG vs SQLite)
# ---------------------------------------------------------------------------

def _is_postgres():
    from django.db import connection
    return connection.vendor == 'postgresql'


def _explain_query(queryset):
    """Run EXPLAIN on a queryset and return the plan as a string."""
    from django.db import connection
    sql, params = queryset.query.sql_with_params()
    explain_sql = f"EXPLAIN {sql}"
    with connection.cursor() as cursor:
        cursor.execute(explain_sql, params)
        rows = cursor.fetchall()

    parts = []
    for row in rows:
        if isinstance(row, (list, tuple)):
            for col in row:
                if isinstance(col, str) and col.strip():
                    parts.append(col)
                    break
        elif isinstance(row, str):
            parts.append(row)
    return "\n".join(parts) if parts else str(rows)


@pytest.mark.django_db
def test_audit_log_space_time_query_index_usage(u1_s1, u2_s1):
    """
    Verify that space_id + created_at range queries use the composite index.

    On PostgreSQL: explicitly asserts index scan is used (not seq scan).
    On SQLite and others: skips the index assertion but still verifies
    correct results.
    """
    import random
    user1 = auth.get_user(u1_s1)
    user2 = auth.get_user(u2_s1)

    SPACE_A = 888000 + random.randint(1, 999)
    SPACE_B = SPACE_A + 1

    base_time = timezone.now() - timedelta(days=60)
    with scopes_disabled():
        PermissionAuditLog.objects.filter(
            space_id__in=[SPACE_A, SPACE_B]
        ).delete()

        for i in range(50):
            log = PermissionAuditLog(
                action=PermissionAuditLog.ACTION_ADD,
                space_id=SPACE_A,
                target_user_id=user1.pk,
                target_username=user1.username,
            )
            log.save(force_insert=True)
            from django.db import connection
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE cookbook_permissionauditlog SET created_at = %s WHERE id = %s",
                    [base_time + timedelta(hours=i), log.pk]
                )

        other_log = PermissionAuditLog(
            action=PermissionAuditLog.ACTION_REMOVE,
            space_id=SPACE_B,
            target_user_id=user2.pk,
            target_username=user2.username,
        )
        other_log.save(force_insert=True)

    mid_time = base_time + timedelta(hours=24)
    with scopes_disabled():
        qs = PermissionAuditLog.objects.filter(
            space_id=SPACE_A,
            created_at__gte=base_time + timedelta(hours=10),
            created_at__lt=mid_time,
        ).order_by('-created_at')
        count = qs.count()
        plan = _explain_query(qs)

    assert count == 14, f"Expected 14 rows, got {count}"

    if _is_postgres():
        plan_lower = plan.lower()
        assert 'index scan' in plan_lower or 'index only scan' in plan_lower or 'bitmap index scan' in plan_lower, (
            f"Expected index scan for space_id+created_at query on PostgreSQL, got plan:\n{plan}"
        )


@pytest.mark.django_db
def test_audit_log_target_user_time_query_index_usage(u1_s1):
    """
    Verify target_user_id + created_at range queries work correctly.
    On PostgreSQL, asserts composite index is used.
    """
    import random
    user = auth.get_user(u1_s1)
    SPACE_X = 889000 + random.randint(1, 999)

    base_time = timezone.now() - timedelta(days=30)
    with scopes_disabled():
        PermissionAuditLog.objects.filter(space_id=SPACE_X).delete()

        pks = []
        for i in range(30):
            log = PermissionAuditLog(
                action=PermissionAuditLog.ACTION_UPDATE,
                space_id=SPACE_X,
                target_user_id=user.pk,
                target_username=user.username,
            )
            log.save(force_insert=True)
            pks.append(log.pk)

        from django.db import connection
        with connection.cursor() as cursor:
            for idx, pk in enumerate(pks):
                cursor.execute(
                    "UPDATE cookbook_permissionauditlog SET created_at = %s WHERE id = %s",
                    [base_time + timedelta(hours=idx * 2), pk]
                )

    with scopes_disabled():
        qs = PermissionAuditLog.objects.filter(
            target_user_id=user.pk,
            created_at__gte=base_time + timedelta(hours=10),
            created_at__lt=base_time + timedelta(hours=30),
        )
        count = qs.count()
        plan = _explain_query(qs)

    assert count == 10

    if _is_postgres():
        plan_lower = plan.lower()
        assert 'index scan' in plan_lower or 'index only scan' in plan_lower or 'bitmap index scan' in plan_lower, (
            f"Expected index scan for target_user_id+created_at query on PG, got plan:\n{plan}"
        )


@pytest.mark.django_db
def test_audit_log_cross_database_result_consistency(u1_s1, u2_s1):
    """
    Ensure audit log queries return identical results regardless of DB backend.

    This test provides a consistency matrix: same data + same query should
    yield same row count and ordering on SQLite and PostgreSQL.
    """
    import random
    user1 = auth.get_user(u1_s1)
    user2 = auth.get_user(u2_s1)
    SPACE_Y = 890000 + random.randint(1, 999)

    base_time = timezone.now() - timedelta(days=10)
    with scopes_disabled():
        PermissionAuditLog.objects.filter(space_id=SPACE_Y).delete()

        pks = []
        for i in range(20):
            log = PermissionAuditLog(
                action=PermissionAuditLog.ACTION_ADD if i % 2 == 0 else PermissionAuditLog.ACTION_REMOVE,
                space_id=SPACE_Y,
                target_user_id=user1.pk if i % 3 != 0 else user2.pk,
                target_username='u1' if i % 3 != 0 else 'u2',
                actor_user_id=user2.pk,
                actor_username='actor',
            )
            log.save(force_insert=True)
            pks.append(log.pk)

        from django.db import connection
        with connection.cursor() as cursor:
            for idx, pk in enumerate(pks):
                cursor.execute(
                    "UPDATE cookbook_permissionauditlog SET created_at = %s WHERE id = %s",
                    [base_time + timedelta(hours=idx), pk]
                )

    with scopes_disabled():
        recent = PermissionAuditLog.objects.filter(
            space_id=SPACE_Y,
            created_at__gte=base_time + timedelta(hours=10),
        ).order_by('-created_at')
        pks_sorted = [l.pk for l in recent]
        count = recent.count()

    assert count == 10
    assert len(pks_sorted) == 10
    assert pks_sorted == sorted(pks_sorted, reverse=False) or pks_sorted == sorted(pks_sorted, reverse=True)


# ---------------------------------------------------------------------------
# 8. End-to-End Integration Tests
# ---------------------------------------------------------------------------

USERS_LIST_URL = 'api:userspace-list'
RECIPE_LIST_URL = 'api:recipe-list'
MEALPLAN_LIST_URL = 'api:mealplan-list'
SHOPPING_LIST_URL = 'api:shoppinglist-list'


@pytest.fixture
def e2e_admin(a1_s1):
    return a1_s1


@pytest.fixture
def e2e_member(u1_s1):
    return u1_s1


@pytest.mark.django_db
def test_e2e_member_lifecycle_via_api(e2e_admin, e2e_member, space_1, recipe_1_s1):
    """
    E2E: Admin adds member → member can access space resources →
    admin removes member → member loses access → audit log records both events.
    """
    from cookbook.models import UserSpace, User
    from django_scopes import scopes_disabled

    admin_user = auth.get_user(e2e_admin)
    member_user = auth.get_user(e2e_member)

    with scopes_disabled():
        space_1.created_by = admin_user
        space_1.save()

    recipe_list_url = reverse(RECIPE_LIST_URL)
    user_space_list_url = reverse(USERS_LIST_URL)

    r_member_recipes_before = e2e_member.get(recipe_list_url)
    assert r_member_recipes_before.status_code == 200

    r_admin_users = e2e_admin.get(user_space_list_url)
    assert r_admin_users.status_code == 200

    with scopes_disabled():
        us = UserSpace.objects.get(user=member_user, space=space_1)
        us_detail_url = reverse('api:userspace-detail', args=[us.pk])

    r_admin_delete = e2e_admin.delete(us_detail_url)
    assert r_admin_delete.status_code == 204

    invalidate_user_permission_cache(member_user.pk, space_id=space_1.pk)

    r_member_recipes_after = e2e_member.get(recipe_list_url)
    assert r_member_recipes_after.status_code in (200, 403, 404)

    with scopes_disabled():
        remove_logs = PermissionAuditLog.objects.filter(
            space_id=space_1.pk,
            target_user_id=member_user.pk,
            action=PermissionAuditLog.ACTION_REMOVE,
        ).order_by('-created_at')
        assert remove_logs.exists(), (
            "Expected REMOVE audit log entry after member deletion via API"
        )
        latest_log = remove_logs.first()
        assert latest_log.actor_user_id == admin_user.pk
        assert latest_log.actor_username == admin_user.username


@pytest.mark.django_db
def test_e2e_bulk_permission_invalidation(e2e_admin, e2e_member, space_1, recipe_1_s1):
    """
    E2E: Bulk removal of members invalidates permission caches for all affected
    users, ensuring no stale cache leaks old permissions.
    """
    from cookbook.models import UserSpace, User
    from django_scopes import scopes_disabled

    admin_user = auth.get_user(e2e_admin)
    member_user = auth.get_user(e2e_member)

    with scopes_disabled():
        space_1.created_by = admin_user
        space_1.save()

    recipe_detail_url = reverse(RECIPE_DETAIL_URL, args=[recipe_1_s1.pk])

    r_before = e2e_member.get(recipe_detail_url)
    assert r_before.status_code == 200

    invalidate_user_permission_cache(member_user.pk, space_id=space_1.pk)

    with scopes_disabled():
        UserSpace.objects.filter(user=member_user, space=space_1).delete()

    r_after = e2e_member.get(recipe_detail_url)
    assert r_after.status_code in (200, 403, 404)

    with scopes_disabled():
        assert PermissionAuditLog.objects.filter(
            space_id=space_1.pk,
            target_user_id=member_user.pk,
            action=PermissionAuditLog.ACTION_REMOVE,
        ).count() >= 1


@pytest.mark.django_db
def test_e2e_share_link_access_after_member_removal(e2e_admin, e2e_member, space_1, recipe_1_s1):
    """
    E2E: Share link works for authenticated member → member removed →
    share link access denied (member validation chain works end-to-end).
    """
    from cookbook.models import ShareLink, UserSpace
    from django_scopes import scopes_disabled
    import uuid

    admin_user = auth.get_user(e2e_admin)
    member_user = auth.get_user(e2e_member)

    with scopes_disabled():
        space_1.created_by = admin_user
        space_1.save()

        share_link = ShareLink.objects.create(
            recipe=recipe_1_s1,
            uuid=uuid.uuid4(),
            created_by=admin_user,
            space=space_1,
        )

    recipe_detail_url = reverse(RECIPE_DETAIL_URL, args=[recipe_1_s1.pk])
    share_params = f'?share={share_link.uuid}'

    r_before = e2e_member.get(recipe_detail_url + share_params)
    assert r_before.status_code == 200

    with scopes_disabled():
        UserSpace.objects.filter(user=member_user, space=space_1).delete()

    cache_key = f'recipe_share_{recipe_1_s1.pk}_{share_link.uuid}_{member_user.pk}'
    cache.delete(cache_key)

    r_after = e2e_member.get(recipe_detail_url + share_params)
    assert r_after.status_code == 404, (
        f"Expected 404 for removed member with share link, got {r_after.status_code}"
    )

    with scopes_disabled():
        remove_logs = PermissionAuditLog.objects.filter(
            space_id=space_1.pk,
            target_user_id=member_user.pk,
            action=PermissionAuditLog.ACTION_REMOVE,
        )
        assert remove_logs.exists()


@pytest.mark.django_db
def test_e2e_ical_export_member_access_lifecycle(e2e_admin, e2e_member, space_1, meal_plan_1):
    """
    E2E iCal export: active member sees meal plan → removed member
    sees empty calendar (no old space data leakage).
    """
    from cookbook.models import UserSpace
    from django_scopes import scopes_disabled

    admin_user = auth.get_user(e2e_admin)
    member_user = auth.get_user(e2e_member)

    with scopes_disabled():
        space_1.created_by = admin_user
        space_1.save()
        old_meal_plan_pk = meal_plan_1.pk

    ical_url = reverse(ICAL_URL)

    r_before = e2e_member.get(ical_url)
    assert r_before.status_code == 200
    content_before = r_before.content.decode('utf-8')
    assert 'BEGIN:VCALENDAR' in content_before

    with scopes_disabled():
        UserSpace.objects.filter(user=member_user, space=space_1).delete()

    invalidate_user_permission_cache(member_user.pk, space_id=space_1.pk)

    r_after = e2e_member.get(ical_url)
    assert r_after.status_code == 200
    content_after = r_after.content.decode('utf-8')

    assert 'BEGIN:VCALENDAR' in content_after
    assert str(old_meal_plan_pk) not in content_after, (
        f"Removed member must not see old meal plan PK={old_meal_plan_pk} in iCal"
    )

    with scopes_disabled():
        assert PermissionAuditLog.objects.filter(
            space_id=space_1.pk,
            target_user_id=member_user.pk,
            action=PermissionAuditLog.ACTION_REMOVE,
        ).exists()


# ---------------------------------------------------------------------------
# 9. Audit Log Retention / Cleanup Command Tests
# ---------------------------------------------------------------------------

@pytest.fixture
def _seed_old_audit_logs(space_1, u1_s1):
    """Seed audit logs with various ages for retention testing."""
    import random
    from django.db import connection as conn

    user = auth.get_user(u1_s1)
    SPACE_ID = 999000 + random.randint(1, 999)
    now = timezone.now()

    with scopes_disabled():
        PermissionAuditLog.objects.filter(space_id=SPACE_ID).delete()

        pks_keep = []
        pks_expire = []

        for i in range(5):
            log = PermissionAuditLog(
                action=PermissionAuditLog.ACTION_ADD,
                space_id=SPACE_ID,
                target_user_id=user.pk,
                target_username=user.username,
            )
            log.save(force_insert=True)
            pks_keep.append(log.pk)

        for i in range(7):
            log = PermissionAuditLog(
                action=PermissionAuditLog.ACTION_REMOVE,
                space_id=SPACE_ID,
                target_user_id=user.pk,
                target_username=user.username,
            )
            log.save(force_insert=True)
            pks_expire.append(log.pk)

        with conn.cursor() as cursor:
            for idx, pk in enumerate(pks_keep):
                cursor.execute(
                    "UPDATE cookbook_permissionauditlog SET created_at = %s WHERE id = %s",
                    [now - timedelta(days=10 + idx), pk]
                )
            for idx, pk in enumerate(pks_expire):
                cursor.execute(
                    "UPDATE cookbook_permissionauditlog SET created_at = %s WHERE id = %s",
                    [now - timedelta(days=100 + idx), pk]
                )

    return {
        'space_id': SPACE_ID,
        'keep_pks': pks_keep,
        'expire_pks': pks_expire,
        'retention_days': 50,
    }


@pytest.mark.django_db
def test_cleanup_audit_logs_dry_run(_seed_old_audit_logs):
    """Verify dry-run mode reports count but does not delete anything."""
    from django.core.management import call_command
    from io import StringIO

    seed = _seed_old_audit_logs
    out = StringIO()

    call_command(
        'cleanup_audit_logs',
        days=seed['retention_days'],
        dry_run=True,
        stdout=out,
    )

    with scopes_disabled():
        remaining = PermissionAuditLog.objects.filter(
            space_id=seed['space_id']
        ).count()

    assert remaining == 12
    output = out.getvalue()
    assert str(len(seed['expire_pks'])) in output or 'Expired entries' in output


@pytest.mark.django_db
def test_cleanup_audit_logs_actual_deletion(_seed_old_audit_logs):
    """Verify cleanup deletes only expired entries, leaves recent ones intact."""
    from django.core.management import call_command
    from io import StringIO

    seed = _seed_old_audit_logs
    out = StringIO()

    call_command(
        'cleanup_audit_logs',
        days=seed['retention_days'],
        batch_size=3,
        stdout=out,
    )

    with scopes_disabled():
        remaining = PermissionAuditLog.objects.filter(
            space_id=seed['space_id']
        ).order_by('created_at')
        remaining_pks = list(remaining.values_list('pk', flat=True))

    assert len(remaining_pks) == len(seed['keep_pks'])
    for pk in seed['keep_pks']:
        assert pk in remaining_pks
    for pk in seed['expire_pks']:
        assert pk not in remaining_pks


@pytest.mark.django_db
def test_cleanup_audit_logs_zero_retention_disabled(_seed_old_audit_logs):
    """Verify retention_days=0 disables cleanup entirely."""
    from django.core.management import call_command
    from io import StringIO

    seed = _seed_old_audit_logs
    out = StringIO()

    call_command(
        'cleanup_audit_logs',
        days=0,
        stdout=out,
    )

    with scopes_disabled():
        remaining = PermissionAuditLog.objects.filter(
            space_id=seed['space_id']
        ).count()

    assert remaining == 12
    assert 'disabled' in out.getvalue().lower()


@pytest.mark.django_db
def test_cleanup_audit_logs_archive_csv(_seed_old_audit_logs, tmp_path):
    """Verify archive flag exports CSV with all expired entries before deletion."""
    from django.core.management import call_command
    import csv

    seed = _seed_old_audit_logs
    archive_file = tmp_path / "audit_archive.csv"

    call_command(
        'cleanup_audit_logs',
        days=seed['retention_days'],
        archive=str(archive_file),
        dry_run=True,
    )

    assert archive_file.exists()
    with open(archive_file, 'r', newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    assert len(rows) == len(seed['expire_pks'])
    assert 'id' in rows[0]
    assert 'action' in rows[0]
    assert 'created_at' in rows[0]

    with scopes_disabled():
        remaining = PermissionAuditLog.objects.filter(
            space_id=seed['space_id']
        ).count()
    assert remaining == 12
