from decimal import Decimal
from django.utils.translation import gettext as _

from cookbook.models import NutritionInformation


class NutritionReviewHelper:
    space = None

    CONFIDENCE_THRESHOLD_HIGH = Decimal('70.00')
    CONFIDENCE_THRESHOLD_MEDIUM = Decimal('40.00')
    CONFIDENCE_THRESHOLD_LOW = Decimal('20.00')

    REASON_MISSING_FOOD = 'missing_food'
    REASON_MISSING_UNIT = 'missing_unit'
    REASON_MISSING_AMOUNT = 'missing_amount'
    REASON_MISSING_PROPERTY = 'missing_property'
    REASON_MISSING_CONVERSION = 'missing_conversion'
    REASON_LOW_CONFIDENCE_PROPERTY = 'low_confidence_property'
    REASON_AI_ESTIMATED = 'ai_estimated'
    REASON_FDC_UNVERIFIED = 'fdc_unverified'

    REVIEW_REASONS = {
        REASON_MISSING_FOOD: _('Ingredient food reference is missing'),
        REASON_MISSING_UNIT: _('Ingredient unit is missing'),
        REASON_MISSING_AMOUNT: _('Ingredient amount is missing or zero'),
        REASON_MISSING_PROPERTY: _('Food has no nutrition property defined'),
        REASON_MISSING_CONVERSION: _('Unit conversion is not available'),
        REASON_LOW_CONFIDENCE_PROPERTY: _('Property value has low confidence score'),
        REASON_AI_ESTIMATED: _('Value was estimated using AI'),
        REASON_FDC_UNVERIFIED: _('FDC data is unverified'),
    }

    def __init__(self, space):
        self.space = space

    def calculate_overall_confidence(self, computed_properties):
        total_ingredients = 0
        confident_ingredients = 0
        missing = []
        low_confidence = []

        for pt_id, pt_data in computed_properties.items():
            for food_id, food_data in pt_data.get('food_values', {}).items():
                total_ingredients += 1

                reasons = []
                if food_data.get('value') is None:
                    if food_data.get('missing_unit'):
                        reasons.append(self.REASON_MISSING_UNIT)
                    elif food_data.get('missing_conversion'):
                        reasons.append(self.REASON_MISSING_CONVERSION)
                    else:
                        reasons.append(self.REASON_MISSING_PROPERTY)

                if reasons:
                    entry = {
                        'food_id': food_data.get('food', {}).get('id'),
                        'food_name': food_data.get('food', {}).get('name'),
                        'reasons': reasons,
                        'reasons_text': [self.REVIEW_REASONS.get(r, r) for r in reasons],
                    }
                    missing.append(entry)
                else:
                    confident_ingredients += 1

        if total_ingredients == 0:
            return Decimal('100.00'), [], []

        confidence = (Decimal(confident_ingredients) / Decimal(total_ingredients)) * Decimal('100')
        return round(confidence, 2), missing, low_confidence

    def generate_review_items(self, recipe, computed_properties):
        review_items = []
        ingredients_seen = set()

        for s in recipe.steps.all():
            for i in s.ingredients.all():
                if i.is_header:
                    continue
                if i.id in ingredients_seen:
                    continue
                ingredients_seen.add(i.id)

                item = self._build_ingredient_review_item(i, computed_properties)
                if item and item['needs_review']:
                    review_items.append(item)

        return review_items

    def _build_ingredient_review_item(self, ingredient, computed_properties):
        if ingredient.food is None:
            return {
                'ingredient_id': ingredient.id,
                'original_text': ingredient.original_text,
                'food': None,
                'amount': str(ingredient.amount) if ingredient.amount else None,
                'unit': None,
                'needs_review': True,
                'review_reasons': [self.REASON_MISSING_FOOD],
                'review_reasons_text': [self.REVIEW_REASONS[self.REASON_MISSING_FOOD]],
                'confidence_score': '0.00',
            }

        issues = []
        confidences = []

        if ingredient.amount == 0 and not ingredient.no_amount:
            issues.append(self.REASON_MISSING_AMOUNT)

        if ingredient.unit is None and not ingredient.no_amount:
            issues.append(self.REASON_MISSING_UNIT)

        for pt_id, pt_data in computed_properties.items():
            food_val = pt_data.get('food_values', {}).get(ingredient.food.id, {})
            if food_val.get('value') is None:
                if food_val.get('missing_conversion'):
                    if self.REASON_MISSING_CONVERSION not in issues:
                        issues.append(self.REASON_MISSING_CONVERSION)
                else:
                    if self.REASON_MISSING_PROPERTY not in issues:
                        issues.append(self.REASON_MISSING_PROPERTY)
            else:
                for p in ingredient.food.properties.all():
                    if p.property_type_id == pt_id:
                        confidences.append(Decimal(str(p.confidence_score)))
                        if p.confidence_score < self.CONFIDENCE_THRESHOLD_HIGH:
                            if self.REASON_LOW_CONFIDENCE_PROPERTY not in issues:
                                issues.append(self.REASON_LOW_CONFIDENCE_PROPERTY)

        if confidences:
            avg_confidence = sum(confidences) / Decimal(len(confidences))
        elif issues:
            avg_confidence = Decimal('0.00')
        else:
            avg_confidence = Decimal('100.00')

        return {
            'ingredient_id': ingredient.id,
            'original_text': ingredient.original_text,
            'food': {
                'id': ingredient.food.id,
                'name': ingredient.food.name,
                'fdc_id': ingredient.food.fdc_id,
            },
            'amount': str(ingredient.amount) if ingredient.amount else None,
            'unit': {
                'id': ingredient.unit.id,
                'name': ingredient.unit.name,
            } if ingredient.unit else None,
            'needs_review': len(issues) > 0 or avg_confidence < self.CONFIDENCE_THRESHOLD_HIGH,
            'review_reasons': issues,
            'review_reasons_text': [self.REVIEW_REASONS.get(r, r) for r in issues],
            'confidence_score': str(round(avg_confidence, 2)),
        }

    def apply_review_to_nutrition(self, nutrition_info, recipe, computed_properties):
        confidence, missing, low_conf = self.calculate_overall_confidence(computed_properties)
        review_items = self.generate_review_items(recipe, computed_properties)

        nutrition_info.confidence_score = confidence
        nutrition_info.missing_ingredients = missing
        nutrition_info.low_confidence_ingredients = low_conf

        needs_review = len(review_items) > 0 or confidence < self.CONFIDENCE_THRESHOLD_HIGH
        if needs_review:
            nutrition_info.needs_review = True
            nutrition_info.review_status = NutritionInformation.REVIEW_PENDING
        else:
            nutrition_info.needs_review = False
            nutrition_info.review_status = NutritionInformation.REVIEW_AUTO_APPROVED

        nutrition_info.save()

        return {
            'confidence_score': str(confidence),
            'needs_review': needs_review,
            'review_status': nutrition_info.review_status,
            'review_items': review_items,
            'missing_ingredients': missing,
            'low_confidence_ingredients': low_conf,
        }

    def get_pending_reviews(self, space=None):
        target_space = space or self.space
        return NutritionInformation.objects.filter(
            space=target_space,
            needs_review=True,
            review_status=NutritionInformation.REVIEW_PENDING,
        ).order_by('-pk')

    def get_review_summary(self, space=None):
        target_space = space or self.space
        qs = NutritionInformation.objects.filter(space=target_space)
        total = qs.count()
        pending = qs.filter(review_status=NutritionInformation.REVIEW_PENDING).count()
        approved = qs.filter(review_status=NutritionInformation.REVIEW_APPROVED).count()
        rejected = qs.filter(review_status=NutritionInformation.REVIEW_REJECTED).count()
        auto_approved = qs.filter(review_status=NutritionInformation.REVIEW_AUTO_APPROVED).count()
        needs_review = qs.filter(needs_review=True).count()

        return {
            'total': total,
            'pending': pending,
            'approved': approved,
            'rejected': rejected,
            'auto_approved': auto_approved,
            'needs_review': needs_review,
        }
