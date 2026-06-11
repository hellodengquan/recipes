import json
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch, MagicMock

import pytest
from django.contrib import auth
from django.db import DatabaseError, IntegrityError, connection
from django.urls import reverse
from django_scopes import scopes_disabled
from django.utils import timezone

from cookbook.helper.shopping_helper import (
    CHANGE_TYPE_ADD, CHANGE_TYPE_MERGE, CHANGE_TYPE_REMOVE,
    MealPlanShoppingSync, RecipeShoppingEditor,
)
from cookbook.models import (
    Household, Ingredient, MealPlan, MealType, Recipe, ShoppingListEntry,
    ShoppingListRecipe, Step, Unit, Food, UserSpace,
)

APPLY_URL = 'api:meal-plan-shopping-sync-apply'
PREVIEW_URL = 'api:meal-plan-shopping-sync-preview'


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
def tx_meal_type(space_1, u1_s1):
    return MealType.objects.get_or_create(
        name='tx_consistency_test',
        space=space_1,
        created_by=auth.get_user(u1_s1)
    )[0]


@pytest.fixture()
def tx_recipe_10(space_1, u1_s1):
    user = auth.get_user(u1_s1)
    return _make_recipe_with_n_ingredients(space_1, user, 10)


@pytest.fixture()
def tx_recipe_5(space_1, u1_s1):
    user = auth.get_user(u1_s1)
    return _make_recipe_with_n_ingredients(space_1, user, 5)


