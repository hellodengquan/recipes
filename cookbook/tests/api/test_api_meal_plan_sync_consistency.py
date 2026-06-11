import json
import uuid
from datetime import timedelta
from decimal import Decimal
from django.utils import timezone

import pytest
from django.contrib import auth
from django.urls import reverse
from django_scopes import scope, scopes_disabled

from cookbook.helper.shopping_helper import (
    MealPlanShoppingSync, CHANGE_TYPE_ADD, CHANGE_TYPE_MERGE, CHANGE_TYPE_REMOVE
)
from cookbook.models import (
    Household, Ingredient, MealPlan, MealType, Recipe, ShoppingListEntry,
    ShoppingListRecipe, Step, Unit, Food, UserSpace
)
from cookbook.tests.factories import RecipeFactory

PREVIEW_URL = 'api:meal-plan-shopping-sync-preview'
APPLY_URL = 'api:meal-plan-shopping-sync-apply'


def _assert_consistency(preview_summary, apply_result, scenario=""):
    """严格断言预览与实际写入的三类变更数量完全一致。"""
    assert preview_summary[CHANGE_TYPE_ADD] == apply_result['added'], \
        f"[{scenario}] 新增变更不一致: 预览={preview_summary[CHANGE_TYPE_ADD]}, 实际={apply_result['added']}"
    assert preview_summary[CHANGE_TYPE_MERGE] == apply_result['merged'], \
        f"[{scenario}] 合并变更不一致: 预览={preview_summary[CHANGE_TYPE_MERGE]}, 实际={apply_result['merged']}"
    assert preview_summary[CHANGE_TYPE_REMOVE] == apply_result['removed'], \
        f"[{scenario}] 移除变更不一致: 预览={preview_summary[CHANGE_TYPE_REMOVE]}, 实际={apply_result['removed']}"
    assert apply_result['failed'] == 0, \
        f"[{scenario}] 存在失败的操作: {apply_result['errors']}"


def _make_recipe_with_n_ingredients(space, user, n):
    """创建带有 n 个 ingredients 的 recipe，与 conftest.py 模式一致。"""
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
    """从 mealplan 创建 ShoppingListRecipe 和对应的 entries。"""
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
def sync_meal_type(space_1, u1_s1):
    return MealType.objects.get_or_create(
        name='consistency_test',
        space=space_1,
        created_by=auth.get_user(u1_s1)
    )[0]


@pytest.fixture()
def recipe_10_ing(space_1, u1_s1):
    user = auth.get_user(u1_s1)
    return _make_recipe_with_n_ingredients(space_1, user, 10)


@pytest.fixture()
def recipe_5_ing(space_1, u1_s1):
    user = auth.get_user(u1_s1)
    return _make_recipe_with_n_ingredients(space_1, user, 5)


