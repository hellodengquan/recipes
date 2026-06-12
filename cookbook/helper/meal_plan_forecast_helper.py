
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from django.db import transaction
from django.db.models import Sum, Q, F
from django.utils import timezone

from cookbook.helper.permission_helper import get_household_user_ids
from cookbook.helper.unit_conversion_helper import UnitConversionHelper, ConversionException
from cookbook.models import (
    Food,
    Ingredient,
    InventoryEntry,
    MealPlan,
    Recipe,
    Step,
    Unit,
)


class ForecastConflictError(Exception):
    """
    Raised when optimistic locking detects a concurrent modification
    to an InventoryEntry during a reservation (pre-allocation) operation.
    """
    def __init__(self, message=None, food_id=None, entry_id=None):
        self.food_id = food_id
        self.entry_id = entry_id
        super().__init__(message or 'Inventory was modified by another session. Please retry.')


MAX_OPTIMISTIC_RETRIES = 2


@dataclass
class ForecastFoodUsage:
    meal_plan_id: int
    meal_plan_title: str
    recipe_id: Optional[int]
    recipe_name: str
    meal_type_name: str
    from_date: str
    required_amount: Decimal
    unit_name: Optional[str]
    covered_by_stock: Decimal = Decimal('0')
    covered_by_purchase: Decimal = Decimal('0')


@dataclass
class ForecastFoodEntry:
    food_id: int
    food_name: str
    unit_name: Optional[str] = None
    base_unit_name: Optional[str] = None

    total_inventory: Decimal = Decimal('0')
    total_required: Decimal = Decimal('0')

    status_available: Decimal = Decimal('0')
    status_reserved: Decimal = Decimal('0')
    status_to_buy: Decimal = Decimal('0')

    usages: list = field(default_factory=list)


def _get_household_for_user(user_space):
    try:
        return user_space.household
    except AttributeError:
        return None


def _get_meal_plan_ingredients(meal_plan: MealPlan, uch: UnitConversionHelper):
    """
    Get all ingredients required for a meal plan, scaled by servings.
    Returns list of dicts with: food_id, food_name, amount, unit_id, unit_name, base_unit_key
    """
    if not meal_plan.recipe:
        return []

    recipe = meal_plan.recipe
    base_servings = getattr(recipe, 'servings', None) or Decimal('1')
    plan_servings = meal_plan.servings or Decimal('1')
    try:
        scale_factor = Decimal(plan_servings) / Decimal(base_servings)
    except (ZeroDivisionError, ValueError, TypeError):
        scale_factor = Decimal('1')

    ingredients = []
    steps = Step.objects.filter(recipe=recipe).prefetch_related('ingredients', 'ingredients__food', 'ingredients__unit')
    for step in steps:
        for ing in step.ingredients.all():
            if ing.is_header or ing.no_amount or not ing.food:
                continue
            if ing.food.ignore_shopping:
                continue

            scaled_amount = Decimal(ing.amount or 0) * scale_factor
            base_unit_key = None
            unit_name = None
            if ing.unit:
                unit_name = ing.unit.name
                base_unit_key = getattr(ing.unit, 'base_unit', None) or ing.unit.id or ing.unit.name

            ingredients.append({
                'ingredient_id': ing.id,
                'food_id': ing.food_id,
                'food_name': ing.food.name,
                'amount': scaled_amount,
                'unit_id': ing.unit_id,
                'unit_name': unit_name,
                'base_unit_key': base_unit_key,
                'original_unit': ing.unit,
            })

    return ingredients


def _get_food_reserve_retry_limits(space, food_ids):
    """
    Fetch reserve_retry_limit for the given food_ids.
    Returns a dict: {food_id: retry_limit_int}
    Any food not found or with NULL limit defaults to 1.
    """
    from cookbook.models import Food

    limits = {}
    qs = Food.objects.filter(space=space, id__in=list(food_ids))
    for f in qs.only('id', 'reserve_retry_limit'):
        limits[f.id] = int(getattr(f, 'reserve_retry_limit', 1) or 1)
    for fid in food_ids:
        limits.setdefault(fid, 1)
    return limits


