import threading
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib import auth
from django.db import connection
from django.utils import timezone
from django_scopes import scopes_disabled

from cookbook.helper.meal_plan_forecast_helper import (
    ForecastConflictError,
    MAX_OPTIMISTIC_RETRIES,
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
def forecast_space(space_1):
    return space_1


@pytest.fixture
def forecast_user(u1_s1):
    return auth.get_user(u1_s1)


@pytest.fixture
def forecast_user_space(forecast_user, forecast_space):
    return UserSpace.objects.filter(user=forecast_user, space=forecast_space).first()


@pytest.fixture
def forecast_household(forecast_space):
    with scopes_disabled():
        return Household.objects.create(name='test_household', space=forecast_space)


@pytest.fixture
def forecast_location(forecast_space, forecast_user, forecast_household):
    with scopes_disabled():
        return InventoryLocation.objects.create(
            name='test_location',
            space=forecast_space,
            created_by=forecast_user,
            household=forecast_household,
        )


@pytest.fixture
def forecast_food(forecast_space):
    with scopes_disabled():
        return Food.objects.create(name='TestFood_Forecast', space=forecast_space)


@pytest.fixture
def forecast_unit(forecast_space):
    with scopes_disabled():
        return Unit.objects.create(name='TestUnit_Forecast', space=forecast_space)


@pytest.fixture
def inventory_entry(forecast_space, forecast_user, forecast_location, forecast_food, forecast_unit):
    with scopes_disabled():
        entry = InventoryEntry.objects.create(
            inventory_location=forecast_location,
            amount=Decimal('10'),
            unit=forecast_unit,
            food=forecast_food,
            created_by=forecast_user,
            space=forecast_space,
            version=0,
        )
    return entry


def _make_recipe_with_ingredient(space, user, food, unit, amount=Decimal('2')):
    with scopes_disabled():
        recipe = Recipe.objects.create(
            name=f'recipe_{food.name}',
            servings=2,
            created_by=user,
            space=space,
            internal=True,
        )
        step = Step.objects.create(name='step', instruction='test', space=space)
        recipe.steps.add(step)
        ingredient = Ingredient.objects.create(
            amount=amount,
            food=food,
            unit=unit,
            space=space,
        )
        step.ingredients.add(ingredient)
    return recipe


def _make_meal_plan(space, user, recipe, from_date, meal_type):
    with scopes_disabled():
        return MealPlan.objects.create(
            recipe=recipe,
            space=space,
            meal_type=meal_type,
            from_date=from_date,
            to_date=from_date,
            created_by=user,
        )


@pytest.fixture
def meal_type(forecast_space, forecast_user):
    with scopes_disabled():
        return MealType.objects.create(
            name='test_meal_type',
            space=forecast_space,
            created_by=forecast_user,
        )


@pytest.mark.django_db(transaction=True)
def test_optimistic_lock_single_reservation(
    forecast_space, forecast_user, forecast_user_space,
    forecast_household, inventory_entry, forecast_food, forecast_unit,
    meal_type,
):
    recipe = _make_recipe_with_ingredient(
        forecast_space, forecast_user, forecast_food, forecast_unit, amount=Decimal('3')
    )
    tomorrow = timezone.now() + timedelta(days=1)
    _make_meal_plan(forecast_space, forecast_user, recipe, tomorrow, meal_type)

    result = calculate_meal_plan_forecast(
        user=forecast_user,
        user_space=forecast_user_space,
        space=forecast_space,
        from_date=timezone.now().date(),
        to_date=(timezone.now() + timedelta(days=7)).date(),
        commit_reservation=True,
    )

    inventory_entry.refresh_from_db()
    assert inventory_entry.amount == Decimal('7')
    assert inventory_entry.version == 1


@pytest.mark.django_db(transaction=True)
def test_optimistic_lock_concurrent_reservation_detects_conflict(
    forecast_space, forecast_user, forecast_user_space,
    forecast_household, forecast_location, forecast_food, forecast_unit,
    meal_type,
):
    with scopes_disabled():
        entry1 = InventoryEntry.objects.create(
            inventory_location=forecast_location,
            amount=Decimal('10'),
            unit=forecast_unit,
            food=forecast_food,
            created_by=forecast_user,
            space=forecast_space,
            version=0,
        )

    recipe = _make_recipe_with_ingredient(
        forecast_space, forecast_user, forecast_food, forecast_unit, amount=Decimal('4')
    )
    tomorrow = timezone.now() + timedelta(days=1)
    _make_meal_plan(forecast_space, forecast_user, recipe, tomorrow, meal_type)

    conflict_raised = threading.Event()
    first_session_done = threading.Event()

    def session1_reserve():
        from django.db import connections
        try:
            conn = connections['default']
            conn.ensure_connection()
            result = calculate_meal_plan_forecast(
                user=forecast_user,
                user_space=forecast_user_space,
                space=forecast_space,
                from_date=timezone.now().date(),
                to_date=(timezone.now() + timedelta(days=7)).date(),
                commit_reservation=True,
            )
            first_session_done.set()
        except Exception:
            first_session_done.set()

    def session2_direct_update():
        first_session_done.wait(timeout=5)
        with scopes_disabled():
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE cookbook_inventoryentry SET version = version + 1 WHERE id = %s",
                    [entry1.pk]
                )
            connection.commit()

    t1 = threading.Thread(target=session1_reserve)
    t2 = threading.Thread(target=session2_direct_update)

    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    with scopes_disabled():
        entry1.refresh_from_db()
        assert entry1.version >= 1


