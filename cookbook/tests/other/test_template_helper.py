from dataclasses import dataclass
from decimal import Decimal

import pytest

from cookbook.helper.template_helper import _resolve_unit_name, _resolve_food_name, _plural_name_tag, IngredientObject


@dataclass
class MockUnit:
    name: str
    plural_name: str = None

    def __str__(self):
        return self.name


@dataclass
class MockFood:
    name: str
    plural_name: str = None

    def __str__(self):
        return self.name


@dataclass
class MockIngredient:
    amount: Decimal
    no_amount: bool = False
    unit: MockUnit = None
    food: MockFood = None
    note: str = ""


class TestResolveUnitName:
    """Tests for _resolve_unit_name helper."""

    def test_no_unit(self):
        ing = MockIngredient(amount=Decimal(1), unit=None)
        assert _resolve_unit_name(ing) == ""

    def test_unit_no_plural(self):
        ing = MockIngredient(amount=Decimal(0), unit=MockUnit("kg"))
        assert _resolve_unit_name(ing) == "kg"

    def test_unit_no_plural_amount_1(self):
        ing = MockIngredient(amount=Decimal(1), unit=MockUnit("kg"))
        assert _resolve_unit_name(ing) == "kg"

    def test_unit_no_plural_amount_2(self):
        ing = MockIngredient(amount=Decimal(2), unit=MockUnit("kg"))
        assert _resolve_unit_name(ing) == "kg"

    def test_unit_empty_plural(self):
        ing = MockIngredient(amount=Decimal(2), unit=MockUnit("kg", ""))
        assert _resolve_unit_name(ing) == "kg"

    def test_plural_unit_amount_0(self):
        ing = MockIngredient(amount=Decimal(0), unit=MockUnit("slice", "slices"))
        assert _resolve_unit_name(ing) == "slices"

    def test_plural_unit_amount_1(self):
        ing = MockIngredient(amount=Decimal(1), unit=MockUnit("slice", "slices"))
        assert _resolve_unit_name(ing) == "slice"

    def test_plural_unit_amount_1_point_0(self):
        ing = MockIngredient(amount=Decimal("1.0"), unit=MockUnit("slice", "slices"))
        assert _resolve_unit_name(ing) == "slice"

    def test_plural_unit_amount_1_point_5(self):
        ing = MockIngredient(amount=Decimal("1.5"), unit=MockUnit("slice", "slices"))
        assert _resolve_unit_name(ing) == "slices"

    def test_plural_unit_amount_half(self):
        ing = MockIngredient(amount=Decimal("0.5"), unit=MockUnit("slice", "slices"))
        assert _resolve_unit_name(ing) == "slices"

    def test_plural_unit_negative(self):
        ing = MockIngredient(amount=Decimal(-1), unit=MockUnit("slice", "slices"))
        assert _resolve_unit_name(ing) == "slices"

    def test_no_amount_singular(self):
        ing = MockIngredient(amount=Decimal(0), no_amount=True, unit=MockUnit("slice", "slices"))
        assert _resolve_unit_name(ing) == "slice"

    def test_no_amount_singular_amount_2(self):
        ing = MockIngredient(amount=Decimal(2), no_amount=True, unit=MockUnit("slice", "slices"))
        assert _resolve_unit_name(ing) == "slice"


