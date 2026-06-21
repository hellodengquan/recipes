import pytest
from django.contrib import auth
from django.test import RequestFactory
from django_scopes import scope, scopes_disabled

from cookbook.helper.ingredient_parser import IngredientParser
from cookbook.models import Unit, Food, Ingredient


@pytest.mark.parametrize("arg", [
    [True],
    [False],
])
def test_ingredient_parser(arg, u1_s1):
    expectations = {
        "2¼ l Wasser": (2.25, "l", "Wasser", ""),
        "3¼l Wasser": (3.25, "l", "Wasser", ""),
        "¼ l Wasser": (0.25, "l", "Wasser", ""),
        "3l Wasser": (3, "l", "Wasser", ""),
        "4 l Wasser": (4, "l", "Wasser", ""),
        "½l Wasser": (0.5, "l", "Wasser", ""),
        "⅛ Liter Sauerrahm": (0.125, "Liter", "Sauerrahm", ""),
        "5 Zwiebeln": (5, None, "Zwiebeln", ""),
        "3 Zwiebeln, gehackt": (3, None, "Zwiebeln", "gehackt"),
        "5 Zwiebeln (gehackt)": (5, None, "Zwiebeln", "gehackt"),
        "1 Zwiebel(n)": (1, None, "Zwiebel(n)", ""),
        "4 1/2 Zwiebeln": (4.5, None, "Zwiebeln", ""),
        "4 ½ Zwiebeln": (4.5, None, "Zwiebeln", ""),
        "1/2 EL Mehl": (0.5, "EL", "Mehl", ""),
        "1/2 Zwiebel": (0.5, None, "Zwiebel", ""),
        "1/5g Mehl, gesiebt": (0.2, "g", "Mehl", "gesiebt"),
        "1/2 Zitrone, ausgepresst": (0.5, None, "Zitrone", "ausgepresst"),
        "etwas Mehl": (0, None, "etwas Mehl", ""),
        "Öl zum Anbraten": (0, None, "Öl zum Anbraten", ""),
        "n. B. Knoblauch, zerdrückt": (0, None, "n. B. Knoblauch", "zerdrückt"),
        "Kräuter, mediterrane (Oregano, Rosmarin, Basilikum)": (
            0, None, "Kräuter, mediterrane", "Oregano, Rosmarin, Basilikum"),
        "600 g Kürbisfleisch (Hokkaido), geschält, entkernt und geraspelt": (
            600, "g", "Kürbisfleisch (Hokkaido)", "geschält, entkernt und geraspelt"),
        "Muskat": (0, None, "Muskat", ""),
        "200 g Mehl, glattes": (200, "g", "Mehl", "glattes"),
        "1 Ei(er)": (1, None, "Ei(er)", ""),
        "1 Prise(n) Salz": (1, "Prise(n)", "Salz", ""),
        "etwas Wasser, lauwarmes": (0, None, "etwas Wasser", "lauwarmes"),
        "Strudelblätter, fertige, für zwei Strudel": (0, None, "Strudelblätter", "fertige, für zwei Strudel"),
        "barrel-aged Bourbon": (0, None, "barrel-aged Bourbon", ""),
        "golden syrup": (0, None, "golden syrup", ""),
        "unsalted butter, for greasing": (0, None, "unsalted butter", "for greasing"),
        "unsalted butter , for greasing": (0, None, "unsalted butter", "for greasing"),  # trim
        "1 small sprig of fresh rosemary": (1, "small", "sprig of fresh rosemary", ""),
        # does not always work perfectly!
        "75 g fresh breadcrumbs": (75, "g", "fresh breadcrumbs", ""),
        "4 acorn squash , or onion squash (600-800g)": (4, "acorn", "squash, or onion squash", "600-800g"),
        "1 x 250 g packet of cooked mixed grains , such as spelt and wild rice": (
            1, "x", "250 g packet of cooked mixed grains", "such as spelt and wild rice"),
        "1 big bunch of fresh mint , (60g)": (1, "big", "bunch of fresh mint,", "60g"),
        "1 large red onion": (1, "large", "red onion", ""),
        # "2-3 TL Curry": (), # idk what it should use here either
        "1 Zwiebel gehackt": (1, "Zwiebel", "gehackt", ""),
        "1 EL Kokosöl": (1, "EL", "Kokosöl", ""),
        "0.5 paket jäst (à 50 g)": (0.5, "paket", "jäst", "à 50 g"),
        "ägg": (0, None, "ägg", ""),
        "50 g smör eller margarin": (50, "g", "smör eller margarin", ""),
        "3,5 l Wasser": (3.5, "l", "Wasser", ""),
        "3.5 l Wasser": (3.5, "l", "Wasser", ""),
        "400 g Karotte(n)": (400, "g", "Karotte(n)", ""),
        "400g unsalted butter": (400, "g", "unsalted butter", ""),
        "2L Wasser": (2, "L", "Wasser", ""),
        "1 (16 ounce) package dry lentils, rinsed": (1, "package", "dry lentils, rinsed", "16 ounce"),
        "2-3 c Water": (2, "c", "Water", "2-3"),
        "Pane (raffermo o secco) 80 g": (80, "g", "Pane", "raffermo o secco"),
        "1 Knoblauchzehe(n), gehackt oder gepresst": (1.0, None, 'Knoblauchzehe(n)', 'gehackt oder gepresst'),
        "1 Porreestange(n) , ca. 200 g": (1.0, None, 'Porreestange(n)', 'ca. 200 g'),  # leading space before comma
        # test for over long food entries to get properly split into the note field
        "1 Lorem ipsum dolor sit amet consetetur sadipscing elitr sed diam nonumy eirmod tempor invidunt ut l Lorem ipsum dolor sit amet consetetur sadipscing elitr sed diam nonumy eirmod tempor invidunt ut l": (
            1.0, 'Lorem', 'ipsum',
            'dolor sit amet consetetur sadipscing elitr sed diam nonumy eirmod tempor invidunt ut l Lorem ipsum dolor sit amet consetetur sadipscing elitr sed diam nonumy eirmod tempor invidunt ut l'),
        "1 LoremipsumdolorsitametconsetetursadipscingelitrseddiamnonumyeirmodtemporinviduntutlLoremipsumdolorsitametconsetetursadipscingelitrseddiamnonumyeirmodtemporinviduntutl": (
            1.0, None, 'LoremipsumdolorsitametconsetetursadipscingelitrseddiamnonumyeirmodtemporinviduntutlLoremipsumdolorsitametconsetetursadipscingeli',
            'LoremipsumdolorsitametconsetetursadipscingelitrseddiamnonumyeirmodtemporinviduntutlLoremipsumdolorsitametconsetetursadipscingelitrseddiamnonumyeirmodtemporinviduntutl'),
        "砂糖 50g": (50, "g", "砂糖", ""),
        "卵 4個": (4, "個", "卵", ""),
        ', Lemon wedges,': (0, None, 'Lemon wedges', ''),
        '... Lemon wedges': (0, None, 'Lemon wedges', ''),
        '. Lemon wedges': (0, None, 'Lemon wedges', ''),
        '- Lemon wedges': (0, None, 'Lemon wedges', ''),
        '| Lemon wedges': (0, None, 'Lemon wedges', ''),
        '= Lemon wedges': (0, None, 'Lemon wedges', ''),
        '+ Lemon wedges': (0, None, 'Lemon wedges', ''),
        '# Lemon wedges': (0, None, 'Lemon wedges', ''),
        '* Lemon wedges': (0, None, 'Lemon wedges', ''),
        '_ Lemon wedges': (0, None, 'Lemon wedges', ''),
    }
    # for German you could say that if an ingredient does not have
    # an amount # and it starts with a lowercase letter, then that
    # is a unit ("etwas", "evtl.") does not apply to English tho

    # TODO maybe add/improve support for weired stuff like this https://www.rainbownourishments.com/vegan-lemon-tart/#recipe

    user = auth.get_user(u1_s1)
    space = user.userspace_set.first().space
    request = RequestFactory()
    request.user = user
    request.space = space
    ingredient_parser = IngredientParser(request, False, ignore_automations=arg[0])

    count = 0
    with scope(space=space):
        for key, val in expectations.items():
            count += 1
            parsed = ingredient_parser.parse(key)
            print(f'testing if {key} becomes {val}')
            assert parsed == val