def _compute_max_attempts(space, food_ids, last_conflict_food_id=None):
    """
    Based on Food.reserve_retry_limit, determine the retry policy.

    Strategy:
    - If a specific food caused the last conflict and its reserve_retry_limit=0
      → return 1 (immediate failure, no retries)
    - Otherwise the global attempt count is 1 + max(reserve_retry_limit across foods),
      i.e. first try + N retries.
    """
    limits = _get_food_reserve_retry_limits(space, food_ids)
    if not limits:
        return MAX_OPTIMISTIC_RETRIES

    if last_conflict_food_id is not None and last_conflict_food_id in limits:
        if limits[last_conflict_food_id] == 0:
            return 1

    max_limit = max(limits.values())
    return 1 + max(0, max_limit)


def _write_forecast_logs(
    space,
    user,
    food_ids,
    status,
    retry_count,
    hit_limit,
    conflict_food_id=None,
    limits_by_food=None,
    note=None,
):
    """
    Write ForecastLog entries for each food involved in a reservation run.

    Args:
        space: Space object
        user: User object (can be None)
        food_ids: iterable of food_ids involved
        status: 'success' or 'conflict'
        retry_count: number of retries that actually occurred
        hit_limit: whether the retry limit was hit (only meaningful for conflict)
        conflict_food_id: which food triggered the conflict (if any)
        limits_by_food: dict of {food_id: reserve_retry_limit} at time of operation
        note: optional note string
    """
    from cookbook.models import ForecastLog

    if limits_by_food is None:
        limits_by_food = _get_food_reserve_retry_limits(space, set(food_ids))

    logs = []
    for fid in food_ids:
        is_conflict_trigger = (fid == conflict_food_id)
        logs.append(ForecastLog(
            space=space,
            food_id=fid,
            created_by=user,
            status=status,
            retry_count=retry_count,
            hit_limit=hit_limit and is_conflict_trigger if hit_limit else False,
            reserve_retry_limit=limits_by_food.get(fid, 1),
            note=note,
        ))

    if logs:
        ForecastLog.objects.bulk_create(logs)


