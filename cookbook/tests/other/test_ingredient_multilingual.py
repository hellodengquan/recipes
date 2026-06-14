import pytest
from decimal import Decimal
from django.contrib import auth
from django.test import RequestFactory
from django_scopes import scope, scopes_disabled

from cookbook.helper.ingredient_normalization_service import (
    IngredientNormalizationService,
    NormalizedIngredient,
)
from cookbook.models import Food, Unit


@pytest.fixture
def zh_service(u1_s1, space_1):
    user = auth.get_user(u1_s1)
    request = RequestFactory()
    request.user = user
    request.space = space_1
    return IngredientNormalizationService(request=request, space=space_1, use_cache=False, ignore_automations=True)


class TestChineseIngredientParsing:
    def test_chinese_simple_ingredient(self, zh_service, space_1):
        with scope(space=space_1):
            result = zh_service.normalize('200 克 苹果')
            assert result.amount == Decimal('200')
            assert '克' in result.unit_name or 'g' in result.unit_name
            assert '苹果' in result.food_name

    def test_chinese_no_unit(self, zh_service, space_1):
        with scope(space=space_1):
            result = zh_service.normalize('3 苹果')
            assert result.amount == Decimal('3')
            assert '苹果' in result.food_name

    def test_chinese_amount_with_chinese_numerals(self, zh_service, space_1):
        with scope(space=space_1):
            result = zh_service.normalize('500毫升 牛奶')
            assert result.amount == Decimal('500')

    def test_chinese_with_comma_note(self, zh_service, space_1):
        with scope(space=space_1):
            result = zh_service.normalize('2个 鸡蛋，新鲜的')
            assert result.amount == Decimal('2')
            assert '鸡蛋' in result.food_name

    def test_chinese_with_parentheses_note(self, zh_service, space_1):
        with scope(space=space_1):
            result = zh_service.normalize('100克 猪肉（五花肉）')
            assert result.amount == Decimal('100')
            assert '猪肉' in result.food_name
            assert '五花肉' in result.note

    def test_chinese_unicode_fraction(self, zh_service, space_1):
        with scope(space=space_1):
            result = zh_service.normalize('½ 茶匙 盐')
            assert result.amount == Decimal('0.5')

    def test_chinese_decimal_amount(self, zh_service, space_1):
        with scope(space=space_1):
            result = zh_service.normalize('1.5 斤 白菜')
            assert result.amount == Decimal('1.5')

    def test_chinese_food_only(self, zh_service, space_1):
        with scope(space=space_1):
            result = zh_service.normalize('酱油')
            assert result.food_name == '酱油'
            assert result.amount == Decimal('0')

    def test_chinese_ingredient_list(self, zh_service, space_1):
        ingredients = [
            '200克 面粉',
            '100克 糖',
            '3个 鸡蛋',
        ]
        with scope(space=space_1):
            normalized = [zh_service.normalize(ing) for ing in ingredients]
            assert len(normalized) == 3
            assert normalized[0].food_name == '面粉'
            assert normalized[1].food_name == '糖'
            assert normalized[2].food_name == '鸡蛋'

    def test_chinese_merge_same_food(self, zh_service, space_1):
        ingredients = [
            '100克 大米',
            '200克 大米',
        ]
        with scope(space=space_1):
            merged = zh_service.merge_ingredients(ingredients)
            assert len(merged) == 1
            assert merged[0].amount == Decimal('300')
            assert '大米' in merged[0].food_name

    def test_chinese_scale_by_factor(self, zh_service, space_1):
        with scope(space=space_1):
            result = zh_service.scale('100克 黄豆', 2)
            assert result.amount == Decimal('200')
            assert '黄豆' in result.food_name

    def test_chinese_scale_by_servings(self, zh_service, space_1):
        with scope(space=space_1):
            result = zh_service.scale_by_servings('200克 面条', 2, 4)
            assert result.amount == Decimal('400')
            assert '面条' in result.food_name

    def test_chinese_connected_amount_unit(self, zh_service, space_1):
        with scope(space=space_1):
            result = zh_service.normalize('500g 番茄')
            assert result.amount == Decimal('500')


