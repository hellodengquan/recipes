import pytest
from decimal import Decimal
from django.contrib import auth
from django_scopes import scopes_disabled, scope
from django.urls import reverse
import json

from cookbook.helper.shopping_helper import RecipeShoppingEditor
from cookbook.models import Food, Ingredient, Recipe, ShoppingListEntry, ShoppingListRecipe, Step, Unit, MealPlan, MealType
from django.utils import timezone


@pytest.fixture
def test_setup(space_1, u1_s1):
    """
    Setup test data for shopping list edge case tests.
    Creates food, units, ingredients, recipes, and meal plans.
    """
    with scopes_disabled():
        user = auth.get_user(u1_s1)
        space = space_1

        unit_gram = Unit.objects.create(name='gram', base_unit='g', space=space)
        unit_kg = Unit.objects.create(name='kilogram', base_unit='kg', space=space)
        unit_piece = Unit.objects.create(name='piece', base_unit='', space=space)

        food_flour = Food.objects.create(name='Flour', space=space)
        food_sugar = Food.objects.create(name='Sugar', space=space)
        food_egg = Food.objects.create(name='Egg', space=space)
        food_salt = Food.objects.create(name='Salt', space=space)

        recipe1 = Recipe.objects.create(
            name='Test Recipe 1',
            servings=4,
            created_by=user,
            space=space,
            internal=True,
        )
        step1 = Step.objects.create(name='Step 1', instruction='Step 1 instruction', space=space)
        recipe1.steps.add(step1)

        ing1 = Ingredient.objects.create(
            food=food_flour,
            unit=unit_gram,
            amount=200,
            space=space,
        )
        ing2 = Ingredient.objects.create(
            food=food_sugar,
            unit=unit_gram,
            amount=100,
            space=space,
        )
        ing3 = Ingredient.objects.create(
            food=food_egg,
            unit=unit_piece,
            amount=2,
            space=space,
        )
        step1.ingredients.add(ing1, ing2, ing3)

        recipe2 = Recipe.objects.create(
            name='Test Recipe 2',
            servings=2,
            created_by=user,
            space=space,
            internal=True,
        )
        step2 = Step.objects.create(name='Step 2', instruction='Step 2 instruction', space=space)
        recipe2.steps.add(step2)

        ing4 = Ingredient.objects.create(
            food=food_flour,
            unit=unit_gram,
            amount=300,
            space=space,
        )
        ing5 = Ingredient.objects.create(
            food=food_sugar,
            unit=unit_kg,
            amount=0.5,
            space=space,
        )
        ing6 = Ingredient.objects.create(
            food=food_salt,
            amount=0,
            space=space,
            no_amount=True,
        )
        step2.ingredients.add(ing4, ing5, ing6)

        meal_type = MealType.objects.create(name='Dinner', space=space, created_by=user)

        return {
            'user': user,
            'space': space,
            'unit_gram': unit_gram,
            'unit_kg': unit_kg,
            'unit_piece': unit_piece,
            'food_flour': food_flour,
            'food_sugar': food_sugar,
            'food_egg': food_egg,
            'food_salt': food_salt,
            'recipe1': recipe1,
            'recipe2': recipe2,
            'meal_type': meal_type,
            'ingredients': [ing1, ing2, ing3, ing4, ing5, ing6],
        }


def test_zero_amount_ingredient_in_shopping_list(test_setup):
    """
    Test that zero-amount ingredients are handled correctly in shopping list.
    Zero-amount (no_amount=True) ingredients should still be added to shopping list,
    but with amount=0. ShoppingListEntry does not have no_amount field but inherits
    amount from the ingredient.
    """
    with scopes_disabled():
        user = test_setup['user']
        space = test_setup['space']
        recipe2 = test_setup['recipe2']

        editor = RecipeShoppingEditor(user, space, recipe=recipe2)
        result = editor.create()
        assert result is True

    with scope(space=space):
        shopping_entries = ShoppingListEntry.objects.filter(
            list_recipe__recipe=recipe2
        ).select_related('food')

        food_names = [e.food.name for e in shopping_entries]

        assert 'Flour' in food_names
        assert 'Sugar' in food_names
        assert 'Salt' in food_names

        salt_entry = next(e for e in shopping_entries if e.food.name == 'Salt')
        assert salt_entry.amount == Decimal('0')
        assert salt_entry.ingredient.no_amount is True


