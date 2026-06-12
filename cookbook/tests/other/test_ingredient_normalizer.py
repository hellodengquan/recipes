import pytest

from cookbook.helper.ingredient_normalizer import (
    normalize_ingredient_name,
    get_canonical_name,
    normalize_ingredient_list,
    get_all_supported_ingredients,
    get_ingredient_language,
    CANONICAL_NAMES,
)


class TestIngredientNormalizerEnglish:
    """Tests for English ingredient name normalization."""

    def test_basic_english_ingredient(self):
        result = normalize_ingredient_name("tomato")
        assert result["is_mapped"] is True
        assert result["canonical_name"] == "tomato"
        assert result["canonical_key"] == "tomato"
        assert result["languages"]["en"] == "tomato"

    def test_english_plural_form(self):
        result = normalize_ingredient_name("tomatoes")
        assert result["is_mapped"] is True
        assert result["canonical_name"] == "tomato"

    def test_english_with_modifier_prefix(self):
        result = normalize_ingredient_name("yellow onion")
        assert result["is_mapped"] is True
        assert result["canonical_name"] == "onion"

    def test_english_compound_name(self):
        result = normalize_ingredient_name("soy sauce")
        assert result["is_mapped"] is True
        assert result["canonical_name"] == "soy sauce"

    def test_english_abbreviation_ap_flour(self):
        result = normalize_ingredient_name("all-purpose flour")
        assert result["is_mapped"] is True
        assert result["canonical_name"] == "flour"

    def test_english_unknown_ingredient(self):
        result = normalize_ingredient_name("xylophone candy")
        assert result["is_mapped"] is False
        assert result["canonical_name"] == "xylophone candy"


class TestIngredientNormalizerChinese:
    """Tests for Chinese ingredient name normalization."""

    def test_basic_chinese_ingredient(self):
        result = normalize_ingredient_name("番茄")
        assert result["is_mapped"] is True
        assert result["canonical_name"] == "tomato"
        assert result["canonical_key"] == "tomato"
        assert result["languages"]["zh"] == "番茄"

    def test_chinese_alias_tomato(self):
        result = normalize_ingredient_name("西红柿")
        assert result["is_mapped"] is True
        assert result["canonical_name"] == "tomato"

    def test_chinese_another_alias(self):
        result = normalize_ingredient_name("蕃茄")
        assert result["is_mapped"] is True
        assert result["canonical_name"] == "tomato"

    def test_chinese_egg_alias(self):
        result = normalize_ingredient_name("鸡蛋")
        assert result["is_mapped"] is True
        assert result["canonical_name"] == "egg"

    def test_chinese_egg_short_form(self):
        result = normalize_ingredient_name("蛋")
        assert result["is_mapped"] is True
        assert result["canonical_name"] == "egg"

    def test_chinese_soy_sauce_types(self):
        result = normalize_ingredient_name("生抽")
        assert result["is_mapped"] is True
        assert result["canonical_name"] == "soy sauce"

    def test_chinese_soy_sauce_another(self):
        result = normalize_ingredient_name("老抽")
        assert result["is_mapped"] is True
        assert result["canonical_name"] == "soy sauce"

    def test_chinese_unknown_ingredient(self):
        result = normalize_ingredient_name("神秘食材")
        assert result["is_mapped"] is False
        assert result["canonical_name"] == "神秘食材"


class TestIngredientNormalizerFrench:
    """Tests for French ingredient name normalization."""

    def test_basic_french_ingredient(self):
        result = normalize_ingredient_name("tomate")
        assert result["is_mapped"] is True
        assert result["canonical_name"] == "tomato"
        assert result["canonical_key"] == "tomato"
        assert result["languages"]["fr"] == "tomate"

    def test_french_plural_form(self):
        result = normalize_ingredient_name("tomates")
        assert result["is_mapped"] is True
        assert result["canonical_name"] == "tomato"

    def test_french_oeuf_with_accent(self):
        result = normalize_ingredient_name("œuf")
        assert result["is_mapped"] is True
        assert result["canonical_name"] == "egg"

    def test_french_oeuf_without_accent(self):
        result = normalize_ingredient_name("oeuf")
        assert result["is_mapped"] is True
        assert result["canonical_name"] == "egg"

    def test_french_oeuf_plural(self):
        result = normalize_ingredient_name("œufs")
        assert result["is_mapped"] is True
        assert result["canonical_name"] == "egg"

    def test_french_with_de_article(self):
        result = normalize_ingredient_name("crème de tomate")
        assert result["is_mapped"] is True
        assert result["canonical_name"] == "tomato"

    def test_french_butter(self):
        result = normalize_ingredient_name("beurre")
        assert result["is_mapped"] is True
        assert result["canonical_name"] == "butter"

    def test_french_salt(self):
        result = normalize_ingredient_name("sel")
        assert result["is_mapped"] is True
        assert result["canonical_name"] == "salt"


