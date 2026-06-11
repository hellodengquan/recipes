import json
import time
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib import auth
from django.db import connection
from django.urls import reverse
from django_scopes import scopes_disabled
from django.utils import timezone

from cookbook.helper.shopping_helper import (
    CHANGE_TYPE_ADD, CHANGE_TYPE_MERGE, CHANGE_TYPE_REMOVE,
    MealPlanShoppingSync,
)
from cookbook.models import (
    Household, Ingredient, MealPlan, MealType, Recipe, ShoppingListEntry,
    ShoppingListRecipe, Step, Unit, Food, UserSpace,
)

PREVIEW_URL = 'api:meal-plan-shopping-sync-preview'
APPLY_URL = 'api:meal-plan-shopping-sync-apply'


def _make_recipe_with_n_ingredients(space, user, n):
    r = Recipe.objects.create(
        name=str(uuid.uuid4()),
        waiting_time=20,
        working_time=20,
        servings=2,
        created_by=user,
        space=space,
        internal=True,
    )
    s = Step.objects.create(
        name=str(uuid.uuid4()),
        instruction=str(uuid.uuid4()),
        space=space,
    )
    r.steps.add(s)
    for _ in range(n):
        s.ingredients.add(
            Ingredient.objects.create(
                amount=1,
                food=Food.objects.get_or_create(
                    name=str(uuid.uuid4()), space=space)[0],
                unit=Unit.objects.create(
                    name=str(uuid.uuid4()), space=space),
                note=str(uuid.uuid4()),
                space=space,
            )
        )
    return r


def _make_slr_from_mealplan(mealplan, recipe, user, space, servings=2, ingredient_subset=None):
    slr = ShoppingListRecipe.objects.create(
        mealplan=mealplan,
        recipe=recipe,
        servings=servings,
        space=space,
        created_by=user
    )
    step = recipe.step_set.first()
    ingredients = list(step.ingredient_set.all())
    if ingredient_subset is not None:
        ingredients = ingredients[:ingredient_subset]
    for ing in ingredients:
        ShoppingListEntry.objects.create(
            list_recipe=slr,
            food=ing.food,
            unit=ing.unit,
            ingredient=ing,
            amount=ing.amount,
            created_by=user,
            space=space
        )
    return slr


@pytest.fixture()
def cc_meal_type(space_1, u1_s1):
    return MealType.objects.get_or_create(
        name='cc_consistency_test',
        space=space_1,
        created_by=auth.get_user(u1_s1)
    )[0]


@pytest.fixture()
def cc_recipe_10(space_1, u1_s1):
    user = auth.get_user(u1_s1)
    return _make_recipe_with_n_ingredients(space_1, user, 10)


@pytest.fixture()
def cc_recipe_5(space_1, u1_s1):
    user = auth.get_user(u1_s1)
    return _make_recipe_with_n_ingredients(space_1, user, 5)