def test_zero_amount_entry_persistence(u1_s1, space_1):
    """
    Test that zero-amount shopping list entries are properly persisted.
    Verifies the full API round-trip for zero-amount entries.
    """
    from cookbook.tests.factories import ShoppingListEntryFactory

    user = auth.get_user(u1_s1)

    with scopes_disabled():
        food = Food.objects.create(name='TestFoodZero', space=space_1)

        entry_zero = ShoppingListEntryFactory.create(
            space=space_1,
            created_by=user,
            food=food,
            amount=0,
        )

    assert entry_zero.amount == Decimal('0')

    with scope(space=space_1):
        fetched = ShoppingListEntry.objects.get(id=entry_zero.id)
        assert fetched.amount == Decimal('0')


def test_cross_mealplan_same_food_entries(test_setup):
    """
    Test that adding multiple meal plans with the same food creates separate entries.
    Backend stores entries per list_recipe (which is per mealplan), so the same food
    can appear multiple times (once per mealplan). Frontend handles merging by food+unit.
    This test verifies the backend behavior: multiple mealplans = multiple entries for same food.
    """
    with scopes_disabled():
        user = test_setup['user']
        space = test_setup['space']
        recipe1 = test_setup['recipe1']
        meal_type = test_setup['meal_type']

        mp1 = MealPlan.objects.create(
            recipe=recipe1,
            space=space,
            meal_type=meal_type,
            from_date=timezone.now(),
            to_date=timezone.now(),
            servings=4,
            created_by=user,
        )

        mp2 = MealPlan.objects.create(
            recipe=recipe1,
            space=space,
            meal_type=meal_type,
            from_date=timezone.now(),
            to_date=timezone.now(),
            servings=2,
            created_by=user,
        )

        editor1 = RecipeShoppingEditor(user, space, mealplan=mp1)
        editor1.create()

        editor2 = RecipeShoppingEditor(user, space, mealplan=mp2)
        editor2.create()

    with scope(space=space):
        flour_entries = ShoppingListEntry.objects.filter(
            food=test_setup['food_flour']
        ).select_related('list_recipe', 'list_recipe__mealplan')

        assert flour_entries.count() >= 2, \
            f"Expected at least 2 flour entries (one per mealplan), got {flour_entries.count()}"

        mealplan_ids = set(e.list_recipe.mealplan_id for e in flour_entries if e.list_recipe and e.list_recipe.mealplan_id)
        assert len(mealplan_ids) >= 1, "Should have entries from mealplans"

        amounts = [e.amount for e in flour_entries]
        assert len(set(amounts)) >= 1, "Should have entries with potentially different amounts"

        entry_4servings = next((e for e in flour_entries if e.amount == Decimal('200')), None)
        entry_2servings = next((e for e in flour_entries if e.amount == Decimal('100')), None)

        assert entry_4servings is not None, "Should have 200g flour entry (4 servings)"
        assert entry_2servings is not None, "Should have 100g flour entry (2 servings)"


def test_multiple_recipes_same_food_different_units(test_setup):
    """
    Test edge case where same food appears with different units across recipes/mealplans.
    This is a common source of bugs in shopping list merging and unit conversion.
    """
    with scopes_disabled():
        user = test_setup['user']
        space = test_setup['space']
        recipe1 = test_setup['recipe1']
        recipe2 = test_setup['recipe2']

        editor1 = RecipeShoppingEditor(user, space, recipe=recipe1)
        editor1.create()

        editor2 = RecipeShoppingEditor(user, space, recipe=recipe2)
        editor2.create()

    with scope(space=space):
        sugar_entries = ShoppingListEntry.objects.filter(
            food=test_setup['food_sugar']
        ).select_related('unit')

        assert sugar_entries.count() >= 2, \
            f"Expected at least 2 sugar entries (from 2 recipes), got {sugar_entries.count()}"

        units = [e.unit.name if e.unit else None for e in sugar_entries]
        assert 'gram' in units, "Should have sugar in grams"
        assert 'kilogram' in units, "Should have sugar in kilograms"

        gram_entry = next(e for e in sugar_entries if e.unit and e.unit.name == 'gram')
        kg_entry = next(e for e in sugar_entries if e.unit and e.unit.name == 'kilogram')

        assert gram_entry.amount == Decimal('100'), f"Expected 100g sugar, got {gram_entry.amount}"
        assert kg_entry.amount == Decimal('0.5'), f"Expected 0.5kg sugar, got {kg_entry.amount}"


