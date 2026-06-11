from decimal import Decimal
from django.core.cache import caches

from cookbook.helper.cache_helper import CacheHelper
from cookbook.helper.nutrition_review_helper import NutritionReviewHelper
from cookbook.helper.unit_conversion_helper import UnitConversionHelper
from cookbook.models import NutritionInformation, PropertyType


class FoodPropertyHelper:
    space = None

    def __init__(self, space):
        self.space = space

    def calculate_recipe_properties(self, recipe, run_review=True):
        ingredients = []
        computed_properties = {}

        for s in recipe.steps.all():
            ingredients += s.ingredients.all()

        property_types = caches['default'].get(CacheHelper(self.space).PROPERTY_TYPE_CACHE_KEY, None)

        if not property_types:
            property_types = PropertyType.objects.filter(space=self.space).all()
            caches['default'].set(CacheHelper(self.space).PROPERTY_TYPE_CACHE_KEY, property_types, 60 * 60)

        for fpt in property_types:
            computed_properties[fpt.id] = {
                'id': fpt.id, 'name': fpt.name, 'description': fpt.description,
                'unit': fpt.unit, 'order': fpt.order,
                'food_values': {}, 'total_value': 0, 'missing_value': False,
                'category': getattr(fpt, 'category', None),
            }

        uch = UnitConversionHelper(self.space)

        for i in ingredients:
            if i.food is not None:
                conversions = uch.get_conversions(i)
                for pt in property_types:
                    found_property = False
                    has_property_value = False
                    min_confidence = Decimal('100.00')

                    if (i.food.properties_food_amount == 0 or i.food.properties_food_unit is None) and not (i.amount == 0 or i.no_amount):
                        computed_properties[pt.id]['food_values'][i.food.id] = {
                            'id': i.food.id,
                            'food': {'id': i.food.id, 'name': i.food.name},
                            'value': None,
                            'confidence_score': '0.00',
                        }
                        computed_properties[pt.id]['missing_value'] = True
                    else:
                        for p in i.food.properties.all():
                            if p.property_type == pt and p.property_amount is not None:
                                has_property_value = True
                                p_confidence = Decimal(str(p.confidence_score))
                                if p_confidence < min_confidence:
                                    min_confidence = p_confidence
                                for c in conversions:
                                    if c.unit == i.food.properties_food_unit and i.food.properties_food_amount != 0:
                                        found_property = True
                                        computed_amount = (c.amount / i.food.properties_food_amount) * p.property_amount
                                        computed_properties[pt.id]['total_value'] += computed_amount
                                        computed_properties[pt.id]['food_values'] = self.add_or_create(
                                            computed_properties[p.property_type.id]['food_values'],
                                            c.food.id, computed_amount, c.food, min_confidence,
                                        )
                    if not found_property:
                        if i.amount == 0 or i.no_amount:
                            if i.food.id not in computed_properties[pt.id]['food_values']:
                                computed_properties[pt.id]['food_values'][i.food.id] = {
                                    'id': i.food.id,
                                    'food': {'id': i.food.id, 'name': i.food.name},
                                    'value': 0,
                                    'confidence_score': '100.00',
                                }
                        elif i.unit is None:
                            if i.food.id not in computed_properties[pt.id]['food_values']:
                                computed_properties[pt.id]['food_values'][i.food.id] = {
                                    'id': i.food.id,
                                    'food': {'id': i.food.id, 'name': i.food.name},
                                    'value': 0,
                                    'confidence_score': '0.00',
                                }
                            computed_properties[pt.id]['food_values'][i.food.id]['missing_unit'] = True
                        else:
                            computed_properties[pt.id]['missing_value'] = True
                            if i.food.id not in computed_properties[pt.id]['food_values']:
                                computed_properties[pt.id]['food_values'][i.food.id] = {
                                    'id': i.food.id,
                                    'food': {'id': i.food.id, 'name': i.food.name},
                                    'value': None,
                                    'confidence_score': '0.00',
                                }
                            if has_property_value and i.unit is not None:
                                computed_properties[pt.id]['food_values'][i.food.id]['missing_conversion'] = {
                                    'base_unit': {'id': i.unit.id, 'name': i.unit.name},
                                    'converted_unit': {
                                        'id': i.food.properties_food_unit.id,
                                        'name': i.food.properties_food_unit.name,
                                    },
                                }

        for pt_id in computed_properties:
            total_confidence = Decimal('0')
            count = 0
            for fv in computed_properties[pt_id]['food_values'].values():
                conf = fv.get('confidence_score', '100.00')
                try:
                    total_confidence += Decimal(str(conf))
                    count += 1
                except Exception:
                    pass
            if count > 0:
                computed_properties[pt_id]['confidence_score'] = str(round(total_confidence / Decimal(count), 2))
            else:
                computed_properties[pt_id]['confidence_score'] = '100.00'

        if run_review:
            review_helper = NutritionReviewHelper(self.space)
            review_result = review_helper.apply_review_to_nutrition(
                recipe.nutrition if recipe.nutrition else NutritionInformation(space=self.space),
                recipe, computed_properties,
            )
            computed_properties['_review'] = review_result

        return computed_properties

    @staticmethod
    def add_or_create(d, key, value, food, confidence=Decimal('100.00')):
        conf_str = str(round(Decimal(str(confidence)), 2))
        if key in d:
            if d[key].get('value'):
                d[key]['value'] += value
            else:
                d[key]['value'] = value
            existing_conf = Decimal(str(d[key].get('confidence_score', '100.00')))
            if Decimal(str(confidence)) < existing_conf:
                d[key]['confidence_score'] = conf_str
        else:
            d[key] = {
                'id': food.id,
                'food': {'id': food.id, 'name': food.name},
                'value': value,
                'confidence_score': conf_str,
            }
        return d