class TestFrenchIngredientParsing:
    @pytest.fixture
    def fr_service(self, u1_s1, space_1):
        user = auth.get_user(u1_s1)
        request = RequestFactory()
        request.user = user
        request.space = space_1
        return IngredientNormalizationService(request=request, space=space_1, use_cache=False, ignore_automations=True)

    def test_french_simple_ingredient(self, fr_service, space_1):
        with scope(space=space_1):
            result = fr_service.normalize('200 g pomme')
            assert result.amount == Decimal('200')
            assert 'pomme' in result.food_name

    def test_french_unit_grammes(self, fr_service, space_1):
        with scope(space=space_1):
            result = fr_service.normalize('500 grammes farine')
            assert result.amount == Decimal('500')
            assert 'farine' in result.food_name

    def test_french_unit_litre(self, fr_service, space_1):
        with scope(space=space_1):
            result = fr_service.normalize('1 litre lait')
            assert result.amount == Decimal('1')
            assert 'lait' in result.food_name

    def test_french_with_comma_note(self, fr_service, space_1):
        with scope(space=space_1):
            result = fr_service.normalize('3 oeufs, frais')
            assert result.amount == Decimal('3')
            assert 'oeufs' in result.food_name
            assert 'frais' in result.note

    def test_french_with_parentheses_note(self, fr_service, space_1):
        with scope(space=space_1):
            result = fr_service.normalize('100 g beurre (doux)')
            assert result.amount == Decimal('100')
            assert 'beurre' in result.food_name
            assert 'doux' in result.note

    def test_french_fraction(self, fr_service, space_1):
        with scope(space=space_1):
            result = fr_service.normalize('1/2 cuillère à soupe sel')
            assert result.amount == Decimal('0.5')

    def test_french_unicode_fraction(self, fr_service, space_1):
        with scope(space=space_1):
            result = fr_service.normalize('½ cuillère à café poivre')
            assert result.amount == Decimal('0.5')

    def test_french_decimal_comma(self, fr_service, space_1):
        with scope(space=space_1):
            result = fr_service.normalize('2,5 kg pommes de terre')
            assert result.amount == Decimal('2.5')

    def test_french_food_only(self, fr_service, space_1):
        with scope(space=space_1):
            result = fr_service.normalize('fromage')
            assert result.food_name == 'fromage'
            assert result.amount == Decimal('0')

    def test_french_merge_same_food(self, fr_service, space_1):
        ingredients = [
            '100 g sucre',
            '200 g sucre',
        ]
        with scope(space=space_1):
            merged = fr_service.merge_ingredients(ingredients)
            assert len(merged) == 1
            assert merged[0].amount == Decimal('300')

    def test_french_scale_by_factor(self, fr_service, space_1):
        with scope(space=space_1):
            result = fr_service.scale('150 g chocolat', 2)
            assert result.amount == Decimal('300')
            assert 'chocolat' in result.food_name

    def test_french_scale_by_servings(self, fr_service, space_1):
        with scope(space=space_1):
            result = fr_service.scale_by_servings('300 g pâtes', 4, 6)
            assert result.amount == Decimal('450')
            assert 'pâtes' in result.food_name

    def test_french_ingredient_list(self, fr_service, space_1):
        ingredients = [
            '200 g farine',
            '100 g sucre',
            '3 oeufs',
            '1 sachet levure',
        ]
        with scope(space=space_1):
            normalized = [fr_service.normalize(ing) for ing in ingredients]
            assert len(normalized) == 4
            foods = [n.food_name for n in normalized]
            assert 'farine' in foods
            assert 'sucre' in foods
            assert 'oeufs' in foods
            assert 'levure' in foods