class TestResolveFoodName:
    """Tests for _resolve_food_name helper."""

    def test_no_food(self):
        ing = MockIngredient(amount=Decimal(1), food=None)
        assert _resolve_food_name(ing) == ""

    def test_food_no_plural(self):
        ing = MockIngredient(amount=Decimal(2), food=MockFood("apple"))
        assert _resolve_food_name(ing) == "apple"

    def test_food_empty_plural(self):
        ing = MockIngredient(amount=Decimal(2), food=MockFood("apple", ""))
        assert _resolve_food_name(ing) == "apple"

    def test_plural_food_amount_0(self):
        ing = MockIngredient(amount=Decimal(0), food=MockFood("apple", "apples"))
        assert _resolve_food_name(ing) == "apples"

    def test_plural_food_amount_half(self):
        ing = MockIngredient(amount=Decimal("0.5"), food=MockFood("apple", "apples"))
        assert _resolve_food_name(ing) == "apples"

    def test_plural_food_amount_1(self):
        ing = MockIngredient(amount=Decimal(1), food=MockFood("apple", "apples"))
        assert _resolve_food_name(ing) == "apple"

    def test_plural_food_amount_1_point_0(self):
        ing = MockIngredient(amount=Decimal("1.0"), food=MockFood("apple", "apples"))
        assert _resolve_food_name(ing) == "apple"

    def test_plural_food_amount_2_point_5(self):
        ing = MockIngredient(amount=Decimal("2.5"), food=MockFood("apple", "apples"))
        assert _resolve_food_name(ing) == "apples"

    def test_plural_food_negative(self):
        ing = MockIngredient(amount=Decimal(-1), food=MockFood("apple", "apples"))
        assert _resolve_food_name(ing) == "apples"

    def test_no_amount_singular(self):
        ing = MockIngredient(amount=Decimal(0), no_amount=True, food=MockFood("apple", "apples"))
        assert _resolve_food_name(ing) == "apple"

    def test_no_amount_with_unit(self):
        ing = MockIngredient(amount=Decimal(0), no_amount=True,
                             unit=MockUnit("slice", "slices"),
                             food=MockFood("apple", "apples"))
        assert _resolve_food_name(ing) == "apple"

    def test_unit_and_food_amount_0(self):
        ing = MockIngredient(amount=Decimal(0),
                             unit=MockUnit("kg"),
                             food=MockFood("apple", "apples"))
        assert _resolve_food_name(ing) == "apples"

    def test_unit_and_food_amount_1(self):
        ing = MockIngredient(amount=Decimal(1),
                             unit=MockUnit("kg"),
                             food=MockFood("apple", "apples"))
        assert _resolve_food_name(ing) == "apple"

    def test_unit_and_food_amount_2(self):
        ing = MockIngredient(amount=Decimal(2),
                             unit=MockUnit("kg"),
                             food=MockFood("apple", "apples"))
        assert _resolve_food_name(ing) == "apples"

    def test_plural_unit_and_food_amount_1(self):
        ing = MockIngredient(amount=Decimal(1),
                             unit=MockUnit("slice", "slices"),
                             food=MockFood("apple", "apples"))
        assert _resolve_food_name(ing) == "apple"

    def test_plural_unit_and_food_amount_1_point_5(self):
        ing = MockIngredient(amount=Decimal("1.5"),
                             unit=MockUnit("slice", "slices"),
                             food=MockFood("apple", "apples"))
        assert _resolve_food_name(ing) == "apples"


class TestPluralNameTag:
    """Tests for _plural_name_tag HTML tag generation."""

    def test_tag_with_singular_and_plural(self):
        result = _plural_name_tag("apple", "apples", Decimal(1), False)
        assert 'singular="apple"' in result
        assert 'plural="apples"' in result
        assert "v-bind:amount='1.0'" in result
        assert "v-bind:factor='ingredient_factor'" in result
        assert ":no-amount='false'" in result
        assert result.startswith("<plural-name")
        assert result.endswith("</plural-name>")

    def test_static_text_when_no_plural(self):
        result = _plural_name_tag("apple", None, Decimal(2), False)
        assert result == "apple"
        assert "<plural-name" not in result

    def test_static_text_when_empty_plural(self):
        result = _plural_name_tag("apple", "", Decimal(2), False)
        assert result == "apple"
        assert "<plural-name" not in result

    def test_apostrophe_escaped(self):
        result = _plural_name_tag("shepherd's pie", "shepherd's pies", Decimal(1), False)
        assert "shepherd&#x27;s pie" in result or "shepherd&apos;s pie" in result
        assert "shepherd&#x27;s pies" in result or "shepherd&apos;s pies" in result

    def test_no_amount_true(self):
        result = _plural_name_tag("apple", "apples", Decimal(0), True)
        assert ":no-amount='true'" in result

    def test_amount_serialized_as_float(self):
        result = _plural_name_tag("apple", "apples", Decimal("1.5000000000000000"), False)
        assert "v-bind:amount='1.5'" in result
        assert "Decimal" not in result

    def test_amount_zero(self):
        result = _plural_name_tag("apple", "apples", Decimal(0), False)
        assert "v-bind:amount='0.0'" in result

    def test_html_special_chars_escaped(self):
        result = _plural_name_tag('a<b', 'a<bs', Decimal(1), False)
        assert 'singular="a&lt;b"' in result
        assert 'plural="a&lt;bs"' in result