def test_ingredient_parser_integer_fraction_mixed(u1_s1):
    """
    Test integer and fraction mixing scenarios that previously caused regression issues.
    Covers various combinations of whole numbers with both string and unicode fractions.
    """
    expectations = {
        "1 1/2 cups flour": (1.5, "cups", "flour", ""),
        "2 3/4 l milk": (2.75, "l", "milk", ""),
        "3 1/4 kg apples": (3.25, "kg", "apples", ""),
        "5 1/8 tbsp sugar": (5.125, "tbsp", "sugar", ""),
        "1 1/2 Zwiebeln": (1.5, None, "Zwiebeln", ""),
        "2 1/2 EL Honig": (2.5, "EL", "Honig", ""),
        "1 ½ cups flour": (1.5, "cups", "flour", ""),
        "2 ¾ l milk": (2.75, "l", "milk", ""),
        "3 ¼ kg apples": (3.25, "kg", "apples", ""),
        "5 ⅛ tbsp sugar": (5.125, "tbsp", "sugar", ""),
        "1 ½ Zwiebeln": (1.5, None, "Zwiebeln", ""),
        "2 ½ EL Honig": (2.5, "EL", "Honig", ""),
        "1 1/2 große Zwiebeln, gehackt": (1.5, "große", "Zwiebeln", "gehackt"),
        "2 3/4 Tassen Mehl, gesiebt": (2.75, "Tassen", "Mehl", "gesiebt"),
        "1 ½ cup rice": (1.5, "cup", "rice", ""),
        "3 1/3 Liter Wasser": (3.3333333333333335, "Liter", "Wasser", ""),
        "7 2/3 g Salz": (7.666666666666667, "g", "Salz", ""),
        "0 1/2 l Wasser": (0.5, "l", "Wasser", ""),
        "10 1/4 g Butter": (10.25, "g", "Butter", ""),
        "100 1/2 ml Milch": (100.5, "ml", "Milch", ""),
    }

    user = auth.get_user(u1_s1)
    space = user.userspace_set.first().space
    request = RequestFactory()
    request.user = user
    request.space = space
    ingredient_parser = IngredientParser(request, False, ignore_automations=True)

    with scope(space=space):
        for key, val in expectations.items():
            parsed = ingredient_parser.parse(key)
            print(f'testing if {key} becomes {val}')
            assert abs(parsed[0] - val[0]) < 0.0001, f"Amount mismatch for '{key}': expected {val[0]}, got {parsed[0]}"
            assert parsed[1] == val[1], f"Unit mismatch for '{key}': expected {val[1]}, got {parsed[1]}"
            assert parsed[2] == val[2], f"Food mismatch for '{key}': expected {val[2]}, got {parsed[2]}"