def _reserve_inventory_with_optimistic_lock(
    space,
    household,
    reservations,
    user=None,
    write_logs=False,
):
    """
    Perform the actual inventory reservation (deduction) using
    optimistic locking (version field) + select_for_update within a transaction.

    Each reservation run's overall retry limit is determined by the maximum
    Food.reserve_retry_limit among the foods being reserved that round.
    If any single food has limit=0 and causes a conflict, the entire batch
    fails immediately.

    Args:
        space: the current space
        household: the household to filter inventory by (can be None)
        reservations: list of dicts with keys:
            - food_id: int
            - base_unit_key: the unit key for matching
            - amount: Decimal amount to reserve (deduct from inventory)
        user: the user performing the operation (for logging)
        write_logs: if True, write ForecastLog entries for each food

    Returns:
        dict mapping (food_id, base_unit_key) -> Decimal of actually reserved amount

    Raises:
        ForecastConflictError: if optimistic lock fails after retries exhausted
    """
    if not reservations:
        return {}

    food_unit_pairs = set()
    food_ids = set()
    for r in reservations:
        food_unit_pairs.add((r['food_id'], r['base_unit_key']))
        food_ids.add(r['food_id'])

    reserved_amounts = defaultdict(Decimal)
    conflict_food_id = None
    total_retry_count = 0
    final_hit_limit = False
    final_limits = None

    while True:
        max_attempts = _compute_max_attempts(space, food_ids, conflict_food_id)
        limits = _get_food_reserve_retry_limits(space, food_ids)
        final_limits = limits

        for attempt in range(max_attempts):
            try:
                with transaction.atomic():
                    qs = InventoryEntry.objects.filter(
                        space=space,
                        amount__gt=0,
                    )
                    if household is not None:
                        qs = qs.filter(inventory_location__household=household)

                    qs = qs.filter(food_id__in=food_ids)

                    entries = qs.select_for_update().select_related('food', 'unit')

                    entries_by_key = defaultdict(list)
                    for entry in entries:
                        unit = entry.unit
                        base_unit_key = None
                        if unit:
                            base_unit_key = getattr(unit, 'base_unit', None) or unit.id or unit.name
                        key = (entry.food_id, base_unit_key)
                        entries_by_key[key].append(entry)

                    for r in reservations:
                        key = (r['food_id'], r['base_unit_key'])
                        to_reserve = r['amount']
                        if to_reserve <= 0:
                            continue

                        relevant_entries = entries_by_key.get(key, [])
                        remaining = to_reserve

                        for entry in relevant_entries:
                            if remaining <= 0:
                                break

                            deduct = min(entry.amount, remaining)
                            old_version = entry.version

                            updated = InventoryEntry.objects.filter(
                                pk=entry.pk,
                                version=old_version,
                            ).update(
                                amount=F('amount') - deduct,
                                version=F('version') + 1,
                            )

                            if updated == 0:
                                raise ForecastConflictError(
                                    f'Optimistic lock conflict on InventoryEntry {entry.pk} '
                                    f'(food_id={entry.food_id}, expected_version={old_version})',
                                    food_id=entry.food_id,
                                    entry_id=entry.pk,
                                )

                            entry.amount -= deduct
                            entry.version = old_version + 1
                            remaining -= deduct
                            reserved_amounts[key] += deduct

                if write_logs:
                    _write_forecast_logs(
                        space=space,
                        user=user,
                        food_ids=food_ids,
                        status='success',
                        retry_count=total_retry_count + attempt,
                        hit_limit=False,
                        conflict_food_id=None,
                        limits_by_food=final_limits,
                    )

                return dict(reserved_amounts)

            except ForecastConflictError as e:
                conflict_food_id = e.food_id
                if attempt >= max_attempts - 1:
                    total_retry_count += attempt
                    final_hit_limit = True
                    if write_logs:
                        _write_forecast_logs(
                            space=space,
                            user=user,
                            food_ids=food_ids,
                            status='conflict',
                            retry_count=total_retry_count,
                            hit_limit=True,
                            conflict_food_id=conflict_food_id,
                            limits_by_food=final_limits,
                        )
                    raise
                continue

        total_retry_count += max_attempts
        if conflict_food_id and limits.get(conflict_food_id, 1) == 0:
            if write_logs:
                _write_forecast_logs(
                    space=space,
                    user=user,
                    food_ids=food_ids,
                    status='conflict',
                    retry_count=total_retry_count,
                    hit_limit=True,
                    conflict_food_id=conflict_food_id,
                    limits_by_food=final_limits,
                )
            raise ForecastConflictError(
                f'Retry limit exhausted for food {conflict_food_id} (limit=0)',
                food_id=conflict_food_id,
            )

    return dict(reserved_amounts)