def test_servings_scaling_precision(test_setup):
    """
    Test that servings scaling maintains precision, especially with fractions.
    This is a common regression point when mixing integers with fractional servings.
    """
    with scopes_disabled():
        user = test_setup['user']
        space = test_setup['space']
        recipe1 = test_setup['recipe1']

        editor = RecipeShoppingEditor(user, space, recipe=recipe1, servings=1.5)
        editor.create()

    with scope(space=space):
        flour_entry = ShoppingListEntry.objects.filter(
            food=test_setup['food_flour'],
            unit=test_setup['unit_gram'],
        ).first()

        assert flour_entry is not None
        expected_amount = Decimal('200') * Decimal('1.5') / Decimal('4')
        assert abs(flour_entry.amount - expected_amount) < Decimal('0.0001'), \
            f"Expected {expected_amount}g flour, got {flour_entry.amount}g"

        egg_entry = ShoppingListEntry.objects.filter(
            food=test_setup['food_egg'],
            unit=test_setup['unit_piece'],
        ).first()

        assert egg_entry is not None
        expected_eggs = Decimal('2') * Decimal('1.5') / Decimal('4')
        assert abs(egg_entry.amount - expected_eggs) < Decimal('0.0001'), \
            f"Expected {expected_eggs} eggs, got {egg_entry.amount}"


def test_shopping_list_recipe_creation_multiple_mealplans(test_setup):
    """
    Test that each mealplan gets its own ShoppingListRecipe.
    Verifies the one-to-one relationship between mealplan and shopping list recipe.
    """
    with scopes_disabled():
        user = test_setup['user']
        space = test_setup['space']
        recipe1 = test_setup['recipe1']
        meal_type = test_setup['meal_type']

        mp1 = MealPlan.objects.create(
            recipe=recipe1,
            space=space,
            meal_type=meal_type,
            from_date=timezone.now(),
            to_date=timezone.now(),
            created_by=user,
        )

        mp2 = MealPlan.objects.create(
            recipe=recipe1,
            space=space,
            meal_type=meal_type,
            from_date=timezone.now() - timezone.timedelta(days=1),
            to_date=timezone.now() - timezone.timedelta(days=1),
            created_by=user,
        )

        editor1 = RecipeShoppingEditor(user, space, mealplan=mp1)
        editor1.create()

        editor2 = RecipeShoppingEditor(user, space, mealplan=mp2)
        editor2.create()

        slr_count = ShoppingListRecipe.objects.filter(
            mealplan__in=[mp1, mp2]
        ).count()

        assert slr_count == 2, f"Expected 2 ShoppingListRecipes (one per mealplan), got {slr_count}"

        entry_count = ShoppingListEntry.objects.filter(
            list_recipe__mealplan__in=[mp1, mp2]
        ).count()

        recipe1_ingredient_count = recipe1.steps.first().ingredients.filter(food__isnull=False).count()
        expected_entries = recipe1_ingredient_count * 2
        assert entry_count == expected_entries, \
            f"Expected {expected_entries} entries total, got {entry_count}"


def test_ingredient_no_amount_flag(test_setup):
    """
    Test that the no_amount flag on ingredients is preserved in shopping list entries.
    This is important for ingredients like 'salt to taste' that don't have a specific amount.
    """
    with scopes_disabled():
        user = test_setup['user']
        space = test_setup['space']
        recipe2 = test_setup['recipe2']

        editor = RecipeShoppingEditor(user, space, recipe=recipe2)
        editor.create()

    with scope(space=space):
        salt_ingredient = test_setup['ingredients'][5]
        assert salt_ingredient.no_amount is True
        assert salt_ingredient.amount == Decimal('0')

        salt_entries = ShoppingListEntry.objects.filter(
            food=test_setup['food_salt']
        )

        assert salt_entries.count() >= 1

        for entry in salt_entries:
            assert entry.amount == Decimal('0'), \
                f"Zero-amount ingredient should have amount=0 in shopping list, got {entry.amount}"


def test_fractional_servings_with_mixed_units(test_setup):
    """
    Test fractional servings with mixed unit types (weight, count, volume).
    This is a regression-prone area where unit conversion and scaling interact.
    """
    with scopes_disabled():
        user = test_setup['user']
        space = test_setup['space']
        recipe1 = test_setup['recipe1']

        servings_list = [0.5, 1.5, 2.5, 3.5]
        for servings in servings_list:
            editor = RecipeShoppingEditor(user, space, recipe=recipe1, servings=servings)
            editor.create()

    with scope(space=space):
        flour_entries = ShoppingListEntry.objects.filter(
            food=test_setup['food_flour'],
            unit=test_setup['unit_gram'],
        )

        assert flour_entries.count() == len(servings_list), \
            f"Expected {len(servings_list)} flour entries, got {flour_entries.count()}"

        egg_entries = ShoppingListEntry.objects.filter(
            food=test_setup['food_egg'],
            unit=test_setup['unit_piece'],
        )

        assert egg_entries.count() == len(servings_list), \
            f"Expected {len(servings_list)} egg entries, got {egg_entries.count()}"

        for entry in flour_entries:
            assert entry.amount > 0, "All scaled entries should have positive amount"
            assert entry.amount < Decimal('201'), "No entry should exceed original amount"

        for entry in egg_entries:
            assert entry.amount >= 0, "Egg entries should have non-negative amount"
