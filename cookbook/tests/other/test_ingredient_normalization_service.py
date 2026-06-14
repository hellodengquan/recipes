import pytest
from decimal import Decimal
from django.contrib import auth
from django.test import RequestFactory
from django_scopes import scope, scopes_disabled

from cookbook.helper.ingredient_normalization_service import (
    IngredientNormalizationService,
    NormalizedIngredient,
)
from cookbook.models import Food, Ingredient, Unit


@pytest.fixture
def normalization_service(u1_s1, space_1):
    user = auth.get_user(u1_s1)
    request = RequestFactory()
    request.user = user
    request.space = space_1
    return IngredientNormalizationService(request=request, space=space_1, use_cache=False, ignore_automations=True)


@pytest.fixture
def test_food(space_1):
    with scopes_disabled():
        return Food.objects.create(name='Apple', space=space_1)


@pytest.fixture
def test_unit(space_1):
    with scopes_disabled():
        return Unit.objects.create(name='g', base_unit='g', space=space_1)


@pytest.fixture
def test_unit_kg(space_1):
    with scopes_disabled():
        return Unit.objects.create(name='kg', base_unit='kg', space=space_1)


class TestNormalizedIngredient:
    def test_default_values(self):
        ni = NormalizedIngredient()
        assert ni.amount == Decimal('0')
        assert ni.unit is None
        assert ni.food is None
        assert ni.note == ''
        assert ni.original_text == ''
        assert ni.unit_name == ''
        assert ni.food_name == ''


class TestAmountNormalization:
    def test_normalize_amount_integer(self, normalization_service):
        result = normalization_service.normalize_amount(5)
        assert result == Decimal('5')

    def test_normalize_amount_float(self, normalization_service):
        result = normalization_service.normalize_amount(3.5)
        assert result == Decimal('3.5')

    def test_normalize_amount_string(self, normalization_service):
        result = normalization_service.normalize_amount('2.5')
        assert result == Decimal('2.5')

    def test_normalize_amount_string_comma(self, normalization_service):
        result = normalization_service.normalize_amount('3,5')
        assert result == Decimal('3.5')

    def test_normalize_amount_decimal(self, normalization_service):
        result = normalization_service.normalize_amount(Decimal('4.25'))
        assert result == Decimal('4.25')

    def test_normalize_amount_fraction_string(self, normalization_service):
        result = normalization_service.normalize_amount('1/2')
        assert result == Decimal('0.5')

    def test_normalize_amount_zero(self, normalization_service):
        result = normalization_service.normalize_amount(0)
        assert result == Decimal('0')

    def test_normalize_amount_none(self, normalization_service):
        result = normalization_service.normalize_amount(None)
        assert result == Decimal('0')

    def test_normalize_amount_empty_string(self, normalization_service):
        result = normalization_service.normalize_amount('')
        assert result == Decimal('0')

    def test_normalize_amount_invalid_string(self, normalization_service):
        result = normalization_service.normalize_amount('abc')
        assert result == Decimal('0')

    def test_normalize_amount_precision(self, normalization_service):
        result = normalization_service.normalize_amount(3.1415926535)
        assert result == Decimal('3.1416')

    def test_normalize_amount_integral_removes_decimal(self, normalization_service):
        result = normalization_service.normalize_amount(5.0)
        assert result == Decimal('5')
        assert str(result) == '5'


class TestNameNormalization:
    def test_normalize_name_trim(self, normalization_service):
        result = normalization_service.normalize_name('  Apple  ')
        assert result == 'Apple'

    def test_normalize_name_multiple_spaces(self, normalization_service):
        result = normalization_service.normalize_name('Apple   Juice')
        assert result == 'Apple Juice'

    def test_normalize_name_trailing_punctuation(self, normalization_service):
        result = normalization_service.normalize_name('Apple,')
        assert result == 'Apple'

    def test_normalize_name_empty(self, normalization_service):
        result = normalization_service.normalize_name('')
        assert result == ''

    def test_normalize_name_none(self, normalization_service):
        result = normalization_service.normalize_name(None)
        assert result == ''

    def test_normalize_food_name(self, normalization_service):
        result = normalization_service.normalize_food_name('  apple juice  ')
        assert result == 'apple juice'

    def test_normalize_unit_name(self, normalization_service):
        result = normalization_service.normalize_unit_name('  g  ')
        assert result == 'g'


