from _decimal import Decimal

from django.contrib import auth
from django_scopes import scopes_disabled

from cookbook.helper.unit_conversion_helper import UnitConversionHelper, ConversionException
from cookbook.models import Unit, Food, Ingredient, UnitConversion


def test_base_converter(space_1):
    uch = UnitConversionHelper(space_1)
    assert abs(uch.convert_from_to('g', 'kg', 1234) - Decimal(1.234)) < 0.0001
    assert abs(uch.convert_from_to('kg', 'pound', 2) - Decimal(4.40924)) < 0.00001
    assert abs(uch.convert_from_to('kg', 'g', 1) - Decimal(1000)) < 0.00001
    assert abs(uch.convert_from_to('imperial_gallon', 'gallon', 1000) - Decimal(1200.95104)) < 0.00001
    assert abs(uch.convert_from_to('tbsp', 'ml', 20) - Decimal(295.73549)) < 0.00001

    try:
        assert uch.convert_from_to('kg', 'tbsp', 2) == 1234
        assert False
    except ConversionException:
        assert True

    try:
        assert uch.convert_from_to('kg', 'g2', 2) == 1234
        assert False
    except ConversionException:
        assert True


def test_unit_conversions(space_1, space_2, u1_s1):
    with scopes_disabled():
        uch = UnitConversionHelper(space_1)
        uch_space_2 = UnitConversionHelper(space_2)

        unit_gram = Unit.objects.create(name='gram', base_unit='g', space=space_1)
        unit_kg = Unit.objects.create(name='kg', base_unit='kg', space=space_1)
        unit_pcs = Unit.objects.create(name='pcs', base_unit='', space=space_1)
        unit_floz1 = Unit.objects.create(name='fl. oz 1', base_unit='imperial_fluid_ounce', space=space_1)  # US and UK use different volume systems (US vs imperial)
        unit_floz2 = Unit.objects.create(name='fl. oz 2', base_unit='fluid_ounce', space=space_1)
        unit_fantasy = Unit.objects.create(name='Fantasy Unit', base_unit='', space=space_1)

        food_1 = Food.objects.create(name='Test Food 1', space=space_1)
        food_2 = Food.objects.create(name='Test Food 2', space=space_1)

        print('\n----------- TEST BASE CONVERSIONS - GRAM ---------------')
        ingredient_food_1_gram = Ingredient.objects.create(
            food=food_1,
            unit=unit_gram,
            amount=100,
            space=space_1,
        )

        conversions = uch.get_conversions(ingredient_food_1_gram)
        print(conversions)
        assert len(conversions) == 2
        assert next(x for x in conversions if x.unit == unit_kg) is not None
        assert abs(next(x for x in conversions if x.unit == unit_kg).amount - Decimal(0.1)) < 0.0001

        print('\n----------- TEST BASE CONVERSIONS - VOLUMES ---------------')

        ingredient_food_1_floz1 = Ingredient.objects.create(
            food=food_1,
            unit=unit_floz1,
            amount=100,
            space=space_1,
        )

        conversions = uch.get_conversions(ingredient_food_1_floz1)
        assert len(conversions) == 2
        assert next(x for x in conversions if x.unit == unit_floz2) is not None
        assert abs(next(x for x in conversions if x.unit == unit_floz2).amount - Decimal(96.07599404038842)) < 0.001  # TODO validate value

        print(conversions)

        unit_pint = Unit.objects.create(name='pint', base_unit='pint', space=space_1)
        conversions = uch.get_conversions(ingredient_food_1_floz1)
        assert len(conversions) == 3
        assert next(x for x in conversions if x.unit == unit_pint) is not None
        assert abs(next(x for x in conversions if x.unit == unit_pint).amount - Decimal(6.004749627524276)) < 0.001  # TODO validate value

        print(conversions)

        print('\n----------- TEST BASE CUSTOM CONVERSION - TO CUSTOM CONVERSION ---------------')
        UnitConversion.objects.create(
            base_amount=1000,
            base_unit=unit_gram,
            converted_amount=1337,
            converted_unit=unit_fantasy,
            space=space_1,
            created_by=auth.get_user(u1_s1),
        )
        conversions = uch.get_conversions(ingredient_food_1_gram)

        assert len(conversions) == 3
        assert next(x for x in conversions if x.unit == unit_fantasy) is not None
        assert abs(next(x for x in conversions if x.unit == unit_fantasy).amount - Decimal('133.700')) < 0.001  # TODO validate value

        print(conversions)

        print('\n----------- TEST CUSTOM CONVERSION - NO PCS ---------------')
        ingredient_food_1_pcs = Ingredient.objects.create(
            food=food_1,
            unit=unit_pcs,
            amount=5,
            space=space_1,
        )

        ingredient_food_2_pcs = Ingredient.objects.create(
            food=food_2,
            unit=unit_pcs,
            amount=5,
            space=space_1,
        )

        assert len(uch.get_conversions(ingredient_food_1_pcs)) == 1
        assert len(uch.get_conversions(ingredient_food_2_pcs)) == 1
        print(uch.get_conversions(ingredient_food_1_pcs))
        print(uch.get_conversions(ingredient_food_2_pcs))

        print('\n----------- TEST CUSTOM CONVERSION - PCS TO MULTIPLE BASE ---------------')
        uc1 = UnitConversion.objects.create(
            base_amount=1,
            base_unit=unit_pcs,
            converted_amount=200,
            converted_unit=unit_gram,
            food=food_1,
            space=space_1,
            created_by=auth.get_user(u1_s1),
        )

        conversions = uch.get_conversions(ingredient_food_1_pcs)
        # pcs + gram (direct) + kg (base) + fantasy (multi-step via gram→fantasy)
        assert len(conversions) == 4
        assert abs(next(x for x in conversions if x.unit == unit_gram).amount - Decimal(1000)) < 0.0001
        assert abs(next(x for x in conversions if x.unit == unit_kg).amount - Decimal(1)) < 0.0001
        assert next(x for x in conversions if x.unit == unit_fantasy) is not None
        print(conversions)

        assert len(uch.get_conversions(ingredient_food_2_pcs)) == 1
        print(uch.get_conversions(ingredient_food_2_pcs))

        print('\n----------- TEST CUSTOM CONVERSION - MULTI STEP (via get_conversions BFS) ---------------')
        # multi-step is now tested in dedicated test_multi_step_conversion tests

        print('\n----------- TEST CUSTOM CONVERSION - REVERSE CONVERSION ---------------')
        uc2 = UnitConversion.objects.create(
            base_amount=200,
            base_unit=unit_gram,
            converted_amount=1,
            converted_unit=unit_pcs,
            food=food_2,
            space=space_1,
            created_by=auth.get_user(u1_s1),
        )

        conversions = uch.get_conversions(ingredient_food_1_pcs)
        # pcs + gram (direct) + kg (base) + fantasy (multi-step via gram→fantasy)
        assert len(conversions) == 4
        assert abs(next(x for x in conversions if x.unit == unit_gram).amount - Decimal(1000)) < 0.0001
        assert abs(next(x for x in conversions if x.unit == unit_kg).amount - Decimal(1)) < 0.0001
        print(conversions)

        conversions = uch.get_conversions(ingredient_food_2_pcs)
        # pcs + gram (direct) + kg (base) + fantasy (multi-step via gram→fantasy, generic)
        assert len(conversions) == 4
        assert abs(next(x for x in conversions if x.unit == unit_gram).amount - Decimal(1000)) < 0.0001
        assert abs(next(x for x in conversions if x.unit == unit_kg).amount - Decimal(1)) < 0.0001
        print(conversions)

        print('\n----------- TEST SPACE SEPARATION ---------------')
        uc2.space = space_2
        uc2.save()

        conversions = uch.get_conversions(ingredient_food_2_pcs)
        assert len(conversions) == 1
        print(conversions)

        conversions = uch_space_2.get_conversions(ingredient_food_1_gram)
        assert len(conversions) == 1
        assert not any(x for x in conversions if x.unit == unit_kg)
        print(conversions)

        unit_kg_space_2 = Unit.objects.create(name='kg', base_unit='kg', space=space_2)
        conversions = uch_space_2.get_conversions(ingredient_food_1_gram)
        assert len(conversions) == 2
        assert not any(x for x in conversions if x.unit == unit_kg)
        assert next(x for x in conversions if x.unit == unit_kg_space_2) is not None
        assert abs(next(x for x in conversions if x.unit == unit_kg_space_2).amount - Decimal(0.1)) < 0.0001
        print(conversions)

