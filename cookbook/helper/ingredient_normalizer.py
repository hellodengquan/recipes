import logging
import os
import threading
import time

import yaml

logger = logging.getLogger(__name__)

YAML_PATH = os.path.join(os.path.dirname(__file__), 'ingredient_aliases.yaml')

_canonical_names = None
_alias_map = None
_yaml_mtime = None
_lock = threading.Lock()
_poll_interval = 30
_watcher_started = False


def load_canonical_names_from_yaml(yaml_path=None):
    """
    Load canonical names dictionary from a YAML file.

    Args:
        yaml_path: Path to the YAML file. Defaults to the bundled ingredient_aliases.yaml.

    Returns:
        dict of canonical name entries
    """
    path = yaml_path or YAML_PATH
    if not os.path.exists(path):
        logger.warning(f'Ingredient aliases YAML file not found: {path}')
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            logger.error(f'Invalid YAML structure in {path}: expected dict, got {type(data)}')
            return {}
        valid_data = {}
        for key, entry in data.items():
            if not isinstance(entry, dict):
                logger.error(f'Invalid entry for key {key}: expected dict, got {type(entry)}')
                continue
            if 'languages' not in entry:
                logger.error(f'Missing languages in entry {key}')
                continue
            if 'aliases' not in entry:
                entry['aliases'] = []
            valid_data[key] = entry
        return valid_data
    except yaml.YAMLError as e:
        logger.error(f'YAML parse error in {path}: {e}')
        return {}
    except Exception as e:
        logger.error(f'Error loading YAML from {path}: {e}')
        return {}


def save_canonical_names_to_yaml(data, yaml_path=None):
    """
    Save canonical names dictionary to a YAML file.

    Args:
        data: dict of canonical name entries
        yaml_path: Path to the YAML file. Defaults to the bundled ingredient_aliases.yaml.

    Returns:
        True if save was successful, False otherwise
    """
    path = yaml_path or YAML_PATH
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            yaml.dump(
                data, f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=True,
            )
        _invalidate_cache()
        return True
    except Exception as e:
        logger.error(f'Error saving YAML to {path}: {e}')
        return False


def _build_alias_map(canonical_names):
    """
    Build a lookup map from canonical names data.

    Args:
        canonical_names: dict of canonical name entries

    Returns:
        dict mapping normalized alias strings to their canonical entry info
    """
    alias_map = {}
    for canonical_key, data in canonical_names.items():
        languages = data.get('languages', {})
        canonical_name = languages.get('en', canonical_key)
        aliases = data.get('aliases', [])
        for alias in aliases:
            normalized = _normalize_for_lookup(alias)
            if normalized and normalized not in alias_map:
                alias_map[normalized] = {
                    'canonical_key': canonical_key,
                    'canonical_name': canonical_name,
                    'languages': languages,
                }
    return alias_map