class TestCrossLanguageEquivalence:
    """Tests to verify that the same ingredient in different languages maps to the same canonical name."""

    def test_tomato_three_languages_same_canonical(self):
        en = normalize_ingredient_name("tomato")
        zh = normalize_ingredient_name("番茄")
        fr = normalize_ingredient_name("tomate")
        assert en["canonical_name"] == zh["canonical_name"] == fr["canonical_name"]
        assert en["canonical_key"] == zh["canonical_key"] == fr["canonical_key"]
        assert en["is_mapped"] and zh["is_mapped"] and fr["is_mapped"]

    def test_egg_three_languages_same_canonical(self):
        en = normalize_ingredient_name("eggs")
        zh = normalize_ingredient_name("鸡蛋")
        fr = normalize_ingredient_name("œufs")
        assert en["canonical_name"] == zh["canonical_name"] == fr["canonical_name"]
        assert en["is_mapped"] and zh["is_mapped"] and fr["is_mapped"]

    def test_onion_three_languages_same_canonical(self):
        en = normalize_ingredient_name("onions")
        zh = normalize_ingredient_name("洋葱")
        fr = normalize_ingredient_name("oignons")
        assert en["canonical_name"] == zh["canonical_name"] == fr["canonical_name"]

    def test_butter_three_languages_same_canonical(self):
        en = normalize_ingredient_name("unsalted butter")
        zh = normalize_ingredient_name("黄油")
        fr = normalize_ingredient_name("beurre doux")
        assert en["canonical_name"] == zh["canonical_name"] == fr["canonical_name"]

    def test_salt_three_languages_same_canonical(self):
        en = normalize_ingredient_name("sea salt")
        zh = normalize_ingredient_name("海盐")
        fr = normalize_ingredient_name("sel de mer")
        assert en["canonical_name"] == zh["canonical_name"] == fr["canonical_name"]

    def test_flour_three_languages_same_canonical(self):
        en = normalize_ingredient_name("all-purpose flour")
        zh = normalize_ingredient_name("中筋面粉")
        fr = normalize_ingredient_name("farine de blé")
        assert en["canonical_name"] == zh["canonical_name"] == fr["canonical_name"]

    def test_chicken_three_languages_same_canonical(self):
        en = normalize_ingredient_name("chicken breast")
        zh = normalize_ingredient_name("鸡胸肉")
        fr = normalize_ingredient_name("blanc de poulet")
        assert en["canonical_name"] == zh["canonical_name"] == fr["canonical_name"]

    def test_mushroom_three_languages_same_canonical(self):
        en = normalize_ingredient_name("button mushrooms")
        zh = normalize_ingredient_name("口蘑")
        fr = normalize_ingredient_name("champignons de Paris")
        assert en["canonical_name"] == zh["canonical_name"] == fr["canonical_name"]


class TestPluralAndVariations:
    """Tests for plural forms and other variations."""

    def test_english_plural_to_singular(self):
        pairs = [
            ("tomatoes", "tomato"),
            ("eggs", "egg"),
            ("onions", "onion"),
            ("carrots", "carrot"),
            ("potatoes", "potato"),
            ("mushrooms", "mushroom"),
            ("noodles", "noodle"),
            ("cheeses", "cheese"),
        ]
        for plural, expected in pairs:
            result = normalize_ingredient_name(plural)
            assert result["canonical_name"] == expected, f"Failed for {plural}"

    def test_french_plural_to_singular(self):
        pairs = [
            ("tomates", "tomato"),
            ("œufs", "egg"),
            ("oignons", "onion"),
            ("carottes", "carrot"),
            ("champignons", "mushroom"),
            ("nouilles", "noodle"),
            ("épinards", "spinach"),
        ]
        for plural, expected in pairs:
            result = normalize_ingredient_name(plural)
            assert result["canonical_name"] == expected, f"Failed for {plural}"


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_get_canonical_name_simple(self):
        assert get_canonical_name("番茄") == "tomato"
        assert get_canonical_name("tomate") == "tomato"
        assert get_canonical_name("tomatoes") == "tomato"

    def test_get_canonical_name_unknown(self):
        assert get_canonical_name("unknown ingredient") == "unknown ingredient"

    def test_normalize_ingredient_list(self):
        names = ["番茄", "tomato", "鸡蛋", "œufs", "胡萝卜", "unknown"]
        canonical = normalize_ingredient_list(names)
        assert "tomato" in canonical
        assert "egg" in canonical
        assert "carrot" in canonical
        assert "unknown" in canonical
        assert len(canonical) == 4

    def test_normalize_ingredient_list_cross_language_dedup(self):
        names = ["tomato", "番茄", "tomate", "西红柿", "tomates"]
        canonical = normalize_ingredient_list(names)
        assert len(canonical) == 1
        assert "tomato" in canonical

    def test_get_all_supported_ingredients(self):
        ingredients = get_all_supported_ingredients()
        assert len(ingredients) == len(CANONICAL_NAMES)
        for ing in ingredients:
            assert "canonical_key" in ing
            assert "canonical_name" in ing
            assert "languages" in ing
            assert "en" in ing["languages"]
            assert "zh" in ing["languages"]
            assert "fr" in ing["languages"]