@pytest.mark.django_db(transaction=True)
def test_optimistic_lock_version_increments_on_each_reservation(
    forecast_space, forecast_user, forecast_user_space,
    forecast_household, forecast_location, forecast_food, forecast_unit,
    meal_type,
):
    with scopes_disabled():
        entry = InventoryEntry.objects.create(
            inventory_location=forecast_location,
            amount=Decimal('20'),
            unit=forecast_unit,
            food=forecast_food,
            created_by=forecast_user,
            space=forecast_space,
            version=0,
        )

    recipe = _make_recipe_with_ingredient(
        forecast_space, forecast_user, forecast_food, forecast_unit, amount=Decimal('3')
    )

    for i in range(3):
        tomorrow = timezone.now() + timedelta(days=i + 1)
        _make_meal_plan(forecast_space, forecast_user, recipe, tomorrow, meal_type)

    calculate_meal_plan_forecast(
        user=forecast_user,
        user_space=forecast_user_space,
        space=forecast_space,
        from_date=timezone.now().date(),
        to_date=(timezone.now() + timedelta(days=7)).date(),
        commit_reservation=True,
    )

    entry.refresh_from_db()
    assert entry.amount == Decimal('11')
    assert entry.version == 1


@pytest.mark.django_db(transaction=True)
def test_optimistic_lock_conflict_error_raised_on_version_mismatch(
    forecast_space, forecast_user, forecast_household,
    forecast_location, forecast_food, forecast_unit,
):
    with scopes_disabled():
        entry = InventoryEntry.objects.create(
            inventory_location=forecast_location,
            amount=Decimal('5'),
            unit=forecast_unit,
            food=forecast_food,
            created_by=forecast_user,
            space=forecast_space,
            version=0,
        )
        entry.version = 99
        entry.save()

    reservations = [
        {
            'food_id': forecast_food.id,
            'base_unit_key': forecast_unit.id,
            'amount': Decimal('3'),
        }
    ]

    with pytest.raises(ForecastConflictError) as exc_info:
        _reserve_inventory_with_optimistic_lock(
            space=forecast_space,
            household=forecast_household,
            reservations=reservations,
        )

    assert exc_info.value.food_id == forecast_food.id
    assert exc_info.value.entry_id == entry.pk


@pytest.mark.django_db(transaction=True)
def test_optimistic_lock_no_double_deduction_under_concurrency(
    forecast_space, forecast_user, forecast_user_space,
    forecast_household, forecast_location, forecast_food, forecast_unit,
    meal_type,
):
    with scopes_disabled():
        entry = InventoryEntry.objects.create(
            inventory_location=forecast_location,
            amount=Decimal('10'),
            unit=forecast_unit,
            food=forecast_food,
            created_by=forecast_user,
            space=forecast_space,
            version=0,
        )

    recipe = _make_recipe_with_ingredient(
        forecast_space, forecast_user, forecast_food, forecast_unit, amount=Decimal('4')
    )
    tomorrow = timezone.now() + timedelta(days=1)
    _make_meal_plan(forecast_space, forecast_user, recipe, tomorrow, meal_type)

    results = []
    errors = []

    def run_forecast():
        try:
            result = calculate_meal_plan_forecast(
                user=forecast_user,
                user_space=forecast_user_space,
                space=forecast_space,
                from_date=timezone.now().date(),
                to_date=(timezone.now() + timedelta(days=7)).date(),
                commit_reservation=True,
            )
            results.append(result)
        except ForecastConflictError as e:
            errors.append(e)
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=run_forecast)
    t2 = threading.Thread(target=run_forecast)

    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    entry.refresh_from_db()
    assert entry.amount >= Decimal('2'), f"Inventory should not go below 2, got {entry.amount}"
    assert entry.amount <= Decimal('6'), f"Inventory should not exceed 6 after one deduction, got {entry.amount}"

    total_deducted = Decimal('10') - entry.amount
    assert total_deducted == Decimal('4'), (
        f"Total deducted should be exactly 4 (one recipe's worth), got {total_deducted}"
    )


@pytest.mark.django_db(transaction=True)
def test_optimistic_lock_read_only_no_reservation(
    forecast_space, forecast_user, forecast_user_space,
    forecast_household, inventory_entry, forecast_food, forecast_unit,
    meal_type,
):
    recipe = _make_recipe_with_ingredient(
        forecast_space, forecast_user, forecast_food, forecast_unit, amount=Decimal('3')
    )
    tomorrow = timezone.now() + timedelta(days=1)
    _make_meal_plan(forecast_space, forecast_user, recipe, tomorrow, meal_type)

    result = calculate_meal_plan_forecast(
        user=forecast_user,
        user_space=forecast_user_space,
        space=forecast_space,
        from_date=timezone.now().date(),
        to_date=(timezone.now() + timedelta(days=7)).date(),
        commit_reservation=False,
    )

    inventory_entry.refresh_from_db()
    assert inventory_entry.amount == Decimal('10')
    assert inventory_entry.version == 0

    food_results = [e for e in result if e.food_id == forecast_food.id]
    assert len(food_results) >= 1
    food_entry = food_results[0]
    assert food_entry.status_reserved == Decimal('3')
    assert food_entry.status_available == Decimal('7')
    assert food_entry.status_to_buy == Decimal('0')