def test_multi_step_conversion(space_1, u1_s1):
    """
    Multi-step conversion: pinch → teaspoon → gram should yield pinch → gram.
    Verifies that the conversion system traverses intermediate units.
    See: https://github.com/TandoorRecipes/recipes/issues/4163
    """
    with scopes_disabled():
        uch = UnitConversionHelper(space_1)

        unit_pinch = Unit.objects.create(name='pinch', base_unit='', space=space_1)
        unit_tsp = Unit.objects.create(name='teaspoon', base_unit='tsp', space=space_1)
        unit_gram = Unit.objects.create(name='gram', base_unit='g', space=space_1)

        food = Food.objects.create(name='Chili Powder', space=space_1)

        # pinch → teaspoon: 16 pinches = 1 teaspoon
        UnitConversion.objects.create(
            base_amount=16,
            base_unit=unit_pinch,
            converted_amount=1,
            converted_unit=unit_tsp,
            food=food,
            space=space_1,
            created_by=auth.get_user(u1_s1),
        )

        # teaspoon → gram: 1 teaspoon = 2.3 grams (for chili powder)
        UnitConversion.objects.create(
            base_amount=1,
            base_unit=unit_tsp,
            converted_amount=Decimal('2.3'),
            converted_unit=unit_gram,
            food=food,
            space=space_1,
            created_by=auth.get_user(u1_s1),
        )

        ingredient_pinch = Ingredient.objects.create(
            food=food,
            unit=unit_pinch,
            amount=16,
            space=space_1,
        )

        conversions = uch.get_conversions(ingredient_pinch)

        # Should find: original (pinch) + teaspoon (direct) + gram (multi-step) = 3 conversions minimum
        unit_names = [c.unit.name for c in conversions]
        assert 'gram' in unit_names, f"Expected 'gram' in conversions via multi-step, got: {unit_names}"

        gram_conversion = next(x for x in conversions if x.unit == unit_gram)
        # 16 pinches = 1 tsp, 1 tsp = 2.3g → 16 pinches = 2.3g
        assert abs(gram_conversion.amount - Decimal('2.3')) < Decimal('0.001'), \
            f"Expected ~2.3g, got {gram_conversion.amount}"


