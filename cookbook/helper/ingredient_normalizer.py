import re
from collections import defaultdict


CANONICAL_NAMES = {
    "tomato": {
        "languages": {"en": "tomato", "zh": "番茄", "fr": "tomate"},
        "aliases": [
            "tomato", "tomatoes", "tomatoe",
            "番茄", "西红柿", "蕃茄", "洋柿子",
            "tomate", "tomates",
            "cherry tomato", "cherry tomatoes", "圣女果", "小番茄", "tomate cerise",
        ],
    },
    "egg": {
        "languages": {"en": "egg", "zh": "鸡蛋", "fr": "œuf"},
        "aliases": [
            "egg", "eggs",
            "鸡蛋", "蛋", "鸡子", "鸡卵", "土鸡蛋", "柴鸡蛋",
            "œuf", "œufs", "oeuf", "oeufs",
            "鸡卵",
        ],
    },
    "onion": {
        "languages": {"en": "onion", "zh": "洋葱", "fr": "oignon"},
        "aliases": [
            "onion", "onions", "yellow onion", "white onion", "red onion",
            "洋葱", "洋蒜", "圆葱", "葱头", "元葱",
            "oignon", "oignons", "oignon jaune", "oignon rouge",
        ],
    },
    "garlic": {
        "languages": {"en": "garlic", "zh": "大蒜", "fr": "ail"},
        "aliases": [
            "garlic", "garlics", "garlic clove", "garlic cloves",
            "大蒜", "蒜", "蒜头", "蒜瓣", "紫皮蒜",
            "ail", "ails", "gousse d'ail", "gousses d'ail",
        ],
    },
    "carrot": {
        "languages": {"en": "carrot", "zh": "胡萝卜", "fr": "carotte"},
        "aliases": [
            "carrot", "carrots",
            "胡萝卜", "红萝卜", "甘荀", "黄萝卜",
            "carotte", "carottes",
        ],
    },
    "potato": {
        "languages": {"en": "potato", "zh": "土豆", "fr": "pomme de terre"},
        "aliases": [
            "potato", "potatoes", "potatoe",
            "土豆", "马铃薯", "洋芋", "地蛋", "薯仔",
            "pomme de terre", "pommes de terre", "patate", "patates",
        ],
    },
    "salt": {
        "languages": {"en": "salt", "zh": "盐", "fr": "sel"},
        "aliases": [
            "salt", "sea salt", "table salt", "kosher salt",
            "盐", "食盐", "海盐", "精盐", "粗盐", "咸盐",
            "sel", "sel de mer", "sel fin", "gros sel",
        ],
    },
    "sugar": {
        "languages": {"en": "sugar", "zh": "糖", "fr": "sucre"},
        "aliases": [
            "sugar", "sugars", "granulated sugar", "white sugar", "cane sugar",
            "糖", "白糖", "砂糖", "蔗糖", "白砂糖", "白绵糖",
            "sucre", "sucres", "sucre en poudre", "sucre blanc", "sucre de canne",
        ],
    },
    "flour": {
        "languages": {"en": "flour", "zh": "面粉", "fr": "farine"},
        "aliases": [
            "flour", "all-purpose flour", "ap flour", "bread flour", "wheat flour",
            "面粉", "小麦粉", "白面", "中筋面粉", "低筋面粉", "高筋面粉",
            "farine", "farine de blé", "farine tout usage", "farine T45", "farine T55",
        ],
    },
    "milk": {
        "languages": {"en": "milk", "zh": "牛奶", "fr": "lait"},
        "aliases": [
            "milk", "whole milk", "skim milk", "cow milk", "cow's milk",
            "牛奶", "奶", "牛乳", "鲜奶", "全脂牛奶", "脱脂牛奶",
            "lait", "lait entier", "lait écrémé", "lait de vache",
        ],
    },
    "butter": {
        "languages": {"en": "butter", "zh": "黄油", "fr": "beurre"},
        "aliases": [
            "butter", "unsalted butter", "salted butter",
            "黄油", "牛油", "奶油", "白脱",
            "beurre", "beurre doux", "beurre salé", "beurre de baratte",
        ],
    },
    "oil": {
        "languages": {"en": "oil", "zh": "油", "fr": "huile"},
        "aliases": [
            "oil", "olive oil", "vegetable oil", "canola oil", "sunflower oil", "cooking oil",
            "油", "食用油", "橄榄油", "菜籽油", "花生油", "玉米油", "葵花籽油",
            "huile", "huile d'olive", "huile végétale", "huile de colza", "huile de tournesol",
        ],
    },
    "chicken": {
        "languages": {"en": "chicken", "zh": "鸡肉", "fr": "poulet"},
        "aliases": [
            "chicken", "chickens", "chicken breast", "chicken thigh", "chicken drumstick",
            "鸡肉", "鸡", "鸡胸肉", "鸡腿", "鸡翅", "鸡块",
            "poulet", "poulets", "blanc de poulet", "cuisse de poulet", "aile de poulet",
        ],
    },
    "beef": {
        "languages": {"en": "beef", "zh": "牛肉", "fr": "bœuf"},
        "aliases": [
            "beef", "beefs", "ground beef", "beef steak", "steak",
            "牛肉", "牛", "牛排", "牛腩", "牛肉末", "肥牛",
            "bœuf", "boeuf", "bœufs", "boeufs", "steak de bœuf", "viande de bœuf",
            "hachis", "viande hachée",
        ],
    },
    "pork": {
        "languages": {"en": "pork", "zh": "猪肉", "fr": "porc"},
        "aliases": [
            "pork", "pork belly", "pork chop", "ground pork",
            "猪肉", "猪", "五花肉", "猪排", "猪里脊", "肉片",
            "porc", "porcs", "poitrine de porc", "côtelette de porc", "viande de porc",
        ],
    },
    "rice": {
        "languages": {"en": "rice", "zh": "米", "fr": "riz"},
        "aliases": [
            "rice", "white rice", "brown rice", "long grain rice", "jasmine rice",
            "米", "大米", "白米", "糙米", "粳米", "籼米", "糯米",
            "riz", "riz blanc", "riz brun", "riz long", "riz thaï",
        ],
    },
    "noodle": {
        "languages": {"en": "noodle", "zh": "面条", "fr": "nouille"},
        "aliases": [
            "noodle", "noodles", "pasta", "spaghetti", "macaroni",
            "面条", "面", "挂面", "拉面", "切面", "意面", "意大利面", "通心粉",
            "nouille", "nouilles", "pâte", "pâtes", "spaghetti", "macaroni",
        ],
    },
    "soy sauce": {
        "languages": {"en": "soy sauce", "zh": "酱油", "fr": "sauce soja"},
        "aliases": [
            "soy sauce", "soya sauce", "soy", "light soy sauce", "dark soy sauce",
            "酱油", "生抽", "老抽", "豉油", "豆酱油",
            "sauce soja", "sauce de soja", "soja", "sauce soja claire", "sauce soja foncée",
        ],
    },
    "vinegar": {
        "languages": {"en": "vinegar", "zh": "醋", "fr": "vinaigre"},
        "aliases": [
            "vinegar", "vinegars", "white vinegar", "apple cider vinegar", "balsamic vinegar",
            "醋", "食醋", "白醋", "陈醋", "米醋", "香醋",
            "vinaigre", "vinaigres", "vinaigre blanc", "vinaigre de cidre", "vinaigre balsamique",
        ],
    },
    "pepper": {
        "languages": {"en": "pepper", "zh": "胡椒", "fr": "poivre"},
        "aliases": [
            "pepper", "peppers", "black pepper", "white pepper", "ground pepper",
            "胡椒", "胡椒粉", "黑胡椒", "白胡椒",
            "poivre", "poivres", "poivre noir", "poivre blanc", "poivre moulu",
        ],
    },
    "spinach": {
        "languages": {"en": "spinach", "zh": "菠菜", "fr": "épinard"},
        "aliases": [
            "spinach", "spinaches", "baby spinach",
            "菠菜", "菠薐菜", "赤根菜",
            "épinard", "épinards", "spinard", "spinards", "épinard frais",
        ],
    },
    "mushroom": {
        "languages": {"en": "mushroom", "zh": "蘑菇", "fr": "champignon"},
        "aliases": [
            "mushroom", "mushrooms", "button mushroom", "shiitake mushroom", "cremini",
            "蘑菇", "菇", "香菇", "平菇", "金针菇", "杏鲍菇", "口蘑",
            "champignon", "champignons", "champignon de Paris", "shiitaké", "cèpe",
        ],
    },
    "cheese": {
        "languages": {"en": "cheese", "zh": "奶酪", "fr": "fromage"},
        "aliases": [
            "cheese", "cheeses", "parmesan", "mozzarella", "cheddar",
            "奶酪", "乳酪", "芝士", "起司", "干酪",
            "fromage", "fromages", "parmesan", "mozzarella", "cheddar",
        ],
    },
    "bread": {
        "languages": {"en": "bread", "zh": "面包", "fr": "pain"},
        "aliases": [
            "bread", "breads", "loaf", "sliced bread", "white bread", "whole wheat bread",
            "面包", "吐司", "土司", "切片面包", "白面包", "全麦面包",
            "pain", "pains", "miche", "pain de mie", "pain blanc", "pain complet",
        ],
    },
    "water": {
        "languages": {"en": "water", "zh": "水", "fr": "eau"},
        "aliases": [
            "water", "hot water", "cold water", "warm water",
            "水", "清水", "热水", "冷水", "温水",
            "eau", "eau chaude", "eau froide", "eau tiède",
        ],
    },
    "lemon": {
        "languages": {"en": "lemon", "zh": "柠檬", "fr": "citron"},
        "aliases": [
            "lemon", "lemons", "lemon juice", "lemon zest",
            "柠檬", "柠果", "柠檬汁", "柠檬皮",
            "citron", "citrons", "jus de citron", "zeste de citron",
        ],
    },
    "ginger": {
        "languages": {"en": "ginger", "zh": "姜", "fr": "gingembre"},
        "aliases": [
            "ginger", "ginger root", "fresh ginger", "ground ginger",
            "姜", "生姜", "老姜", "姜片", "姜丝", "姜末",
            "gingembre", "gingembre frais", "racine de gingembre", "gingembre moulu",
        ],
    },
    "cucumber": {
        "languages": {"en": "cucumber", "zh": "黄瓜", "fr": "concombre"},
        "aliases": [
            "cucumber", "cucumbers", "english cucumber",
            "黄瓜", "胡瓜", "青瓜",
            "concombre", "concombres", "concombre anglais",
        ],
    },
    "lettuce": {
        "languages": {"en": "lettuce", "zh": "生菜", "fr": "laitue"},
        "aliases": [
            "lettuce", "lettuces", "romaine lettuce", "iceberg lettuce", "green lettuce",
            "生菜", "莴苣", "叶用莴苣", "莴笋叶", "罗马生菜",
            "laitue", "laitues", "laitue romaine", "laitue iceberg",
        ],
    },
    "fish": {
        "languages": {"en": "fish", "zh": "鱼", "fr": "poisson"},
        "aliases": [
            "fish", "fishes", "salmon", "tuna", "cod", "tilapia",
            "鱼", "鱼肉", "三文鱼", "鲑鱼", "金枪鱼", "吞拿鱼", "鳕鱼", "罗非鱼",
            "poisson", "poissons", "saumon", "thon", "cabillaud", "tilapia",
        ],
    },
}


