import threading
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch, MagicMock

import pytest
from django.contrib import auth
from django.db import connection
from django.utils import timezone
from django_scopes import scopes_disabled

from cookbook.helper.meal_plan_forecast_helper import (
    ForecastConflictError,
    MAX_OPTIMISTIC_RETRIES,
    _compute_max_attempts,
    _get_food_reserve_retry_limits,
    _reserve_inventory_with_optimistic_lock,
    calculate_meal_plan_forecast,
)
from cookbook.models import (
    Food,
    Household,
    Ingredient,
    InventoryEntry,
    InventoryLocation,
    MealPlan,
    MealType,
    Recipe,
    Step,
    Unit,
    UserSpace,
)


@pytest.fixture
def rt_space(space_1):
    return space_1


@pytest.fixture
def rt_user(u1_s1):
    return auth.get_user(u1_s1)


@pytest.fixture
def rt_user_space(rt_user, rt_space):
    return UserSpace.objects.filter(user=rt_user, space=rt_space).first()


@pytest.fixture
def rt_household(rt_space):
    with scopes_disabled():
        return Household.objects.create(name='rt_hh', space=rt_space)


@pytest.fixture
def rt_location(rt_space, rt_user, rt_household):
    with scopes_disabled():
        return InventoryLocation.objects.create(
            name='rt_loc', space=rt_space, created_by=rt_user, household=rt_household,
        )


@pytest.fixture
def rt_unit(rt_space):
    with scopes_disabled():
        return Unit.objects.create(name='RT_Unit', space=rt_space)


@pytest.fixture
def rt_food_factory(rt_space):
    def _make(name, reserve_retry_limit=1):
        with scopes_disabled():
            return Food.objects.create(
                name=name, space=rt_space, reserve_retry_limit=reserve_retry_limit,
            )
    return _make


@pytest.fixture
def rt_meal_type(rt_space, rt_user):
    with scopes_disabled():
        return MealType.objects.create(name='mt', space=rt_space, created_by=rt_user)


def _make_entry(space, user, location, food, unit, amount=Decimal('10'), version=0):
    with scopes_disabled():
        return InventoryEntry.objects.create(
            inventory_location=location,
            amount=amount,
            unit=unit,
            food=food,
            created_by=user,
            space=space,
            version=version,
        )


def _make_recipe(space, user, food, unit, amount=Decimal('2'), servings=2):
    with scopes_disabled():
        recipe = Recipe.objects.create(
            name=f'r_{food.name}', servings=servings, created_by=user, space=space, internal=True,
        )
        step = Step.objects.create(name='s1', instruction='x', space=space)
        recipe.steps.add(step)
        ing = Ingredient.objects.create(amount=amount, food=food, unit=unit, space=space)
        step.ingredients.add(ing)
    return recipe


def _make_mp(space, user, recipe, from_date, meal_type):
    with scopes_disabled():
        return MealPlan.objects.create(
            recipe=recipe, space=space, meal_type=meal_type,
            from_date=from_date, to_date=from_date, created_by=user,
        )


# ---------------------------------------------------------------------------
# 场景 1: 默认值生效 — reserve_retry_limit=1 (未显式设置), 冲突后只重试 1 次
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_default_retry_limit_is_one(rt_space, rt_food_factory):
    """Food 默认 reserve_retry_limit=1，_compute_max_attempts 应返回 2 (1次尝试 + 1次重试)."""
    food_a = rt_food_factory('FoodA_default')

    assert food_a.reserve_retry_limit == 1, "reserve_retry_limit should default to 1"

    limits = _get_food_reserve_retry_limits(rt_space, {food_a.id})
    assert limits[food_a.id] == 1

    max_attempts = _compute_max_attempts(rt_space, {food_a.id})
    assert max_attempts == 2, "1 + reserve_retry_limit(1) = 2 attempts total"


@pytest.mark.django_db(transaction=True)
def test_default_retry_conflict_after_two_attempts_raises(
    rt_space, rt_user, rt_household, rt_location, rt_unit, rt_food_factory,
):
    """
    默认 limit=1，总尝试次数 2。
    通过 mock 让 InventoryEntry update 永远返回 0（模拟持续冲突），
    经过 2 次尝试后应抛出 ForecastConflictError。
    """
    food = rt_food_factory('FoodDef2', reserve_retry_limit=1)
    entry = _make_entry(rt_space, rt_user, rt_location, food, rt_unit, amount=Decimal('10'))

    reservations = [
        {'food_id': food.id, 'base_unit_key': rt_unit.id, 'amount': Decimal('3')}
    ]

    call_counter = {'n': 0}
    original_filter_update = None

    def fake_update_conflict_always(**kwargs):
        call_counter['n'] += 1
        return 0

    with patch('cookbook.models.InventoryEntry.objects.filter') as mock_filter:
        mock_qs = MagicMock()
        mock_filter.return_value = mock_qs
        mock_qs.update = fake_update_conflict_always
        mock_qs.select_for_update.return_value = mock_qs
        mock_qs.select_related.return_value = mock_qs

        def mock_iter():
            return iter([entry])
        mock_qs.__iter__ = mock_iter

        with pytest.raises(ForecastConflictError):
            _reserve_inventory_with_optimistic_lock(
                space=rt_space, household=rt_household, reservations=reservations,
            )

    # 默认 limit=1 → max_attempts=2 → 共调用 update 2 次
    assert call_counter['n'] >= 2, (
        f"Expected at least 2 update calls with default retry=1, got {call_counter['n']}"
    )