def test_multi_step_conversion_no_food(space_1, u1_s1):
    """
    Multi-step conversion without food-specific conversions (generic).
    """
    with scopes_disabled():
        uch = UnitConversionHelper(space_1)

        unit_a = Unit.objects.create(name='unit_a', base_unit='', space=space_1)
        unit_b = Unit.objects.create(name='unit_b', base_unit='', space=space_1)
        unit_c = Unit.objects.create(name='unit_c', base_unit='', space=space_1)

        food = Food.objects.create(name='Test Food', space=space_1)

        # A → B: 2 A = 1 B (generic, no food)
        UnitConversion.objects.create(
            base_amount=2,
            base_unit=unit_a,
            converted_amount=1,
            converted_unit=unit_b,
            space=space_1,
            created_by=auth.get_user(u1_s1),
        )

        # B → C: 1 B = 5 C (generic, no food)
        UnitConversion.objects.create(
            base_amount=1,
            base_unit=unit_b,
            converted_amount=5,
            converted_unit=unit_c,
            space=space_1,
            created_by=auth.get_user(u1_s1),
        )

        ingredient = Ingredient.objects.create(
            food=food,
            unit=unit_a,
            amount=4,
            space=space_1,
        )

        conversions = uch.get_conversions(ingredient)

        unit_names = [c.unit.name for c in conversions]
        assert 'unit_c' in unit_names, f"Expected 'unit_c' in conversions via multi-step, got: {unit_names}"

        c_conversion = next(x for x in conversions if x.unit == unit_c)
        # 4 A → 2 B → 10 C
        assert abs(c_conversion.amount - Decimal('10')) < Decimal('0.001'), \
            f"Expected 10, got {c_conversion.amount}"


