import json
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib import auth
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

ERROR_MSG_TOKEN_REQUIRED = "Sync token is required. Please generate a new preview."
ERROR_MSG_DATA_MODIFIED = "Data has been modified by another user. Please generate a new preview and try again."
ERROR_PREFIX_TOKEN_FAILED = "Sync token validation failed:"

EXPECTED_PREVIEW_FIELDS = {'changes', 'summary', 'sync_token'}
EXPECTED_CHANGE_FIELDS = {
    'change_type', 'mealplan_id', 'mealplan_label', 'recipe_id', 'recipe_name',
    'servings', 'ingredients', 'shopping_list_recipe_id', 'merged_from_ids'
}
EXPECTED_INGREDIENT_FIELDS = {'id', 'food_name', 'amount', 'unit_name'}
EXPECTED_SUMMARY_FIELDS = {CHANGE_TYPE_ADD, CHANGE_TYPE_MERGE, CHANGE_TYPE_REMOVE}
EXPECTED_APPLY_SUCCESS_FIELDS = {
    'added', 'merged', 'removed', 'failed', 'errors', 'rolled_back',
    'token_invalid', 'token_error'
}
EXPECTED_APPLY_REJECT_FIELDS = EXPECTED_APPLY_SUCCESS_FIELDS


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


def _assert_field_subset(actual_keys, expected_fields, label=""):
    actual_set = set(actual_keys)
    missing = expected_fields - actual_set
    assert not missing, f"{label} Missing required fields: {missing}"


@pytest.fixture()
def ct_meal_type(space_1, u1_s1):
    return MealType.objects.get_or_create(
        name='contract_test',
        space=space_1,
        created_by=auth.get_user(u1_s1)
    )[0]


@pytest.fixture()
def ct_recipe_10(space_1, u1_s1):
    user = auth.get_user(u1_s1)
    return _make_recipe_with_n_ingredients(space_1, user, 10)


@pytest.fixture()
def ct_recipe_5(space_1, u1_s1):
    user = auth.get_user(u1_s1)
    return _make_recipe_with_n_ingredients(space_1, user, 5)


