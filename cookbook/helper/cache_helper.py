class CacheHelper:
    space = None

    BASE_UNITS_CACHE_KEY = None
    PROPERTY_TYPE_CACHE_KEY = None
    DELETE_COLLECTOR_CACHE_PREFIX = None

    def __init__(self, space):
        self.space = space

        self.BASE_UNITS_CACHE_KEY = f'SPACE_{space.id}_BASE_UNITS'
        self.PROPERTY_TYPE_CACHE_KEY = f'SPACE_{space.id}_PROPERTY_TYPES'
        self.DELETE_COLLECTOR_CACHE_PREFIX = f'DELETE_COLLECTOR_{space.id}_'

    def clear_unit_related_caches(self):
        from django.core.cache import caches

        from cookbook.helper.unit_conversion_helper import UnitConversionHelper

        caches['default'].delete(self.BASE_UNITS_CACHE_KEY)
        caches['default'].delete(self.PROPERTY_TYPE_CACHE_KEY)
        UnitConversionHelper._base_units_cache.pop(self.space.id, None)

        cache_keys_patterns = [
            f'{self.DELETE_COLLECTOR_CACHE_PREFIX}PROTECTING_Unit_*',
            f'{self.DELETE_COLLECTOR_CACHE_PREFIX}CASCADING_Unit_*',
            f'{self.DELETE_COLLECTOR_CACHE_PREFIX}NULLING_Unit_*',
            f'{self.DELETE_COLLECTOR_CACHE_PREFIX}PROTECTING_UnitConversion_*',
            f'{self.DELETE_COLLECTOR_CACHE_PREFIX}CASCADING_UnitConversion_*',
            f'{self.DELETE_COLLECTOR_CACHE_PREFIX}NULLING_UnitConversion_*',
            f'{self.DELETE_COLLECTOR_CACHE_PREFIX}PROTECTING_Ingredient_*',
            f'{self.DELETE_COLLECTOR_CACHE_PREFIX}CASCADING_Ingredient_*',
            f'{self.DELETE_COLLECTOR_CACHE_PREFIX}NULLING_Ingredient_*',
            f'{self.DELETE_COLLECTOR_CACHE_PREFIX}PROTECTING_ShoppingListEntry_*',
            f'{self.DELETE_COLLECTOR_CACHE_PREFIX}CASCADING_ShoppingListEntry_*',
            f'{self.DELETE_COLLECTOR_CACHE_PREFIX}NULLING_ShoppingListEntry_*',
        ]

        try:
            for pattern in cache_keys_patterns:
                caches['default'].delete_pattern(pattern)
        except Exception:
            pass