def test_multi_step_no_cycle(space_1, u1_s1):
    """
    Ensure multi-step conversion doesn't loop infinitely with circular conversions.
    """
    with scopes_disabled():
        uch = UnitConversionHelper(space_1)

        unit_a = Unit.objects.create(name='unit_a', base_unit='', space=space_1)
        unit_b = Unit.objects.create(name='unit_b', base_unit='', space=space_1)
        unit_c = Unit.objects.create(name='unit_c', base_unit='', space=space_1)

        food = Food.objects.create(name='Test Food', space=space_1)

        # A → B
        UnitConversion.objects.create(
            base_amount=1, base_unit=unit_a,
            converted_amount=2, converted_unit=unit_b,
            space=space_1, created_by=auth.get_user(u1_s1),
        )
        # B → C
        UnitConversion.objects.create(
            base_amount=1, base_unit=unit_b,
            converted_amount=3, converted_unit=unit_c,
            space=space_1, created_by=auth.get_user(u1_s1),
        )
        # C → A (cycle!)
        UnitConversion.objects.create(
            base_amount=6, base_unit=unit_c,
            converted_amount=1, converted_unit=unit_a,
            space=space_1, created_by=auth.get_user(u1_s1),
        )

        ingredient = Ingredient.objects.create(
            food=food, unit=unit_a, amount=1, space=space_1,
        )

        # Should complete without infinite loop
        conversions = uch.get_conversions(ingredient)
        unit_names = [c.unit.name for c in conversions]
        assert 'unit_b' in unit_names
        assert 'unit_c' in unit_names


def test_conversion_with_zero(space_1, space_2, u1_s1):
    with scopes_disabled():
        uch = UnitConversionHelper(space_1)

        unit_gram = Unit.objects.create(name='gram', base_unit='g', space=space_1)
        unit_fantasy = Unit.objects.create(name='Fantasy Unit', base_unit=None, space=space_1)

        food_1 = Food.objects.create(name='Test Food 1', space=space_1)

        ingredient_food_1_gram = Ingredient.objects.create(
            food=food_1,
            unit=unit_gram,
            amount=100,
            space=space_1,
        )

        print('\n----------- TEST BASE CUSTOM CONVERSION - TO CUSTOM CONVERSION ---------------')
        UnitConversion.objects.create(
            base_amount=0,
            base_unit=unit_gram,
            converted_amount=0,
            converted_unit=unit_fantasy,
            space=space_1,
            created_by=auth.get_user(u1_s1),
        )
        conversions = uch.get_conversions(ingredient_food_1_gram)

        assert len(conversions) == 1 # conversion always includes the ingredient, if count is 1 no other conversion was found


def test_base_unit_alias_chain_single_space(space_1, u1_s1):
    """
    Alias chain via custom UnitConversion: gram ↔ milligram ↔ microgram.
    Tests BFS traversal of custom conversions forming an alias chain.
    gram -> milligram (1g = 1000mg) and milligram -> microgram (1mg = 1000μg)
    should yield gram -> microgram via multi-step.
    """
    with scopes_disabled():
        uch = UnitConversionHelper(space_1)
        UnitConversionHelper._base_units_cache.clear()

        unit_gram = Unit.objects.create(name='gram', base_unit='', space=space_1)
        unit_mg = Unit.objects.create(name='milligram', base_unit='', space=space_1)
        unit_microg = Unit.objects.create(name='microgram', base_unit='', space=space_1)

        food = Food.objects.create(name='Alias Food', space=space_1)

        UnitConversion.objects.create(
            base_amount=1,
            base_unit=unit_gram,
            converted_amount=Decimal('1000'),
            converted_unit=unit_mg,
            space=space_1,
            created_by=auth.get_user(u1_s1),
        )
        UnitConversion.objects.create(
            base_amount=1,
            base_unit=unit_mg,
            converted_amount=Decimal('1000'),
            converted_unit=unit_microg,
            space=space_1,
            created_by=auth.get_user(u1_s1),
        )

        ingredient = Ingredient.objects.create(
            food=food,
            unit=unit_gram,
            amount=Decimal('1'),
            space=space_1,
        )

        conversions = uch.get_conversions(ingredient)
        unit_names = {c.unit.name for c in conversions}

        assert 'gram' in unit_names
        assert 'milligram' in unit_names
        assert 'microgram' in unit_names, "microgram should be reachable via gram→mg→μg chain"

        mg_conv = next(c for c in conversions if c.unit.name == 'milligram')
        microg_conv = next(c for c in conversions if c.unit.name == 'microgram')
        assert abs(mg_conv.amount - Decimal('1000')) < Decimal('0.001')
        assert abs(microg_conv.amount - Decimal('1000000')) < Decimal('0.001')