def _build_alias_map():
    alias_map = {}
    for canonical_key, data in CANONICAL_NAMES.items():
        canonical_name = data["languages"]["en"]
        for alias in data["aliases"]:
            normalized = _normalize_for_lookup(alias)
            if normalized and normalized not in alias_map:
                alias_map[normalized] = {
                    "canonical_key": canonical_key,
                    "canonical_name": canonical_name,
                    "languages": data["languages"],
                }
    return alias_map


def _normalize_for_lookup(text):
    if not text:
        return ""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s'\u4e00-\u9fff]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


_ALIAS_MAP = None


def _get_alias_map():
    global _ALIAS_MAP
    if _ALIAS_MAP is None:
        _ALIAS_MAP = _build_alias_map()
    return _ALIAS_MAP


def _try_plural_to_singular(word):
    """
    Try to convert a plural word to singular form for common patterns.
    Handles English and French patterns.
    """
    word = word.strip()
    if len(word) <= 3:
        return word

    en_singular = word
    if word.endswith('ies') and len(word) > 4:
        en_singular = word[:-3] + 'y'
    elif word.endswith('oes') and len(word) > 4:
        en_singular = word[:-2]
    elif word.endswith('es') and len(word) > 3:
        if word[-3] in ('s', 'x', 'z', 'c', 'h'):
            en_singular = word[:-2]
        else:
            en_singular = word[:-1]
    elif word.endswith('s') and not word.endswith('ss') and len(word) > 3:
        en_singular = word[:-1]

    fr_singular = word
    if word.endswith('aux'):
        fr_singular = word[:-3] + 'al'
    elif word.endswith('eaux'):
        fr_singular = word[:-2]
    elif word.endswith('s') and not word.endswith('ss') and len(word) > 3:
        fr_singular = word[:-1]

    candidates = [word]
    if en_singular != word:
        candidates.append(en_singular)
    if fr_singular != word and fr_singular not in candidates:
        candidates.append(fr_singular)
    return candidates


