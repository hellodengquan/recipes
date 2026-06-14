import json
import threading
import uuid

import pytest
from django.contrib import auth
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
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