class TestAtomicRollbackViaDirectClass:
    """直接调用类方法测试原子回滚 - 通过 mock 内部方法模拟异常"""

    def test_add_then_mock_exception_rolls_back_all(
        self, u1_s1, space_1, tx_meal_type, tx_recipe_10, tx_recipe_5
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_date = today.date()
        tomorrow_date = today_date + timedelta(days=1)

        mp1 = MealPlan.objects.create(
            recipe=tx_recipe_10,
            space=space_1,
            meal_type=tx_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=2
        )
        mp2 = MealPlan.objects.create(
            recipe=tx_recipe_5,
            space=space_1,
            meal_type=tx_meal_type,
            from_date=today + timedelta(days=1),
            to_date=today + timedelta(days=1),
            created_by=user,
            servings=2
        )

        with scopes_disabled():
            initial_slr_count = ShoppingListRecipe.objects.count()
            initial_entry_count = ShoppingListEntry.objects.count()

        sync = MealPlanShoppingSync(
            user=user,
            space=space_1,
            from_date=today_date,
            to_date=tomorrow_date
        )
        preview = sync.calculate_changes()
        assert preview.summary[CHANGE_TYPE_ADD] == 2

        original_apply_add = sync._apply_add
        call_count = {'n': 0}

        def _faulty_apply_add(change):
            call_count['n'] += 1
            if call_count['n'] == 2:
                raise DatabaseError("Simulated DB error on second add")
            return original_apply_add(change)

        with patch.object(sync, '_apply_add', side_effect=_faulty_apply_add):
            result = sync.apply_changes(preview)

        assert result['rolled_back'] is True
        assert result['failed'] == 2
        assert result['added'] == 0
        assert result['merged'] == 0
        assert result['removed'] == 0
        assert len(result['errors']) > 0
        assert 'Atomic transaction rolled back' in result['errors'][0]

        with scopes_disabled():
            final_slr_count = ShoppingListRecipe.objects.count()
            final_entry_count = ShoppingListEntry.objects.count()
            assert final_slr_count == initial_slr_count, \
                "部分新增成功后失败，ShoppingListRecipe 数量应完全回滚"
            assert final_entry_count == initial_entry_count, \
                "部分新增成功后失败，ShoppingListEntry 数量应完全回滚"
            assert ShoppingListRecipe.objects.filter(mealplan=mp1).count() == 0
            assert ShoppingListRecipe.objects.filter(mealplan=mp2).count() == 0

    def test_merge_then_remove_failure_rolls_back_both(
        self, u1_s1, space_1, tx_meal_type, tx_recipe_10, tx_recipe_5
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_date = today.date()

        mp_merge = MealPlan.objects.create(
            recipe=tx_recipe_10,
            space=space_1,
            meal_type=tx_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=4
        )
        slr_merge = _make_slr_from_mealplan(
            mp_merge, tx_recipe_10, user, space_1, servings=2, ingredient_subset=5
        )
        slr_merge_id = slr_merge.id

        mp_remove = MealPlan.objects.create(
            recipe=tx_recipe_5,
            space=space_1,
            meal_type=tx_meal_type,
            from_date=today - timedelta(days=60),
            to_date=today - timedelta(days=60),
            created_by=user,
            servings=2
        )
        slr_remove = _make_slr_from_mealplan(mp_remove, tx_recipe_5, user, space_1, servings=2)
        slr_remove_id = slr_remove.id

        with scopes_disabled():
            before_slr_count = ShoppingListRecipe.objects.count()
            before_merge_servings = ShoppingListRecipe.objects.get(id=slr_merge_id).servings
            before_merge_entries = ShoppingListEntry.objects.filter(list_recipe_id=slr_merge_id).count()

        sync = MealPlanShoppingSync(
            user=user,
            space=space_1,
            from_date=today_date,
            to_date=today_date
        )
        preview = sync.calculate_changes()
        assert preview.summary[CHANGE_TYPE_MERGE] == 1
        assert preview.summary[CHANGE_TYPE_REMOVE] == 1

        original_apply_remove = sync._apply_remove

        def _faulty_apply_remove(change):
            original_apply_remove(change)
            raise IntegrityError("Simulated integrity error after remove")

        with patch.object(sync, '_apply_remove', side_effect=_faulty_apply_remove):
            result = sync.apply_changes(preview)

        assert result['rolled_back'] is True
        assert result['failed'] == 2
        assert result['merged'] == 0
        assert result['removed'] == 0

        with scopes_disabled():
            after_slr_count = ShoppingListRecipe.objects.count()
            assert after_slr_count == before_slr_count, \
                "合并+移除事务中途失败，SLR 总数应完全回滚"
            assert ShoppingListRecipe.objects.filter(id=slr_remove_id).exists(), \
                "应回滚被删除的 SLR"
            merge_slr = ShoppingListRecipe.objects.get(id=slr_merge_id)
            assert merge_slr.servings == before_merge_servings, \
                "应回滚已修改的份数"
            after_merge_entries = ShoppingListEntry.objects.filter(list_recipe_id=slr_merge_id).count()
            assert after_merge_entries == before_merge_entries, \
                "应回滚已变更的 entry 数量"

    def test_all_three_types_failure_midway_full_rollback(
        self, u1_s1, space_1, tx_meal_type, tx_recipe_10, tx_recipe_5
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_date = today.date()

        mp_add = MealPlan.objects.create(
            recipe=tx_recipe_5,
            space=space_1,
            meal_type=tx_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=2
        )

        mp_merge = MealPlan.objects.create(
            recipe=tx_recipe_10,
            space=space_1,
            meal_type=tx_meal_type,
            from_date=today + timedelta(days=1),
            to_date=today + timedelta(days=1),
            created_by=user,
            servings=4
        )
        slr_merge = _make_slr_from_mealplan(
            mp_merge, tx_recipe_10, user, space_1, servings=2, ingredient_subset=5
        )
        slr_merge_id = slr_merge.id
        before_merge_servings = slr_merge.servings

        mp_remove = MealPlan.objects.create(
            recipe=tx_recipe_10,
            space=space_1,
            meal_type=tx_meal_type,
            from_date=today - timedelta(days=60),
            to_date=today - timedelta(days=60),
            created_by=user,
            servings=2
        )
        slr_remove = _make_slr_from_mealplan(mp_remove, tx_recipe_10, user, space_1, servings=2)
        slr_remove_id = slr_remove.id

        with scopes_disabled():
            before_total_slrs = ShoppingListRecipe.objects.count()
            before_total_entries = ShoppingListEntry.objects.count()

        sync = MealPlanShoppingSync(
            user=user,
            space=space_1,
            from_date=today_date,
            to_date=today_date + timedelta(days=1)
        )
        preview = sync.calculate_changes()
        assert preview.summary[CHANGE_TYPE_ADD] == 1
        assert preview.summary[CHANGE_TYPE_MERGE] == 1
        assert preview.summary[CHANGE_TYPE_REMOVE] == 1

        original_apply_merge = sync._apply_merge
        call_counter = {'count': 0}

        def _faulty_merge(change):
            call_counter['count'] += 1
            if call_counter['count'] == 1:
                raise DatabaseError("Simulated DB crash during merge operation")

        with patch.object(sync, '_apply_merge', side_effect=_faulty_merge):
            result = sync.apply_changes(preview)

        assert result['rolled_back'] is True
        assert result['failed'] == 3

        with scopes_disabled():
            after_total_slrs = ShoppingListRecipe.objects.count()
            after_total_entries = ShoppingListEntry.objects.count()
            assert after_total_slrs == before_total_slrs, \
                "三类变更混合事务失败，SLR 总数应完全回滚"
            assert after_total_entries == before_total_entries, \
                "三类变更混合事务失败，Entry 总数应完全回滚"
            assert ShoppingListRecipe.objects.filter(mealplan=mp_add).count() == 0, \
                "ADD 操作应被回滚"
            assert ShoppingListRecipe.objects.filter(id=slr_remove_id).exists(), \
                "REMOVE 操作应被回滚"
            current_merge = ShoppingListRecipe.objects.get(id=slr_merge_id)
            assert current_merge.servings == before_merge_servings, \
                "MERGE 操作应被回滚"


class TestAtomicRollbackViaAPI:
    """通过 API 层测试原子回滚"""

    def test_api_exception_during_apply_full_rollback(
        self, u1_s1, space_1, tx_meal_type, tx_recipe_10, tx_recipe_5
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")
        tomorrow_str = (today + timedelta(days=1)).strftime("%Y-%m-%d")

        mp1 = MealPlan.objects.create(
            recipe=tx_recipe_10,
            space=space_1,
            meal_type=tx_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=2
        )
        mp2 = MealPlan.objects.create(
            recipe=tx_recipe_5,
            space=space_1,
            meal_type=tx_meal_type,
            from_date=today + timedelta(days=1),
            to_date=today + timedelta(days=1),
            created_by=user,
            servings=2
        )

        with scopes_disabled():
            initial_slr = ShoppingListRecipe.objects.count()
            initial_entries = ShoppingListEntry.objects.count()

        original_apply_add = MealPlanShoppingSync._apply_add
        call_counter = {'n': 0}

        def _faulty_apply_add(self_inst, change):
            call_counter['n'] += 1
            if call_counter['n'] == 2:
                raise DatabaseError("Simulated DB connection lost mid-batch")
            return original_apply_add(self_inst, change)

        with patch.object(MealPlanShoppingSync, '_apply_add', _faulty_apply_add):
            apply_r = u1_s1.post(reverse(APPLY_URL), {
                'from_date': today_str,
                'to_date': tomorrow_str,
            }, content_type='application/json')

        assert apply_r.status_code == 200
        apply_data = json.loads(apply_r.content)
        assert apply_data['rolled_back'] is True
        assert apply_data['failed'] == 2
        assert apply_data['added'] == 0

        with scopes_disabled():
            final_slr = ShoppingListRecipe.objects.count()
            final_entries = ShoppingListEntry.objects.count()
            assert final_slr == initial_slr
            assert final_entries == initial_entries
            assert ShoppingListRecipe.objects.filter(mealplan=mp1).count() == 0
            assert ShoppingListRecipe.objects.filter(mealplan=mp2).count() == 0

    def test_api_success_no_rollback_flag(
        self, u1_s1, space_1, tx_meal_type, tx_recipe_10
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        MealPlan.objects.create(
            recipe=tx_recipe_10,
            space=space_1,
            meal_type=tx_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=2
        )

        apply_r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')

        assert apply_r.status_code == 200
        apply_data = json.loads(apply_r.content)
        assert apply_data['rolled_back'] is False
        assert apply_data['added'] == 1
        assert apply_data['failed'] == 0
        assert apply_data['errors'] == []


class TestRollbackResultStateVerification:
    """详细验证回滚后数据库状态完全恢复"""

    def test_add_rollback_slr_and_entries_gone(
        self, u1_s1, space_1, tx_meal_type, tx_recipe_10, tx_recipe_5
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_date = today.date()

        mp_to_add = MealPlan.objects.create(
            recipe=tx_recipe_10,
            space=space_1,
            meal_type=tx_meal_type,
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
        preview = sync.calculate_changes()

        with patch.object(
            RecipeShoppingEditor, 'create',
            side_effect=DatabaseError("Simulated DB write failure after partial entry insert")
        ):
            result = sync.apply_changes(preview)

        assert result['rolled_back'] is True
        assert result['added'] == 0

        with scopes_disabled():
            matching_slrs = ShoppingListRecipe.objects.filter(
                mealplan=mp_to_add, recipe=tx_recipe_10
            )
            assert matching_slrs.count() == 0, \
                "事务回滚后不应残留该 mealplan 对应的任何 SLR"
            related_entries = ShoppingListEntry.objects.filter(
                list_recipe__mealplan=mp_to_add
            )
            assert related_entries.count() == 0, \
                "事务回滚后不应残留该 mealplan 对应的任何 Entry"

    def test_remove_rollback_slr_and_entries_restored(
        self, u1_s1, space_1, tx_meal_type, tx_recipe_5
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_date = today.date()

        mp_old = MealPlan.objects.create(
            recipe=tx_recipe_5,
            space=space_1,
            meal_type=tx_meal_type,
            from_date=today - timedelta(days=100),
            to_date=today - timedelta(days=100),
            created_by=user,
            servings=2
        )
        slr_old = _make_slr_from_mealplan(mp_old, tx_recipe_5, user, space_1, servings=2)
        slr_old_id = slr_old.id
        original_entry_ids = list(
            ShoppingListEntry.objects.filter(list_recipe=slr_old).values_list('id', flat=True)
        )

        sync = MealPlanShoppingSync(
            user=user,
            space=space_1,
            from_date=today_date,
            to_date=today_date
        )
        preview = sync.calculate_changes()
        assert preview.summary[CHANGE_TYPE_REMOVE] == 1

        original_delete = RecipeShoppingEditor.delete
        call_counter = {'n': 0}

        def _delete_with_failure(self_inst, **kwargs):
            call_counter['n'] += 1
            original_delete(self_inst, **kwargs)
            if call_counter['n'] >= 1:
                raise DatabaseError("Simulated late DB failure after delete")

        with patch.object(RecipeShoppingEditor, 'delete', _delete_with_failure):
            result = sync.apply_changes(preview)

        assert result['rolled_back'] is True
        assert result['removed'] == 0

        with scopes_disabled():
            assert ShoppingListRecipe.objects.filter(id=slr_old_id).exists(), \
                "删除后事务失败，SLR 必须被回滚恢复"
            restored_entry_ids = list(
                ShoppingListEntry.objects.filter(list_recipe=slr_old).values_list('id', flat=True)
            )
            assert sorted(restored_entry_ids) == sorted(original_entry_ids), \
                "删除后事务失败，所有 Entry 必须被回滚恢复"

    def test_merge_rollback_entries_restored(
        self, u1_s1, space_1, tx_meal_type, tx_recipe_10
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_date = today.date()

        mp = MealPlan.objects.create(
            recipe=tx_recipe_10,
            space=space_1,
            meal_type=tx_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=5
        )
        slr = _make_slr_from_mealplan(mp, tx_recipe_10, user, space_1, servings=2, ingredient_subset=3)
        slr_id = slr.id
        original_state = {
            'servings': slr.servings,
            'entry_ids': sorted(
                ShoppingListEntry.objects.filter(list_recipe=slr).values_list('id', flat=True)
            ),
            'entry_count': ShoppingListEntry.objects.filter(list_recipe=slr).count()
        }

        sync = MealPlanShoppingSync(
            user=user,
            space=space_1,
            from_date=today_date,
            to_date=today_date
        )
        preview = sync.calculate_changes()
        assert preview.summary[CHANGE_TYPE_MERGE] == 1

        with patch.object(
            RecipeShoppingEditor, 'edit',
            side_effect=DatabaseError("Simulated DB failure after partial merge writes")
        ):
            result = sync.apply_changes(preview)

        assert result['rolled_back'] is True
        assert result['merged'] == 0

        with scopes_disabled():
            slr_current = ShoppingListRecipe.objects.get(id=slr_id)
            current_entry_ids = sorted(
                ShoppingListEntry.objects.filter(list_recipe=slr_id).values_list('id', flat=True)
            )
            current_entry_count = ShoppingListEntry.objects.filter(list_recipe=slr_id).count()
            assert slr_current.servings == original_state['servings'], \
                "合并失败后 servings 必须回滚"
            assert current_entry_ids == original_state['entry_ids'], \
                "合并失败后 entry IDs 必须完全恢复"
            assert current_entry_count == original_state['entry_count'], \
                "合并失败后 entry 数量必须完全恢复"


class TestDatabaseLevelFailureSimulation:
    """模拟更底层的数据库级别异常"""

    def test_integrity_error_rollback(
        self, u1_s1, space_1, tx_meal_type, tx_recipe_10
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_date = today.date()

        mp = MealPlan.objects.create(
            recipe=tx_recipe_10,
            space=space_1,
            meal_type=tx_meal_type,
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
        preview = sync.calculate_changes()

        with patch.object(
            ShoppingListEntry.objects, 'bulk_create',
            side_effect=IntegrityError("Simulated unique constraint violation")
        ):
            result = sync.apply_changes(preview)

        assert result['rolled_back'] is True
        assert result['added'] == 0

        with scopes_disabled():
            assert ShoppingListRecipe.objects.filter(mealplan=mp).count() == 0

    def test_generic_exception_still_rolls_back(
        self, u1_s1, space_1, tx_meal_type, tx_recipe_5, tx_recipe_10
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_date = today.date()

        for i, recipe in enumerate([tx_recipe_5, tx_recipe_10]):
            MealPlan.objects.create(
                recipe=recipe,
                space=space_1,
                meal_type=tx_meal_type,
                from_date=today + timedelta(days=i),
                to_date=today + timedelta(days=i),
                created_by=user,
                servings=2
            )

        with scopes_disabled():
            before_slr_count = ShoppingListRecipe.objects.count()

        sync = MealPlanShoppingSync(
            user=user,
            space=space_1,
            from_date=today_date,
            to_date=today_date + timedelta(days=1)
        )
        preview = sync.calculate_changes()
        assert preview.summary[CHANGE_TYPE_ADD] == 2

        original_create = ShoppingListRecipe.objects.create
        call_counter = {'n': 0}

        def _create_with_random_failure(**kwargs):
            call_counter['n'] += 1
            obj = original_create(**kwargs)
            if call_counter['n'] == 2:
                raise RuntimeError("Simulated random runtime error after 2nd SLR create")
            return obj

        with patch.object(ShoppingListRecipe.objects, 'create', side_effect=_create_with_random_failure):
            result = sync.apply_changes(preview)

        assert result['rolled_back'] is True
        assert result['added'] == 0
        assert 'RuntimeError' in result['errors'][0]

        with scopes_disabled():
            after_slr_count = ShoppingListRecipe.objects.count()
            assert after_slr_count == before_slr_count, \
                "RuntimeError 等非 DB 异常也必须触发回滚"
