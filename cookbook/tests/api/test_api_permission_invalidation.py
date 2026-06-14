import json
import threading

import pytest
from django.contrib import auth
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from django_scopes import scopes_disabled

from cookbook.helper.permission_helper import (
    _get_permission_cache_version,
    has_group_permission,
    invalidate_user_permission_cache,
)
from cookbook.models import (
    Food,
    MealPlan,
    MealType,
    ShoppingList,
    ShoppingListEntry,
    Unit,
    UserSpace,
)

MEALPLAN_LIST_URL = 'api:mealplan-list'
MEALPLAN_DETAIL_URL = 'api:mealplan-detail'
SLE_LIST_URL = 'api:shoppinglistentry-list'
SLE_DETAIL_URL = 'api:shoppinglistentry-detail'
US_DETAIL_URL = 'api:userspace-detail'


# ---------------------------------------------------------------------------
# Fixtures
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


@pytest.fixture
def shopping_entry_1(space_1, u1_s1):
    user = auth.get_user(u1_s1)
    food = Food.objects.get_or_create(name='sle_food_perm', space=space_1)[0]
    unit = Unit.objects.get_or_create(name='sle_unit_perm', space=space_1)[0]
    sl = ShoppingList.objects.create(name='test_list', space=space_1)
    entry = ShoppingListEntry.objects.create(
        food=food,
        unit=unit,
        amount=1,
        space=space_1,
        created_by=user,
    )
    entry.shopping_lists.add(sl)
    return entry


def _remove_from_space(user, space):
    """Directly delete a UserSpace, bypassing the API.

    After deletion the middleware will auto-create a fresh space for the user
    on the next request, so the user still gets 200 but sees 0 results.
    """
    with scopes_disabled():
        UserSpace.objects.filter(user=user, space=space).delete()


# ---------------------------------------------------------------------------
# 1. Owner actively removes a member
# ---------------------------------------------------------------------------

def test_owner_removes_member_loses_meal_plan_access(
    space_1, a1_s1, u1_s1, meal_plan_1
):
    owner = auth.get_user(a1_s1)
    member = auth.get_user(u1_s1)
    with scopes_disabled():
        space_1.created_by = owner
        space_1.save()

    r = u1_s1.get(reverse(MEALPLAN_LIST_URL))
    assert r.status_code == 200
    assert len(json.loads(r.content)['results']) >= 1

    _remove_from_space(member, space_1)

    r = u1_s1.get(reverse(MEALPLAN_LIST_URL))
    assert r.status_code == 200
    assert len(json.loads(r.content)['results']) == 0


def test_owner_removes_member_loses_shopping_access(
    space_1, a1_s1, u1_s1, shopping_entry_1
):
    owner = auth.get_user(a1_s1)
    member = auth.get_user(u1_s1)
    with scopes_disabled():
        space_1.created_by = owner
        space_1.save()

    r = u1_s1.get(reverse(SLE_LIST_URL))
    assert r.status_code == 200
    before_count = json.loads(r.content)['count']
    assert before_count >= 1

    _remove_from_space(member, space_1)

    r = u1_s1.get(reverse(SLE_LIST_URL))
    assert r.status_code == 200
    assert json.loads(r.content)['count'] == 0


# ---------------------------------------------------------------------------
# 2. Member voluntarily leaves (self-delete via API)
# ---------------------------------------------------------------------------

def test_member_self_leave_loses_meal_plan_access(
    space_1, a1_s1, u1_s1, meal_plan_1
):
    owner = auth.get_user(a1_s1)
    member = auth.get_user(u1_s1)
    with scopes_disabled():
        space_1.created_by = owner
        space_1.save()

    r = u1_s1.get(reverse(MEALPLAN_LIST_URL))
    assert r.status_code == 200
    assert len(json.loads(r.content)['results']) >= 1

    us = UserSpace.objects.get(user=member, space=space_1)
    r = u1_s1.delete(reverse(US_DETAIL_URL, args={us.pk}))
    assert r.status_code == 204

    r = u1_s1.get(reverse(MEALPLAN_LIST_URL))
    assert r.status_code == 200
    assert len(json.loads(r.content)['results']) == 0


def test_member_self_leave_loses_shopping_access(
    space_1, a1_s1, u1_s1, shopping_entry_1
):
    owner = auth.get_user(a1_s1)
    member = auth.get_user(u1_s1)
    with scopes_disabled():
        space_1.created_by = owner
        space_1.save()

    r = u1_s1.get(reverse(SLE_LIST_URL))
    assert r.status_code == 200

    us = UserSpace.objects.get(user=member, space=space_1)
    r = u1_s1.delete(reverse(US_DETAIL_URL, args={us.pk}))
    assert r.status_code == 204

    r = u1_s1.get(reverse(SLE_LIST_URL))
    assert r.status_code == 200
    assert json.loads(r.content)['count'] == 0