class TestPreviewResponseContract:
    """测试 Preview 接口响应契约"""

    def test_preview_success_status_code_200(
        self, u1_s1, space_1, ct_meal_type, ct_recipe_10
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        MealPlan.objects.create(
            recipe=ct_recipe_10,
            space=space_1,
            meal_type=ct_meal_type,
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

    def test_preview_response_top_level_fields(
        self, u1_s1, space_1, ct_meal_type, ct_recipe_10
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        MealPlan.objects.create(
            recipe=ct_recipe_10,
            space=space_1,
            meal_type=ct_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=2
        )

        r = u1_s1.post(reverse(PREVIEW_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')
        data = json.loads(r.content)

        _assert_field_subset(data.keys(), EXPECTED_PREVIEW_FIELDS, "Preview response")

        assert isinstance(data['changes'], list)
        assert isinstance(data['summary'], dict)
        assert isinstance(data['sync_token'], str)

    def test_preview_response_change_fields(
        self, u1_s1, space_1, ct_meal_type, ct_recipe_10
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        MealPlan.objects.create(
            recipe=ct_recipe_10,
            space=space_1,
            meal_type=ct_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=2
        )

        r = u1_s1.post(reverse(PREVIEW_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')
        data = json.loads(r.content)

        assert len(data['changes']) > 0
        for i, change in enumerate(data['changes']):
            _assert_field_subset(
                change.keys(), EXPECTED_CHANGE_FIELDS,
                f"Change[{i}]"
            )
            assert change['change_type'] in (CHANGE_TYPE_ADD, CHANGE_TYPE_MERGE, CHANGE_TYPE_REMOVE)
            assert isinstance(change['ingredients'], list)
            assert isinstance(change['merged_from_ids'], list)

    def test_preview_response_ingredient_fields(
        self, u1_s1, space_1, ct_meal_type, ct_recipe_10
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        MealPlan.objects.create(
            recipe=ct_recipe_10,
            space=space_1,
            meal_type=ct_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=2
        )

        r = u1_s1.post(reverse(PREVIEW_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')
        data = json.loads(r.content)

        add_changes = [c for c in data['changes'] if c['change_type'] == CHANGE_TYPE_ADD]
        assert len(add_changes) > 0
        assert len(add_changes[0]['ingredients']) > 0

        for j, ing in enumerate(add_changes[0]['ingredients']):
            _assert_field_subset(
                ing.keys(), EXPECTED_INGREDIENT_FIELDS,
                f"Change[0].ingredients[{j}]"
            )
            assert isinstance(ing['id'], int)
            assert isinstance(ing['food_name'], str)
            assert isinstance(ing['amount'], float)

    def test_preview_response_summary_fields(
        self, u1_s1, space_1, ct_meal_type, ct_recipe_10
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        MealPlan.objects.create(
            recipe=ct_recipe_10,
            space=space_1,
            meal_type=ct_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=2
        )

        r = u1_s1.post(reverse(PREVIEW_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')
        data = json.loads(r.content)

        _assert_field_subset(
            data['summary'].keys(), EXPECTED_SUMMARY_FIELDS,
            "Preview summary"
        )
        assert isinstance(data['summary'][CHANGE_TYPE_ADD], int)
        assert isinstance(data['summary'][CHANGE_TYPE_MERGE], int)
        assert isinstance(data['summary'][CHANGE_TYPE_REMOVE], int)
        assert data['summary'][CHANGE_TYPE_ADD] >= 0
        assert data['summary'][CHANGE_TYPE_MERGE] >= 0
        assert data['summary'][CHANGE_TYPE_REMOVE] >= 0

    def test_preview_response_sync_token_format(
        self, u1_s1, space_1, ct_meal_type, ct_recipe_10
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        MealPlan.objects.create(
            recipe=ct_recipe_10,
            space=space_1,
            meal_type=ct_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=2
        )

        r = u1_s1.post(reverse(PREVIEW_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')
        data = json.loads(r.content)

        assert len(data['sync_token']) == 64
        assert all(c in '0123456789abcdef' for c in data['sync_token'])

    def test_preview_empty_range_zero_changes(
        self, u1_s1, space_1, ct_meal_type
    ):
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        r = u1_s1.post(reverse(PREVIEW_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')

        assert r.status_code == 200
        data = json.loads(r.content)
        assert data['summary'][CHANGE_TYPE_ADD] == 0
        assert data['summary'][CHANGE_TYPE_MERGE] == 0
        assert data['summary'][CHANGE_TYPE_REMOVE] == 0
        assert data['changes'] == []
        assert isinstance(data['sync_token'], str)
        assert len(data['sync_token']) == 64


class TestApplyRejectionResponseContract:
    """测试 Apply 接口拒绝时的响应契约"""

    def test_apply_missing_token_returns_400(
        self, u1_s1, space_1, ct_meal_type, ct_recipe_10
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        MealPlan.objects.create(
            recipe=ct_recipe_10,
            space=space_1,
            meal_type=ct_meal_type,
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

    def test_apply_missing_token_response_structure(
        self, u1_s1, space_1, ct_meal_type, ct_recipe_10
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        MealPlan.objects.create(
            recipe=ct_recipe_10,
            space=space_1,
            meal_type=ct_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=2
        )

        r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')

        data = json.loads(r.content)
        assert 'sync_token' in data
        assert any('required' in str(msg).lower() for msg in data['sync_token'])

    def test_apply_invalid_token_returns_409(
        self, u1_s1, space_1, ct_meal_type, ct_recipe_10
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        MealPlan.objects.create(
            recipe=ct_recipe_10,
            space=space_1,
            meal_type=ct_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=2
        )

        r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': today_str,
            'sync_token': 'invalid_token_' + 'a' * 50,
        }, content_type='application/json')

        assert r.status_code == 409

    def test_apply_invalid_token_response_fields(
        self, u1_s1, space_1, ct_meal_type, ct_recipe_10
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        MealPlan.objects.create(
            recipe=ct_recipe_10,
            space=space_1,
            meal_type=ct_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=2
        )

        r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': today_str,
            'sync_token': 'invalid_token_' + 'a' * 50,
        }, content_type='application/json')
        data = json.loads(r.content)

        _assert_field_subset(data.keys(), EXPECTED_APPLY_REJECT_FIELDS, "Apply rejection response")

        assert isinstance(data['added'], int)
        assert isinstance(data['merged'], int)
        assert isinstance(data['removed'], int)
        assert isinstance(data['failed'], int)
        assert isinstance(data['errors'], list)
        assert isinstance(data['rolled_back'], bool)
        assert isinstance(data['token_invalid'], bool)
        assert isinstance(data['token_error'], str)

    def test_apply_invalid_token_zero_counts(
        self, u1_s1, space_1, ct_meal_type, ct_recipe_10
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        MealPlan.objects.create(
            recipe=ct_recipe_10,
            space=space_1,
            meal_type=ct_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=2
        )

        r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': today_str,
            'sync_token': 'invalid_token_' + 'a' * 50,
        }, content_type='application/json')
        data = json.loads(r.content)

        assert data['added'] == 0
        assert data['merged'] == 0
        assert data['removed'] == 0
        assert data['rolled_back'] is False

    def test_apply_invalid_token_flags(
        self, u1_s1, space_1, ct_meal_type, ct_recipe_10
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        MealPlan.objects.create(
            recipe=ct_recipe_10,
            space=space_1,
            meal_type=ct_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=2
        )

        r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': today_str,
            'sync_token': 'invalid_token_' + 'a' * 50,
        }, content_type='application/json')
        data = json.loads(r.content)

        assert data['token_invalid'] is True
        assert data['token_error'] is not None
        assert len(data['token_error']) > 0
        assert data['failed'] >= 1


class TestApplyRejectionMessagesContract:
    """测试拒绝提示文案的一致性"""

    def test_apply_invalid_token_error_message_content(
        self, u1_s1, space_1, ct_meal_type, ct_recipe_10
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        MealPlan.objects.create(
            recipe=ct_recipe_10,
            space=space_1,
            meal_type=ct_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=2
        )

        r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': today_str,
            'sync_token': 'invalid_token_' + 'a' * 50,
        }, content_type='application/json')
        data = json.loads(r.content)

        assert ERROR_PREFIX_TOKEN_FAILED in data['errors'][0]
        assert 'Data has been modified' in data['token_error']
        assert 'Please generate a new preview' in data['token_error']

    def test_apply_token_expired_after_mealplan_change_error_message(
        self, u1_s1, space_1, ct_meal_type, ct_recipe_10
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        mp = MealPlan.objects.create(
            recipe=ct_recipe_10,
            space=space_1,
            meal_type=ct_meal_type,
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

        mp.servings = 5
        mp.save()

        apply_r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': today_str,
            'sync_token': valid_token,
        }, content_type='application/json')
        data = json.loads(apply_r.content)

        assert apply_r.status_code == 409
        assert data['token_invalid'] is True
        assert 'Data has been modified by another user' in data['token_error']
        assert 'Please generate a new preview and try again' in data['token_error']
        assert ERROR_PREFIX_TOKEN_FAILED in data['errors'][0]
        assert ERROR_MSG_DATA_MODIFIED in data['errors'][0]

    def test_direct_class_empty_token_error_message(
        self, u1_s1, space_1, ct_meal_type, ct_recipe_10
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_date = today.date()

        sync = MealPlanShoppingSync(
            user=user,
            space=space_1,
            from_date=today_date,
            to_date=today_date
        )

        valid_empty, msg_empty = sync.validate_sync_token("")
        assert valid_empty is False
        assert msg_empty == ERROR_MSG_TOKEN_REQUIRED

        valid_none, msg_none = sync.validate_sync_token(None)
        assert valid_none is False
        assert msg_none == ERROR_MSG_TOKEN_REQUIRED

    def test_direct_class_wrong_token_error_message(
        self, u1_s1, space_1, ct_meal_type, ct_recipe_10
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_date = today.date()

        sync = MealPlanShoppingSync(
            user=user,
            space=space_1,
            from_date=today_date,
            to_date=today_date
        )

        valid, msg = sync.validate_sync_token("0" * 64)
        assert valid is False
        assert msg == ERROR_MSG_DATA_MODIFIED

    def test_apply_rejection_errors_list_not_empty(
        self, u1_s1, space_1, ct_meal_type, ct_recipe_10
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        MealPlan.objects.create(
            recipe=ct_recipe_10,
            space=space_1,
            meal_type=ct_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=2
        )

        r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': today_str,
            'sync_token': 'invalid_' + 'a' * 57,
        }, content_type='application/json')
        data = json.loads(r.content)

        assert isinstance(data['errors'], list)
        assert len(data['errors']) >= 1
        for err in data['errors']:
            assert isinstance(err, str)
            assert len(err) > 0


class TestApplySuccessResponseContract:
    """测试 Apply 成功时的响应契约"""

    def test_apply_success_status_code_200(
        self, u1_s1, space_1, ct_meal_type, ct_recipe_10
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        MealPlan.objects.create(
            recipe=ct_recipe_10,
            space=space_1,
            meal_type=ct_meal_type,
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

        apply_r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': today_str,
            'sync_token': token,
        }, content_type='application/json')

        assert apply_r.status_code == 200

    def test_apply_success_response_fields(
        self, u1_s1, space_1, ct_meal_type, ct_recipe_10
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        MealPlan.objects.create(
            recipe=ct_recipe_10,
            space=space_1,
            meal_type=ct_meal_type,
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

        apply_r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': today_str,
            'sync_token': token,
        }, content_type='application/json')
        data = json.loads(apply_r.content)

        _assert_field_subset(data.keys(), EXPECTED_APPLY_SUCCESS_FIELDS, "Apply success response")

        assert isinstance(data['added'], int)
        assert isinstance(data['merged'], int)
        assert isinstance(data['removed'], int)
        assert isinstance(data['failed'], int)
        assert isinstance(data['errors'], list)
        assert isinstance(data['rolled_back'], bool)
        assert isinstance(data['token_invalid'], bool)
        assert data['token_error'] is None

    def test_apply_success_flags(
        self, u1_s1, space_1, ct_meal_type, ct_recipe_10
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        MealPlan.objects.create(
            recipe=ct_recipe_10,
            space=space_1,
            meal_type=ct_meal_type,
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

        apply_r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': today_str,
            'sync_token': token,
        }, content_type='application/json')
        data = json.loads(apply_r.content)

        assert data['token_invalid'] is False
        assert data['token_error'] is None
        assert data['rolled_back'] is False
        assert data['failed'] == 0
        assert data['errors'] == []
        assert data['added'] == 1


class TestPermissionDeniedContract:
    """测试权限拒绝的响应契约"""

    def test_apply_permission_denied_403(
        self, g1_s1, space_1, ct_meal_type, ct_recipe_10
    ):
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        r = g1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': today_str,
            'sync_token': 'a' * 64,
        }, content_type='application/json')

        assert r.status_code == 403

    def test_preview_permission_denied_403(
        self, g1_s1, space_1, ct_meal_type, ct_recipe_10
    ):
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        r = g1_s1.post(reverse(PREVIEW_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')

        assert r.status_code == 403


class TestPreviewReapplyFullFlowContract:
    """测试预览→拒绝→重新预览→成功 的完整流程契约"""

    def test_full_re_preview_flow_response_contracts(
        self, u1_s1, space_1, ct_meal_type, ct_recipe_10
    ):
        user = auth.get_user(u1_s1)
        today = timezone.now()
        today_str = today.strftime("%Y-%m-%d")

        mp = MealPlan.objects.create(
            recipe=ct_recipe_10,
            space=space_1,
            meal_type=ct_meal_type,
            from_date=today,
            to_date=today,
            created_by=user,
            servings=2
        )

        preview1_r = u1_s1.post(reverse(PREVIEW_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')
        assert preview1_r.status_code == 200
        preview1_data = json.loads(preview1_r.content)
        _assert_field_subset(preview1_data.keys(), EXPECTED_PREVIEW_FIELDS, "Preview 1")
        token1 = preview1_data['sync_token']

        mp.servings = 5
        mp.save()

        apply1_r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': today_str,
            'sync_token': token1,
        }, content_type='application/json')
        assert apply1_r.status_code == 409
        apply1_data = json.loads(apply1_r.content)
        _assert_field_subset(apply1_data.keys(), EXPECTED_APPLY_REJECT_FIELDS, "Apply 1 reject")
        assert apply1_data['token_invalid'] is True
        assert apply1_data['added'] == 0

        preview2_r = u1_s1.post(reverse(PREVIEW_URL), {
            'from_date': today_str,
            'to_date': today_str,
        }, content_type='application/json')
        assert preview2_r.status_code == 200
        preview2_data = json.loads(preview2_r.content)
        _assert_field_subset(preview2_data.keys(), EXPECTED_PREVIEW_FIELDS, "Preview 2")
        token2 = preview2_data['sync_token']
        assert token1 != token2

        apply2_r = u1_s1.post(reverse(APPLY_URL), {
            'from_date': today_str,
            'to_date': today_str,
            'sync_token': token2,
        }, content_type='application/json')
        assert apply2_r.status_code == 200
        apply2_data = json.loads(apply2_r.content)
        _assert_field_subset(apply2_data.keys(), EXPECTED_APPLY_SUCCESS_FIELDS, "Apply 2 success")
        assert apply2_data['token_invalid'] is False
        assert apply2_data['added'] == 1
        assert apply2_data['failed'] == 0

        with scopes_disabled():
            slr = ShoppingListRecipe.objects.get(mealplan=mp)
            assert slr.servings == 5