class TestPureAddConsistency:
    """测试纯新增场景的一致性"""

    def test_single_meal_plan_pure_add(
        self, u1_s1, space_1, sync_meal_type, recipe_10_ing
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        mp = MealPlan.objects.create(
            recipe=recipe_10_ing,
            space=space_1,
            meal_type=sync_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=2
        )

        preview_r = u1_s1.post(reverse(PREVIEW_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')
        assert preview_r.status_code == 200
        preview_data = json.loads(preview_r.content)

        apply_r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')
        assert apply_r.status_code == 200
        apply_data = json.loads(apply_r.content)

        _assert_consistency(
            preview_data['summary'],
            apply_data,
            scenario="single_meal_plan_pure_add"
        )

        assert apply_data['added'] == 1
        assert apply_data['merged'] == 0
        assert apply_data['removed'] == 0

        with scopes_disabled():
            slr_count = ShoppingListRecipe.objects.filter(mealplan=mp).count()
            assert slr_count == 1
            slr = ShoppingListRecipe.objects.filter(mealplan=mp).first()
            entry_count = ShoppingListEntry.objects.filter(list_recipe=slr).count()
            assert entry_count == 10

    def test_multiple_meal_plans_pure_add(
        self, u1_s1, space_1, sync_meal_type, recipe_10_ing, recipe_5_ing
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")
        tomorrow_str = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        day_after_str = (today + timedelta(days=2)).strftime("%Y-%m-%d")

        for i, (recipe, offset) in enumerate([
            (recipe_10_ing, 0),
            (recipe_5_ing, 1),
            (recipe_10_ing, 2),
        ]):
            MealPlan.objects.create(
                recipe=recipe,
                space=space_1,
                meal_type=sync_meal_type,
                from_date=today + timedelta(days=offset),
                to_date=today + timedelta(days=offset),
                created_by=user,
                servings=2 + i
            )

        preview_r = u1_s1.post(reverse(PREVIEW_URL), {
            'from_date': today_str,
            'to_date': day_after_str,
        }, content_type='application/json')
        preview_data = json.loads(preview_r.content)

        apply_r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': day_after_str,
        }, content_type='application/json')
        apply_data = json.loads(apply_r.content)

        _assert_consistency(
            preview_data['summary'],
            apply_data,
            scenario="multiple_meal_plans_pure_add"
        )

        assert apply_data['added'] == 3
        assert apply_data['merged'] == 0
        assert apply_data['removed'] == 0


class TestPureMergeConsistency:
    """测试纯合并场景的一致性"""

    def test_servings_only_merge(
        self, u1_s1, space_1, sync_meal_type, recipe_10_ing
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        mp = MealPlan.objects.create(
            recipe=recipe_10_ing,
            space=space_1,
            meal_type=sync_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=2
        )

        slr = _make_slr_from_mealplan(mp, recipe_10_ing, user, space_1, servings=2)

        mp.servings = 4
        mp.save()

        preview_r = u1_s1.post(reverse(PREVIEW_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')
        preview_data = json.loads(preview_r.content)

        apply_r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')
        apply_data = json.loads(apply_r.content)

        _assert_consistency(
            preview_data['summary'],
            apply_data,
            scenario="servings_only_merge"
        )

        assert apply_data['merged'] == 1
        assert apply_data['added'] == 0
        assert apply_data['removed'] == 0

        with scopes_disabled():
            slr.refresh_from_db()
            assert slr.servings == 4

    def test_ingredients_only_merge(
        self, u1_s1, space_1, sync_meal_type, recipe_10_ing
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        mp = MealPlan.objects.create(
            recipe=recipe_10_ing,
            space=space_1,
            meal_type=sync_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=2
        )

        slr = _make_slr_from_mealplan(
            mp, recipe_10_ing, user, space_1, servings=2, ingredient_subset=5
        )

        preview_r = u1_s1.post(reverse(PREVIEW_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')
        preview_data = json.loads(preview_r.content)

        apply_r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')
        apply_data = json.loads(apply_r.content)

        _assert_consistency(
            preview_data['summary'],
            apply_data,
            scenario="ingredients_only_merge"
        )

        assert apply_data['merged'] == 1
        assert apply_data['added'] == 0
        assert apply_data['removed'] == 0

        with scopes_disabled():
            entry_count = ShoppingListEntry.objects.filter(list_recipe=slr).count()
            assert entry_count == 10

    def test_both_servings_and_ingredients_merge(
        self, u1_s1, space_1, sync_meal_type, recipe_10_ing
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        mp = MealPlan.objects.create(
            recipe=recipe_10_ing,
            space=space_1,
            meal_type=sync_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=3
        )

        slr = _make_slr_from_mealplan(
            mp, recipe_10_ing, user, space_1, servings=2, ingredient_subset=7
        )

        preview_r = u1_s1.post(reverse(PREVIEW_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')
        preview_data = json.loads(preview_r.content)

        apply_r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')
        apply_data = json.loads(apply_r.content)

        _assert_consistency(
            preview_data['summary'],
            apply_data,
            scenario="both_servings_and_ingredients_merge"
        )

        assert apply_data['merged'] == 1
        assert apply_data['added'] == 0
        assert apply_data['removed'] == 0

        with scopes_disabled():
            slr.refresh_from_db()
            assert slr.servings == 3
            entry_count = ShoppingListEntry.objects.filter(list_recipe=slr).count()
            assert entry_count == 10

    def test_no_merge_when_exact_match(
        self, u1_s1, space_1, sync_meal_type, recipe_10_ing
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        mp = MealPlan.objects.create(
            recipe=recipe_10_ing,
            space=space_1,
            meal_type=sync_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=2
        )

        _make_slr_from_mealplan(mp, recipe_10_ing, user, space_1, servings=2)

        preview_r = u1_s1.post(reverse(PREVIEW_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')
        preview_data = json.loads(preview_r.content)

        apply_r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')
        apply_data = json.loads(apply_r.content)

        _assert_consistency(
            preview_data['summary'],
            apply_data,
            scenario="no_merge_when_exact_match"
        )

        assert apply_data['added'] == 0
        assert apply_data['merged'] == 0
        assert apply_data['removed'] == 0


class TestPureRemoveConsistency:
    """测试纯移除场景的一致性"""

    def test_single_remove_out_of_range(
        self, u1_s1, space_1, sync_meal_type, recipe_10_ing
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")
        outside_date = today - timedelta(days=30)

        mp_old = MealPlan.objects.create(
            recipe=recipe_10_ing,
            space=space_1,
            meal_type=sync_meal_type,
            from_date=outside_date,
            to_date=outside_date,
            created_by=user,
            servings=2
        )

        slr_old = _make_slr_from_mealplan(
            mp_old, recipe_10_ing, user, space_1, servings=2
        )
        slr_old_id = slr_old.id

        preview_r = u1_s1.post(reverse(PREVIEW_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')
        preview_data = json.loads(preview_r.content)

        apply_r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')
        apply_data = json.loads(apply_r.content)

        _assert_consistency(
            preview_data['summary'],
            apply_data,
            scenario="single_remove_out_of_range"
        )

        assert apply_data['removed'] == 1
        assert apply_data['added'] == 0
        assert apply_data['merged'] == 0

        with scopes_disabled():
            assert not ShoppingListRecipe.objects.filter(id=slr_old_id).exists()

    def test_multiple_removes_out_of_range(
        self, u1_s1, space_1, sync_meal_type, recipe_10_ing, recipe_5_ing
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        old_slr_ids = []
        for i, recipe in enumerate([recipe_10_ing, recipe_5_ing, recipe_10_ing]):
            offset_days = 60 + i * 10
            mp_old = MealPlan.objects.create(
                recipe=recipe,
                space=space_1,
                meal_type=sync_meal_type,
                from_date=today - timedelta(days=offset_days),
                to_date=today - timedelta(days=offset_days),
                created_by=user,
                servings=2
            )
            slr = _make_slr_from_mealplan(mp_old, recipe, user, space_1, servings=2)
            old_slr_ids.append(slr.id)

        preview_r = u1_s1.post(reverse(PREVIEW_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')
        preview_data = json.loads(preview_r.content)

        apply_r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')
        apply_data = json.loads(apply_r.content)

        _assert_consistency(
            preview_data['summary'],
            apply_data,
            scenario="multiple_removes_out_of_range"
        )

        assert apply_data['removed'] == 3

        with scopes_disabled():
            for slr_id in old_slr_ids:
                assert not ShoppingListRecipe.objects.filter(id=slr_id).exists()


class TestMixedScenarioConsistency:
    """测试新增、合并、移除混合场景的一致性"""

    def test_add_merge_remove_mixed(
        self, u1_s1, space_1, sync_meal_type, recipe_10_ing, recipe_5_ing
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")
        tomorrow_str = (today + timedelta(days=1)).strftime("%Y-%m-%d")

        mp_add = MealPlan.objects.create(
            recipe=recipe_5_ing,
            space=space_1,
            meal_type=sync_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=2
        )

        mp_merge = MealPlan.objects.create(
            recipe=recipe_10_ing,
            space=space_1,
            meal_type=sync_meal_type,
            from_date=today + timedelta(days=1),
            to_date=today + timedelta(days=1),
            created_by=user,
            servings=4
        )
        _make_slr_from_mealplan(
            mp_merge, recipe_10_ing, user, space_1, servings=2, ingredient_subset=5
        )

        mp_remove = MealPlan.objects.create(
            recipe=recipe_5_ing,
            space=space_1,
            meal_type=sync_meal_type,
            from_date=today - timedelta(days=60),
            to_date=today - timedelta(days=60),
            created_by=user,
            servings=2
        )
        slr_remove = _make_slr_from_mealplan(
            mp_remove, recipe_5_ing, user, space_1, servings=2
        )
        slr_remove_id = slr_remove.id

        preview_r = u1_s1.post(reverse(PREVIEW_URL), {
            'from_date': today_str,
            'to_date': tomorrow_str,
        }, content_type='application/json')
        preview_data = json.loads(preview_r.content)

        apply_r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': tomorrow_str,
        }, content_type='application/json')
        apply_data = json.loads(apply_r.content)

        _assert_consistency(
            preview_data['summary'],
            apply_data,
            scenario="add_merge_remove_mixed"
        )

        assert apply_data['added'] == 1
        assert apply_data['merged'] == 1
        assert apply_data['removed'] == 1

        with scopes_disabled():
            assert ShoppingListRecipe.objects.filter(mealplan=mp_add).count() == 1
            assert not ShoppingListRecipe.objects.filter(id=slr_remove_id).exists()
            merged_slr = ShoppingListRecipe.objects.filter(mealplan=mp_merge).first()
            assert merged_slr.servings == 4
            assert ShoppingListEntry.objects.filter(list_recipe=merged_slr).count() == 10


class TestIdempotencyConsistency:
    """测试幂等性：应用后再次预览应为零变更"""

    def test_apply_then_preview_is_empty(
        self, u1_s1, space_1, sync_meal_type, recipe_10_ing
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        MealPlan.objects.create(
            recipe=recipe_10_ing,
            space=space_1,
            meal_type=sync_meal_type,
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
        assert preview1_data['summary']['add'] == 1

        apply_r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')
        apply_data = json.loads(apply_r.content)
        assert apply_data['added'] == 1

        preview2_r = u1_s1.post(reverse(PREVIEW_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')
        preview2_data = json.loads(preview2_r.content)

        assert preview2_data['summary']['add'] == 0, \
            "应用后再次预览的新增变更应为0"
        assert preview2_data['summary']['merge'] == 0, \
            "应用后再次预览的合并变更应为0"
        assert preview2_data['summary']['remove'] == 0, \
            "应用后再次预览的移除变更应为0"

    def test_merge_apply_then_preview_is_empty(
        self, u1_s1, space_1, sync_meal_type, recipe_10_ing
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        mp = MealPlan.objects.create(
            recipe=recipe_10_ing,
            space=space_1,
            meal_type=sync_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=4
        )

        _make_slr_from_mealplan(
            mp, recipe_10_ing, user, space_1, servings=2, ingredient_subset=5
        )

        apply_r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')
        apply_data = json.loads(apply_r.content)
        assert apply_data['merged'] == 1

        preview_r = u1_s1.post(reverse(PREVIEW_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')
        preview_data = json.loads(preview_r.content)

        assert preview_data['summary']['add'] == 0
        assert preview_data['summary']['merge'] == 0
        assert preview_data['summary']['remove'] == 0


class TestDirectClassConsistency:
    """直接测试 MealPlanShoppingSync 类的一致性"""

    def test_direct_class_call_consistency(
        self, u1_s1, space_1, sync_meal_type, recipe_10_ing, recipe_5_ing
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_date = today.date()
        tomorrow_date = today_date + timedelta(days=1)

        mp_add = MealPlan.objects.create(
            recipe=recipe_5_ing,
            space=space_1,
            meal_type=sync_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=2
        )

        mp_merge = MealPlan.objects.create(
            recipe=recipe_10_ing,
            space=space_1,
            meal_type=sync_meal_type,
            from_date=today + timedelta(days=1),
            to_date=today + timedelta(days=1),
            created_by=user,
            servings=5
        )
        _make_slr_from_mealplan(
            mp_merge, recipe_10_ing, user, space_1, servings=2, ingredient_subset=3
        )

        mp_remove = MealPlan.objects.create(
            recipe=recipe_5_ing,
            space=space_1,
            meal_type=sync_meal_type,
            from_date=today - timedelta(days=45),
            to_date=today - timedelta(days=45),
            created_by=user,
            servings=2
        )
        slr_remove = _make_slr_from_mealplan(
            mp_remove, recipe_5_ing, user, space_1, servings=2
        )
        slr_remove_id = slr_remove.id

        sync = MealPlanShoppingSync(
            user=user,
            space=space_1,
            from_date=today_date,
            to_date=tomorrow_date
        )

        preview = sync.calculate_changes()
        apply_result = sync.apply_changes(preview)

        _assert_consistency(
            preview.summary,
            apply_result,
            scenario="direct_class_call_consistency"
        )

        assert apply_result['added'] == 1
        assert apply_result['merged'] == 1
        assert apply_result['removed'] == 1
        assert apply_result['failed'] == 0

        with scopes_disabled():
            assert ShoppingListRecipe.objects.filter(mealplan=mp_add).count() == 1
            assert not ShoppingListRecipe.objects.filter(id=slr_remove_id).exists()
            merged_slr = ShoppingListRecipe.objects.filter(mealplan=mp_merge).first()
            assert merged_slr.servings == 5
            assert ShoppingListEntry.objects.filter(list_recipe=merged_slr).count() == 10

    def test_direct_class_household_member_consistency(
        self, u1_s1, u2_s1, space_1, sync_meal_type, recipe_10_ing
    ):
        user1 = auth.get_user(u1_s1)
        user2 = auth.get_user(u2_s1)
        today = timezone.now()
        today_date = today.date()

        household = Household.objects.create(name='consistency_household', space=space_1)
        UserSpace.objects.filter(
            user__in=[user1, user2], space=space_1
        ).update(household=household)

        mp = MealPlan.objects.create(
            recipe=recipe_10_ing,
            space=space_1,
            meal_type=sync_meal_type,
            from_date=today,
            to_date=today,
            created_by=user1,
            servings=2
        )

        sync_user2 = MealPlanShoppingSync(
            user=user2,
            space=space_1,
            from_date=today_date,
            to_date=today_date
        )

        preview = sync_user2.calculate_changes()
        apply_result = sync_user2.apply_changes(preview)

        _assert_consistency(
            preview.summary,
            apply_result,
            scenario="direct_class_household_member_consistency"
        )

        assert apply_result['added'] == 1
        assert apply_result['failed'] == 0

        with scopes_disabled():
            assert ShoppingListRecipe.objects.filter(mealplan=mp).count() == 1


class TestBoundaryConsistency:
    """边界场景测试：确保边界条件下预览与写入完全一致"""

    def test_empty_range_no_changes(
        self, u1_s1, space_1, sync_meal_type, recipe_10_ing
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")
        future_str = (today + timedelta(days=365)).strftime("%Y-%m-%d")

        preview_r = u1_s1.post(reverse(PREVIEW_URL), {
            'from_date': today_str,
            'to_date': future_str,
        }, content_type='application/json')
        preview_data = json.loads(preview_r.content)

        apply_r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': future_str,
        }, content_type='application/json')
        apply_data = json.loads(apply_r.content)

        _assert_consistency(
            preview_data['summary'],
            apply_data,
            scenario="empty_range_no_changes"
        )

        assert apply_data['added'] == 0
        assert apply_data['merged'] == 0
        assert apply_data['removed'] == 0

    def test_selected_changes_partial_apply(
        self, u1_s1, space_1, sync_meal_type, recipe_10_ing, recipe_5_ing
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")
        tomorrow_str = (today + timedelta(days=1)).strftime("%Y-%m-%d")

        mp1 = MealPlan.objects.create(
            recipe=recipe_10_ing,
            space=space_1,
            meal_type=sync_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=2
        )
        mp2 = MealPlan.objects.create(
            recipe=recipe_5_ing,
            space=space_1,
            meal_type=sync_meal_type,
            from_date=today + timedelta(days=1),
            to_date=today + timedelta(days=1),
            created_by=user,
            servings=2
        )
        mp3_outside = MealPlan.objects.create(
            recipe=recipe_10_ing,
            space=space_1,
            meal_type=sync_meal_type,
            from_date=today - timedelta(days=90),
            to_date=today - timedelta(days=90),
            created_by=user,
            servings=2
        )
        slr3 = _make_slr_from_mealplan(mp3_outside, recipe_10_ing, user, space_1, servings=2)
        slr3_id = slr3.id

        preview_r = u1_s1.post(reverse(PREVIEW_URL), {
            'from_date': today_str,
            'to_date': tomorrow_str,
        }, content_type='application/json')
        preview_data = json.loads(preview_r.content)

        assert len(preview_data['changes']) == 3

        add_indices = [
            i for i, c in enumerate(preview_data['changes'])
            if c['change_type'] == CHANGE_TYPE_ADD
        ]
        remove_indices = [
            i for i, c in enumerate(preview_data['changes'])
            if c['change_type'] == CHANGE_TYPE_REMOVE
        ]

        assert len(add_indices) == 2
        assert len(remove_indices) == 1

        selected = [add_indices[0]] + remove_indices

        apply_r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': tomorrow_str,
            'selected_changes': selected
        }, content_type='application/json')
        apply_data = json.loads(apply_r.content)

        assert apply_data['added'] == 1
        assert apply_data['removed'] == 1
        assert apply_data['merged'] == 0
        assert apply_data['failed'] == 0

        with scopes_disabled():
            created_count = ShoppingListRecipe.objects.filter(
                mealplan__in=[mp1, mp2]
            ).count()
            assert created_count == 1
            assert not ShoppingListRecipe.objects.filter(id=slr3_id).exists()