# ---------------------------------------------------------------------------
# 场景 2: 单一食材调高 reserve_retry_limit → 能够成功重试更多次
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_high_retry_limit_succeeds_after_multiple_conflicts(
    rt_space, rt_user, rt_user_space, rt_household, rt_location,
    rt_unit, rt_food_factory, rt_meal_type,
):
    """
    将某个热门食材的 reserve_retry_limit 调高到 5，
    模拟前 5 次 UPDATE 都冲突（返回 0），第 6 次成功。
    预期：最终不会抛错，库存正确扣减。
    """
    hot_food = rt_food_factory('HotFood_Eggs', reserve_retry_limit=5)
    entry = _make_entry(rt_space, rt_user, rt_location, hot_food, rt_unit, amount=Decimal('100'))

    recipe = _make_recipe(rt_space, rt_user, hot_food, rt_unit, amount=Decimal('4'))
    tomorrow = timezone.now() + timedelta(days=1)
    _make_mp(rt_space, rt_user, recipe, tomorrow, rt_meal_type)

    # 用计数器 + patch 模拟冲突 N 次，然后放行
    update_counter = {'n': 0}
    succeed_after_attempts = 5  # 前 5 次 update 冲突，第 6 次成功

    original_manager = InventoryEntry.objects

    class ConflictThenSuccessQueryset:
        """
        模拟冲突多次后成功的 queryset。
        我们直接拦截 InventoryEntry.objects 顶层调用。
        """

    # 我们使用另一种更精确的方式: 直接记录 entry.pk 的当前内存 version,
    # 通过 patch django.db.models.query.QuerySet.update 实现
    from django.db.models import F as _F

    real_update = None

    def patch_update(self, **kwargs):
        nonlocal real_update
        # 只拦截针对 InventoryEntry 的 update
        if self.model is not InventoryEntry:
            return real_update(self, **kwargs)
        call_no = update_counter['n'] + 1
        update_counter['n'] = call_no
        if call_no <= succeed_after_attempts:
            return 0
        return real_update(self, **kwargs)

    from django.db.models import QuerySet
    real_update = QuerySet.update
    QuerySet.update = patch_update
    try:
        result = calculate_meal_plan_forecast(
            user=rt_user,
            user_space=rt_user_space,
            space=rt_space,
            from_date=timezone.now().date(),
            to_date=(timezone.now() + timedelta(days=7)).date(),
            commit_reservation=True,
        )
    finally:
        QuerySet.update = real_update

    assert update_counter['n'] >= succeed_after_attempts + 1, (
        f"Expected {succeed_after_attempts + 1}+ update calls, got {update_counter['n']}"
    )
    entry.refresh_from_db()
    assert entry.amount == Decimal('96'), f"Expected 96 after 4 deduction, got {entry.amount}"
    assert entry.version >= 1, "Version should have been incremented on success"


@pytest.mark.django_db(transaction=True)
def test_compute_max_attempts_high_limit(rt_space, rt_food_factory):
    """_compute_max_attempts 对高 limit 食材应返回正确的总尝试数."""
    f1 = rt_food_factory('HighLimitFood', reserve_retry_limit=5)
    f2 = rt_food_factory('NormalLimitFood', reserve_retry_limit=1)

    # f1=5, f2=1 → max=5 → 总尝试数 = 1+5 = 6
    assert _compute_max_attempts(rt_space, {f1.id, f2.id}) == 6


# ---------------------------------------------------------------------------
# 场景 3: reserve_retry_limit=0 → 一旦冲突立即返回, 不进行任何重试
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_zero_retry_limit_immediate_conflict(
    rt_space, rt_user, rt_household, rt_location, rt_unit, rt_food_factory,
):
    """
    reserve_retry_limit=0 的食材：冲突一次就立即抛错。
    max_attempts = 1 (只有首次尝试，0 次重试).
    """
    no_retry_food = rt_food_factory('NoRetryFood', reserve_retry_limit=0)

    assert no_retry_food.reserve_retry_limit == 0

    # 第一次就会冲突 → 不重试 → 立即返回
    assert _compute_max_attempts(rt_space, {no_retry_food.id},
                                 last_conflict_food_id=no_retry_food.id) == 1

    entry = _make_entry(rt_space, rt_user, rt_location, no_retry_food, rt_unit,
                        amount=Decimal('10'))

    reservations = [
        {'food_id': no_retry_food.id, 'base_unit_key': rt_unit.id, 'amount': Decimal('2')}
    ]

    call_counter = {'n': 0}

    from django.db.models import QuerySet
    real_update = QuerySet.update

    def patch_update_zero_retry(self, **kwargs):
        if self.model is not InventoryEntry:
            return real_update(self, **kwargs)
        call_counter['n'] += 1
        # 永远冲突
        return 0

    QuerySet.update = patch_update_zero_retry
    try:
        with pytest.raises(ForecastConflictError) as exc_info:
            _reserve_inventory_with_optimistic_lock(
                space=rt_space, household=rt_household, reservations=reservations,
            )
    finally:
        QuerySet.update = real_update

    # limit=0 → 首次尝试冲突就抛错 → update 只被调用 1 次
    assert call_counter['n'] == 1, (
        f"reserve_retry_limit=0 should cause exactly 1 update call, got {call_counter['n']}"
    )
    assert exc_info.value.food_id == no_retry_food.id


