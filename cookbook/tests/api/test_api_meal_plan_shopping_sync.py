import json
from datetime import timedelta
from django.utils import timezone

import pytest
from django.contrib import auth
from django.urls import reverse
from django_scopes import scope, scopes_disabled
from rest_framework.test import APIClient

from cookbook.helper.shopping_helper import MealPlanShoppingSync, CHANGE_TYPE_ADD, CHANGE_TYPE_MERGE, CHANGE_TYPE_REMOVE
from cookbook.models import Household, MealPlan, MealType, Recipe, ShoppingListRecipe, UserSpace
from cookbook.tests.factories import RecipeFactory

PREVIEW_URL = 'api:meal-plan-shopping-sync-preview'
APPLY_URL = 'api:meal-plan-shopping-sync-apply'


@pytest.fixture()
def meal_type(space_1, u1_s1):
    return MealType.objects.get_or_create(name='test_sync',
                                          space=space_1,
                                          created_by=auth.get_user(u1_s1))[0]


@pytest.fixture()
def recipe_with_ingredients(space_1, u1_s1):
    user = auth.get_user(u1_s1)
    with scope(space=space_1):
        recipe = RecipeFactory.create(space=space_1, created_by=user)
    return recipe


@pytest.fixture()
def meal_plan_1(space_1, recipe_with_ingredients, meal_type, u1_s1):
    user = auth.get_user(u1_s1)
    return MealPlan.objects.create(
        recipe=recipe_with_ingredients,
        space=space_1,
        meal_type=meal_type,
        from_date=timezone.now(),
        to_date=timezone.now(),
        created_by=user,
        servings=2
    )


@pytest.fixture()
def meal_plan_2(space_1, recipe_with_ingredients, meal_type, u1_s1):
    user = auth.get_user(u1_s1)
    return MealPlan.objects.create(
        recipe=recipe_with_ingredients,
        space=space_1,
        meal_type=meal_type,
        from_date=timezone.now() + timedelta(days=1),
        to_date=timezone.now() + timedelta(days=1),
        created_by=user,
        servings=2
    )


@pytest.fixture()
def meal_plan_outside_range(space_1, recipe_with_ingredients, meal_type, u1_s1):
    user = auth.get_user(u1_s1)
    return MealPlan.objects.create(
        recipe=recipe_with_ingredients,
        space=space_1,
        meal_type=meal_type,
        from_date=timezone.now() - timedelta(days=30),
        to_date=timezone.now() - timedelta(days=30),
        created_by=user,
        servings=2
    )


@pytest.fixture()
def existing_slr(space_1, meal_plan_1, u1_s1):
    user = auth.get_user(u1_s1)
    slr = ShoppingListRecipe.objects.create(
        mealplan=meal_plan_1,
        recipe=meal_plan_1.recipe,
        servings=2,
        space=space_1,
        created_by=user
    )
    return slr


@pytest.mark.parametrize("arg, expected_status", [
    ['a_u', 403],
    ['g1_s1', 403],
    ['u1_s1', 200],
    ['a1_s1', 200],
])
def test_preview_permission(arg, expected_status, request, meal_plan_1, u1_s1):
    c = request.getfixturevalue(arg)
    today = timezone.localtime(timezone.now()).strftime("%Y-%m-%d")
    r = c.post(reverse(PREVIEW_URL), {
        'from_date': today,
        'to_date': today,
    }, content_type='application/json')
    assert r.status_code == expected_status


@pytest.mark.parametrize("arg, expected_status", [
    ['a_u', 403],
    ['g1_s1', 403],
    ['u1_s1', 200],
    ['a1_s1', 200],
])
def test_apply_permission(arg, expected_status, request, meal_plan_1, u1_s1):
    c = request.getfixturevalue(arg)
    today = timezone.localtime(timezone.now()).strftime("%Y-%m-%d")
    r = c.post(reverse(APPLY_URL), {
        'from_date': today,
        'to_date': today,
    }, content_type='application/json')
    assert r.status_code == expected_status


def test_preview_add_changes(u1_s1, meal_plan_1, meal_plan_2):
    today = timezone.localtime(timezone.now()).strftime("%Y-%m-%d")
    tomorrow = (timezone.localtime(timezone.now()) + timedelta(days=1)).strftime("%Y-%m-%d")

    r = u1_s1.post(reverse(PREVIEW_URL), {
        'from_date': today,
        'to_date': tomorrow,
    }, content_type='application/json')

    assert r.status_code == 200
    response = json.loads(r.content)
    assert 'changes' in response
    assert 'summary' in response
    assert response['summary']['add'] == 2
    assert response['summary']['merge'] == 0
    assert response['summary']['remove'] == 0

    for change in response['changes']:
        assert change['change_type'] == CHANGE_TYPE_ADD
        assert change['mealplan_id'] in [meal_plan_1.id, meal_plan_2.id]
        assert len(change['ingredients']) > 0