def _find_best_match(normalized_text, alias_map):
    """
    Find the best matching ingredient in the alias map.
    Tries multiple strategies: exact match, prefix match from any position,
    plural-to-singular conversion.

    Returns the mapping dict or None.
    """
    if not normalized_text:
        return None

    if normalized_text in alias_map:
        return alias_map[normalized_text]

    words = normalized_text.split()
    if not words:
        return None

    if len(words) == 1:
        singular_forms = _try_plural_to_singular(words[0])
        for form in singular_forms:
            if form in alias_map:
                return alias_map[form]
        return None

    best_match = None
    best_match_len = 0

    for start in range(len(words)):
        for end in range(len(words), start, -1):
            candidate = ' '.join(words[start:end])
            candidate_len = end - start

            if candidate_len <= best_match_len:
                break

            if candidate in alias_map:
                best_match = alias_map[candidate]
                best_match_len = candidate_len
                break

            if candidate_len == 1:
                singular_forms = _try_plural_to_singular(words[start])
                for form in singular_forms:
                    if form in alias_map:
                        best_match = alias_map[form]
                        best_match_len = 1
                        break

    return best_match


def normalize_ingredient_name(ingredient_name):
    """
    Normalize an ingredient name to its canonical form.

    Handles:
    - Multi-language aliases (English, Chinese, French)
    - Plural forms
    - Common variations and abbreviations
    - Compound names with modifiers (e.g., "yellow onion" -> "onion")
    - Ingredients embedded in longer descriptions

    Args:
        ingredient_name: The ingredient name string to normalize

    Returns:
        dict with:
            - canonical_name: The canonical English name
            - canonical_key: The canonical key identifier
            - languages: Dict of name translations (en, zh, fr)
            - is_mapped: Whether the name was mapped to a canonical form
            - original: The original input name
            - normalized: The normalized (standardized) form
    """
    result = {
        "canonical_name": ingredient_name if ingredient_name else "",
        "canonical_key": None,
        "languages": {},
        "is_mapped": False,
        "original": ingredient_name,
        "normalized": _normalize_for_lookup(ingredient_name),
    }

    if not ingredient_name or not ingredient_name.strip():
        return result

    alias_map = _get_alias_map()
    normalized = result["normalized"]

    match = _find_best_match(normalized, alias_map)
    if match:
        result["canonical_name"] = match["canonical_name"]
        result["canonical_key"] = match["canonical_key"]
        result["languages"] = match["languages"]
        result["is_mapped"] = True
        return result

    return result