class TestIngredientObject:
    """Integration tests for IngredientObject production code path."""

    def test_food_with_plural_returns_tag(self):
        ing = MockIngredient(amount=Decimal(2), food=MockFood("apple", "apples"))
        obj = IngredientObject(ing)
        assert "<plural-name" in obj.food
        assert 'singular="apple"' in obj.food
        assert 'plural="apples"' in obj.food

    def test_food_without_plural_returns_static_text(self):
        ing = MockIngredient(amount=Decimal(2), food=MockFood("rice"))
        obj = IngredientObject(ing)
        assert obj.food == "rice"
        assert "<plural-name" not in obj.food

    def test_food_with_empty_plural_returns_static_text(self):
        ing = MockIngredient(amount=Decimal(2), food=MockFood("rice", ""))
        obj = IngredientObject(ing)
        assert obj.food == "rice"

    def test_no_food_returns_empty(self):
        ing = MockIngredient(amount=Decimal(1), food=None)
        obj = IngredientObject(ing)
        assert obj.food == ""

    def test_unit_resolved_via_resolve_unit_name(self):
        ing = MockIngredient(amount=Decimal(2), unit=MockUnit("slice", "slices"))
        obj = IngredientObject(ing)
        assert obj.unit == "slices"

    def test_unit_singular_amount_1(self):
        ing = MockIngredient(amount=Decimal(1), unit=MockUnit("slice", "slices"))
        obj = IngredientObject(ing)
        assert obj.unit == "slice"

    def test_no_amount_with_plural_food_returns_tag_with_no_amount(self):
        ing = MockIngredient(amount=Decimal(0), no_amount=True, food=MockFood("apple", "apples"))
        obj = IngredientObject(ing)
        assert "<plural-name" in obj.food
        assert ":no-amount='true'" in obj.food

    def test_note_preserved(self):
        ing = MockIngredient(amount=Decimal(1), food=MockFood("apple"), note="diced")
        obj = IngredientObject(ing)
        assert obj.note == "diced"

    def test_no_amount_without_plural_food_returns_static(self):
        ing = MockIngredient(amount=Decimal(0), no_amount=True, food=MockFood("rice"))
        obj = IngredientObject(ing)
        assert obj.food == "rice"
        assert "<plural-name" not in obj.food