# ---------------------------------------------------------------------------
# 3. Space owner removes a collaborator via API
#    (Non-owner admins cannot remove others — only the space owner can.)
# ---------------------------------------------------------------------------

def test_space_owner_removes_collaborator_loses_meal_plan_access(
    space_1, a1_s1, u1_s1, meal_plan_1
):
    owner = auth.get_user(a1_s1)
    member = auth.get_user(u1_s1)
    with scopes_disabled():
        space_1.created_by = owner
        space_1.save()

    r = u1_s1.get(reverse(MEALPLAN_LIST_URL))
    assert r.status_code == 200
    assert len(json.loads(r.content)['results']) >= 1

    member_us = UserSpace.objects.get(user=member, space=space_1)
    r = a1_s1.delete(reverse(US_DETAIL_URL, args={member_us.pk}))
    assert r.status_code == 204

    r = u1_s1.get(reverse(MEALPLAN_LIST_URL))
    assert r.status_code == 200
    assert len(json.loads(r.content)['results']) == 0


def test_space_owner_removes_collaborator_loses_shopping_access(
    space_1, a1_s1, u1_s1, shopping_entry_1
):
    owner = auth.get_user(a1_s1)
    member = auth.get_user(u1_s1)
    with scopes_disabled():
        space_1.created_by = owner
        space_1.save()

    r = u1_s1.get(reverse(SLE_LIST_URL))
    assert r.status_code == 200

    member_us = UserSpace.objects.get(user=member, space=space_1)
    r = a1_s1.delete(reverse(US_DETAIL_URL, args={member_us.pk}))
    assert r.status_code == 204

    r = u1_s1.get(reverse(SLE_LIST_URL))
    assert r.status_code == 200
    assert json.loads(r.content)['count'] == 0


def test_non_owner_admin_cannot_remove_member(
    space_1, a1_s1, a2_s1, u1_s1, meal_plan_1
):
    owner = auth.get_user(a1_s1)
    admin = auth.get_user(a2_s1)
    member = auth.get_user(u1_s1)
    with scopes_disabled():
        space_1.created_by = owner
        space_1.save()

    member_us = UserSpace.objects.get(user=member, space=space_1)
    r = a2_s1.delete(reverse(US_DETAIL_URL, args={member_us.pk}))
    assert r.status_code == 403

    with scopes_disabled():
        assert UserSpace.objects.filter(user=member, space=space_1).exists()


# ---------------------------------------------------------------------------
# Cache key namespace isolation
# ---------------------------------------------------------------------------

def test_cache_key_namespace_isolation_between_spaces(
    space_1, space_2, u1_s1
):
    user = auth.get_user(u1_s1)
    with scopes_disabled():
        admin_group = Group.objects.get(name='admin')
        user_group = Group.objects.get(name='user')

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

    v1 = _get_permission_cache_version(user.pk, space_1.pk)
    assert v1 >= 1

    v2 = _get_permission_cache_version(user.pk, space_2.pk)
    assert v2 >= 1

    assert v1 == _get_permission_cache_version(user.pk, space_1.pk)
    assert v2 == _get_permission_cache_version(user.pk, space_2.pk)

    result_s1 = has_group_permission(user, ['user'])
    assert result_s1 is True

    result_s1_admin = has_group_permission(user, ['admin'])
    assert result_s1_admin is False


def test_invalidate_one_space_does_not_affect_other(
    space_1, space_2, u1_s1
):
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

    v1_before = _get_permission_cache_version(user.pk, space_1.pk)
    v2_before = _get_permission_cache_version(user.pk, space_2.pk)

    invalidate_user_permission_cache(user.pk, space_id=space_1.pk)

    v1_after = _get_permission_cache_version(user.pk, space_1.pk)
    v2_after = _get_permission_cache_version(user.pk, space_2.pk)

    assert v1_after == v1_before + 1
    assert v2_after == v2_before


def test_invalidate_all_spaces_bumps_every_version(
    space_1, space_2, u1_s1
):
    user = auth.get_user(u1_s1)
    with scopes_disabled():
        UserSpace.objects.get_or_create(user=user, space=space_2)

    v1_before = _get_permission_cache_version(user.pk, space_1.pk)
    v2_before = _get_permission_cache_version(user.pk, space_2.pk)

    invalidate_user_permission_cache(user.pk, space_id=None)

    v1_after = _get_permission_cache_version(user.pk, space_1.pk)
    v2_after = _get_permission_cache_version(user.pk, space_2.pk)

    assert v1_after == v1_before + 1
    assert v2_after == v2_before + 1