def get_canonical_name(ingredient_name):
    """
    Simple helper: get just the canonical name for an ingredient.

    Args:
        ingredient_name: The ingredient name string

    Returns:
        The canonical name (string). Returns the original if no mapping found.
    """
    result = normalize_ingredient_name(ingredient_name)
    return result["canonical_name"]


def normalize_ingredient_list(ingredient_names):
    """
    Normalize a list of ingredient names, returning a set of canonical names.

    Args:
        ingredient_names: Iterable of ingredient name strings

    Returns:
        set of canonical ingredient names
    """
    canonical_names = set()
    for name in ingredient_names:
        canonical = get_canonical_name(name)
        if canonical:
            canonical_names.add(canonical.lower())
    return canonical_names


def get_all_supported_ingredients():
    """
    Get all supported canonical ingredient names.

    Returns:
        list of dicts with canonical_key, canonical_name, languages
    """
    result = []
    for key, data in CANONICAL_NAMES.items():
        result.append({
            "canonical_key": key,
            "canonical_name": data["languages"]["en"],
            "languages": data["languages"],
            "alias_count": len(data["aliases"]),
        })
    return sorted(result, key=lambda x: x["canonical_name"])


def get_ingredient_language(ingredient_name):
    """
    Attempt to detect the language of an ingredient name.

    Args:
        ingredient_name: The ingredient name string

    Returns:
        Language code string ('zh', 'en', 'fr', or 'unknown')
    """
    if not ingredient_name:
        return "unknown"

    if re.search(r"[\u4e00-\u9fff]", ingredient_name):
        return "zh"

    normalized = ingredient_name.lower().strip()
    fr_indicators = [
        "à", "â", "ç", "é", "è", "ê", "ë",
        "î", "ï", "ô", "œ", "ù", "û", "ü",
        "d'", "l'", "de ", "du ", "des ",
    ]
    for indicator in fr_indicators:
        if indicator in normalized:
            return "fr"

    if re.match(r"^[a-z\s']+$", normalized):
        return "en"

    return "unknown"