class TestParseFraction:
    def test_parse_fraction_simple(self, normalization_service):
        result = normalization_service.parse_fraction('1/2')
        assert result == 0.5

    def test_parse_fraction_third(self, normalization_service):
        result = normalization_service.parse_fraction('1/3')
        assert abs(result - 0.333333333) < 0.0001

    def test_parse_fraction_invalid(self, normalization_service):
        with pytest.raises(ValueError):
            normalization_service.parse_fraction('abc')

    def test_parse_fraction_zero_denominator(self, normalization_service):
        with pytest.raises(ValueError):
            normalization_service.parse_fraction('1/0')


class TestParseAmount:
    def test_parse_amount_simple_number(self, normalization_service):
        amount, unit, note = normalization_service.parse_amount('200')
        assert amount == Decimal('200')
        assert unit is None
        assert note == ''

    def test_parse_amount_with_unit(self, normalization_service):
        amount, unit, note = normalization_service.parse_amount('200g')
        assert amount == Decimal('200')
        assert unit == 'g'
        assert note == ''

    def test_parse_amount_decimal(self, normalization_service):
        amount, unit, note = normalization_service.parse_amount('3.5l')
        assert amount == Decimal('3.5')
        assert unit == 'l'

    def test_parse_amount_comma_decimal(self, normalization_service):
        amount, unit, note = normalization_service.parse_amount('3,5kg')
        assert amount == Decimal('3.5')
        assert unit == 'kg'

    def test_parse_amount_fraction(self, normalization_service):
        amount, unit, note = normalization_service.parse_amount('1/2')
        assert amount == Decimal('0.5')
        assert unit is None

    def test_parse_amount_empty(self, normalization_service):
        amount, unit, note = normalization_service.parse_amount('')
        assert amount == Decimal('0')
        assert unit is None
        assert note == ''


class TestParseIngredientString:
    def test_parse_simple_ingredient(self, normalization_service):
        amount, unit, food, note = normalization_service.parse_ingredient_string('200 g Apple')
        assert amount == Decimal('200')
        assert unit == 'g'
        assert food == 'Apple'
        assert note == ''

    def test_parse_ingredient_no_amount(self, normalization_service):
        amount, unit, food, note = normalization_service.parse_ingredient_string('Apple')
        assert amount == Decimal('0')
        assert unit is None
        assert food == 'Apple'
        assert note == ''

    def test_parse_ingredient_with_note(self, normalization_service):
        amount, unit, food, note = normalization_service.parse_ingredient_string('3 Apple, red')
        assert amount == Decimal('3')
        assert unit is None
        assert food == 'Apple'
        assert note == 'red'

    def test_parse_ingredient_with_parentheses_note(self, normalization_service):
        amount, unit, food, note = normalization_service.parse_ingredient_string('5 Apple (red delicious)')
        assert amount == Decimal('5')
        assert unit is None
        assert food == 'Apple'
        assert note == 'red delicious'

    def test_parse_ingredient_fraction(self, normalization_service):
        amount, unit, food, note = normalization_service.parse_ingredient_string('1/2 l Milk')
        assert amount == Decimal('0.5')
        assert unit == 'l'
        assert food == 'Milk'

    def test_parse_ingredient_unicode_fraction(self, normalization_service):
        amount, unit, food, note = normalization_service.parse_ingredient_string('½ l Milk')
        assert amount == Decimal('0.5')
        assert unit == 'l'
        assert food == 'Milk'

    def test_parse_ingredient_mixed_fraction(self, normalization_service):
        amount, unit, food, note = normalization_service.parse_ingredient_string('2 1/2 Apple')
        assert amount == Decimal('2.5')
        assert unit is None
        assert food == 'Apple'

    def test_parse_ingredient_connected_amount_unit(self, normalization_service):
        amount, unit, food, note = normalization_service.parse_ingredient_string('200g Flour')
        assert amount == Decimal('200')
        assert unit == 'g'
        assert food == 'Flour'

    def test_parse_ingredient_range(self, normalization_service):
        amount, unit, food, note = normalization_service.parse_ingredient_string('2-3 Apple')
        assert amount == Decimal('2')
        assert unit is None
        assert food == 'Apple'
        assert '2-3' in note

    def test_parse_ingredient_empty_string(self, normalization_service):
        with pytest.raises(ValueError):
            normalization_service.parse_ingredient_string('')

    def test_parse_ingredient_leading_symbols(self, normalization_service):
        amount, unit, food, note = normalization_service.parse_ingredient_string('... Apple')
        assert food == 'Apple'


