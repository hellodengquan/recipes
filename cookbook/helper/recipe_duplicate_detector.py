import re
from difflib import SequenceMatcher
from urllib.parse import urlparse, urlunparse

from django.contrib.postgres.search import TrigramSimilarity
from django.db.models import Q

from cookbook.models import Recipe
from cookbook.helper.ingredient_normalizer import normalize_ingredient_name


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


def extract_ingredient_names(ingredients_data, use_normalization=True):
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
            if use_normalization:
                canonical = normalize_ingredient_name(name)
                if canonical['is_mapped']:
                    names.add(canonical['canonical_name'].lower())
                    continue
            normalized = normalize_name(name)
            if normalized:
                names.add(normalized)
    return names


def get_recipe_ingredient_names(recipe, use_normalization=True):
    names = set()
    try:
        for step in recipe.steps.all():
            for ing in step.ingredients.all():
                if hasattr(ing, 'food') and ing.food:
                    if use_normalization:
                        canonical = normalize_ingredient_name(ing.food.name)
                        if canonical['is_mapped']:
                            names.add(canonical['canonical_name'].lower())
                            continue
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


def _get_name_match_details(query_name, recipe, use_trigram=False):
    normalized_query = normalize_name(query_name)
    normalized_existing = normalize_name(recipe.name)
    if not normalized_query or not normalized_existing:
        return {
            'matched': False,
            'similarity': 0.0,
            'query_value': query_name or '',
            'existing_value': recipe.name or '',
            'normalized_query': normalized_query,
            'normalized_existing': normalized_existing,
        }

    similarity = calculate_similarity(normalized_query, normalized_existing)
    is_exact = normalized_query == normalized_existing

    return {
        'matched': similarity >= NAME_SIMILARITY_THRESHOLD,
        'similarity': round(similarity, 4),
        'is_exact': is_exact,
        'query_value': query_name or '',
        'existing_value': recipe.name or '',
        'normalized_query': normalized_query,
        'normalized_existing': normalized_existing,
    }


def _get_url_match_details(query_url, recipe):
    normalized_query = normalize_url(query_url)
    normalized_existing = normalize_url(recipe.source_url or '')
    if not normalized_query or not normalized_existing:
        return {
            'matched': False,
            'similarity': 0.0,
            'query_value': query_url or '',
            'existing_value': recipe.source_url or '',
            'normalized_query': normalized_query,
            'normalized_existing': normalized_existing,
        }

    similarity = calculate_similarity(normalized_query, normalized_existing)
    is_exact = normalized_query == normalized_existing

    return {
        'matched': similarity >= URL_SIMILARITY_THRESHOLD,
        'similarity': round(similarity, 4),
        'is_exact': is_exact,
        'query_value': query_url or '',
        'existing_value': recipe.source_url or '',
        'normalized_query': normalized_query,
        'normalized_existing': normalized_existing,
    }


def _get_ingredient_match_details(query_ingredient_names, recipe):
    existing_names = get_recipe_ingredient_names(recipe)
    if not query_ingredient_names or not existing_names:
        return {
            'matched': False,
            'similarity': 0.0,
            'query_count': len(query_ingredient_names) if query_ingredient_names else 0,
            'existing_count': len(existing_names),
            'matching_ingredients': [],
            'query_ingredients': sorted(list(query_ingredient_names)) if query_ingredient_names else [],
            'existing_ingredients': sorted(list(existing_names)),
        }

    similarity = calculate_set_similarity(query_ingredient_names, existing_names)
    matching = sorted(list(query_ingredient_names & existing_names))

    return {
        'matched': similarity >= INGREDIENT_SIMILARITY_THRESHOLD,
        'similarity': round(similarity, 4),
        'query_count': len(query_ingredient_names),
        'existing_count': len(existing_names),
        'matching_ingredients': matching,
        'matching_count': len(matching),
        'query_ingredients': sorted(list(query_ingredient_names)),
        'existing_ingredients': sorted(list(existing_names)),
    }


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


def get_duplicate_recipes_with_details(
    space,
    name=None,
    source_url=None,
    ingredient_names=None,
    recipe_json=None,
    require_name_match=True,
):
    """
    Find potentially duplicate recipes with detailed match evidence for each signal type.

    Args:
        space: Space object to search within
        name: Recipe name to check (string)
        source_url: Recipe source URL to check (string)
        ingredient_names: Set/list of ingredient name strings (pre-normalized)
        recipe_json: Optional recipe JSON dict containing name, source_url, and steps/ingredients
        require_name_match: If True, name match is required in combination with other signals

    Returns:
        list of dicts with recipe info and detailed match evidence:
        [
            {
                'id': recipe_id,
                'name': recipe_name,
                'source_url': recipe_source_url,
                'overall_similarity': float,
                'name_match': { 'matched': bool, 'similarity': float, ... },
                'url_match': { 'matched': bool, 'similarity': float, ... },
                'ingredient_match': { 'matched': bool, 'similarity': float, ... },
                'matched_signals': ['name', 'url', 'ingredient'],
            },
            ...
        ]
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

    duplicates = find_duplicate_recipes(
        space=space,
        name=name,
        source_url=source_url,
        ingredient_names=ingredient_names,
        require_name_match=require_name_match,
    )

    results = []
    for recipe in duplicates:
        name_detail = _get_name_match_details(name, recipe) if name else {
            'matched': False, 'similarity': 0.0, 'query_value': '', 'existing_value': recipe.name or ''
        }
        url_detail = _get_url_match_details(source_url, recipe) if source_url else {
            'matched': False, 'similarity': 0.0, 'query_value': '', 'existing_value': recipe.source_url or ''
        }
        ingredient_detail = _get_ingredient_match_details(ingredient_names, recipe) if ingredient_names else {
            'matched': False, 'similarity': 0.0, 'query_count': 0, 'existing_count': 0, 'matching_ingredients': []
        }

        matched_signals = []
        signal_weights = []
        if name_detail.get('matched'):
            matched_signals.append('name')
            signal_weights.append(name_detail.get('similarity', 0))
        if url_detail.get('matched'):
            matched_signals.append('url')
            signal_weights.append(url_detail.get('similarity', 0))
        if ingredient_detail.get('matched'):
            matched_signals.append('ingredient')
            signal_weights.append(ingredient_detail.get('similarity', 0))

        overall_similarity = sum(signal_weights) / len(signal_weights) if signal_weights else 0.0

        results.append({
            'id': recipe.pk,
            'name': recipe.name,
            'source_url': recipe.source_url or '',
            'overall_similarity': round(overall_similarity, 4),
            'name_match': name_detail,
            'url_match': url_detail,
            'ingredient_match': ingredient_detail,
            'matched_signals': matched_signals,
            'match_count': len(matched_signals),
        })

    results.sort(key=lambda x: (-x['match_count'], -x['overall_similarity']))

    return results


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