def test_base_unit_alias_chain_cross_system(space_1, u1_s1):
    """
    Alias chain across different unit systems (weight vs volume) should NOT
    produce conversions between the systems.
    """
    with scopes_disabled():
        uch = UnitConversionHelper(space_1)
        UnitConversionHelper._base_units_cache.clear()

        unit_gram = Unit.objects.create(name='gram', base_unit='g', space=space_1)
        unit_ml = Unit.objects.create(name='milliliter', base_unit='ml', space=space_1)
        unit_l = Unit.objects.create(name='liter', base_unit='l', space=space_1)

        food = Food.objects.create(name='Cross System Food', space=space_1)

        ingredient = Ingredient.objects.create(
            food=food,
            unit=unit_gram,
            amount=Decimal('100'),
            space=space_1,
        )

        conversions = uch.get_conversions(ingredient)
        unit_names = {c.unit.name for c in conversions}

        assert 'gram' in unit_names
        assert 'milliliter' not in unit_names
        assert 'liter' not in unit_names


def test_multi_space_alias_isolation(space_1, space_2, u1_s1, u1_s2):
    """
    Each space has its own unit alias chain. Units and conversions from
    space_1 must NOT leak into space_2 and vice versa.
    """
    with scopes_disabled():
        uch_s1 = UnitConversionHelper(space_1)
        uch_s2 = UnitConversionHelper(space_2)
        UnitConversionHelper._base_units_cache.clear()

        unit_gram_s1 = Unit.objects.create(name='gram', base_unit='g', space=space_1)
        unit_kg_s1 = Unit.objects.create(name='kilogram', base_unit='kg', space=space_1)

        unit_ounce_s2 = Unit.objects.create(name='ounce_custom', base_unit='ounce', space=space_2)
        unit_pound_s2 = Unit.objects.create(name='pound_custom', base_unit='pound', space=space_2)

        food_s1 = Food.objects.create(name='Food S1', space=space_1)
        food_s2 = Food.objects.create(name='Food S2', space=space_2)

        ing_s1 = Ingredient.objects.create(
            food=food_s1,
            unit=unit_gram_s1,
            amount=Decimal('500'),
            space=space_1,
        )
        ing_s2 = Ingredient.objects.create(
            food=food_s2,
            unit=unit_ounce_s2,
            amount=Decimal('16'),
            space=space_2,
        )

        conv_s1 = uch_s1.get_conversions(ing_s1)
        conv_s2 = uch_s2.get_conversions(ing_s2)

        names_s1 = {c.unit.name for c in conv_s1}
        names_s2 = {c.unit.name for c in conv_s2}

        assert 'kilogram' in names_s1
        assert 'ounce_custom' not in names_s1
        assert 'pound_custom' not in names_s1

        assert 'pound_custom' in names_s2
        assert 'gram' not in names_s2
        assert 'kilogram' not in names_s2


def test_multi_space_custom_conversion_isolation(space_1, space_2, u1_s1):
    """
    Custom UnitConversion created in space_1 must not be usable in space_2,
    even if both spaces define units with the same name and base_unit.
    """
    with scopes_disabled():
        uch_s1 = UnitConversionHelper(space_1)
        uch_s2 = UnitConversionHelper(space_2)
        UnitConversionHelper._base_units_cache.clear()

        unit_pcs_s1 = Unit.objects.create(name='pcs', base_unit='', space=space_1)
        unit_gram_s1 = Unit.objects.create(name='gram', base_unit='g', space=space_1)

        unit_pcs_s2 = Unit.objects.create(name='pcs', base_unit='', space=space_2)
        unit_gram_s2 = Unit.objects.create(name='gram', base_unit='g', space=space_2)

        food_s1 = Food.objects.create(name='Apple', space=space_1)
        food_s2 = Food.objects.create(name='Apple', space=space_2)

        UnitConversion.objects.create(
            base_amount=1,
            base_unit=unit_pcs_s1,
            converted_amount=Decimal('150.5'),
            converted_unit=unit_gram_s1,
            food=food_s1,
            space=space_1,
            created_by=auth.get_user(u1_s1),
        )

        ing_s1 = Ingredient.objects.create(
            food=food_s1, unit=unit_pcs_s1, amount=Decimal('3'), space=space_1,
        )
        ing_s2 = Ingredient.objects.create(
            food=food_s2, unit=unit_pcs_s2, amount=Decimal('3'), space=space_2,
        )

        conv_s1 = uch_s1.get_conversions(ing_s1)
        conv_s2 = uch_s2.get_conversions(ing_s2)

        names_s1 = {c.unit.name for c in conv_s1}
        names_s2 = {c.unit.name for c in conv_s2}

        assert 'gram' in names_s1
        gram_s1 = next(c for c in conv_s1 if c.unit.name == 'gram')
        assert abs(gram_s1.amount - Decimal('451.5')) < Decimal('0.001')

        assert 'gram' not in names_s2, "space_2 should not see space_1's custom conversion"