class TestNormalizeMethod:
    def test_normalize_from_string(self, normalization_service, space_1):
        with scope(space=space_1):
            result = normalization_service.normalize('200 g Apple')
            assert isinstance(result, NormalizedIngredient)
            assert result.amount == Decimal('200')
            assert result.unit_name == 'g'
            assert result.food_name == 'Apple'
            assert result.original_text == '200 g Apple'

    def test_normalize_from_ingredient_object(self, normalization_service, space_1, test_food, test_unit):
        with scope(space=space_1):
            ingredient = Ingredient.objects.create(
                amount=Decimal('100'),
                unit=test_unit,
                food=test_food,
                note='test note',
                original_text='original',
                space=space_1,
            )
            result = normalization_service.normalize(ingredient)
            assert isinstance(result, NormalizedIngredient)
            assert result.amount == Decimal('100')
            assert result.unit == test_unit
            assert result.food == test_food
            assert result.note == 'test note'

    def test_normalize_from_dict(self, normalization_service, space_1):
        with scope(space=space_1):
            data = {
                'amount': 150,
                'unit': 'g',
                'food': 'Orange',
                'note': 'fresh',
                'original_text': '150 g Orange',
            }
            result = normalization_service.normalize(data)
            assert isinstance(result, NormalizedIngredient)
            assert result.amount == Decimal('150')
            assert result.unit_name == 'g'
            assert result.food_name == 'Orange'
            assert result.note == 'fresh'

    def test_normalize_unsupported_type(self, normalization_service):
        with pytest.raises(TypeError):
            normalization_service.normalize(123)


class TestScaling:
    def test_scale_by_factor(self, normalization_service, space_1):
        with scope(space=space_1):
            result = normalization_service.scale('100 g Sugar', 2)
            assert result.amount == Decimal('200')
            assert result.unit_name == 'g'
            assert result.food_name == 'Sugar'

    def test_scale_by_half(self, normalization_service, space_1):
        with scope(space=space_1):
            result = normalization_service.scale('100 g Sugar', 0.5)
            assert result.amount == Decimal('50')

    def test_scale_by_servings(self, normalization_service, space_1):
        with scope(space=space_1):
            result = normalization_service.scale_by_servings('200 g Rice', 4, 6)
            assert result.amount == Decimal('300')

    def test_scale_by_servings_zero_original(self, normalization_service, space_1):
        with pytest.raises(ValueError, match='Original servings cannot be zero'):
            normalization_service.scale_by_servings('100 g Sugar', 0, 4)


