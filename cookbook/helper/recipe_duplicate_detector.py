import re
from difflib import SequenceMatcher
from urllib.parse import urlparse, urlunparse

from django.contrib.postgres.search import TrigramSimilarity
from django.db.models import Q

from cookbook.models import Recipe


NAME_SIMILARITY_THRESHOLD = 0.85
URL_SIMILARITY_THRESHOLD = 0.90
INGREDIENT_SIMILARITY_THRESHOLD = 0.75


def normalize_name(name):
    if not name:
        return ''
    name = name.lower().strip()
    name = re.sub(r'[^\w\s]', '', name)
    name = re.sub(r'\s+', ' ', name)
    return name.strip()


def normalize_url(url):
    if not url:
        return ''
    try:
        parsed = urlparse(url.strip())
        scheme = parsed.scheme or 'https'
        netloc = parsed.netloc.lower()
        if netloc.startswith('www.'):
            netloc = netloc[4:]
        path = parsed.path.rstrip('/')
        if path.endswith('/index.html'):
            path = path[:-11]
        if path.endswith('.html'):
            path = path[:-5]
        normalized = urlunparse((scheme, netloc, path, '', '', ''))
        return normalized
    except Exception:
        return url.strip()


def extract_ingredient_names(ingredients_data):
    names = set()
    if not ingredients_data:
        return names
    for ing in ingredients_data:
        name = None
        if isinstance(ing, dict):
            food = ing.get('food', {})
            if isinstance(food, dict):
                name = food.get('name')
            elif hasattr(food, 'name'):
                name = food.name
            else:
                name = str(food) if food else None
            if not name:
                name = ing.get('original_text') or ing.get('note')
        elif hasattr(ing, 'food'):
            if hasattr(ing.food, 'name'):
                name = ing.food.name
            else:
                name = str(ing.food)
        if name:
            normalized = normalize_name(name)
            if normalized:
                names.add(normalized)
    return names


def get_recipe_ingredient_names(recipe):
    names = set()
    try:
        for step in recipe.steps.all():
            for ing in step.ingredients.all():
                if hasattr(ing, 'food') and ing.food:
                    normalized = normalize_name(ing.food.name)
                    if normalized:
                        names.add(normalized)
    except Exception:
        pass
    return names


def get_json_ingredient_names(recipe_json):
    all_names = set()
    steps = recipe_json.get('steps', [])
    for step in steps:
        ingredients = step.get('ingredients', [])
        all_names.update(extract_ingredient_names(ingredients))
    return all_names


def calculate_similarity(str1, str2):
    if not str1 or not str2:
        return 0.0
    return SequenceMatcher(None, str1, str2).ratio()


def calculate_set_similarity(set1, set2):
    if not set1 or not set2:
        return 0.0
    intersection = len(set1 & set2)
    union = len(set1 | set2)
    if union == 0:
        return 0.0
    return intersection / union


def _check_name_match(query_name, existing_recipes, use_trigram=True):
    normalized_query = normalize_name(query_name)
    if not normalized_query:
        return Recipe.objects.none()

    matches = []
    for recipe in existing_recipes:
        normalized_existing = normalize_name(recipe.name)
        if not normalized_existing:
            continue
        if normalized_query == normalized_existing:
            matches.append((recipe.pk, 1.0))
            continue
        similarity = calculate_similarity(normalized_query, normalized_existing)
        if similarity >= NAME_SIMILARITY_THRESHOLD:
            matches.append((recipe.pk, similarity))

    if use_trigram:
        try:
            trigram_matches = existing_recipes.annotate(
                similarity=TrigramSimilarity('name', query_name)
            ).filter(
                similarity__gte=NAME_SIMILARITY_THRESHOLD * 0.9
            ).values_list('id', 'similarity')
            for pk, sim in trigram_matches:
                if not any(m[0] == pk for m in matches):
                    matches.append((pk, sim))
        except Exception:
            pass

    matched_pks = [pk for pk, _ in matches]
    return existing_recipes.filter(pk__in=matched_pks)