def test_preview_merge_changes(u1_s1, meal_plan_1, existing_slr):
    today = timezone.localtime(timezone.now()).strftime("%Y-%m-%d")

    meal_plan_1.servings = 4
    meal_plan_1.save()

    r = u1_s1.post(reverse(PREVIEW_URL), {
        'from_date': today,
        'to_date': today,
    }, content_type='application/json')

    assert r.status_code == 200
    response = json.loads(r.content)
    assert response['summary']['merge'] == 1
    assert response['summary']['add'] == 0
    assert response['summary']['remove'] == 0

    change = response['changes'][0]
    assert change['change_type'] == CHANGE_TYPE_MERGE
    assert change['mealplan_id'] == meal_plan_1.id
    assert change['shopping_list_recipe_id'] == existing_slr.id


def test_preview_remove_changes(u1_s1, meal_plan_1, meal_plan_outside_range, existing_slr):
    today = timezone.localtime(timezone.now()).strftime("%Y-%m-%d")
    tomorrow = (timezone.localtime(timezone.now()) + timedelta(days=1)).strftime("%Y-%m-%d")

    with scopes_disabled():
        slr_outside = ShoppingListRecipe.objects.create(
            mealplan=meal_plan_outside_range,
            recipe=meal_plan_outside_range.recipe,
            servings=2,
            space=meal_plan_outside_range.space,
            created_by=auth.get_user(u1_s1)
        )

    r = u1_s1.post(reverse(PREVIEW_URL), {
        'from_date': today,
        'to_date': tomorrow,
    }, content_type='application/json')

    assert r.status_code == 200
    response = json.loads(r.content)
    assert response['summary']['remove'] >= 1

    remove_changes = [c for c in response['changes'] if c['change_type'] == CHANGE_TYPE_REMOVE]
    assert len(remove_changes) >= 1
    assert any(c['shopping_list_recipe_id'] == slr_outside.id for c in remove_changes)


def test_apply_add_changes(u1_s1, meal_plan_1):
    today = timezone.localtime(timezone.now()).strftime("%Y-%m-%d")

    with scopes_disabled():
        initial_count = ShoppingListRecipe.objects.filter(mealplan=meal_plan_1).count()
        assert initial_count == 0

    r = u1_s1.post(reverse(APPLY_URL), {
        'from_date': today,
        'to_date': today,
    }, content_type='application/json')

    assert r.status_code == 200
    response = json.loads(r.content)
    assert response['added'] == 1
    assert response['merged'] == 0
    assert response['removed'] == 0
    assert response['failed'] == 0

    with scopes_disabled():
        final_count = ShoppingListRecipe.objects.filter(mealplan=meal_plan_1).count()
        assert final_count == 1


def test_apply_merge_changes(u1_s1, meal_plan_1, existing_slr):
    today = timezone.localtime(timezone.now()).strftime("%Y-%m-%d")

    meal_plan_1.servings = 4
    meal_plan_1.save()

    r = u1_s1.post(reverse(APPLY_URL), {
        'from_date': today,
        'to_date': today,
    }, content_type='application/json')

    assert r.status_code == 200
    response = json.loads(r.content)
    assert response['merged'] == 1

    with scopes_disabled():
        existing_slr.refresh_from_db()
        assert existing_slr.servings == 4


def test_apply_remove_changes(u1_s1, meal_plan_outside_range):
    today = timezone.localtime(timezone.now()).strftime("%Y-%m-%d")
    tomorrow = (timezone.localtime(timezone.now()) + timedelta(days=1)).strftime("%Y-%m-%d")

    user = auth.get_user(u1_s1)
    with scopes_disabled():
        slr = ShoppingListRecipe.objects.create(
            mealplan=meal_plan_outside_range,
            recipe=meal_plan_outside_range.recipe,
            servings=2,
            space=meal_plan_outside_range.space,
            created_by=user
        )
        initial_count = ShoppingListRecipe.objects.filter(id=slr.id).count()
        assert initial_count == 1

    r = u1_s1.post(reverse(APPLY_URL), {
        'from_date': today,
        'to_date': tomorrow,
    }, content_type='application/json')

    assert r.status_code == 200
    response = json.loads(r.content)
    assert response['removed'] >= 1

    with scopes_disabled():
        final_count = ShoppingListRecipe.objects.filter(id=slr.id).count()
        assert final_count == 0