class TestUnitConversion:
    def test_convert_unit_base_units(self, normalization_service, space_1):
        with scopes_disabled():
            Unit.objects.create(name='gram', base_unit='g', space=space_1)
            Unit.objects.create(name='kilogram', base_unit='kg', space=space_1)

        with scope(space=space_1):
            result = normalization_service.convert_unit('1000 gram Flour', 'kilogram')
            assert result.amount == Decimal('1')
            assert result.unit_name == 'kilogram'

    def test_convert_unit_no_unit(self, normalization_service, space_1):
        with scope(space=space_1):
            with pytest.raises(ValueError, match='Ingredient has no unit'):
                normalization_service.convert_unit('Apple', 'g')

    def test_get_all_conversions(self, normalization_service, space_1):
        with scopes_disabled():
            u1 = Unit.objects.create(name='g', base_unit='g', space=space_1)
            Unit.objects.create(name='kg', base_unit='kg', space=space_1)

        with scope(space=space_1):
            conversions = normalization_service.get_all_conversions('100 g Sugar')
            assert len(conversions) >= 1


class TestMergeIngredients:
    def test_merge_same_food_unit(self, normalization_service, space_1):
        with scope(space=space_1):
            ingredients = [
                '100 g Apple',
                '200 g Apple',
            ]
            merged = normalization_service.merge_ingredients(ingredients)
            assert len(merged) == 1
            assert merged[0].amount == Decimal('300')
            assert merged[0].food_name == 'Apple'
            assert merged[0].unit_name == 'g'

    def test_merge_same_food_different_unit(self, normalization_service, space_1):
        with scope(space=space_1):
            ingredients = [
                '100 g Apple',
                '2 Apple',
            ]
            merged = normalization_service.merge_ingredients(ingredients)
            assert len(merged) == 2

    def test_merge_different_foods(self, normalization_service, space_1):
        with scope(space=space_1):
            ingredients = [
                '100 g Apple',
                '200 g Orange',
            ]
            merged = normalization_service.merge_ingredients(ingredients)
            assert len(merged) == 2

    def test_merge_with_notes(self, normalization_service, space_1):
        with scope(space=space_1):
            ingredients = [
                '100 g Apple, red',
                '200 g Apple, green',
            ]
            merged = normalization_service.merge_ingredients(ingredients)
            assert len(merged) == 1
            assert 'red' in merged[0].note
            assert 'green' in merged[0].note


class TestGetOrCreate:
    def test_get_or_create_food_new(self, normalization_service, space_1):
        with scope(space=space_1):
            food = normalization_service.get_or_create_food('Banana')
            assert food is not None
            assert food.name == 'Banana'

    def test_get_or_create_food_existing(self, normalization_service, space_1, test_food):
        with scope(space=space_1):
            food = normalization_service.get_or_create_food('Apple')
            assert food.id == test_food.id

    def test_get_or_create_food_empty(self, normalization_service):
        result = normalization_service.get_or_create_food('')
        assert result is None

    def test_get_or_create_unit_new(self, normalization_service, space_1):
        with scope(space=space_1):
            unit = normalization_service.get_or_create_unit('liter')
            assert unit is not None
            assert unit.name == 'liter'

    def test_get_or_create_unit_existing(self, normalization_service, space_1, test_unit):
        with scope(space=space_1):
            unit = normalization_service.get_or_create_unit('g')
            assert unit.id == test_unit.id

    def test_get_or_create_unit_empty(self, normalization_service):
        result = normalization_service.get_or_create_unit('')
        assert result is None


class TestToIngredient:
    def test_to_ingredient(self, normalization_service, space_1):
        with scope(space=space_1):
            normalized = normalization_service.normalize('150 g Carrot')
            ingredient = normalization_service.to_ingredient(normalized)
            assert isinstance(ingredient, Ingredient)
            assert ingredient.amount == Decimal('150')
            assert ingredient.food.name == 'Carrot'
            assert ingredient.unit.name == 'g'

    def test_normalize_to_ingredient(self, normalization_service, space_1):
        with scope(space=space_1):
            ingredient = normalization_service.normalize_to_ingredient('200 g Bread')
            assert isinstance(ingredient, Ingredient)
            assert ingredient.amount == Decimal('200')
            assert ingredient.food.name == 'Bread'
            assert ingredient.unit.name == 'g'

    def test_to_ingredient_no_space(self, normalization_service):
        normalized = NormalizedIngredient(amount=Decimal('100'))
        service = IngredientNormalizationService(ignore_automations=True)
        with pytest.raises(ValueError, match='Space is required'):
            service.to_ingredient(normalized)