@pytest.mark.django_db(transaction=True)
def test_mixed_limits_zero_retry_food_fails_immediately_even_when_others_high(
    rt_space, rt_user, rt_household, rt_location, rt_unit, rt_food_factory,
):
    """
    混合场景：同时涉及一个 limit=0 食材 + 一个 limit=10 食材。
    如果冲突发生在 limit=0 的食材上 → 立即失败（不会享受 limit=10 的重试）。
    """
    f_sensitive = rt_food_factory('SensitiveFood', reserve_retry_limit=0)
    f_hot = rt_food_factory('HotFood', reserve_retry_limit=10)

    # 初始 limit=0 的食材在冲突之后整体尝试数只有 1
    attempts = _compute_max_attempts(rt_space, {f_sensitive.id, f_hot.id},
                                     last_conflict_food_id=f_sensitive.id)
    assert attempts == 1, (
        f"When limit=0 food conflicts, max_attempts should be 1, got {attempts}"
    )


# ---------------------------------------------------------------------------
# 补充: 验证 reserve_retry_limit=0 的 ForecastConflictError 可以被正常 raise
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_mismatched_version_zero_retry_raises_instantly(
    rt_space, rt_user, rt_household, rt_location, rt_unit, rt_food_factory,
):
    """
    数据库 entry.version=5，但对象内存中 old_version=0。
    reserve_retry_limit=0 → 一次 update 命中 WHERE version=0 → 0 rows → 直接抛异常, 不重试。
    """
    food = rt_food_factory('VersionMismatch', reserve_retry_limit=0)
    entry = _make_entry(rt_space, rt_user, rt_location, food, rt_unit, amount=Decimal('20'), version=5)

    reservations = [
        {'food_id': food.id, 'base_unit_key': rt_unit.id, 'amount': Decimal('5')}
    ]

    call_counter = {'n': 0}

    # 我们用 select_for_update 返回的对象 version 始终为 5，但实际 update 前模拟
    # entry 被我们直接修改到了一个错误版本
    class InjectMemoryEntry:
        pass

    original_entry_version = entry.version
    try:
        # 使用直接的 version 不匹配: 我们手动把 memory entry.version 改成错的,
        # 实际数据库里是 version=5 → 所以 UPDATE 永远不会命中
        with patch(
            'cookbook.helper.meal_plan_forecast_helper.InventoryEntry.objects.filter'
        ) as mock_filter:
            mock_qs = MagicMock()
            mock_filter.return_value = mock_qs
            mock_qs.select_for_update.return_value = mock_qs
            mock_qs.select_related.return_value = mock_qs

            # 返回一份 entry，但内存中 version 错了（模拟并发 update 后 version 已推进）
            fake_entry = MagicMock()
            fake_entry.pk = entry.pk
            fake_entry.food_id = entry.food_id
            fake_entry.unit = entry.unit
            fake_entry.amount = Decimal('20')
            fake_entry.version = 0  # 故意错误
            # getattr on food / unit
            def _get_fake_attr(name):
                if name == 'base_unit':
                    return None
                return getattr(entry.unit, name, None)
            fake_entry.unit.__getattr__.side_effect = _get_fake_attr if False else lambda n: None
            # set up simpler fake unit object
            class FakeUnit:
                id = rt_unit.id
                name = rt_unit.name
                base_unit = None
            fake_entry.unit = FakeUnit()

            def mock_iter():
                return iter([fake_entry])
            mock_qs.__iter__ = mock_iter

            def real_update_call(**kwargs):
                call_counter['n'] += 1
                # 模拟数据库返回 0 行被更新
                return 0
            mock_qs.update = real_update_call

            with pytest.raises(ForecastConflictError):
                _reserve_inventory_with_optimistic_lock(
                    space=rt_space, household=rt_household, reservations=reservations,
                )
    finally:
        pass

    assert call_counter['n'] == 1, (
        f"reserve_retry_limit=0 must fail without retry: 1 update call expected, got {call_counter['n']}"
    )