def test_ingredient_parser_end_to_end_unit_fallback(u1_s1):
    """
    End-to-end test for unit dictionary fallback using real IngredientParser.
    Verifies that unknown units are properly created as fallback instead of crashing.
    Tests the full parse_as_ingredient flow: parse -> get_unit -> get_food.
    """
    user = auth.get_user(u1_s1)
    space = user.userspace_set.first().space
    request = RequestFactory()
    request.user = user
    request.space = space

    with scopes_disabled():
        Unit.objects.filter(space=space, name='pinch').delete()
        Unit.objects.filter(space=space, name='dash').delete()
        Unit.objects.filter(space=space, name='handful').delete()
        Food.objects.filter(space=space, name='Salt').delete()
        Food.objects.filter(space=space, name='Pepper').delete()
        Food.objects.filter(space=space, name='Peanuts').delete()

    ingredient_parser = IngredientParser(request, False, ignore_automations=True)

    with scope(space=space):
        test_cases = [
            ("1 pinch Salt", 1.0, "pinch", "Salt"),
            ("2 dash Pepper", 2.0, "dash", "Pepper"),
            ("3 handful Peanuts", 3.0, "handful", "Peanuts"),
        ]

        for text, expected_amount, expected_unit_name, expected_food_name in test_cases:
            ingredient = ingredient_parser.parse_as_ingredient(text)

            assert ingredient is not None, f"Failed to parse '{text}'"
            assert abs(ingredient.amount - expected_amount) < 0.0001, \
                f"Amount mismatch for '{text}': expected {expected_amount}, got {ingredient.amount}"

            assert ingredient.unit is not None, f"Unit should not be None for '{text}'"
            assert ingredient.unit.name == expected_unit_name, \
                f"Unit name mismatch for '{text}': expected {expected_unit_name}, got {ingredient.unit.name}"
            assert ingredient.unit.space == space, "Unit should be in the correct space"

            assert ingredient.food is not None, f"Food should not be None for '{text}'"
            assert ingredient.food.name == expected_food_name, \
                f"Food name mismatch for '{text}': expected {expected_food_name}, got {ingredient.food.name}"
            assert ingredient.food.space == space, "Food should be in the correct space"