class TestGrayscaleAndFallback:
    def test_grayscale_100_uses_new_service(self, normalization_service, space_1):
        normalization_service.grayscale_percent = 100
        with scope(space=space_1):
            result = normalization_service.normalize('200 g Apple')
            assert result.amount == Decimal('200')
            assert result.food_name == 'Apple'

    def test_grayscale_0_uses_fallback(self, normalization_service, space_1):
        normalization_service.grayscale_percent = 0
        normalization_service.fallback_enabled = True
        with scope(space=space_1):
            result = normalization_service.normalize('200 g Apple')
            assert isinstance(result, NormalizedIngredient)

    def test_fallback_disabled_uses_new_service(self, normalization_service, space_1):
        normalization_service.fallback_enabled = False
        normalization_service.grayscale_percent = 0
        with scope(space=space_1):
            result = normalization_service.normalize('200 g Apple')
            assert result.amount == Decimal('200')
            assert result.food_name == 'Apple'

    def test_deterministic_grayscale(self, normalization_service, space_1):
        normalization_service.grayscale_percent = 50
        normalization_service.fallback_enabled = True
        with scope(space=space_1):
            results = set()
            for _ in range(5):
                use_new = normalization_service._use_new_service('200 g Apple')
                results.add(use_new)
            assert len(results) == 1

    def test_grayscale_50_distributes(self, normalization_service, space_1):
        normalization_service.grayscale_percent = 50
        normalization_service.fallback_enabled = True
        new_count = 0
        old_count = 0
        for i in range(100):
            if normalization_service._use_new_service(f'test ingredient {i}'):
                new_count += 1
            else:
                old_count += 1
        assert new_count > 10
        assert old_count > 10

    def test_rollback_on_error(self, normalization_service, space_1):
        normalization_service.rollback_on_error = True
        normalization_service.fallback_enabled = True
        with scope(space=space_1):
            result = normalization_service._fallback_normalize('200 g Apple')
            assert isinstance(result, NormalizedIngredient)

    def test_no_rollback_raises_error(self, normalization_service, space_1):
        normalization_service.rollback_on_error = False
        normalization_service.fallback_enabled = True
        with scope(space=space_1):
            with pytest.raises(Exception):
                normalization_service._normalize_from_string('')

    def test_fallback_returns_normalized_ingredient(self, normalization_service, space_1):
        normalization_service.grayscale_percent = 0
        normalization_service.fallback_enabled = True
        with scope(space=space_1):
            result = normalization_service.normalize('100 g Sugar')
            assert isinstance(result, NormalizedIngredient)
            assert result.original_text == '100 g Sugar'

    def test_settings_grayscale_config(self, u1_s1, space_1, settings):
        settings.INGREDIENT_NORMALIZATION_GRAYSCALE_PERCENT = 50
        settings.INGREDIENT_NORMALIZATION_FALLBACK_ENABLED = True
        settings.INGREDIENT_NORMALIZATION_FALLBACK_ROLLBACK_ON_ERROR = True
        settings.INGREDIENT_NORMALIZATION_LOG_COMPARISON = False
        user = auth.get_user(u1_s1)
        request = RequestFactory()
        request.user = user
        request.space = space_1
        service = IngredientNormalizationService(request=request, space=space_1, use_cache=False, ignore_automations=True)
        assert service.grayscale_percent == 50
        assert service.fallback_enabled is True
        assert service.rollback_on_error is True
        assert service.log_comparison is False