class TestLanguageDetection:
    """Tests for language detection."""

    def test_detect_chinese(self):
        assert get_ingredient_language("番茄") == "zh"
        assert get_ingredient_language("鸡蛋炒番茄") == "zh"
        assert get_ingredient_language("salt and pepper 盐") == "zh"

    def test_detect_english(self):
        assert get_ingredient_language("tomato") == "en"
        assert get_ingredient_language("chicken breast") == "en"

    def test_detect_french(self):
        assert get_ingredient_language("crème") == "fr"
        assert get_ingredient_language("cuisine française") == "fr"
        assert get_ingredient_language("gousse d'ail") == "fr"

    def test_detect_unknown(self):
        assert get_ingredient_language("") == "unknown"
        assert get_ingredient_language("12345") == "unknown"

    def test_detect_french_with_oe(self):
        assert get_ingredient_language("œuf") == "fr"


class TestEdgeCases:
    """Tests for edge cases."""

    def test_empty_string(self):
        result = normalize_ingredient_name("")
        assert result["is_mapped"] is False
        assert result["canonical_name"] == ""

    def test_whitespace_only(self):
        result = normalize_ingredient_name("   ")
        assert result["is_mapped"] is False

    def test_none_input(self):
        result = normalize_ingredient_name(None)
        assert result["is_mapped"] is False

    def test_case_insensitive(self):
        assert normalize_ingredient_name("TOMATO")["canonical_name"] == "tomato"
        assert normalize_ingredient_name("Tomato")["canonical_name"] == "tomato"
        assert normalize_ingredient_name("  tomato  ")["canonical_name"] == "tomato"

    def test_punctuation_removed(self):
        assert normalize_ingredient_name("tomato!")["canonical_name"] == "tomato"
        assert normalize_ingredient_name("tomato, fresh")["is_mapped"] is True
        assert normalize_ingredient_name("tomato, fresh")["canonical_name"] == "tomato"

    def test_chinese_full_name_match(self):
        result = normalize_ingredient_name("圣女果")
        assert result["is_mapped"] is True
        assert result["canonical_name"] == "tomato"

    def test_french_compound_with_de(self):
        result = normalize_ingredient_name("fromage de chèvre")
        assert result["is_mapped"] is True
        assert result["canonical_name"] == "cheese"

    def test_long_prefix_still_matches(self):
        result = normalize_ingredient_name("organic cherry tomatoes from the garden")
        assert result["is_mapped"] is True
        assert result["canonical_name"] == "tomato"


class TestCanonicalNamesCompleteness:
    """Tests to ensure data completeness across languages."""

    def test_all_have_three_language_entries(self):
        for key, data in CANONICAL_NAMES.items():
            assert "en" in data["languages"], f"{key} missing English"
            assert "zh" in data["languages"], f"{key} missing Chinese"
            assert "fr" in data["languages"], f"{key} missing French"

    def test_all_have_aliases(self):
        for key, data in CANONICAL_NAMES.items():
            assert len(data["aliases"]) > 0, f"{key} has no aliases"

    def test_canonical_name_appears_in_aliases(self):
        for key, data in CANONICAL_NAMES.items():
            en_name = data["languages"]["en"].lower()
            zh_name = data["languages"]["zh"]
            fr_name = data["languages"]["fr"].lower()
            aliases_lower = [a.lower() for a in data["aliases"]]
            assert en_name in aliases_lower, f"{key}: English name not in aliases"
            assert zh_name in data["aliases"] or zh_name.lower() in aliases_lower, f"{key}: Chinese name not in aliases"
            assert fr_name in aliases_lower, f"{key}: French name not in aliases"
