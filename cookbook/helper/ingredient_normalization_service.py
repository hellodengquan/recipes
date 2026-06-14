import hashlib
import logging
import re
import string
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional, Tuple, Union

from django.conf import settings
from django.db.models import Q

from cookbook.helper.automation_helper import AutomationEngine
from cookbook.helper.unit_conversion_helper import ConversionException, UnitConversionHelper
from cookbook.models import Food, Ingredient, Unit

logger = logging.getLogger(__name__)


@dataclass
class NormalizedIngredient:
    amount: Decimal = Decimal('0')
    unit: Optional[Unit] = None
    food: Optional[Food] = None
    note: str = ''
    original_text: str = ''
    unit_name: str = ''
    food_name: str = ''


class IngredientNormalizationService:
    request = None
    space = None
    automation = None
    unit_conversion = None
    ignore_automations = False
    use_cache = True
    amount_decimal_places = 4
    max_food_name_length = 128
    max_note_length = 256
    merge_notes = True
    auto_unit_conversion = False
    preferred_units = []
    enable_multilingual = True
    fallback_enabled = True
    rollback_on_error = True
    grayscale_percent = 100
    log_comparison = False
    cache_ttl = 86400
    cache_version = 1
    nlp_chinese_backend = 'auto'
    nlp_japanese_backend = 'auto'

    _nlp_chinese_available = None
    _nlp_japanese_available = None
    _jieba = None
    _mecab = None

    CACHE_KEY_PREFIX = 'ing_norm_v'

    def __init__(self, request=None, space=None, use_cache=None, ignore_automations=None):
        self.request = request
        self.space = space or getattr(request, 'space', None)

        self.use_cache = use_cache if use_cache is not None else getattr(
            settings, 'INGREDIENT_NORMALIZATION_USE_CACHE', True)
        self.ignore_automations = ignore_automations if ignore_automations is not None else not getattr(
            settings, 'INGREDIENT_NORMALIZATION_ENABLE_AUTOMATIONS', True)

        self.amount_decimal_places = getattr(
            settings, 'INGREDIENT_NORMALIZATION_AMOUNT_DECIMAL_PLACES', 4)
        self.max_food_name_length = getattr(
            settings, 'INGREDIENT_NORMALIZATION_MAX_FOOD_NAME_LENGTH', 128)
        self.max_note_length = getattr(
            settings, 'INGREDIENT_NORMALIZATION_MAX_NOTE_LENGTH', 256)
        self.merge_notes = getattr(
            settings, 'INGREDIENT_NORMALIZATION_MERGE_NOTES', True)
        self.auto_unit_conversion = getattr(
            settings, 'INGREDIENT_NORMALIZATION_AUTO_UNIT_CONVERSION', False)
        self.preferred_units = getattr(
            settings, 'INGREDIENT_NORMALIZATION_PREFERRED_UNITS', [])
        self.enable_multilingual = getattr(
            settings, 'INGREDIENT_NORMALIZATION_ENABLE_MULTILINGUAL', True)
        self.fallback_enabled = getattr(
            settings, 'INGREDIENT_NORMALIZATION_FALLBACK_ENABLED', True)
        self.rollback_on_error = getattr(
            settings, 'INGREDIENT_NORMALIZATION_FALLBACK_ROLLBACK_ON_ERROR', True)
        self.grayscale_percent = max(0, min(100, getattr(
            settings, 'INGREDIENT_NORMALIZATION_GRAYSCALE_PERCENT', 100)))
        self.log_comparison = getattr(
            settings, 'INGREDIENT_NORMALIZATION_LOG_COMPARISON', False)
        self.cache_ttl = getattr(
            settings, 'INGREDIENT_NORMALIZATION_CACHE_TTL', 86400)
        self.cache_version = getattr(
            settings, 'INGREDIENT_NORMALIZATION_CACHE_VERSION', 1)
        self.nlp_chinese_backend = getattr(
            settings, 'INGREDIENT_NORMALIZATION_NLP_CHINESE_BACKEND', 'auto')
        self.nlp_japanese_backend = getattr(
            settings, 'INGREDIENT_NORMALIZATION_NLP_JAPANESE_BACKEND', 'auto')

        if not self.ignore_automations and self.request:
            self.automation = AutomationEngine(self.request, use_cache=self.use_cache)

        if self.space:
            self.unit_conversion = UnitConversionHelper(self.space)

    def parse_fraction(self, x: str) -> float:
        if len(x) == 1 and 'fraction' in unicodedata.decomposition(x):
            frac_split = unicodedata.decomposition(x[-1:]).split()
            return float((frac_split[1]).replace('003', '')) / float((frac_split[3]).replace('003', ''))
        else:
            frac_split = x.split('/')
            if not len(frac_split) == 2:
                raise ValueError
            try:
                return int(frac_split[0]) / int(frac_split[1])
            except ZeroDivisionError:
                raise ValueError

    def parse_amount(self, amount_str: str) -> Tuple[Decimal, str, str]:
        amount = Decimal('0')
        unit = None
        note = ''

        if not amount_str or amount_str.strip() == '':
            return amount, unit, note

        x = amount_str.strip()
        did_check_frac = False
        end = 0

        while end < len(x) and (
            x[end] in string.digits
            or (
                (x[end] == '.' or x[end] == ',' or x[end] == '/')
                and end + 1 < len(x)
                and x[end + 1] in string.digits
            )
        ):
            end += 1

        if end > 0:
            if "/" in x[:end]:
                amount = Decimal(str(self.parse_fraction(x[:end])))
            else:
                amount = Decimal(x[:end].replace(',', '.'))
        else:
            amount = Decimal(str(self.parse_fraction(x[0])))
            end += 1
            did_check_frac = True

        if end < len(x):
            if did_check_frac:
                unit = x[end:]
            else:
                try:
                    amount += Decimal(str(self.parse_fraction(x[end])))
                    unit = x[end + 1:]
                except ValueError:
                    unit = x[end:]

        if unit is not None and unit.strip() == '':
            unit = None

        if unit is not None and (unit.startswith('(') or unit.startswith('-')):
            unit = None
            note = x

        return amount, unit, note

    def normalize_amount(self, amount: Union[float, int, str, Decimal]) -> Decimal:
        if amount is None:
            return Decimal('0')

        if isinstance(amount, Decimal):
            d = amount
        elif isinstance(amount, (int, float)):
            d = Decimal(str(amount))
        elif isinstance(amount, str):
            amount = amount.strip()
            if amount == '':
                return Decimal('0')
            try:
                d = Decimal(amount.replace(',', '.'))
            except Exception:
                try:
                    d = Decimal(str(self.parse_fraction(amount)))
                except Exception:
                    return Decimal('0')
        else:
            return Decimal('0')

        quantize_str = '0.' + '0' * self.amount_decimal_places
        d = d.quantize(Decimal(quantize_str), rounding=ROUND_HALF_UP)

        if d == d.to_integral_value():
            d = Decimal(str(int(d)))

        return d

    def is_chinese_nlp_available(self) -> bool:
        if IngredientNormalizationService._nlp_chinese_available is not None:
            return IngredientNormalizationService._nlp_chinese_available
        try:
            if self.nlp_chinese_backend == 'none':
                IngredientNormalizationService._nlp_chinese_available = False
            elif self.nlp_chinese_backend in ('auto', 'jieba'):
                import jieba as _jieba  # noqa: F401
                IngredientNormalizationService._jieba = _jieba
                IngredientNormalizationService._nlp_chinese_available = True
            else:
                IngredientNormalizationService._nlp_chinese_available = False
        except ImportError:
            IngredientNormalizationService._nlp_chinese_available = False
        return IngredientNormalizationService._nlp_chinese_available

    def is_japanese_nlp_available(self) -> bool:
        if IngredientNormalizationService._nlp_japanese_available is not None:
            return IngredientNormalizationService._nlp_japanese_available
        try:
            if self.nlp_japanese_backend == 'none':
                IngredientNormalizationService._nlp_japanese_available = False
            elif self.nlp_japanese_backend in ('auto', 'mecab'):
                from MeCab import Tagger as _Tagger  # noqa: F401
                IngredientNormalizationService._mecab = _Tagger
                IngredientNormalizationService._nlp_japanese_available = True
            else:
                IngredientNormalizationService._nlp_japanese_available = False
        except ImportError:
            IngredientNormalizationService._nlp_japanese_available = False
        return IngredientNormalizationService._nlp_japanese_available

    def _get_cache(self):
        try:
            from django.core.cache import caches
            return caches['default']
        except Exception:
            return None

    def get_effective_cache_version(self) -> int:
        """
        Get the effective cache version by combining the settings version
        with the Redis global version. This ensures:
        - When a new worker starts with a different CACHE_VERSION setting,
          its cache keys differ from old workers (graceful upgrade).
        - When bump_cache_version() is called (e.g. after automation change),
          all workers immediately see the new version via Redis.
        - Old workers with stale code naturally miss cache and recompute.
        """
        base_ver = self.cache_version
        cache = self._get_cache()
        if not cache:
            return base_ver
        try:
            global_ver = cache.get(f'{self.CACHE_KEY_PREFIX}_global_version')
            if global_ver is not None:
                return base_ver + int(global_ver)
        except Exception:
            pass
        return base_ver

    def _get_cache_key(self, ingredient_text: str) -> Optional[str]:
        if not self.space:
            return None
        hashed = hashlib.sha256(ingredient_text.encode('utf-8')).hexdigest()
        ver = self.get_effective_cache_version()
        return f'{self.CACHE_KEY_PREFIX}{ver}_sp{self.space.id}_{hashed}'

    def _get_cached_normalized(self, ingredient_text: str) -> Optional[NormalizedIngredient]:
        if not self.use_cache:
            return None
        cache = self._get_cache()
        if not cache:
            return None
        key = self._get_cache_key(ingredient_text)
        if not key:
            return None
        try:
            data = cache.get(key)
            if not data:
                return None
            return self._dict_to_normalized(data)
        except Exception as e:
            logger.debug('Cache read failed: %s', str(e))
            return None

    def _set_cached_normalized(self, ingredient_text: str, normalized: NormalizedIngredient):
        if not self.use_cache:
            return
        cache = self._get_cache()
        if not cache:
            return
        key = self._get_cache_key(ingredient_text)
        if not key:
            return
        try:
            data = self._normalized_to_dict(normalized)
            cache.set(key, data, self.cache_ttl)
        except Exception as e:
            logger.debug('Cache write failed: %s', str(e))

    def _normalized_to_dict(self, n: NormalizedIngredient) -> dict:
        return {
            'a': str(n.amount),
            'u_id': n.unit.id if n.unit else None,
            'u_name': n.unit_name,
            'f_id': n.food.id if n.food else None,
            'f_name': n.food_name,
            'note': n.note,
            'orig': n.original_text,
        }

    def _dict_to_normalized(self, d: dict) -> NormalizedIngredient:
        unit = None
        food = None
        if self.space and d.get('u_id'):
            try:
                unit = Unit.objects.filter(id=d['u_id'], space=self.space).first()
            except Exception:
                unit = None
        if self.space and d.get('f_id'):
            try:
                food = Food.objects.filter(id=d['f_id'], space=self.space).first()
            except Exception:
                food = None
        return NormalizedIngredient(
            amount=Decimal(d.get('a', '0')),
            unit=unit,
            food=food,
            note=d.get('note', ''),
            original_text=d.get('orig', ''),
            unit_name=d.get('u_name', ''),
            food_name=d.get('f_name', ''),
        )

    @classmethod
    def invalidate_space_cache(cls, space_id: int):
        """
        Invalidate all cached normalization results for a specific space/tenant.
        Uses delete_pattern when available (Redis), otherwise falls back
        to version bump which achieves the same effect across all workers.
        """
        try:
            from django.core.cache import caches
            cache = caches['default']
            pattern = f'{cls.CACHE_KEY_PREFIX}*_sp{space_id}_*'
            try:
                cache.delete_pattern(pattern)
                logger.info('Invalidated space cache for space_id=%d via delete_pattern', space_id)
            except (AttributeError, NotImplementedError):
                cls.bump_cache_version()
                logger.info('Invalidated space cache for space_id=%d via version bump', space_id)
        except Exception as e:
            logger.debug('Cache invalidation failed: %s', str(e))

    @classmethod
    def invalidate_on_automation_change(cls, space_id: int):
        """
        Called when automation rules change for a space.
        Invalidates cached results that depend on automation output.
        Since automation results are baked into cache keys via version,
        bumping the version ensures all workers recalculate.
        """
        cls.invalidate_space_cache(space_id)

    @classmethod
    def bump_cache_version(cls, delta: int = 1) -> int:
        """
        Atomically increment the global cache version in Redis.
        All workers will see the new version on their next cache key
        computation, causing a natural cache miss and recomputation.
        Old cache entries expire naturally via TTL.

        This is the primary mechanism for cross-worker cache invalidation:
        - No distributed locks needed
        - No need to delete individual keys
        - Old workers with stale code get different keys automatically
        - Old cache entries expire via TTL (no manual cleanup needed)
        """
        from django.core.cache import caches
        cache = caches['default']
        ver_key = f'{cls.CACHE_KEY_PREFIX}_global_version'
        try:
            new_ver = cache.incr(ver_key, delta)
            logger.info('Cache version bumped to %d', new_ver)
        except Exception:
            new_ver = getattr(settings, 'INGREDIENT_NORMALIZATION_CACHE_VERSION', 1) + delta
            try:
                cache.set(ver_key, new_ver, 60 * 60 * 24 * 365)
                logger.info('Cache version initialized to %d', new_ver)
            except Exception:
                pass
        return new_ver

    @classmethod
    def get_invalidation_strategy(cls) -> dict:
        """
        Returns a dict documenting the cache invalidation strategy.
        Useful for operational runbooks and debugging.
        """
        return {
            'key_format': 'ing_norm_v{VERSION}_sp{SPACE_ID}_{SHA256(TEXT)}',
            'version_sources': [
                'INGREDIENT_NORMALIZATION_CACHE_VERSION (settings/env)',
                'Redis global_version (bumped atomically on change)',
            ],
            'invalidation_triggers': {
                'automation_rule_change': 'invalidate_on_automation_change(space_id) - bumps version for space',
                'tenant_data_change': 'invalidate_space_cache(space_id) - delete_pattern or version bump',
                'code_upgrade': 'bump_cache_version() - global version bump, all workers see new keys',
                'dictionary_version_switch': 'bump_cache_version() - same as code upgrade',
                'settings_change': 'INGREDIENT_NORMALIZATION_CACHE_VERSION env change - new keys on restart',
            },
            'old_worker_fallback': {
                'mechanism': 'Old workers use old cache_version in key, naturally miss new-version cache',
                'result': 'Old workers recompute and write to old keys; new workers write to new keys',
                'cleanup': 'Old keys expire via INGREDIENT_NORMALIZATION_CACHE_TTL (default 24h)',
                'convergence': 'All workers converge once redeployed with new CACHE_VERSION setting',
            },
            'ttl': 'INGREDIENT_NORMALIZATION_CACHE_TTL (default 86400 seconds / 24 hours)',
        }

    def normalize_name(self, name: str) -> str:
        if not name:
            return ''

        name = name.strip()
        name = re.sub(r'\s+', ' ', name)
        name = name.strip(' ,.;:')

        return name

    def normalize_food_name(self, food_name: str) -> str:
        if not food_name:
            return ''

        food_name = self.normalize_name(food_name)

        if not self.ignore_automations and self.automation:
            food_name = self.automation.apply_food_automation(food_name)

        return food_name.strip()

    def normalize_unit_name(self, unit_name: str) -> str:
        if not unit_name:
            return ''

        unit_name = self.normalize_name(unit_name)

        if not self.ignore_automations and self.automation:
            unit_name = self.automation.apply_unit_automation(unit_name)

        return unit_name.strip()

    @staticmethod
    def _normalize_fullwidth_chars(text: str) -> str:
        if not text:
            return text
        result = []
        for char in text:
            code = ord(char)
            if 0xFF01 <= code <= 0xFF5E:
                result.append(chr(code - 0xFEE0))
            elif code == 0x3000:
                result.append(' ')
            else:
                result.append(char)
        return ''.join(result)

    def get_or_create_food(self, food_name: str) -> Optional[Food]:
        if not food_name or not food_name.strip():
            return None

        food_name = self.normalize_food_name(food_name)

        if not food_name or not self.space:
            return None

        food_obj = Food.objects.filter(space=self.space).filter(
            Q(name=food_name) | Q(plural_name=food_name)
        ).first()

        if not food_obj:
            food_obj = Food.objects.create(space=self.space, name=food_name)

        return food_obj

    def get_or_create_unit(self, unit_name: str) -> Optional[Unit]:
        if not unit_name or not unit_name.strip():
            return None

        unit_name = self.normalize_unit_name(unit_name)

        if not unit_name or not self.space:
            return None

        unit_obj = Unit.objects.filter(space=self.space).filter(
            Q(name=unit_name) | Q(plural_name=unit_name)
        ).first()

        if not unit_obj:
            unit_obj = Unit.objects.create(space=self.space, name=unit_name)

        return unit_obj

    def parse_food_with_comma(self, tokens: List[str]) -> Tuple[str, str]:
        food = ''
        note = ''
        start = 0

        end_punctuations = (',', ';', ':', '，', '；', '：')

        while start < len(tokens) and not any(tokens[start].endswith(p) for p in end_punctuations):
            start += 1

        if start == len(tokens):
            food = ' '.join(tokens)
        else:
            last_token = tokens[start]
            for p in end_punctuations:
                if last_token.endswith(p):
                    last_token = last_token[:-len(p)]
                    break
            food = ' '.join(tokens[:start] + [last_token])
            note = ' '.join(tokens[start + 1:])

        return food, note

    def parse_food(self, tokens: List[str]) -> Tuple[str, str]:
        food = ''
        note = ''

        bracket_pairs = [('(', ')'), ('（', '）')]

        last_token = tokens[-1]
        found_close = None
        found_open = None

        for open_b, close_b in bracket_pairs:
            if last_token.endswith(close_b):
                found_close = close_b
                found_open = open_b
                break

        if found_close:
            if not last_token.startswith(found_open) and found_open in last_token:
                idx = last_token.rfind(found_open)
                food_part = last_token[:idx]
                note_part = last_token[idx + len(found_open):-len(found_close)]

                if len(tokens) > 1:
                    food = ' '.join(tokens[:-1]) + ' ' + food_part
                else:
                    food = food_part
                note = note_part
                return food.strip(), note.strip()

            start = len(tokens) - 1
            while not tokens[start].startswith(found_open) and not start == 0:
                start -= 1

            if start == 0:
                raise ValueError
            elif start < 0:
                food, note = self.parse_food_with_comma(tokens)
            else:
                note_text = ' '.join(tokens[start:])
                note = note_text[len(found_open):-len(found_close)]
                food = ' '.join(tokens[:start])
        else:
            food, note = self.parse_food_with_comma(tokens)

        return food, note

    def parse_ingredient_string(self, ingredient_str: str) -> Tuple[Decimal, Optional[str], str, str]:
        amount = Decimal('0')
        unit = None
        food = ''
        note = ''
        unit_note = ''

        ingredient = ingredient_str.strip()

        if len(ingredient) == 0:
            raise ValueError('string to parse cannot be empty')

        if self.enable_multilingual:
            ingredient = self._normalize_fullwidth_chars(ingredient)

        if len(ingredient) > 512:
            raise ValueError('cannot parse ingredients with more than 512 characters')

        ingredient = re.sub(r"^[,.\-_=+#*|\\/]+", "", ingredient)

        if len(ingredient) < 1000 and re.search(r'^([^\W\d_])+(.)*[1-9](\d)*\s*([^\W\d_])+', ingredient):
            match = re.search(r'[1-9](\d)*\s*([^\W\d_])+', ingredient)
            ingredient = ingredient[match.start():match.end()] + ' ' + ingredient.replace(ingredient[match.start():match.end()], '')

        if re.match('(.){1,6}\\s\\((.[^\\(\\)])+\\)\\s', ingredient):
            match = re.search('\\((.[^\\(])+\\)', ingredient)
            ingredient = ingredient[:match.start()] + ingredient[match.end():] + ' ' + ingredient[match.start():match.end()]

        ingredient = ingredient.replace(' ,', ',')

        ingredient = re.sub(
            "^(\\d+|\\d+[\\.,]\\d+) - (\\d+|\\d+[\\.,]\\d+) (.*)",
            "\\1 \\3 (\\1 - \\2)",
            ingredient
        )

        if re.match('([0-9])+([A-z])+\\s', ingredient):
            ingredient = re.sub(r'(?<=([a-z])|\d)(?=(?(1)\d|[a-z]))', ' ', ingredient)

        if not self.ignore_automations and self.automation:
            ingredient = self.automation.apply_transpose_automation(ingredient)

        tokens = ingredient.split()

        if len(tokens) == 1:
            food = tokens[0]
        else:
            try:
                amount, unit, unit_note = self.parse_amount(tokens[0])
                amount = self.normalize_amount(amount)

                if len(tokens) > 2:
                    never_unit_applied = False
                    if not self.ignore_automations and self.automation:
                        tokens, never_unit_applied = self.automation.apply_never_unit_automation(tokens)

                    if never_unit_applied:
                        unit = tokens[1]
                        food, note = self.parse_food(tokens[2:])
                    else:
                        try:
                            if unit is not None:
                                raise ValueError
                            if tokens[1]:
                                amount += Decimal(str(self.parse_fraction(tokens[1])))
                                amount = self.normalize_amount(amount)

                            if len(tokens) > 3 and not tokens[2].endswith(','):
                                try:
                                    food, note = self.parse_food(tokens[3:])
                                    unit = tokens[2]
                                except ValueError:
                                    food, note = self.parse_food(tokens[2:])
                            else:
                                food, note = self.parse_food(tokens[2:])
                        except ValueError:
                            if not tokens[1].endswith(','):
                                try:
                                    food, note = self.parse_food(tokens[2:])
                                    if unit is None:
                                        unit = tokens[1]
                                    else:
                                        note = tokens[1]
                                except ValueError:
                                    food, note = self.parse_food(tokens[1:])
                            else:
                                food, note = self.parse_food(tokens[1:])
                else:
                    try:
                        food, note = self.parse_food([tokens[1]])
                    except ValueError:
                        food = tokens[1]
            except ValueError:
                try:
                    food, note = self.parse_food(tokens)
                except ValueError:
                    food = ' '.join(tokens[1:])

        if unit_note not in note:
            note += ' ' + unit_note

        if unit and not self.ignore_automations and self.automation:
            unit = self.automation.apply_unit_automation(unit)

        if food and not self.ignore_automations and self.automation:
            food = self.automation.apply_food_automation(food)

        if len(food) > self.max_food_name_length:
            if len(food.split()) > 1 and len(food.split()[0]) < self.max_food_name_length:
                note = ' '.join(food.split()[1:]) + ' ' + note
                food = food.split()[0]
            else:
                note = food + ' ' + note
                food = food[:self.max_food_name_length]

        if len(food.strip()) == 0:
            raise ValueError(f'Error parsing string {ingredient}, food cannot be empty')

        note = note[:self.max_note_length].strip()

        return amount, unit, food, note

    def normalize(self, ingredient_input: Union[str, Ingredient, dict]) -> NormalizedIngredient:
        if isinstance(ingredient_input, str) and self.fallback_enabled and not self._use_new_service(ingredient_input):
            return self._fallback_normalize(ingredient_input)

        if isinstance(ingredient_input, str) and self.use_cache:
            cached = self._get_cached_normalized(ingredient_input)
            if cached is not None:
                if self.log_comparison:
                    self._log_comparison(ingredient_input, cached)
                return cached

        try:
            if isinstance(ingredient_input, str):
                result = self._normalize_from_string(ingredient_input)
            elif isinstance(ingredient_input, Ingredient):
                result = self._normalize_from_ingredient(ingredient_input)
            elif isinstance(ingredient_input, dict):
                result = self._normalize_from_dict(ingredient_input)
            else:
                raise TypeError(f'Unsupported input type: {type(ingredient_input)}')
        except Exception as e:
            if self.rollback_on_error and self.fallback_enabled and isinstance(ingredient_input, str):
                logger.warning(
                    'IngredientNormalizationService error, falling back to IngredientParser: %s', str(e)
                )
                return self._fallback_normalize(ingredient_input)
            raise

        if isinstance(ingredient_input, str) and self.use_cache:
            self._set_cached_normalized(ingredient_input, result)

        if self.log_comparison and isinstance(ingredient_input, str) and self.fallback_enabled:
            self._log_comparison(ingredient_input, result)

        return result

    def _normalize_from_string(self, ingredient_str: str) -> NormalizedIngredient:
        amount, unit_name, food_name, note = self.parse_ingredient_string(ingredient_str)

        normalized = NormalizedIngredient(
            amount=amount,
            unit_name=unit_name or '',
            food_name=food_name,
            note=note,
            original_text=ingredient_str,
        )

        if self.space:
            normalized.unit = self.get_or_create_unit(unit_name) if unit_name else None
            normalized.food = self.get_or_create_food(food_name) if food_name else None

        return normalized

    def _normalize_from_ingredient(self, ingredient: Ingredient) -> NormalizedIngredient:
        normalized = NormalizedIngredient(
            amount=self.normalize_amount(ingredient.amount),
            unit=ingredient.unit,
            food=ingredient.food,
            note=ingredient.note or '',
            original_text=ingredient.original_text or '',
            unit_name=ingredient.unit.name if ingredient.unit else '',
            food_name=ingredient.food.name if ingredient.food else '',
        )

        return normalized

    def _normalize_from_dict(self, data: dict) -> NormalizedIngredient:
        amount = self.normalize_amount(data.get('amount', 0))
        unit_name = data.get('unit', '') or data.get('unit_name', '')
        food_name = data.get('food', '') or data.get('food_name', '')
        note = data.get('note', '')
        original_text = data.get('original_text', '')

        unit_obj = data.get('unit_obj') or data.get('unit')
        if isinstance(unit_obj, Unit):
            unit = unit_obj
            unit_name = unit_obj.name
        else:
            unit = self.get_or_create_unit(unit_name) if self.space and unit_name else None

        food_obj = data.get('food_obj') or data.get('food')
        if isinstance(food_obj, Food):
            food = food_obj
            food_name = food_obj.name
        else:
            food = self.get_or_create_food(food_name) if self.space and food_name else None

        return NormalizedIngredient(
            amount=amount,
            unit=unit,
            food=food,
            note=note,
            original_text=original_text,
            unit_name=unit_name,
            food_name=food_name,
        )

    def scale(self, ingredient: Union[NormalizedIngredient, Ingredient, str, dict],
              factor: Union[float, int, Decimal]) -> NormalizedIngredient:
        if not isinstance(ingredient, NormalizedIngredient):
            ingredient = self.normalize(ingredient)

        factor = Decimal(str(factor))
        scaled_amount = self.normalize_amount(ingredient.amount * factor)

        return NormalizedIngredient(
            amount=scaled_amount,
            unit=ingredient.unit,
            food=ingredient.food,
            note=ingredient.note,
            original_text=ingredient.original_text,
            unit_name=ingredient.unit_name,
            food_name=ingredient.food_name,
        )

    def scale_by_servings(self, ingredient: Union[NormalizedIngredient, Ingredient, str, dict],
                          original_servings: Union[int, float, Decimal],
                          target_servings: Union[int, float, Decimal]) -> NormalizedIngredient:
        if original_servings == 0:
            raise ValueError('Original servings cannot be zero')

        factor = Decimal(str(target_servings)) / Decimal(str(original_servings))
        return self.scale(ingredient, factor)

    def convert_unit(self, ingredient: Union[NormalizedIngredient, Ingredient, str, dict],
                     target_unit: Union[str, Unit]) -> NormalizedIngredient:
        if not self.unit_conversion:
            raise ValueError('Space is required for unit conversion')

        if not isinstance(ingredient, NormalizedIngredient):
            ingredient = self.normalize(ingredient)

        if not ingredient.unit:
            raise ValueError('Ingredient has no unit to convert from')

        if isinstance(target_unit, str):
            target_unit_obj = self.get_or_create_unit(target_unit)
            if not target_unit_obj:
                raise ValueError(f'Target unit not found: {target_unit}')
        else:
            target_unit_obj = target_unit

        from_unit_name = ingredient.unit.base_unit or ingredient.unit.name
        to_unit_name = target_unit_obj.base_unit or target_unit_obj.name

        try:
            converted_amount = UnitConversionHelper.convert_from_to(
                from_unit_name, to_unit_name, ingredient.amount
            )
            converted_amount = self.normalize_amount(converted_amount)

            return NormalizedIngredient(
                amount=converted_amount,
                unit=target_unit_obj,
                food=ingredient.food,
                note=ingredient.note,
                original_text=ingredient.original_text,
                unit_name=target_unit_obj.name,
                food_name=ingredient.food_name,
            )
        except ConversionException:
            pass

        if ingredient.unit and self.unit_conversion:
            ing = Ingredient(
                amount=ingredient.amount,
                unit=ingredient.unit,
                food=ingredient.food,
                space=self.space,
            )
            conversions = self.unit_conversion.get_conversions(ing)
            for conv in conversions:
                if conv.unit.id == target_unit_obj.id:
                    return NormalizedIngredient(
                        amount=self.normalize_amount(conv.amount),
                        unit=conv.unit,
                        food=conv.food,
                        note=ingredient.note,
                        original_text=ingredient.original_text,
                        unit_name=conv.unit.name,
                        food_name=conv.food.name if conv.food else '',
                    )

        raise ConversionException(f'Cannot convert from {ingredient.unit.name} to {target_unit_obj.name}')

    def get_all_conversions(self, ingredient: Union[NormalizedIngredient, Ingredient, str, dict]) -> List[NormalizedIngredient]:
        if not self.unit_conversion:
            raise ValueError('Space is required for unit conversion')

        if not isinstance(ingredient, NormalizedIngredient):
            ingredient = self.normalize(ingredient)

        if not ingredient.unit:
            return [ingredient]

        ing = Ingredient(
            amount=ingredient.amount,
            unit=ingredient.unit,
            food=ingredient.food,
            space=self.space,
        )

        conversions = self.unit_conversion.get_conversions(ing)
        result = []

        for conv in conversions:
            result.append(NormalizedIngredient(
                amount=self.normalize_amount(conv.amount),
                unit=conv.unit,
                food=conv.food,
                note=ingredient.note,
                original_text=ingredient.original_text,
                unit_name=conv.unit.name,
                food_name=conv.food.name if conv.food else '',
            ))

        return result

    def merge_ingredients(self, ingredients: List[Union[NormalizedIngredient, Ingredient, str, dict]]) -> List[NormalizedIngredient]:
        normalized_list = []
        for ing in ingredients:
            if isinstance(ing, NormalizedIngredient):
                normalized_list.append(ing)
            else:
                normalized_list.append(self.normalize(ing))

        merged = {}

        for ing in normalized_list:
            if not ing.food:
                continue

            key = (ing.food.id, ing.unit.id if ing.unit else None)

            if key in merged:
                merged[key].amount = self.normalize_amount(merged[key].amount + ing.amount)
                if ing.note and ing.note not in merged[key].note:
                    merged[key].note = (merged[key].note + ', ' + ing.note).strip(', ')
            else:
                merged[key] = NormalizedIngredient(
                    amount=ing.amount,
                    unit=ing.unit,
                    food=ing.food,
                    note=ing.note,
                    original_text='',
                    unit_name=ing.unit_name,
                    food_name=ing.food_name,
                )

        return list(merged.values())

    def to_ingredient(self, normalized: NormalizedIngredient, space=None) -> Ingredient:
        space = space or self.space
        if not space:
            raise ValueError('Space is required to create Ingredient object')

        ingredient = Ingredient(
            amount=normalized.amount,
            unit=normalized.unit,
            food=normalized.food,
            note=normalized.note,
            original_text=normalized.original_text,
            space=space,
        )

        return ingredient

    def parse_as_ingredient(self, text: str) -> Ingredient:
        """
        Parse ingredient string into ingredient object with nested food information.
        Backward compatible with IngredientParser.parse_as_ingredient().
        :param text: ingredient string
        :return: Ingredient object
        """
        normalized = self.normalize(text)
        if not normalized.original_text:
            normalized.original_text = text
        return self.to_ingredient(normalized)

    def parse(self, text: str) -> Tuple[Decimal, Optional[str], str, str]:
        """
        Parse ingredient string and return tuple of (amount, unit, food, note).
        Backward compatible with IngredientParser.parse().
        :param text: ingredient string
        :return: tuple (amount, unit, food, note)
        """
        normalized = self.normalize(text)
        return normalized.amount, normalized.unit_name or None, normalized.food_name, normalized.note

    def get_food(self, food_name: str) -> Optional[Food]:
        """
        Get or create food by name.
        Backward compatible with IngredientParser.get_food().
        :param food_name: food name
        :return: Food object or None
        """
        return self.get_or_create_food(food_name)

    def get_unit(self, unit_name: str) -> Optional[Unit]:
        """
        Get or create unit by name.
        Backward compatible with IngredientParser.get_unit().
        :param unit_name: unit name
        :return: Unit object or None
        """
        return self.get_or_create_unit(unit_name)

    def normalize_to_ingredient(self, ingredient_input: Union[str, dict], space=None) -> Ingredient:
        normalized = self.normalize(ingredient_input)
        return self.to_ingredient(normalized, space=space)

    def _use_new_service(self, ingredient_text: str) -> bool:
        """
        Determine whether to use the new normalization service based on grayscale percentage.
        Uses deterministic hashing of the ingredient text so the same ingredient always
        gets the same treatment, ensuring consistency within a session.
        :param ingredient_text: ingredient string to hash
        :return: True if new service should be used, False if fallback to old parser
        """
        if self.grayscale_percent >= 100:
            return True
        if self.grayscale_percent <= 0:
            return False

        hash_val = int(hashlib.md5(ingredient_text.encode('utf-8')).hexdigest(), 16) % 100
        return hash_val < self.grayscale_percent

    def _fallback_normalize(self, ingredient_text: str) -> NormalizedIngredient:
        """
        Fallback to the old IngredientParser for grayscale or error recovery.
        This method imports and uses the original IngredientParser to ensure
        that if the new service is disabled or encounters an error, the system
        can still process ingredients using the legacy code path.
        :param ingredient_text: ingredient string to parse
        :return: NormalizedIngredient object
        """
        from cookbook.helper.ingredient_parser import IngredientParser

        try:
            parser = IngredientParser(self.request, self.use_cache, ignore_automations=self.ignore_automations)
            amount, unit, food, note = parser.parse(ingredient_text)
            f = parser.get_food(food) if food else None
            u = parser.get_unit(unit) if unit else None

            return NormalizedIngredient(
                amount=self.normalize_amount(amount),
                unit=u,
                food=f,
                note=note or '',
                original_text=ingredient_text,
                unit_name=u.name if u else '',
                food_name=f.name if f else '',
            )
        except Exception as e:
            logger.error(
                'IngredientParser fallback also failed for "%s": %s', ingredient_text, str(e)
            )
            return NormalizedIngredient(
                amount=Decimal('0'),
                unit=None,
                food=None,
                note=ingredient_text,
                original_text=ingredient_text,
                unit_name='',
                food_name=ingredient_text,
            )

    def _log_comparison(self, ingredient_text: str, new_result: NormalizedIngredient):
        """
        Log a comparison between the new service and old parser results.
        Used during grayscale validation to detect discrepancies.
        Only logs when INGREDIENT_NORMALIZATION_LOG_COMPARISON is True.
        :param ingredient_text: original ingredient string
        :param new_result: result from the new normalization service
        """
        try:
            from cookbook.helper.ingredient_parser import IngredientParser
            parser = IngredientParser(self.request, self.use_cache, ignore_automations=self.ignore_automations)
            old_amount, old_unit, old_food, old_note = parser.parse(ingredient_text)

            discrepancies = []
            if new_result.amount != self.normalize_amount(old_amount):
                discrepancies.append(f'amount: new={new_result.amount} old={old_amount}')
            if (new_result.unit_name or '') != (old_unit or ''):
                discrepancies.append(f'unit: new={new_result.unit_name} old={old_unit}')
            if new_result.food_name != old_food:
                discrepancies.append(f'food: new={new_result.food_name} old={old_food}')

            if discrepancies:
                logger.info(
                    'Ingredient normalization comparison for "%s": %s',
                    ingredient_text,
                    '; '.join(discrepancies)
                )
        except Exception as e:
            logger.debug('Comparison logging failed: %s', str(e))