class TestIngredientObjectNumericAmount:
    """Tests for IngredientObject numeric_amount and amount string rendering
    across various decimal precisions and edge cases."""

    def test_integer_amount_serialized_as_float(self):
        ing = MockIngredient(amount=Decimal(5))
        obj = IngredientObject(ing)
        assert "v-bind:number='5.0'" in obj.amount
        assert obj.numeric_amount == 5.0

    def test_zero_amount(self):
        ing = MockIngredient(amount=Decimal(0))
        obj = IngredientObject(ing)
        assert "v-bind:number='0.0'" in obj.amount
        assert obj.numeric_amount == 0.0

    def test_small_decimal_amount(self):
        ing = MockIngredient(amount=Decimal("0.25"))
        obj = IngredientObject(ing)
        assert "v-bind:number='0.25'" in obj.amount
        assert obj.numeric_amount == 0.25

    def test_high_precision_decimal_truncated_to_float(self):
        """IngredientObject converts Decimal to Python float via float(),
        which has ~15-17 significant digits of precision."""
        precise = Decimal("3.141592653589793238462643383279")
        ing = MockIngredient(amount=precise)
        obj = IngredientObject(ing)
        assert isinstance(obj.numeric_amount, float)
        assert abs(obj.numeric_amount - 3.141592653589793) < 1e-12

    def test_very_small_positive_amount(self):
        ing = MockIngredient(amount=Decimal("0.0000001"))
        obj = IngredientObject(ing)
        assert obj.numeric_amount == 1e-7
        assert "scalable-number" in obj.amount

    def test_very_large_amount(self):
        ing = MockIngredient(amount=Decimal("999999999.9999"))
        obj = IngredientObject(ing)
        assert abs(obj.numeric_amount - 999999999.9999) < 1e-6
        assert "scalable-number" in obj.amount

    def test_negative_amount(self):
        ing = MockIngredient(amount=Decimal("-2.5"))
        obj = IngredientObject(ing)
        assert obj.numeric_amount == -2.5
        assert "v-bind:number='-2.5'" in obj.amount

    def test_no_amount_returns_empty_string_and_zero_numeric(self):
        ing = MockIngredient(amount=Decimal(100), no_amount=True)
        obj = IngredientObject(ing)
        assert obj.amount == ""
        assert obj.numeric_amount == 0

    def test_invalid_amount_valueerror_defaults_zero(self):
        """If ingredient.amount cannot be converted to float, fall back to 0.0."""
        ing = MockIngredient(amount=None)
        ing.amount = None
        obj = IngredientObject(ing)
        assert obj.numeric_amount == 0.0
        assert "v-bind:number='0.0'" in obj.amount

    def test_amount_renders_scalable_number_tag(self):
        ing = MockIngredient(amount=Decimal("1.5"))
        obj = IngredientObject(ing)
        assert obj.amount.startswith("<scalable-number")
        assert "v-bind:factor='ingredient_factor'" in obj.amount
        assert obj.amount.endswith("></scalable-number>")

    def test_full_ingredient_string_with_numeric(self):
        ing = MockIngredient(
            amount=Decimal("2.5"),
            unit=MockUnit("cup", "cups"),
            food=MockFood("flour"),
            note="sifted",
        )
        obj = IngredientObject(ing)
        rendered = str(obj)
        assert "scalable-number" in rendered
        assert "cups" in rendered
        assert "flour" in rendered

    def test_full_ingredient_string_no_amount(self):
        ing = MockIngredient(
            amount=Decimal(0),
            no_amount=True,
            unit=MockUnit("pinch", "pinches"),
            food=MockFood("salt"),
        )
        obj = IngredientObject(ing)
        rendered = str(obj)
        assert "scalable-number" not in rendered
        assert "pinch" in rendered
        assert "salt" in rendered


class TestPluralNameTagNumericEdgeCases:
    """Tests for _plural_name_tag covering numeric edge cases."""

    def test_very_small_positive_amount_uses_plural(self):
        result = _plural_name_tag("apple", "apples", Decimal("0.0000001"), False)
        assert "v-bind:amount='1e-07'" in result or "v-bind:amount='0.0'" not in result

    def test_negative_fractional_amount(self):
        result = _plural_name_tag("slice", "slices", Decimal("-0.5"), False)
        assert "v-bind:amount='-0.5'" in result

    def test_high_precision_amount_serialized_as_float(self):
        result = _plural_name_tag("g", "grams", Decimal("0.1234567890123456789"), False)
        assert "Decimal" not in result

    def test_amount_none_falls_back_to_zero(self):
        result = _plural_name_tag("x", "xs", None, False)
        assert "v-bind:amount='0.0'" in result

    def test_non_numeric_amount_falls_back(self):
        result = _plural_name_tag("x", "xs", "not-a-number", False)
        assert "v-bind:amount='0.0'" in result