class TestJapaneseIngredientParsing:
    @pytest.fixture
    def jp_service(self, u1_s1, space_1):
        user = auth.get_user(u1_s1)
        request = RequestFactory()
        request.user = user
        request.space = space_1
        return IngredientNormalizationService(request=request, space=space_1, use_cache=False, ignore_automations=True)

    def test_japanese_simple_ingredient(self, jp_service, space_1):
        with scope(space=space_1):
            result = jp_service.normalize('200 g りんご')
            assert result.amount == Decimal('200')
            assert 'りんご' in result.food_name

    def test_japanese_kanji_food(self, jp_service, space_1):
        with scope(space=space_1):
            result = jp_service.normalize('100 g 牛肉')
            assert result.amount == Decimal('100')
            assert '牛肉' in result.food_name

    def test_japanese_unit_gram(self, jp_service, space_1):
        with scope(space=space_1):
            result = jp_service.normalize('500グラム 小麦粉')
            assert result.amount == Decimal('500')
            assert '小麦粉' in result.food_name

    def test_japanese_with_parentheses_note(self, jp_service, space_1):
        with scope(space=space_1):
            result = jp_service.normalize('100g 豚肉（バラ）')
            assert result.amount == Decimal('100')
            assert '豚肉' in result.food_name
            assert 'バラ' in result.note

    def test_japanese_with_comma_note(self, jp_service, space_1):
        with scope(space=space_1):
            result = jp_service.normalize('2個 卵、新鮮なもの')
            assert result.amount == Decimal('2')
            assert '卵' in result.food_name

    def test_japanese_fraction(self, jp_service, space_1):
        with scope(space=space_1):
            result = jp_service.normalize('1/2 小匙 塩')
            assert result.amount == Decimal('0.5')

    def test_japanese_unicode_fraction(self, jp_service, space_1):
        with scope(space=space_1):
            result = jp_service.normalize('½ 大匙 砂糖')
            assert result.amount == Decimal('0.5')

    def test_japanese_decimal_amount(self, jp_service, space_1):
        with scope(space=space_1):
            result = jp_service.normalize('1.5 合 お米')
            assert result.amount == Decimal('1.5')
            assert 'お米' in result.food_name

    def test_japanese_food_only(self, jp_service, space_1):
        with scope(space=space_1):
            result = jp_service.normalize('醤油')
            assert result.food_name == '醤油'
            assert result.amount == Decimal('0')

    def test_japanese_hiragana_katakana_mixed(self, jp_service, space_1):
        with scope(space=space_1):
            result = jp_service.normalize('200ml ミルク')
            assert result.amount == Decimal('200')
            assert 'ミルク' in result.food_name

    def test_japanese_merge_same_food(self, jp_service, space_1):
        ingredients = [
            '100 g 砂糖',
            '200 g 砂糖',
        ]
        with scope(space=space_1):
            merged = jp_service.merge_ingredients(ingredients)
            assert len(merged) == 1
            assert merged[0].amount == Decimal('300')

    def test_japanese_scale_by_factor(self, jp_service, space_1):
        with scope(space=space_1):
            result = jp_service.scale('150 g 豆腐', 2)
            assert result.amount == Decimal('300')
            assert '豆腐' in result.food_name

    def test_japanese_scale_by_servings(self, jp_service, space_1):
        with scope(space=space_1):
            result = jp_service.scale_by_servings('300 g ご飯', 2, 4)
            assert result.amount == Decimal('600')
            assert 'ご飯' in result.food_name

    def test_japanese_ingredient_list(self, jp_service, space_1):
        ingredients = [
            '200 g 米',
            '100 g 砂糖',
            '3個 卵',
            '1本 ネギ',
        ]
        with scope(space=space_1):
            normalized = [jp_service.normalize(ing) for ing in ingredients]
            assert len(normalized) == 4
            foods = [n.food_name for n in normalized]
            assert '米' in foods
            assert '砂糖' in foods
            assert '卵' in foods
            assert 'ネギ' in foods

    def test_japanese_connected_amount_unit(self, jp_service, space_1):
        with scope(space=space_1):
            result = jp_service.normalize('500g 味噌')
            assert result.amount == Decimal('500')
            assert '味噌' in result.food_name


class TestMultilingualNormalization:
    def test_multilingual_food_name_normalization(self, zh_service, space_1):
        with scope(space=space_1):
            test_cases = [
                ('  苹果  ', '苹果'),
                ('  pomme  ', 'pomme'),
                ('  りんご  ', 'りんご'),
            ]
            for input_text, expected in test_cases:
                result = zh_service.normalize_name(input_text)
                assert result == expected, f"Failed for '{input_text}'"

    def test_multilingual_amount_normalization(self, zh_service, space_1):
        with scope(space=space_1):
            test_cases = [
                ('100 g 苹果', Decimal('100')),
                ('200g pomme', Decimal('200')),
                ('300グラム りんご', Decimal('300')),
            ]
            for input_text, expected_amount in test_cases:
                result = zh_service.normalize(input_text)
                assert result.amount == expected_amount, f"Failed for '{input_text}'"

    def test_multilingual_scale_consistency(self, zh_service, space_1):
        with scope(space=space_1):
            test_cases = [
                '100 g 苹果',
                '100 g pomme',
                '100 g りんご',
            ]
            for ing in test_cases:
                scaled = zh_service.scale(ing, 2)
                assert scaled.amount == Decimal('200'), f"Failed scaling for '{ing}'"

    def test_multilingual_merge_consistency(self, zh_service, space_1):
        with scope(space=space_1):
            test_pairs = [
                (['100 g 糖', '200 g 糖'], Decimal('300')),
                (['50 g sucre', '150 g sucre'], Decimal('200')),
                (['80 g 砂糖', '120 g 砂糖'], Decimal('200')),
            ]
            for ingredients, expected_total in test_pairs:
                merged = zh_service.merge_ingredients(ingredients)
                assert len(merged) == 1
                assert merged[0].amount == expected_total