class TestSyncTokenGeneration:
    """测试 sync_token 生成和验证"""

    def test_preview_returns_sync_token(
        self, u1_s1, space_1, cc_meal_type, cc_recipe_10
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        MealPlan.objects.create(
            recipe=cc_recipe_10,
            space=space_1,
            meal_type=cc_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=2
        )

        r = u1_s1.post(reverse(PREVIEW_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')

        assert r.status_code == 200
        data = json.loads(r.content)
        assert 'sync_token' in data
        assert data['sync_token'] is not None
        assert isinstance(data['sync_token'], str)
        assert len(data['sync_token']) == 64

    def test_token_consistent_for_same_data(
        self, u1_s1, space_1, cc_meal_type, cc_recipe_10
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_date = today.date()

        MealPlan.objects.create(
            recipe=cc_recipe_10,
            space=space_1,
            meal_type=cc_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=2
        )

        sync1 = MealPlanShoppingSync(
            user=user,
            space=space_1,
            from_date=today_date,
            to_date=today_date
        )
        token1 = sync1._generate_sync_token()

        sync2 = MealPlanShoppingSync(
            user=user,
            space=space_1,
            from_date=today_date,
            to_date=today_date
        )
        token2 = sync2._generate_sync_token()

        assert token1 == token2

    def test_token_changes_after_mealplan_modification(
        self, u1_s1, space_1, cc_meal_type, cc_recipe_10
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_date = today.date()

        mp = MealPlan.objects.create(
            recipe=cc_recipe_10,
            space=space_1,
            meal_type=cc_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=2
        )

        sync = MealPlanShoppingSync(
            user=user,
            space=space_1,
            from_date=today_date,
            to_date=today_date
        )
        token_before = sync._generate_sync_token()

        mp.servings = 4
        mp.save()

        token_after = sync._generate_sync_token()

        assert token_before != token_after

    def test_token_changes_after_slr_modification(
        self, u1_s1, space_1, cc_meal_type, cc_recipe_10
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_date = today.date()

        mp = MealPlan.objects.create(
            recipe=cc_recipe_10,
            space=space_1,
            meal_type=cc_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=2
        )
        slr = _make_slr_from_mealplan(mp, cc_recipe_10, user, space_1, servings=2)

        sync = MealPlanShoppingSync(
            user=user,
            space=space_1,
            from_date=today_date,
            to_date=today_date
        )
        token_before = sync._generate_sync_token()

        slr.servings = 4
        slr.save()

        token_after = sync._generate_sync_token()

        assert token_before != token_after

    def test_token_changes_after_entry_modification(
        self, u1_s1, space_1, cc_meal_type, cc_recipe_10
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_date = today.date()

        mp = MealPlan.objects.create(
            recipe=cc_recipe_10,
            space=space_1,
            meal_type=cc_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=2
        )
        slr = _make_slr_from_mealplan(mp, cc_recipe_10, user, space_1, servings=2)

        sync = MealPlanShoppingSync(
            user=user,
            space=space_1,
            from_date=today_date,
            to_date=today_date
        )
        token_before = sync._generate_sync_token()

        entry = slr.entries.first()
        entry.checked = True
        entry.save()

        token_after = sync._generate_sync_token()

        assert token_before != token_after


class TestConcurrentModificationRejection:
    """测试并发修改时的拒绝机制"""

    def test_apply_without_token_rejected(
        self, u1_s1, space_1, cc_meal_type, cc_recipe_10
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        MealPlan.objects.create(
            recipe=cc_recipe_10,
            space=space_1,
            meal_type=cc_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=2
        )

        r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')

        assert r.status_code == 400

    def test_apply_with_invalid_token_rejected(
        self, u1_s1, space_1, cc_meal_type, cc_recipe_10
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        MealPlan.objects.create(
            recipe=cc_recipe_10,
            space=space_1,
            meal_type=cc_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=2
        )

        r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': today_str,
            'sync_token': 'invalid_token_123456789012345678901234567890123456789012345678901234567890',
        }, content_type='application/json')

        assert r.status_code == 409
        data = json.loads(r.content)
        assert data['token_invalid'] is True
        assert data['token_error'] is not None
        assert data['added'] == 0
        assert data['merged'] == 0
        assert data['removed'] == 0

    def test_concurrent_mealplan_modification_rejected(
        self, u1_s1, space_1, cc_meal_type, cc_recipe_10
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        mp = MealPlan.objects.create(
            recipe=cc_recipe_10,
            space=space_1,
            meal_type=cc_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=2
        )

        preview_r = u1_s1.post(reverse(PREVIEW_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')
        preview_data = json.loads(preview_r.content)
        valid_token = preview_data['sync_token']
        assert preview_data['summary']['add'] == 1

        mp.servings = 5
        mp.save()

        apply_r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': today_str,
            'sync_token': valid_token,
        }, content_type='application/json')

        assert apply_r.status_code == 409
        apply_data = json.loads(apply_r.content)
        assert apply_data['token_invalid'] is True
        assert 'Data has been modified' in apply_data['token_error']

        with scopes_disabled():
            assert ShoppingListRecipe.objects.filter(mealplan=mp).count() == 0

    def test_concurrent_slr_addition_rejected(
        self, u1_s1, space_1, cc_meal_type, cc_recipe_10, cc_recipe_5
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        mp1 = MealPlan.objects.create(
            recipe=cc_recipe_10,
            space=space_1,
            meal_type=cc_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=2
        )
        mp2 = MealPlan.objects.create(
            recipe=cc_recipe_5,
            space=space_1,
            meal_type=cc_meal_type,
            from_date=today + timedelta(days=1),
            to_date=today + timedelta(days=1),
            created_by=user,
            servings=2
        )

        preview_r = u1_s1.post(reverse(PREVIEW_URL), {
            'from_date': today_str,
            'to_date': (today + timedelta(days=1)).strftime("%Y-%m-%d"),
        }, content_type='application/json')
        preview_data = json.loads(preview_r.content)
        valid_token = preview_data['sync_token']
        assert preview_data['summary']['add'] == 2

        _make_slr_from_mealplan(mp2, cc_recipe_5, user, space_1, servings=2)

        apply_r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': (today + timedelta(days=1)).strftime("%Y-%m-%d"),
            'sync_token': valid_token,
        }, content_type='application/json')

        assert apply_r.status_code == 409
        apply_data = json.loads(apply_r.content)
        assert apply_data['token_invalid'] is True

        with scopes_disabled():
            assert ShoppingListRecipe.objects.filter(mealplan=mp1).count() == 0
            assert ShoppingListRecipe.objects.filter(mealplan=mp2).count() == 1

    def test_concurrent_slr_modification_rejected(
        self, u1_s1, space_1, cc_meal_type, cc_recipe_10
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        mp = MealPlan.objects.create(
            recipe=cc_recipe_10,
            space=space_1,
            meal_type=cc_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=2
        )
        slr = _make_slr_from_mealplan(mp, cc_recipe_10, user, space_1, servings=2, ingredient_subset=5)

        mp.servings = 4
        mp.save()

        preview_r = u1_s1.post(reverse(PREVIEW_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')
        preview_data = json.loads(preview_r.content)
        valid_token = preview_data['sync_token']
        assert preview_data['summary']['merge'] == 1

        entry = slr.entries.first()
        entry.amount = 999
        entry.save()

        apply_r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': today_str,
            'sync_token': valid_token,
        }, content_type='application/json')

        assert apply_r.status_code == 409
        apply_data = json.loads(apply_r.content)
        assert apply_data['token_invalid'] is True

        with scopes_disabled():
            slr.refresh_from_db()
            assert slr.servings == 2
            assert entry.amount == 999

    def test_concurrent_remove_modification_rejected(
        self, u1_s1, space_1, cc_meal_type, cc_recipe_10
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        mp_old = MealPlan.objects.create(
            recipe=cc_recipe_10,
            space=space_1,
            meal_type=cc_meal_type,
            from_date=today - timedelta(days=60),
            to_date=today - timedelta(days=60),
            created_by=user,
            servings=2
        )
        slr_old = _make_slr_from_mealplan(mp_old, cc_recipe_10, user, space_1, servings=2)

        preview_r = u1_s1.post(reverse(PREVIEW_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')
        preview_data = json.loads(preview_r.content)
        valid_token = preview_data['sync_token']
        assert preview_data['summary']['remove'] == 1

        slr_old.servings = 5
        slr_old.save()

        apply_r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': today_str,
            'sync_token': valid_token,
        }, content_type='application/json')

        assert apply_r.status_code == 409
        apply_data = json.loads(apply_r.content)
        assert apply_data['token_invalid'] is True

        with scopes_disabled():
            assert ShoppingListRecipe.objects.filter(id=slr_old.id).exists()


class TestMultiUserConcurrentScenario:
    """模拟多用户并发修改场景"""

    def test_user_b_modifies_after_user_a_preview(
        self, u1_s1, u2_s1, space_1, cc_meal_type, cc_recipe_10
    ):
        user1 = auth.get_user(u1_s1)
        user2 = auth.get_user(u2_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        with scopes_disabled():
            household = Household.objects.create(name='cc_household', space=space_1)
            UserSpace.objects.filter(
                user__in=[user1, user2], space=space_1
            ).update(household=household)

        mp = MealPlan.objects.create(
            recipe=cc_recipe_10,
            space=space_1,
            meal_type=cc_meal_type,
            from_date=today,
            to_date=today,
            created_by=user1,
            servings=2
        )

        preview_r = u1_s1.post(reverse(PREVIEW_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')
        preview_data = json.loads(preview_r.content)
        user_a_token = preview_data['sync_token']

        mp.servings = 6
        mp.save()

        apply_r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': today_str,
            'sync_token': user_a_token,
        }, content_type='application/json')

        assert apply_r.status_code == 409
        apply_data = json.loads(apply_r.content)
        assert apply_data['token_invalid'] is True

        with scopes_disabled():
            assert ShoppingListRecipe.objects.filter(mealplan=mp).count() == 0

    def test_user_b_adds_entry_after_user_a_preview(
        self, u1_s1, u2_s1, space_1, cc_meal_type, cc_recipe_10
    ):
        user1 = auth.get_user(u1_s1)
        user2 = auth.get_user(u2_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        with scopes_disabled():
            household = Household.objects.create(name='cc_household2', space=space_1)
            UserSpace.objects.filter(
                user__in=[user1, user2], space=space_1
            ).update(household=household)

        mp_merge = MealPlan.objects.create(
            recipe=cc_recipe_10,
            space=space_1,
            meal_type=cc_meal_type,
            from_date=today,
            to_date=today,
            created_by=user1,
            servings=4
        )
        slr = _make_slr_from_mealplan(
            mp_merge, cc_recipe_10, user1, space_1, servings=2, ingredient_subset=5
        )

        preview_r = u1_s1.post(reverse(PREVIEW_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')
        preview_data = json.loads(preview_r.content)
        user_a_token = preview_data['sync_token']

        step = cc_recipe_10.step_set.first()
        ing = step.ingredient_set.first()
        ShoppingListEntry.objects.create(
            list_recipe=slr,
            food=ing.food,
            unit=ing.unit,
            ingredient=ing,
            amount=999,
            created_by=user2,
            space=space_1
        )

        apply_r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': today_str,
            'sync_token': user_a_token,
        }, content_type='application/json')

        assert apply_r.status_code == 409
        apply_data = json.loads(apply_r.content)
        assert apply_data['token_invalid'] is True

        with scopes_disabled():
            slr.refresh_from_db()
            assert slr.servings == 2
            entries = ShoppingListEntry.objects.filter(list_recipe=slr).order_by('id')
            assert entries.count() == 6
            assert entries.last().amount == 999


class TestRePreviewAfterRejection:
    """测试拒绝后重新预览并应用"""

    def test_re_preview_and_apply_success(
        self, u1_s1, space_1, cc_meal_type, cc_recipe_10
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        mp = MealPlan.objects.create(
            recipe=cc_recipe_10,
            space=space_1,
            meal_type=cc_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=2
        )

        preview1_r = u1_s1.post(reverse(PREVIEW_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')
        preview1_data = json.loads(preview1_r.content)
        old_token = preview1_data['sync_token']

        mp.servings = 5
        mp.save()

        apply1_r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': today_str,
            'sync_token': old_token,
        }, content_type='application/json')
        assert apply1_r.status_code == 409

        preview2_r = u1_s1.post(reverse(PREVIEW_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')
        preview2_data = json.loads(preview2_r.content)
        new_token = preview2_data['sync_token']

        assert old_token != new_token

        apply2_r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': today_str,
            'sync_token': new_token,
        }, content_type='application/json')
        assert apply2_r.status_code == 200
        apply2_data = json.loads(apply2_r.content)
        assert apply2_data['token_invalid'] is False
        assert apply2_data['added'] == 1

        with scopes_disabled():
            slr = ShoppingListRecipe.objects.get(mealplan=mp)
            assert slr.servings == 5


class TestTokenValidationEdgeCases:
    """token 验证的边界情况"""

    def test_empty_token_rejected(self, u1_s1, space_1, cc_meal_type, cc_recipe_10):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_date = today.date()

        sync = MealPlanShoppingSync(
            user=user,
            space=space_1,
            from_date=today_date,
            to_date=today_date
        )

        valid, error = sync.validate_sync_token("")
        assert valid is False
        assert 'Sync token is required' in error

        valid, error = sync.validate_sync_token(None)
        assert valid is False
        assert 'Sync token is required' in error

    def test_wrong_token_rejected(self, u1_s1, space_1, cc_meal_type, cc_recipe_10):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_date = today.date()

        sync = MealPlanShoppingSync(
            user=user,
            space=space_1,
            from_date=today_date,
            to_date=today_date
        )

        wrong_token = 'a' * 64
        valid, error = sync.validate_sync_token(wrong_token)
        assert valid is False
        assert 'Data has been modified' in error

    def test_new_mealplan_in_range_rejected(
        self, u1_s1, space_1, cc_meal_type, cc_recipe_10, cc_recipe_5
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        mp1 = MealPlan.objects.create(
            recipe=cc_recipe_10,
            space=space_1,
            meal_type=cc_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=2
        )

        preview_r = u1_s1.post(reverse(PREVIEW_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')
        preview_data = json.loads(preview_r.content)
        token = preview_data['sync_token']
        assert preview_data['summary']['add'] == 1

        MealPlan.objects.create(
            recipe=cc_recipe_5,
            space=space_1,
            meal_type=cc_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=2
        )

        apply_r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': today_str,
            'sync_token': token,
        }, content_type='application/json')

        assert apply_r.status_code == 409
        apply_data = json.loads(apply_r.content)
        assert apply_data['token_invalid'] is True

        with scopes_disabled():
            assert ShoppingListRecipe.objects.filter(mealplan=mp1).count() == 0

    def test_mealplan_removed_from_range_rejected(
        self, u1_s1, space_1, cc_meal_type, cc_recipe_10, cc_recipe_5
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")
        tomorrow_str = (today + timedelta(days=1)).strftime("%Y-%m-%d")

        mp1 = MealPlan.objects.create(
            recipe=cc_recipe_10,
            space=space_1,
            meal_type=cc_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=2
        )
        mp2 = MealPlan.objects.create(
            recipe=cc_recipe_5,
            space=space_1,
            meal_type=cc_meal_type,
            from_date=today + timedelta(days=1),
            to_date=today + timedelta(days=1),
            created_by=user,
            servings=2
        )

        preview_r = u1_s1.post(reverse(PREVIEW_URL), {
            'from_date': today_str,
            'to_date': tomorrow_str,
        }, content_type='application/json')
        preview_data = json.loads(preview_r.content)
        token = preview_data['sync_token']
        assert preview_data['summary']['add'] == 2

        mp2.from_date = today + timedelta(days=30)
        mp2.to_date = today + timedelta(days=30)
        mp2.save()

        apply_r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': tomorrow_str,
            'sync_token': token,
        }, content_type='application/json')

        assert apply_r.status_code == 409
        apply_data = json.loads(apply_r.content)
        assert apply_data['token_invalid'] is True

        with scopes_disabled():
            assert ShoppingListRecipe.objects.filter(mealplan=mp1).count() == 0
            assert ShoppingListRecipe.objects.filter(mealplan=mp2).count() == 0
