import re
import unicodedata
from collections import defaultdict

from django.contrib.postgres.search import TrigramSimilarity

from cookbook.models import Food, Unit


UNIT_ALIAS_TABLE = {
    'weight': {
        'g': ['g', 'gram', 'grams', 'gm', 'gms', 'gr', 'gramme', 'grammes'],
        'kg': ['kg', 'kgs', 'kilogram', 'kilograms', 'kilo', 'kilos', 'kilog'],
        'mg': ['mg', 'mgs', 'milligram', 'milligrams', 'millig'],
        'oz': ['oz', 'ounce', 'ounces', 'fl oz'],
        'lb': ['lb', 'lbs', 'pound', 'pounds', '#'],
        'st': ['st', 'stone', 'stones'],
    },
    'volume': {
        'ml': ['ml', 'mls', 'milliliter', 'milliliters', 'millilitre', 'millilitres', 'cc', 'cubic centimeter'],
        'l': ['l', 'liter', 'liters', 'litre', 'litres', 'ltr'],
        'tsp': ['tsp', 'tsps', 'teaspoon', 'teaspoons', 't', 'tspn', 'tea spoon', 't-spoon'],
        'tbsp': ['tbsp', 'tbsps', 'tablespoon', 'tablespoons', 'T', 'tblsp', 'tbs', 'tbl', 'table spoon', 't-spoon large'],
        'cup': ['cup', 'cups', 'c', 'cp', 'us cup'],
        'fl oz': ['fl oz', 'fluid ounce', 'fluid ounces', 'floz', 'fl. oz.', 'fluid oz'],
        'pint': ['pint', 'pints', 'pt', 'pts'],
        'quart': ['quart', 'quarts', 'qt', 'qts'],
        'gallon': ['gallon', 'gallons', 'gal', 'gals'],
        'dl': ['dl', 'dls', 'deciliter', 'deciliters', 'decilitre', 'decilitres'],
        'cl': ['cl', 'cls', 'centiliter', 'centiliters', 'centilitre', 'centilitres'],
    },
    'length': {
        'cm': ['cm', 'cms', 'centimeter', 'centimeters', 'centimetre', 'centimetres'],
        'mm': ['mm', 'mms', 'millimeter', 'millimeters', 'millimetre', 'millimetres'],
        'in': ['in', 'inch', 'inches', '"'],
    },
    'piece': {
        'piece': ['piece', 'pieces', 'pc', 'pcs', 'slice', 'slices', 'whole', 'item', 'items'],
        'can': ['can', 'cans', 'tin', 'tins'],
        'bunch': ['bunch', 'bunches', 'sprig', 'sprigs', 'handful'],
        'clove': ['clove', 'cloves'],
        'stick': ['stick', 'sticks'],
        'dash': ['dash', 'dashes', 'pinch', 'pinches', 'splash', 'hint'],
        'drop': ['drop', 'drops'],
        'large': ['large', 'lg', 'big'],
        'medium': ['medium', 'med', 'md'],
        'small': ['small', 'sm', 'little'],
    },
}