def _normalize_for_lookup(text):
    if not text:
        return ''
    import re
    text = text.lower().strip()
    text = re.sub(r"[^\w\s'\u4e00-\u9fff]", '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def _invalidate_cache():
    """
    Invalidate the in-memory cache so the next access reloads from YAML.
    Thread-safe.
    """
    global _canonical_names, _alias_map, _yaml_mtime
    with _lock:
        _canonical_names = None
        _alias_map = None
        _yaml_mtime = None


def _check_yaml_changed():
    """
    Check if the YAML file has been modified since last load.
    Returns True if the file mtime has changed.
    """
    global _yaml_mtime
    try:
        current_mtime = os.path.getmtime(YAML_PATH)
        if _yaml_mtime is None or current_mtime != _yaml_mtime:
            return True
    except OSError:
        pass
    return False


def _ensure_loaded():
    """
    Ensure the canonical names and alias map are loaded.
    Reloads automatically if the YAML file has been modified (hot reload).
    Thread-safe.
    """
    global _canonical_names, _alias_map, _yaml_mtime
    with _lock:
        if _canonical_names is not None and _alias_map is not None:
            if not _check_yaml_changed():
                return
        _canonical_names = load_canonical_names_from_yaml()
        _alias_map = _build_alias_map(_canonical_names)
        try:
            _yaml_mtime = os.path.getmtime(YAML_PATH)
        except OSError:
            _yaml_mtime = None


def get_canonical_names():
    """
    Get the current canonical names dictionary.
    Automatically reloads if the YAML file has changed.

    Returns:
        dict of canonical name entries
    """
    _ensure_loaded()
    return _canonical_names


def get_alias_map():
    """
    Get the current alias lookup map.
    Automatically reloads if the YAML file has changed.

    Returns:
        dict mapping normalized alias strings to their canonical entry info
    """
    _ensure_loaded()
    return _alias_map


def reload_aliases():
    """
    Force a reload of the aliases from the YAML file, regardless of mtime.
    Useful after programmatic changes via the API.
    """
    _invalidate_cache()
    _ensure_loaded()


def add_alias_entry(canonical_key, languages, aliases):
    """
    Add or update a canonical ingredient entry and persist to YAML.

    Args:
        canonical_key: The unique key for the ingredient (e.g., 'tomato')
        languages: Dict with language codes as keys and names as values (e.g., {'en': 'tomato', 'zh': '番茄', 'fr': 'tomate'})
        aliases: List of alias strings

    Returns:
        True if successful, False otherwise
    """
    data = get_canonical_names().copy()
    data[canonical_key] = {
        'languages': languages,
        'aliases': list(aliases),
    }
    return save_canonical_names_to_yaml(data)


def update_alias_entry(canonical_key, languages=None, aliases=None):
    """
    Update an existing canonical ingredient entry and persist to YAML.

    Args:
        canonical_key: The unique key for the ingredient
        languages: Optional dict of language updates (merged with existing)
        aliases: Optional list to replace the aliases entirely

    Returns:
        True if successful, False otherwise
        Raises ValueError if the canonical_key does not exist
    """
    data = get_canonical_names().copy()
    if canonical_key not in data:
        raise ValueError(f'Canonical key "{canonical_key}" does not exist')
    entry = data[canonical_key].copy()
    if languages is not None:
        merged_langs = dict(entry.get('languages', {}))
        merged_langs.update(languages)
        entry['languages'] = merged_langs
    if aliases is not None:
        entry['aliases'] = list(aliases)
    data[canonical_key] = entry
    return save_canonical_names_to_yaml(data)


def delete_alias_entry(canonical_key):
    """
    Delete a canonical ingredient entry and persist to YAML.

    Args:
        canonical_key: The unique key for the ingredient

    Returns:
        True if successful, False otherwise
        Raises ValueError if the canonical_key does not exist
    """
    data = get_canonical_names().copy()
    if canonical_key not in data:
        raise ValueError(f'Canonical key "{canonical_key}" does not exist')
    del data[canonical_key]
    return save_canonical_names_to_yaml(data)


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
        'canonical_name': ingredient_name if ingredient_name else '',
        'canonical_key': None,
        'languages': {},
        'is_mapped': False,
        'original': ingredient_name,
        'normalized': _normalize_for_lookup(ingredient_name),
    }

    if not ingredient_name or not ingredient_name.strip():
        return result

    alias_map = get_alias_map()
    normalized = result['normalized']

    match = _find_best_match(normalized, alias_map)
    if match:
        result['canonical_name'] = match['canonical_name']
        result['canonical_key'] = match['canonical_key']
        result['languages'] = match['languages']
        result['is_mapped'] = True
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
    return result['canonical_name']


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
        list of dicts with canonical_key, canonical_name, languages, aliases, alias_count
    """
    result = []
    for key, data in get_canonical_names().items():
        languages = data.get('languages', {})
        aliases = data.get('aliases', [])
        result.append({
            'canonical_key': key,
            'canonical_name': languages.get('en', key),
            'languages': languages,
            'aliases': aliases,
            'alias_count': len(aliases),
        })
    return sorted(result, key=lambda x: x['canonical_name'])


def get_ingredient_language(ingredient_name):
    """
    Attempt to detect the language of an ingredient name.

    Args:
        ingredient_name: The ingredient name string

    Returns:
        Language code string ('zh', 'en', 'fr', or 'unknown')
    """
    import re
    if not ingredient_name:
        return 'unknown'

    if re.search(r'[\u4e00-\u9fff]', ingredient_name):
        return 'zh'

    normalized = ingredient_name.lower().strip()
    fr_indicators = [
        'à', 'â', 'ç', 'é', 'è', 'ê', 'ë',
        'î', 'ï', 'ô', 'œ', 'ù', 'û', 'ü',
        "d'", "l'", 'de ', 'du ', 'des ',
    ]
    for indicator in fr_indicators:
        if indicator in normalized:
            return 'fr'

    if re.match(r"^[a-z\s']+$", normalized):
        return 'en'

    return 'unknown'


# Keep CANONICAL_NAMES as a property for backward compatibility with tests
class _CanonicalNamesProxy:
    def __iter__(self):
        return iter(get_canonical_names())

    def __contains__(self, item):
        return item in get_canonical_names()

    def items(self):
        return get_canonical_names().items()

    def __getitem__(self, key):
        return get_canonical_names()[key]

    def __len__(self):
        return len(get_canonical_names())

    def keys(self):
        return get_canonical_names().keys()

    def values(self):
        return get_canonical_names().values()


CANONICAL_NAMES = _CanonicalNamesProxy()