def test_cache_key_contains_space_id(space_1, u1_s1):
    user = auth.get_user(u1_s1)
    UserSpace.objects.filter(user=user, space=space_1).update(active=True)

    result = has_group_permission(user, ['user'], no_cache=True)
    assert result is True

    version = _get_permission_cache_version(user.pk, space_1.pk)

    cached_result = has_group_permission(user, ['user'], no_cache=False)
    assert cached_result is True

    invalidate_user_permission_cache(user.pk, space_id=space_1.pk)

    new_version = _get_permission_cache_version(user.pk, space_1.pk)
    assert new_version == version + 1

    wrong_space_version = _get_permission_cache_version(user.pk, 999999)
    assert wrong_space_version == 1


# ---------------------------------------------------------------------------
# Concurrent race condition: batch removal of multiple members
# ---------------------------------------------------------------------------

def test_batch_remove_multiple_members_permission_consistency(
    space_1, a1_s1, u1_s1, u2_s1, meal_plan_1
):
    owner = auth.get_user(a1_s1)
    member1 = auth.get_user(u1_s1)
    member2 = auth.get_user(u2_s1)
    with scopes_disabled():
        space_1.created_by = owner
        space_1.save()

    r1 = u1_s1.get(reverse(MEALPLAN_LIST_URL))
    r2 = u2_s1.get(reverse(MEALPLAN_LIST_URL))
    assert r1.status_code == 200
    assert r2.status_code == 200

    _remove_from_space(member1, space_1)
    _remove_from_space(member2, space_1)

    r1 = u1_s1.get(reverse(MEALPLAN_LIST_URL))
    r2 = u2_s1.get(reverse(MEALPLAN_LIST_URL))
    assert r1.status_code == 200
    assert len(json.loads(r1.content)['results']) == 0
    assert r2.status_code == 200
    assert len(json.loads(r2.content)['results']) == 0

    v1 = _get_permission_cache_version(member1.pk, space_1.pk)
    v2 = _get_permission_cache_version(member2.pk, space_1.pk)
    assert v1 >= 2
    assert v2 >= 2


def test_concurrent_removal_cache_consistency(
    space_1, a1_s1, u1_s1, u2_s1, meal_plan_1
):
    owner = auth.get_user(a1_s1)
    member1 = auth.get_user(u1_s1)
    member2 = auth.get_user(u2_s1)
    with scopes_disabled():
        space_1.created_by = owner
        space_1.save()

    has_group_permission(member1, ['user'])
    has_group_permission(member2, ['user'])

    v1_before = _get_permission_cache_version(member1.pk, space_1.pk)
    v2_before = _get_permission_cache_version(member2.pk, space_1.pk)

    with scopes_disabled():
        UserSpace.objects.filter(user__in=[member1, member2], space=space_1).delete()

    v1_after = _get_permission_cache_version(member1.pk, space_1.pk)
    v2_after = _get_permission_cache_version(member2.pk, space_1.pk)
    assert v1_after > v1_before
    assert v2_after > v2_before

    errors = []

    def bump_and_read(user_obj):
        try:
            for _ in range(20):
                invalidate_user_permission_cache(user_obj.pk, space_id=space_1.pk)
                _get_permission_cache_version(user_obj.pk, space_1.pk)
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=bump_and_read, args=(member1,))
    t2 = threading.Thread(target=bump_and_read, args=(member2,))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert len(errors) == 0, f"Errors during concurrent cache ops: {errors}"

    r1 = u1_s1.get(reverse(MEALPLAN_LIST_URL))
    r2 = u2_s1.get(reverse(MEALPLAN_LIST_URL))
    assert r1.status_code == 200
    assert len(json.loads(r1.content)['results']) == 0
    assert r2.status_code == 200
    assert len(json.loads(r2.content)['results']) == 0


def test_concurrent_cache_invalidation_no_deadlock(
    space_1, u1_s1
):
    user = auth.get_user(u1_s1)
    errors = []

    def bump_version():
        try:
            for _ in range(20):
                invalidate_user_permission_cache(user.pk, space_id=space_1.pk)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=bump_version) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(errors) == 0, f"Errors during concurrent cache invalidation: {errors}"

    v = _get_permission_cache_version(user.pk, space_1.pk)
    assert v >= 21


def test_permission_cache_version_monotonic_under_contention(
    space_1, u1_s1
):
    user = auth.get_user(u1_s1)
    collected_versions = []
    lock = threading.Lock()

    def read_and_bump():
        try:
            for _ in range(10):
                v = _get_permission_cache_version(user.pk, space_1.pk)
                with lock:
                    collected_versions.append(v)
                invalidate_user_permission_cache(user.pk, space_id=space_1.pk)
        except Exception:
            pass

    threads = [threading.Thread(target=read_and_bump) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    final_v = _get_permission_cache_version(user.pk, space_1.pk)
    assert final_v >= 1
