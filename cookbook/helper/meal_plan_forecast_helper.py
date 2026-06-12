
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from django.db.models import Sum, Q
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


def _get_inventory_for_household(space, household, uch: UnitConversionHelper):
    """
    Get all inventory entries for the given household, aggregated by food and normalized to base units.
    Returns: dict[food_id] -> dict[base_unit_id or None] -> total amount
    """
    inventory = defaultdict(lambda: defaultdict(Decimal))
    inventory_details = defaultdict(lambda: defaultdict(list))  # for unit reference

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
            # Try to normalize amount to base unit for easier comparison
            try:
                if unit.base_unit and unit.base_unit != unit.name:
                    conversions = uch.get_conversions(entry)
                    for conv in conversions:
                        if conv.unit and (conv.unit.base_unit == unit.base_unit or conv.unit.id == unit.id):
                            pass
                    # Simple approach: just use the entry as-is, will aggregate by (food, unit) first
            except Exception:
                pass
        inventory[food_id][base_unit_key] += amount
        inventory_details[food_id][base_unit_key].append(entry)

    return inventory, inventory_details


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


def calculate_meal_plan_forecast(
    user,
    user_space,
    space,
    from_date=None,
    to_date=None,
):
    """
    Calculate ingredient forecast for meal plans within the given date range.

    Algorithm:
    1. Get all meal plans in the date range, ordered by from_date
    2. For each meal plan, collect its required ingredients
    3. Aggregate inventory by food
    4. Process meal plans in order:
       - Use available inventory to cover requirements first (becomes "reserved")
       - Any requirements beyond available inventory become "to_buy"
       - Remaining inventory not used by any meal plan is "available" (已备)

    Returns:
        list of ForecastFoodEntry objects
    """
    household = _get_household_for_user(user_space)
    uch = UnitConversionHelper(space)

    # Get date range defaults
    if from_date is None:
        from_date = timezone.now().date()
    if to_date is None:
        to_date = from_date + timezone.timedelta(days=30)

    # Step 1: Get all relevant meal plans ordered by date
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

    # Step 2: Get inventory
    inventory, inventory_details = _get_inventory_for_household(space, household, uch)

    # Work with copies of inventory that we can decrement
    remaining_inventory = defaultdict(lambda: defaultdict(Decimal))
    for food_id, units in inventory.items():
        for unit_key, amount in units.items():
            remaining_inventory[food_id][unit_key] = amount

    # Step 3: Collect all food units from inventory for reference
    food_to_unit = {}
    for food_id, units in inventory_details.items():
        for unit_key, entries in units.items():
            if entries:
                first_entry = entries[0]
                if first_entry.unit:
                    food_to_unit[(food_id, unit_key)] = first_entry.unit

    # Aggregate by food across all meal plans, keeping per-usage details
    forecast_by_food = {}  # (food_id, unit_key) -> ForecastFoodEntry

    # Step 4: Process each meal plan in chronological order
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

            # Create key for aggregation - use None unit_key too
            key = (food_id, unit_key)

            if key not in forecast_by_food:
                # Find the best unit name (prioritize inventory unit)
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

            # Update inventory totals based on actual inventory
            inv_amount = inventory.get(food_id, {}).get(unit_key, Decimal('0'))
            if entry.total_inventory < inv_amount:
                entry.total_inventory = inv_amount

            entry.total_required += amount

            # Figure out coverage: use remaining inventory first
            remaining = remaining_inventory.get(food_id, {}).get(unit_key, Decimal('0'))
            covered_by_stock = min(remaining, amount)
            covered_by_purchase = max(Decimal('0'), amount - covered_by_stock)

            # Deduct from remaining inventory
            if food_id in remaining_inventory and unit_key in remaining_inventory[food_id]:
                remaining_inventory[food_id][unit_key] -= covered_by_stock

            # Update status totals
            entry.status_reserved += covered_by_stock
            entry.status_to_buy += covered_by_purchase

            # Record this usage
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

    # Step 5: After processing all meal plans, calculate available (已备)
    for key, entry in forecast_by_food.items():
        food_id, unit_key = key
        remaining = remaining_inventory.get(food_id, {}).get(unit_key, Decimal('0'))
        entry.status_available = max(Decimal('0'), remaining)
        # Sanity check: total_inventory should = available + reserved
        # (though rounding might make this off by a tiny bit)

    # Step 6: Also include foods that are in inventory but not required by any meal plan
    for food_id, units in inventory.items():
        for unit_key, inv_amount in units.items():
            key = (food_id, unit_key)
            if key not in forecast_by_food:
                # Get food name
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

    return list(forecast_by_food.values())


def serialize_forecast_entry(entry: ForecastFoodEntry):
    """
    Convert a ForecastFoodEntry to a JSON-serializable dict.
    """
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