def test_ingredient_parser_end_to_end_unknown_unit_persistence(u1_s1):
    """
    End-to-end test verifying that unknown units created by the parser persist in the database
    and can be reused in subsequent parses (fallback unit dictionary behavior).
    """
    user = auth.get_user(u1_s1)
    space = user.userspace_set.first().space
    request = RequestFactory()
    request.user = user
    request.space = space

    unique_unit_name = "test_unique_unit_xyz"
    unique_food_name = "UniqueTestFoodABC"

    with scopes_disabled():
        Unit.objects.filter(space=space, name=unique_unit_name).delete()
        Food.objects.filter(space=space, name=unique_food_name).delete()

    ingredient_parser = IngredientParser(request, False, ignore_automations=True)

    with scope(space=space):
        first_parse = ingredient_parser.parse_as_ingredient(f"5 {unique_unit_name} {unique_food_name}")
        first_unit_id = first_parse.unit.id
        first_food_id = first_parse.food.id

        assert first_parse.unit.name == unique_unit_name
        assert first_parse.food.name == unique_food_name

        second_parse = ingredient_parser.parse_as_ingredient(f"3 {unique_unit_name} {unique_food_name}")

        assert second_parse.unit.id == first_unit_id, \
            "Second parse should reuse the same unit object (fallback dictionary)"
        assert second_parse.food.id == first_food_id, \
            "Second parse should reuse the same food object"

        assert abs(second_parse.amount - 3.0) < 0.0001


def test_ingredient_parser_end_to_end_no_unit_fallback(u1_s1):
    """
    End-to-end test for ingredients without units - verify fallback behavior.
    When no unit is detected, ingredient.unit should be None but food should still be created.
    """
    user = auth.get_user(u1_s1)
    space = user.userspace_set.first().space
    request = RequestFactory()
    request.user = user
    request.space = space

    unique_food = "NoUnitTestFoodXYZ"

    with scopes_disabled():
        Food.objects.filter(space=space, name=unique_food).delete()

    ingredient_parser = IngredientParser(request, False, ignore_automations=True)

    with scope(space=space):
        ingredient = ingredient_parser.parse_as_ingredient(f"3 {unique_food}")

        assert ingredient.food is not None
        assert ingredient.food.name == unique_food
        assert ingredient.unit is None, "Ingredient without unit should have unit=None"
        assert abs(ingredient.amount - 3.0) < 0.0001


def test_ingredient_parser_end_to_end_zero_amount_fallback(u1_s1):
    """
    End-to-end test for zero-amount ingredients (no amount specified).
    Verifies fallback behavior when parser can't extract a numeric amount.
    """
    user = auth.get_user(u1_s1)
    space = user.userspace_set.first().space
    request = RequestFactory()
    request.user = user
    request.space = space

    food_name = "ZeroAmountTestFood"

    with scopes_disabled():
        Food.objects.filter(space=space, name=food_name).delete()

    ingredient_parser = IngredientParser(request, False, ignore_automations=True)

    with scope(space=space):
        ingredient = ingredient_parser.parse_as_ingredient(f"etwas {food_name}")

        assert ingredient.food is not None
        assert ingredient.amount == 0, "Zero-amount ingredient should have amount=0"
        assert ingredient.unit is None