def _check_url_match(query_url, existing_recipes):
    normalized_query = normalize_url(query_url)
    if not normalized_query:
        return Recipe.objects.none()

    matches = []
    for recipe in existing_recipes:
        if not recipe.source_url:
            continue
        normalized_existing = normalize_url(recipe.source_url)
        if not normalized_existing:
            continue
        if normalized_query == normalized_existing:
            matches.append(recipe.pk)
            continue
        similarity = calculate_similarity(normalized_query, normalized_existing)
        if similarity >= URL_SIMILARITY_THRESHOLD:
            matches.append(recipe.pk)

    return existing_recipes.filter(pk__in=matches)


def _check_ingredient_match(query_ingredient_names, existing_recipes):
    if not query_ingredient_names:
        return Recipe.objects.none()

    matches = []
    for recipe in existing_recipes:
        existing_names = get_recipe_ingredient_names(recipe)
        similarity = calculate_set_similarity(query_ingredient_names, existing_names)
        if similarity >= INGREDIENT_SIMILARITY_THRESHOLD:
            matches.append((recipe.pk, similarity))

    matched_pks = [pk for pk, _ in matches]
    return existing_recipes.filter(pk__in=matched_pks)


def find_duplicate_recipes(
    space,
    name=None,
    source_url=None,
    ingredient_names=None,
    recipe_json=None,
    require_name_match=True,
):
    """
    Find potentially duplicate recipes in the given space based on multiple signals.

    Args:
        space: Space object to search within
        name: Recipe name to check (string)
        source_url: Recipe source URL to check (string)
        ingredient_names: Set/list of ingredient name strings (pre-normalized)
        recipe_json: Optional recipe JSON dict containing name, source_url, and steps/ingredients
        require_name_match: If True, name match is required in combination with other signals

    Returns:
        QuerySet of Recipe objects that are potential duplicates
    """
    if recipe_json:
        if not name:
            name = recipe_json.get('name')
        if not source_url:
            source_url = recipe_json.get('source_url')
        if not ingredient_names:
            ingredient_names = get_json_ingredient_names(recipe_json)

    if isinstance(ingredient_names, list):
        ingredient_names = set(normalize_name(n) for n in ingredient_names if n)

    existing_recipes = Recipe.objects.filter(space=space)

    name_matches = Recipe.objects.none()
    url_matches = Recipe.objects.none()
    ingredient_matches = Recipe.objects.none()
    has_any_signal = False

    if name:
        name_matches = _check_name_match(name, existing_recipes)
        has_any_signal = True
    if source_url:
        url_matches = _check_url_match(source_url, existing_recipes)
        has_any_signal = True
    if ingredient_names:
        ingredient_matches = _check_ingredient_match(ingredient_names, existing_recipes)
        has_any_signal = True

    if not has_any_signal:
        return Recipe.objects.none()

    name_pks = set(name_matches.values_list('pk', flat=True))
    url_pks = set(url_matches.values_list('pk', flat=True))
    ingredient_pks = set(ingredient_matches.values_list('pk', flat=True))

    if require_name_match:
        combined_pks = name_pks & (url_pks | ingredient_pks)
        if not combined_pks:
            if len(name_pks) > 0 and (len(url_pks) + len(ingredient_pks) == 0):
                combined_pks = name_pks
    else:
        combined_pks = name_pks | url_pks | ingredient_pks
        strong_pks = (name_pks & url_pks) | (name_pks & ingredient_pks) | (url_pks & ingredient_pks)
        if strong_pks:
            combined_pks = strong_pks | combined_pks

    return existing_recipes.filter(pk__in=combined_pks)


def is_duplicate_recipe(
    space,
    name=None,
    source_url=None,
    ingredient_names=None,
    recipe_json=None,
    require_name_match=True,
):
    """
    Check if a recipe with the given attributes already exists in the space.

    Returns:
        tuple (is_duplicate: bool, duplicates: QuerySet)
    """
    duplicates = find_duplicate_recipes(
        space=space,
        name=name,
        source_url=source_url,
        ingredient_names=ingredient_names,
        recipe_json=recipe_json,
        require_name_match=require_name_match,
    )
    return duplicates.exists(), duplicates