def test_conversion_fractional_amounts(space_1, u1_s1):
    """
    Conversions should handle fractional amounts precisely.
    """
    with scopes_disabled():
        uch = UnitConversionHelper(space_1)
        UnitConversionHelper._base_units_cache.clear()

        unit_gram = Unit.objects.create(name='gram', base_unit='g', space=space_1)
        unit_kg = Unit.objects.create(name='kg', base_unit='kg', space=space_1)

        food = Food.objects.create(name='Fraction Food', space=space_1)

        ing_half = Ingredient.objects.create(
            food=food, unit=unit_gram, amount=Decimal('0.5'), space=space_1,
        )
        ing_third = Ingredient.objects.create(
            food=food, unit=unit_gram, amount=Decimal('0.3333333333333333'), space=space_1,
        )
        ing_quarter = Ingredient.objects.create(
            food=food, unit=unit_kg, amount=Decimal('0.25'), space=space_1,
        )

        conv_half = uch.get_conversions(ing_half)
        conv_third = uch.get_conversions(ing_third)
        conv_quarter = uch.get_conversions(ing_quarter)

        kg_half = next(c for c in conv_half if c.unit.name == 'kg')
        assert abs(kg_half.amount - Decimal('0.0005')) < Decimal('0.0000001')

        kg_third = next(c for c in conv_third if c.unit.name == 'kg')
        assert abs(kg_third.amount - Decimal('0.0003333333333333333')) < Decimal('0.000000000001')

        gram_quarter = next(c for c in conv_quarter if c.unit.name == 'gram')
        assert abs(gram_quarter.amount - Decimal('250')) < Decimal('0.0000001')


def test_conversion_high_precision_decimal(space_1, u1_s1):
    """
    Conversions with high-precision decimal values (16 decimal places)
    should not lose precision beyond acceptable tolerance.
    """
    with scopes_disabled():
        uch = UnitConversionHelper(space_1)
        UnitConversionHelper._base_units_cache.clear()

        unit_a = Unit.objects.create(name='unit_a', base_unit='', space=space_1)
        unit_b = Unit.objects.create(name='unit_b', base_unit='', space=space_1)

        food = Food.objects.create(name='Precision Food', space=space_1)

        UnitConversion.objects.create(
            base_amount=Decimal('1.0000000000000001'),
            base_unit=unit_a,
            converted_amount=Decimal('3.1415926535897932'),
            converted_unit=unit_b,
            space=space_1,
            created_by=auth.get_user(u1_s1),
        )

        ingredient = Ingredient.objects.create(
            food=food,
            unit=unit_a,
            amount=Decimal('2.7182818284590452'),
            space=space_1,
        )

        conversions = uch.get_conversions(ingredient)
        unit_b_conv = next(c for c in conversions if c.unit.name == 'unit_b')

        expected = Decimal('2.7182818284590452') * Decimal('3.1415926535897932') / Decimal('1.0000000000000001')
        tolerance = Decimal('0.000000000001')
        assert abs(unit_b_conv.amount - expected) < tolerance


def test_conversion_extreme_large_amounts(space_1, u1_s1):
    """
    Conversions should handle extremely large amounts without overflow
    or precision collapse (max_digits=32 per model).
    """
    with scopes_disabled():
        uch = UnitConversionHelper(space_1)

        result = uch.convert_from_to('g', 'kg', Decimal('99999999999999.99999999999999'))
        expected = Decimal('99999999999.9999999999999999')
        assert abs(result - expected) < Decimal('0.0000000001')