def test_apply_selected_changes(u1_s1, meal_plan_1, meal_plan_2):
    today = timezone.localtime(timezone.now()).strftime("%Y-%m-%d")
    tomorrow = (timezone.localtime(timezone.now()) + timedelta(days=1)).strftime("%Y-%m-%d")

    preview_r = u1_s1.post(reverse(PREVIEW_URL), {
        'from_date': today,
        'to_date': tomorrow,
    }, content_type='application/json')
    preview_data = json.loads(preview_r.content)
    assert len(preview_data['changes']) == 2

    apply_r = u1_s1.post(reverse(APPLY_URL), {
        'from_date': today,
        'to_date': tomorrow,
        'selected_changes': [0]
    }, content_type='application/json')

    assert apply_r.status_code == 200
    response = json.loads(apply_r.content)
    assert response['added'] == 1
    assert response['merged'] == 0
    assert response['removed'] == 0

    with scopes_disabled():
        mp1_count = ShoppingListRecipe.objects.filter(mealplan=meal_plan_1).count()
        mp2_count = ShoppingListRecipe.objects.filter(mealplan=meal_plan_2).count()
        assert mp1_count + mp2_count == 1


def test_household_permissions(u1_s1, u2_s1, meal_plan_1, space_1):
    today = timezone.localtime(timezone.now()).strftime("%Y-%m-%d")
    user1 = auth.get_user(u1_s1)
    user2 = auth.get_user(u2_s1)

    results = json.loads(u2_s1.get(reverse('api:mealplan-list')).content)['results']
    assert len(results) == 0

    with scopes_disabled():
        household = Household.objects.create(name='test_household', space=space_1)
        UserSpace.objects.filter(user__in=[user1, user2], space=space_1).update(household=household)

    r = u2_s1.post(reverse(PREVIEW_URL), {
        'from_date': today,
        'to_date': today,
    }, content_type='application/json')

    assert r.status_code == 200
    response = json.loads(r.content)
    assert response['summary']['add'] == 1

    change = response['changes'][0]
    assert change['mealplan_id'] == meal_plan_1.id

    apply_r = u2_s1.post(reverse(APPLY_URL), {
        'from_date': today,
        'to_date': today,
    }, content_type='application/json')

    assert apply_r.status_code == 200
    apply_response = json.loads(apply_r.content)
    assert apply_response['added'] == 1


def test_space_scoping(u1_s1, u1_s2, meal_plan_1, space_2):
    today = timezone.localtime(timezone.now()).strftime("%Y-%m-%d")

    r = u1_s2.post(reverse(PREVIEW_URL), {
        'from_date': today,
        'to_date': today,
    }, content_type='application/json')

    assert r.status_code == 200
    response = json.loads(r.content)
    assert response['summary']['add'] == 0

    apply_r = u1_s2.post(reverse(APPLY_URL), {
        'from_date': today,
        'to_date': today,
    }, content_type='application/json')

    assert apply_r.status_code == 200
    apply_response = json.loads(apply_r.content)
    assert apply_response['added'] == 0

    with scopes_disabled():
        count = ShoppingListRecipe.objects.filter(mealplan=meal_plan_1).count()
        assert count == 0


def test_preview_and_apply_same_logic(u1_s1, meal_plan_1):
    today = timezone.localtime(timezone.now()).strftime("%Y-%m-%d")

    preview_r = u1_s1.post(reverse(PREVIEW_URL), {
        'from_date': today,
        'to_date': today,
    }, content_type='application/json')
    preview_data = json.loads(preview_r.content)

    apply_r = u1_s1.post(reverse(APPLY_URL), {
        'from_date': today,
        'to_date': today,
    }, content_type='application/json')
    apply_data = json.loads(apply_r.content)

    assert preview_data['summary']['add'] == apply_data['added']
    assert preview_data['summary']['merge'] == apply_data['merged']
    assert preview_data['summary']['remove'] == apply_data['removed']


def test_sync_class_check_permissions(u1_s1, space_1):
    user = auth.get_user(u1_s1)
    today = timezone.now().date()

    sync = MealPlanShoppingSync(
        user=user,
        space=space_1,
        from_date=today,
        to_date=today
    )

    has_permission, error_msg = sync.check_permissions()
    assert has_permission is True
    assert error_msg is None


def test_sync_class_unauthenticated_user(space_1):
    from django.contrib.auth.models import AnonymousUser
    today = timezone.now().date()

    sync = MealPlanShoppingSync(
        user=AnonymousUser(),
        space=space_1,
        from_date=today,
        to_date=today
    )

    has_permission, error_msg = sync.check_permissions()
    assert has_permission is False
    assert error_msg is not None