def _normalize_unit_alias(raw):
    s = raw.lower().strip()
    s = re.sub(r'[.\-_/]+', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


_FLAT_ALIAS_MAP = {}


def _build_flat_alias_map():
    global _FLAT_ALIAS_MAP
    _FLAT_ALIAS_MAP = {}
    for system, units in UNIT_ALIAS_TABLE.items():
        for canonical, aliases in units.items():
            _FLAT_ALIAS_MAP[_normalize_unit_alias(canonical)] = (system, canonical)
            for alias in aliases:
                _FLAT_ALIAS_MAP[_normalize_unit_alias(alias)] = (system, canonical)


_build_flat_alias_map()


def levenshtein_distance(s1, s2):
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row
    return prev_row[-1]


def normalized_levenshtein_ratio(s1, s2):
    s1, s2 = s1.lower().strip(), s2.lower().strip()
    if not s1 or not s2:
        return 0.0
    dist = levenshtein_distance(s1, s2)
    max_len = max(len(s1), len(s2))
    return 1.0 - (dist / max_len)


class UnitRecognitionHelper:

    EXACT_MATCH_SCORE = 1.0
    ALIAS_MATCH_SCORE = 0.95
    DB_MATCH_SCORE = 0.9
    DEFAULT_TRIGRAM_THRESHOLD = 0.6
    DEFAULT_LEVENSHTEIN_THRESHOLD = 0.7

    def __init__(self, space=None):
        self.space = space
        self._db_units = None
        self.trigram_threshold = self.DEFAULT_TRIGRAM_THRESHOLD
        self.levenshtein_threshold = self.DEFAULT_LEVENSHTEIN_THRESHOLD
        if space is not None:
            try:
                if space.import_review_trigram_threshold is not None:
                    self.trigram_threshold = float(space.import_review_trigram_threshold)
                if space.import_review_fuzzy_threshold is not None:
                    self.levenshtein_threshold = float(space.import_review_fuzzy_threshold)
            except Exception:
                pass

    def _get_db_units(self):
        if self._db_units is None and self.space:
            self._db_units = list(Unit.objects.filter(space=self.space))
        return self._db_units or []

    def recognize_unit(self, raw_name):
        if not raw_name or not raw_name.strip():
            return None

        normalized = _normalize_unit_alias(raw_name)

        if normalized in _FLAT_ALIAS_MAP:
            system, canonical = _FLAT_ALIAS_MAP[normalized]
            db_match = self._find_db_unit_by_name(canonical) or self._find_db_unit_by_name(normalized)
            return {
                'original': raw_name,
                'normalized': normalized,
                'canonical': canonical,
                'system': system,
                'confidence': self.EXACT_MATCH_SCORE,
                'match_method': 'alias_table',
                'db_unit_id': db_match.id if db_match else None,
                'db_unit_name': db_match.name if db_match else canonical,
            }

        db_unit = self._find_db_unit_by_name(raw_name)
        if db_unit:
            return {
                'original': raw_name,
                'normalized': normalized,
                'canonical': db_unit.name,
                'system': 'db',
                'confidence': self.DB_MATCH_SCORE,
                'match_method': 'db_exact',
                'db_unit_id': db_unit.id,
                'db_unit_name': db_unit.name,
            }

        best_alias = self._fuzzy_match_alias(normalized)
        if best_alias and best_alias['confidence'] >= self.levenshtein_threshold:
            return best_alias

        best_db = self._fuzzy_match_db(normalized)
        if best_db and best_db['confidence'] >= self.trigram_threshold:
            return best_db

        return {
            'original': raw_name,
            'normalized': normalized,
            'canonical': None,
            'system': None,
            'confidence': 0.0,
            'match_method': 'no_match',
            'db_unit_id': None,
            'db_unit_name': None,
        }

    def _find_db_unit_by_name(self, name):
        for u in self._get_db_units():
            if u.name.lower().strip() == name.lower().strip():
                return u
        return None

    def _fuzzy_match_alias(self, normalized):
        best = None
        best_score = 0.0
        for alias_key, (system, canonical) in _FLAT_ALIAS_MAP.items():
            score = normalized_levenshtein_ratio(normalized, alias_key)
            if score > best_score:
                best_score = score
                best = (system, canonical, alias_key)
        if best:
            system, canonical, matched_alias = best
            db_match = self._find_db_unit_by_name(canonical)
            return {
                'original': normalized,
                'normalized': normalized,
                'canonical': canonical,
                'system': system,
                'confidence': best_score,
                'match_method': 'edit_distance',
                'db_unit_id': db_match.id if db_match else None,
                'db_unit_name': db_match.name if db_match else canonical,
            }
        return None

    def _fuzzy_match_db(self, normalized):
        if not self.space:
            return None
        try:
            matches = (
                Unit.objects.filter(space=self.space)
                .annotate(similarity=TrigramSimilarity('name', normalized))
                .filter(similarity__gte=self.trigram_threshold)
                .order_by('-similarity')[:1]
            )
            for unit in matches:
                return {
                    'original': normalized,
                    'normalized': normalized,
                    'canonical': unit.name,
                    'system': 'db',
                    'confidence': unit.similarity,
                    'match_method': 'trigram',
                    'db_unit_id': unit.id,
                    'db_unit_name': unit.name,
                }
        except Exception:
            best = None
            best_score = 0.0
            for u in self._get_db_units():
                score = normalized_levenshtein_ratio(normalized, u.name)
                if score > best_score:
                    best_score = score
                    best = u
            if best and best_score >= self.levenshtein_threshold:
                return {
                    'original': normalized,
                    'normalized': normalized,
                    'canonical': best.name,
                    'system': 'db',
                    'confidence': best_score,
                    'match_method': 'edit_distance_db',
                    'db_unit_id': best.id,
                    'db_unit_name': best.name,
                }
        return None

    def scan_recipe_units(self, recipe_data):
        issues = []
        if not recipe_data or 'steps' not in recipe_data:
            return issues

        seen_units = {}
        for step_idx, step in enumerate(recipe_data.get('steps', [])):
            for ing_idx, ing in enumerate(step.get('ingredients', [])):
                unit_info = ing.get('unit')
                if not unit_info:
                    continue
                unit_name = unit_info.get('name', '') if isinstance(unit_info, dict) else str(unit_info)
                if not unit_name:
                    continue

                if unit_name not in seen_units:
                    result = self.recognize_unit(unit_name)
                    seen_units[unit_name] = result

                    if result['match_method'] == 'no_match':
                        issues.append({
                            'issue_type': 'UNIT_ERROR',
                            'severity': 'HIGH',
                            'message': f'Unrecognized unit "{unit_name}"',
                            'field_name': f'steps[{step_idx}].ingredients[{ing_idx}].unit',
                            'original_value': unit_name,
                            'suggested_value': None,
                        })
                    elif result['match_method'] == 'edit_distance':
                        issues.append({
                            'issue_type': 'UNIT_ERROR',
                            'severity': 'MEDIUM',
                            'message': f'Fuzzy matched unit "{unit_name}" -> "{result["canonical"]}" (confidence: {result["confidence"]:.0%})',
                            'field_name': f'steps[{step_idx}].ingredients[{ing_idx}].unit',
                            'original_value': unit_name,
                            'suggested_value': result['canonical'],
                        })
                    elif result['match_method'] == 'trigram':
                        issues.append({
                            'issue_type': 'UNIT_ERROR',
                            'severity': 'MEDIUM',
                            'message': f'Trigram matched unit "{unit_name}" -> "{result["canonical"]}" (confidence: {result["confidence"]:.0%})',
                            'field_name': f'steps[{step_idx}].ingredients[{ing_idx}].unit',
                            'original_value': unit_name,
                            'suggested_value': result['canonical'],
                        })
                    elif result['confidence'] < self.DB_MATCH_SCORE:
                        issues.append({
                            'issue_type': 'UNIT_ERROR',
                            'severity': 'LOW',
                            'message': f'Alias matched unit "{unit_name}" -> "{result["canonical"]}" (confidence: {result["confidence"]:.0%})',
                            'field_name': f'steps[{step_idx}].ingredients[{ing_idx}].unit',
                            'original_value': unit_name,
                            'suggested_value': result['canonical'],
                        })

        return issues


class FoodDeduplicationHelper:

    DEFAULT_TRIGRAM_THRESHOLD = 0.5
    DEFAULT_LEVENSHTEIN_THRESHOLD = 0.75
    DEFAULT_TIEBREAKER = 'LEX'

    STOP_WORDS = frozenset({
        'and', 'or', 'the', 'a', 'an', 'of', 'with', 'fresh', 'dried', 'chopped', 'sliced',
        'minced', 'grated', 'ground', 'whole', 'large', 'small', 'medium', 'finely', 'roughly',
    })

    def __init__(self, space=None):
        self.space = space
        self.trigram_threshold = self.DEFAULT_TRIGRAM_THRESHOLD
        self.levenshtein_threshold = self.DEFAULT_LEVENSHTEIN_THRESHOLD
        self.tiebreaker = self.DEFAULT_TIEBREAKER
        if space is not None:
            try:
                if space.import_review_trigram_threshold is not None:
                    self.trigram_threshold = float(space.import_review_trigram_threshold)
                if space.import_review_fuzzy_threshold is not None:
                    self.levenshtein_threshold = float(space.import_review_fuzzy_threshold)
                if space.import_review_food_tiebreaker:
                    self.tiebreaker = space.import_review_food_tiebreaker
            except Exception:
                pass

    def _normalize_food_name(self, name):
        s = name.lower().strip()
        s = unicodedata.normalize('NFKD', s)
        s = re.sub(r'[^\w\s]', '', s)
        s = re.sub(r'\s+', ' ', s).strip()
        tokens = [t for t in s.split() if t not in self.STOP_WORDS]
        return ' '.join(tokens)

    def _is_likely_variant(self, name1, name2):
        n1 = self._normalize_food_name(name1)
        n2 = self._normalize_food_name(name2)
        if n1 == n2:
            return True
        if n1 in n2 or n2 in n1:
            return True
        if len(n1) > 2 and len(n2) > 2 and levenshtein_distance(n1, n2) <= 1:
            return True
        tokens1 = set(n1.split())
        tokens2 = set(n2.split())
        if tokens1 and tokens2 and tokens1 == tokens2:
            return True
        if tokens1 and tokens2:
            overlap = tokens1 & tokens2
            if len(overlap) / max(len(tokens1), len(tokens2)) >= 0.8:
                return True
        if normalized_levenshtein_ratio(name1, name2) >= self.levenshtein_threshold:
            return True
        return False

    def find_duplicate_foods(self, recipe_data):
        if not recipe_data or 'steps' not in recipe_data:
            return []

        food_names = []
        for step in recipe_data.get('steps', []):
            for ing in step.get('ingredients', []):
                food_info = ing.get('food')
                if food_info:
                    name = food_info.get('name', '') if isinstance(food_info, dict) else str(food_info)
                    if name:
                        food_names.append(name)

        if not food_names:
            return []

        groups = self._cluster_food_names(food_names)
        issues = []
        for group in groups:
            if len(group) > 1:
                canonical = self._pick_canonical(group)
                issues.append({
                    'issue_type': 'DUPLICATE_FOOD',
                    'severity': 'MEDIUM',
                    'message': f'Similar ingredients detected: {", ".join(group)}',
                    'field_name': 'ingredients',
                    'original_value': ', '.join(g for g in group if g != canonical),
                    'suggested_value': canonical,
                    'tiebreaker': self.tiebreaker,
                })
        return issues

    def _pick_canonical(self, names):
        if not names:
            return None
        if len(names) == 1:
            return names[0]

        by_len = defaultdict(list)
        for name in names:
            by_len[len(self._normalize_food_name(name))].append(name)
        max_len = max(by_len.keys())
        candidates = by_len[max_len]
        if len(candidates) == 1:
            return candidates[0]

        if self.space:
            db_foods = {f.name: f for f in Food.objects.filter(space=self.space, name__in=candidates)}
        else:
            db_foods = {}

        if self.tiebreaker == 'ID':
            in_db = [db_foods[n] for n in candidates if n in db_foods]
            if in_db:
                return sorted(in_db, key=lambda f: f.id, reverse=True)[0].name
            return sorted(candidates, key=lambda n: (-len(n), n))[0]
        elif self.tiebreaker == 'CREATED_AT':
            in_db = [db_foods[n] for n in candidates if n in db_foods and hasattr(db_foods[n], 'created_at')]
            if in_db:
                return sorted(in_db, key=lambda f: f.created_at, reverse=True)[0].name
            return sorted(candidates, key=lambda n: (-len(n), n))[0]
        else:
            return sorted(candidates)[0]

    def _cluster_food_names(self, names):
        unique_names = list(dict.fromkeys(names))
        parent = {i: i for i in range(len(unique_names))}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for i in range(len(unique_names)):
            for j in range(i + 1, len(unique_names)):
                if self._is_likely_variant(unique_names[i], unique_names[j]):
                    union(i, j)

        clusters = defaultdict(list)
        for i in range(len(unique_names)):
            clusters[find(i)].append(unique_names[i])
        return list(clusters.values())

    def find_db_food_matches(self, food_name, limit=5):
        if not self.space:
            return []
        normalized = self._normalize_food_name(food_name)
        try:
            matches = (
                Food.objects.filter(space=self.space)
                .annotate(similarity=TrigramSimilarity('name', normalized))
                .filter(similarity__gte=self.trigram_threshold)
                .order_by('-similarity')[:limit]
            )
            return [{'id': f.id, 'name': f.name, 'similarity': f.similarity} for f in matches]
        except Exception:
            all_foods = list(Food.objects.filter(space=self.space))
            results = []
            for f in all_foods:
                score = normalized_levenshtein_ratio(normalized, self._normalize_food_name(f.name))
                if score >= self.levenshtein_threshold:
                    results.append({'id': f.id, 'name': f.name, 'similarity': score})
            results.sort(key=lambda x: x['similarity'], reverse=True)
            return results[:limit]


class ImageSourceHelper:

    PLACEHOLDER_STRATEGY = 'placeholder'
    MANUAL_UPLOAD_STRATEGY = 'manual_upload'
    API_FETCH_STRATEGY = 'api_fetch'

    DEFAULT_CONCURRENCY = 3
    DEFAULT_BATCH_RESULT_LIMIT = 100
    DEFAULT_TIMEOUT = 10

    def __init__(self, space=None):
        self.space = space
        self.concurrency = self.DEFAULT_CONCURRENCY
        self.batch_result_limit = self.DEFAULT_BATCH_RESULT_LIMIT
        self.timeout = self.DEFAULT_TIMEOUT
        if space is not None:
            try:
                if space.import_review_image_fetch_concurrency:
                    self.concurrency = max(1, int(space.import_review_image_fetch_concurrency))
                if space.import_review_batch_result_limit:
                    self.batch_result_limit = max(1, int(space.import_review_batch_result_limit))
            except Exception:
                pass

    def scan_missing_images(self, import_recipes):
        issues = []
        for recipe in import_recipes:
            has_image = bool(recipe.image_url)
            if not has_image and recipe.recipe_data:
                has_image = bool(recipe.recipe_data.get('imageUrl'))
            if not has_image:
                suggestions = self._suggest_image_sources(recipe)
                issues.append({
                    'import_recipe_id': recipe.id,
                    'issue_type': 'MISSING_IMAGE',
                    'severity': 'LOW',
                    'message': f'Recipe "{recipe.name}" has no image',
                    'field_name': 'image_url',
                    'original_value': None,
                    'suggested_value': suggestions[0] if suggestions else None,
                    'suggestions': suggestions,
                })
        return issues

    def _suggest_image_sources(self, import_recipe):
        suggestions = []
        if import_recipe.source_url:
            suggestions.append({
                'strategy': self.API_FETCH_STRATEGY,
                'description': f'Fetch from source page: {import_recipe.source_url}',
                'url': import_recipe.source_url,
            })
        if import_recipe.recipe_data and import_recipe.recipe_data.get('keywords'):
            for kw in import_recipe.recipe_data['keywords']:
                kw_name = kw.get('name', kw) if isinstance(kw, dict) else str(kw)
                suggestions.append({
                    'strategy': self.API_FETCH_STRATEGY,
                    'description': f'Search by keyword: {kw_name}',
                    'keyword': kw_name,
                })
        suggestions.append({
            'strategy': self.PLACEHOLDER_STRATEGY,
            'description': 'Use default placeholder image',
            'url': '/static/placeholder_recipe.png',
        })
        suggestions.append({
            'strategy': self.MANUAL_UPLOAD_STRATEGY,
            'description': 'Upload image manually',
        })
        return suggestions

    def fetch_image_from_url(self, url, timeout=10):
        import requests as http_requests
        try:
            response = http_requests.get(url, timeout=timeout, headers={
                'User-Agent': 'Mozilla/5.0 (compatible; TandoorRecipes/1.0)'
            })
            if response.status_code == 200:
                content_type = response.headers.get('Content-Type', '')
                if 'image' in content_type:
                    return {
                        'success': True,
                        'image_url': url,
                        'content_type': content_type,
                        'size': len(response.content),
                    }
            return {'success': False, 'error': f'HTTP {response.status_code}'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def fetch_image_from_source_page(self, source_url, timeout=10):
        import requests as http_requests
        from urllib.parse import urlparse
        try:
            response = http_requests.get(source_url, timeout=timeout, headers={
                'User-Agent': 'Mozilla/5.0 (compatible; TandoorRecipes/1.0)'
            })
            if response.status_code != 200:
                return {'success': False, 'error': f'HTTP {response.status_code}'}

            og_image_match = re.search(
                r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
                response.text
            )
            if og_image_match:
                img_url = og_image_match.group(1)
                if img_url.startswith('//'):
                    img_url = 'https:' + img_url
                elif img_url.startswith('/'):
                    parsed = urlparse(source_url)
                    img_url = f'{parsed.scheme}://{parsed.netloc}{img_url}'
                return {'success': True, 'image_url': img_url}

            img_matches = re.findall(
                r'<img[^>]+src=["\']([^"\']+\.(?:jpg|jpeg|png|webp))["\']',
                response.text, re.IGNORECASE
            )
            if img_matches:
                img_url = img_matches[0]
                if img_url.startswith('//'):
                    img_url = 'https:' + img_url
                elif img_url.startswith('/'):
                    parsed = urlparse(source_url)
                    img_url = f'{parsed.scheme}://{parsed.netloc}{img_url}'
                return {'success': True, 'image_url': img_url}

            return {'success': False, 'error': 'No image found on page'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def fetch_images_batch(self, import_recipes, page=1, page_size=None):
        """
        Batch fetch images for import recipes concurrently.
        Results are paginated according to the space's batch_result_limit.
        Returns a dict with counts and detailed results.
        """
        import concurrent.futures
        from collections import Counter

        if page_size is None:
            page_size = self.batch_result_limit

        def _fetch_one(recipe):
            if recipe.image_url:
                return {
                    'id': recipe.id,
                    'name': recipe.name,
                    'status': 'skipped',
                    'reason': 'already_has_image',
                }
            if not recipe.source_url:
                return {
                    'id': recipe.id,
                    'name': recipe.name,
                    'status': 'failed',
                    'reason': 'no_source_url',
                }
            result = self.fetch_image_from_source_page(recipe.source_url, timeout=self.timeout)
            if result.get('success'):
                return {
                    'id': recipe.id,
                    'name': recipe.name,
                    'status': 'fetched',
                    'image_url': result['image_url'],
                }
            return {
                'id': recipe.id,
                'name': recipe.name,
                'status': 'failed',
                'reason': result.get('error', 'fetch_error'),
            }

        total = len(import_recipes)
        worker_count = min(self.concurrency, max(1, total))

        fetched_count = 0
        failed_count = 0
        skipped_count = 0
        all_results = []

        if worker_count > 1 and total > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
                future_map = {executor.submit(_fetch_one, r): r for r in import_recipes}
                for future in concurrent.futures.as_completed(future_map):
                    try:
                        res = future.result()
                    except Exception as e:
                        r = future_map[future]
                        res = {
                            'id': r.id,
                            'name': r.name,
                            'status': 'failed',
                            'reason': str(e),
                        }
                    all_results.append(res)
                    if res['status'] == 'fetched':
                        fetched_count += 1
                    elif res['status'] == 'skipped':
                        skipped_count += 1
                    else:
                        failed_count += 1
        else:
            for r in import_recipes:
                res = _fetch_one(r)
                all_results.append(res)
                if res['status'] == 'fetched':
                    fetched_count += 1
                elif res['status'] == 'skipped':
                    skipped_count += 1
                else:
                    failed_count += 1

        page = max(1, int(page))
        start = (page - 1) * page_size
        end = start + page_size
        paged_results = all_results[start:end]
        status_counts = dict(Counter(r['status'] for r in all_results))

        return {
            'results': paged_results,
            'total': total,
            'total_pages': max(1, (total + page_size - 1) // page_size),
            'page': page,
            'page_size': page_size,
            'fetched_count': fetched_count,
            'failed_count': failed_count,
            'skipped_count': skipped_count,
            'status_counts': status_counts,
            'concurrency': worker_count,
            'batch_result_limit': self.batch_result_limit,
        }


def scan_import_recipe(import_recipe, space=None):
    all_issues = []

    unit_helper = UnitRecognitionHelper(space=space)
    unit_issues = unit_helper.scan_recipe_units(import_recipe.recipe_data)
    all_issues.extend(unit_issues)

    food_helper = FoodDeduplicationHelper(space=space)
    food_issues = food_helper.find_duplicate_foods(import_recipe.recipe_data)
    all_issues.extend(food_issues)

    has_image = bool(import_recipe.image_url)
    if not has_image and import_recipe.recipe_data:
        has_image = bool(import_recipe.recipe_data.get('imageUrl'))
    if not has_image:
        all_issues.append({
            'issue_type': 'MISSING_IMAGE',
            'severity': 'LOW',
            'message': f'Recipe "{import_recipe.name}" has no image',
            'field_name': 'image_url',
            'original_value': None,
            'suggested_value': None,
        })

    return all_issues