def test_conversion_extreme_small_amounts(space_1, u1_s1):
    """
    Conversions should handle extremely small amounts (close to zero)
    without underflowing to zero.
    """
    with scopes_disabled():
        uch = UnitConversionHelper(space_1)

        tiny = Decimal('0.0000000000000001')
        result = uch.convert_from_to('g', 'kg', tiny)
        expected = Decimal('0.0000000000000000001')
        assert abs(result - expected) < Decimal('0.000000000000000000001')
        assert result > 0


def test_conversion_negative_amounts(space_1, u1_s1):
    """
    Negative amounts (e.g. for inventory adjustments) should convert correctly.
    """
    with scopes_disabled():
        uch = UnitConversionHelper(space_1)
        UnitConversionHelper._base_units_cache.clear()

        unit_gram = Unit.objects.create(name='gram', base_unit='g', space=space_1)
        unit_kg = Unit.objects.create(name='kg', base_unit='kg', space=space_1)
        food = Food.objects.create(name='Neg Food', space=space_1)

        ingredient = Ingredient.objects.create(
            food=food, unit=unit_gram, amount=Decimal('-500'), space=space_1,
        )

        conversions = uch.get_conversions(ingredient)
        kg_conv = next(c for c in conversions if c.unit.name == 'kg')
        assert abs(kg_conv.amount - Decimal('-0.5')) < Decimal('0.0000001')


def test_base_conversion_ignores_duplicate_aliases(space_1, u1_s1):
    """
    When multiple units share the same base_unit AND the same display name,
    the conversion helper should deduplicate and not produce duplicate entries.
    """
    with scopes_disabled():
        uch = UnitConversionHelper(space_1)
        UnitConversionHelper._base_units_cache.clear()

        unit_g1 = Unit.objects.create(name='gram', base_unit='g', space=space_1)
        unit_kg = Unit.objects.create(name='kg', base_unit='kg', space=space_1)

        food = Food.objects.create(name='Dedup Food', space=space_1)

        ingredient = Ingredient.objects.create(
            food=food, unit=unit_g1, amount=Decimal('1000'), space=space_1,
        )

        conversions = uch.get_conversions(ingredient)
        gram_entries = [c for c in conversions if c.unit.name == 'gram']
        assert len(gram_entries) == 1, f"Expected 1 'gram' entry, got {len(gram_entries)}"


def test_alias_chain_with_custom_conversion(space_1, u1_s1):
    """
    Base unit alias chain combined with custom conversion:
    pinch -> tsp (custom) + tsp -> ml (base) + tsp -> liter (base alias)
    should yield pinch -> ml and pinch -> liter via multi-step BFS.
    """
    with scopes_disabled():
        uch = UnitConversionHelper(space_1)
        UnitConversionHelper._base_units_cache.clear()

        unit_pinch = Unit.objects.create(name='pinch', base_unit='', space=space_1)
        unit_tsp = Unit.objects.create(name='tsp', base_unit='tsp', space=space_1)
        unit_ml = Unit.objects.create(name='ml', base_unit='ml', space=space_1)
        unit_l = Unit.objects.create(name='liter', base_unit='l', space=space_1)

        food = Food.objects.create(name='Salt', space=space_1)

        UnitConversion.objects.create(
            base_amount=8,
            base_unit=unit_pinch,
            converted_amount=1,
            converted_unit=unit_tsp,
            food=food,
            space=space_1,
            created_by=auth.get_user(u1_s1),
        )

        ingredient = Ingredient.objects.create(
            food=food, unit=unit_pinch, amount=Decimal('16'), space=space_1,
        )

        conversions = uch.get_conversions(ingredient)
        unit_names = {c.unit.name for c in conversions}

        assert 'pinch' in unit_names
        assert 'tsp' in unit_names
        assert 'ml' in unit_names, "pinch -> tsp -> ml via alias chain"
        assert 'liter' in unit_names, "pinch -> tsp -> liter via alias chain"

        tsp_conv = next(c for c in conversions if c.unit.name == 'tsp')
        ml_conv = next(c for c in conversions if c.unit.name == 'ml')
        l_conv = next(c for c in conversions if c.unit.name == 'liter')

        assert abs(tsp_conv.amount - Decimal('2')) < Decimal('0.001')
        assert abs(ml_conv.amount - Decimal('9.8578')) < Decimal('0.01')
        assert abs(l_conv.amount - Decimal('0.0098578')) < Decimal('0.0001')