def calculate_meal_plan_forecast(
    user,
    user_space,
    space,
    from_date=None,
    to_date=None,
    commit_reservation=False,
):
    """
    Calculate ingredient forecast for meal plans within the given date range.

    When commit_reservation=True, the function also performs actual inventory
    deduction using optimistic locking + select_for_update to prevent
    concurrent double-deduction.

    Algorithm:
    1. Get all meal plans in the date range, ordered by from_date
    2. For each meal plan, collect its required ingredients
    3. Read current inventory using select_for_update (when committing)
    4. Process meal plans in order:
       - Use available inventory to cover requirements first (becomes "reserved")
       - Any requirements beyond available inventory become "to_buy"
       - Remaining inventory not used by any meal plan is "available" (已备)
    5. If commit_reservation=True, write deductions back to InventoryEntry
       using optimistic lock (version field)

    Returns:
        list of ForecastFoodEntry objects

    Raises:
        ForecastConflictError: when commit_reservation=True and a concurrent
            modification is detected that cannot be resolved by retry
    """
    household = _get_household_for_user(user_space)
    uch = UnitConversionHelper(space)

    if from_date is None:
        from_date = timezone.now().date()
    if to_date is None:
        to_date = from_date + timezone.timedelta(days=30)

    user_ids = get_household_user_ids(user_space)
    meal_plans = MealPlan.objects.filter(
        space=space,
        from_date__date__lte=to_date,
        to_date__date__gte=from_date,
    ).filter(
        Q(created_by=user) | Q(created_by_id__in=user_ids)
    ).select_related(
        'recipe', 'meal_type', 'created_by'
    ).order_by('from_date').distinct()

    inventory = defaultdict(lambda: defaultdict(Decimal))
    inventory_details = defaultdict(lambda: defaultdict(list))
    food_to_unit = {}

    if commit_reservation:
        with transaction.atomic():
            qs = InventoryEntry.objects.filter(
                space=space,
                amount__gt=0,
            )
            if household is not None:
                qs = qs.filter(inventory_location__household=household)

            qs = qs.select_for_update().select_related('food', 'unit')

            for entry in qs:
                food_id = entry.food_id
                amount = entry.amount
                unit = entry.unit
                base_unit_key = None
                if unit:
                    base_unit_key = getattr(unit, 'base_unit', None) or unit.id or unit.name
                inventory[food_id][base_unit_key] += amount
                inventory_details[food_id][base_unit_key].append(entry)
                if unit:
                    food_to_unit[(food_id, base_unit_key)] = unit
    else:
        qs = InventoryEntry.objects.filter(
            space=space,
            amount__gt=0,
        )
        if household is not None:
            qs = qs.filter(inventory_location__household=household)

        qs = qs.select_related('food', 'unit')

        for entry in qs:
            food_id = entry.food_id
            amount = entry.amount
            unit = entry.unit
            base_unit_key = None
            if unit:
                base_unit_key = getattr(unit, 'base_unit', None) or unit.id or unit.name
            inventory[food_id][base_unit_key] += amount
            inventory_details[food_id][base_unit_key].append(entry)
            if unit:
                food_to_unit[(food_id, base_unit_key)] = unit

    remaining_inventory = defaultdict(lambda: defaultdict(Decimal))
    for food_id, units in inventory.items():
        for unit_key, amount in units.items():
            remaining_inventory[food_id][unit_key] = amount

    forecast_by_food = {}
    reservation_list = []

    for mp in meal_plans:
        if not mp.recipe:
            continue

        recipe_name = mp.recipe.name
        meal_plan_title = mp.title or ''
        meal_type_name = mp.meal_type.name if mp.meal_type else ''
        from_date_str = mp.from_date.strftime('%Y-%m-%d')

        mp_ingredients = _get_meal_plan_ingredients(mp, uch)

        for ing in mp_ingredients:
            food_id = ing['food_id']
            amount = ing['amount']
            unit_key = ing['base_unit_key']
            unit_name = ing['unit_name']
            food_name = ing['food_name']

            key = (food_id, unit_key)

            if key not in forecast_by_food:
                ref_unit_name = unit_name
                ref_base_unit_name = None
                if unit_key and (food_id, unit_key) in food_to_unit:
                    inv_unit = food_to_unit[(food_id, unit_key)]
                    ref_unit_name = inv_unit.name
                    ref_base_unit_name = getattr(inv_unit, 'base_unit', None)

                forecast_by_food[key] = ForecastFoodEntry(
                    food_id=food_id,
                    food_name=food_name,
                    unit_name=ref_unit_name,
                    base_unit_name=ref_base_unit_name,
                )

            entry = forecast_by_food[key]

            inv_amount = inventory.get(food_id, {}).get(unit_key, Decimal('0'))
            if entry.total_inventory < inv_amount:
                entry.total_inventory = inv_amount

            entry.total_required += amount

            remaining = remaining_inventory.get(food_id, {}).get(unit_key, Decimal('0'))
            covered_by_stock = min(remaining, amount)
            covered_by_purchase = max(Decimal('0'), amount - covered_by_stock)

            if food_id in remaining_inventory and unit_key in remaining_inventory[food_id]:
                remaining_inventory[food_id][unit_key] -= covered_by_stock

            entry.status_reserved += covered_by_stock
            entry.status_to_buy += covered_by_purchase

            entry.usages.append(ForecastFoodUsage(
                meal_plan_id=mp.id,
                meal_plan_title=meal_plan_title,
                recipe_id=mp.recipe_id,
                recipe_name=recipe_name,
                meal_type_name=meal_type_name,
                from_date=from_date_str,
                required_amount=amount,
                unit_name=unit_name,
                covered_by_stock=covered_by_stock,
                covered_by_purchase=covered_by_purchase,
            ))

            if covered_by_stock > 0 and commit_reservation:
                reservation_list.append({
                    'food_id': food_id,
                    'base_unit_key': unit_key,
                    'amount': covered_by_stock,
                })

    for key, entry in forecast_by_food.items():
        food_id, unit_key = key
        remaining = remaining_inventory.get(food_id, {}).get(unit_key, Decimal('0'))
        entry.status_available = max(Decimal('0'), remaining)

    for food_id, units in inventory.items():
        for unit_key, inv_amount in units.items():
            key = (food_id, unit_key)
            if key not in forecast_by_food:
                try:
                    food = Food.objects.filter(id=food_id, space=space).first()
                    food_name = food.name if food else str(food_id)
                except Exception:
                    food_name = str(food_id)

                ref_unit_name = None
                ref_base_unit_name = None
                if (food_id, unit_key) in food_to_unit:
                    inv_unit = food_to_unit[(food_id, unit_key)]
                    ref_unit_name = inv_unit.name
                    ref_base_unit_name = getattr(inv_unit, 'base_unit', None)

                forecast_by_food[key] = ForecastFoodEntry(
                    food_id=food_id,
                    food_name=food_name,
                    unit_name=ref_unit_name,
                    base_unit_name=ref_base_unit_name,
                    total_inventory=inv_amount,
                    total_required=Decimal('0'),
                    status_available=inv_amount,
                    status_reserved=Decimal('0'),
                    status_to_buy=Decimal('0'),
                    usages=[],
                )

    if commit_reservation and reservation_list:
        _reserve_inventory_with_optimistic_lock(
            space=space,
            household=household,
            reservations=reservation_list,
            user=user,
            write_logs=True,
        )

    return list(forecast_by_food.values())


def serialize_forecast_entry(entry: ForecastFoodEntry):
    return {
        'food_id': entry.food_id,
        'food_name': entry.food_name,
        'unit_name': entry.unit_name,
        'base_unit_name': entry.base_unit_name,
        'total_inventory': float(entry.total_inventory) if entry.total_inventory else 0,
        'total_required': float(entry.total_required) if entry.total_required else 0,
        'status_available': float(entry.status_available) if entry.status_available else 0,
        'status_reserved': float(entry.status_reserved) if entry.status_reserved else 0,
        'status_to_buy': float(entry.status_to_buy) if entry.status_to_buy else 0,
        'usages': [
            {
                'meal_plan_id': u.meal_plan_id,
                'meal_plan_title': u.meal_plan_title,
                'recipe_id': u.recipe_id,
                'recipe_name': u.recipe_name,
                'meal_type_name': u.meal_type_name,
                'from_date': u.from_date,
                'required_amount': float(u.required_amount) if u.required_amount else 0,
                'unit_name': u.unit_name,
                'covered_by_stock': float(u.covered_by_stock) if u.covered_by_stock else 0,
                'covered_by_purchase': float(u.covered_by_purchase) if u.covered_by_purchase else 0,
            }
            for u in entry.usages
        ],
    }
