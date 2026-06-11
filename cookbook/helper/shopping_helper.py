
import hashlib
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from django.db import transaction
from django.db.models import F, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils.translation import gettext as _

from cookbook.connectors.connector_manager import ActionType, ConnectorManager
from cookbook.helper.permission_helper import get_household_user_ids, has_group_permission, is_object_owner, is_object_household
from cookbook.models import Ingredient, MealPlan, Recipe, ShoppingListEntry, ShoppingListRecipe, SupermarketCategoryRelation


def shopping_helper(qs, request):
    supermarket = request.query_params.get('supermarket', None)
    checked = request.query_params.get('checked', 'recent')
    supermarket_order = [F('food__supermarket_category__name').asc(nulls_first=True), 'food__name']

    # TODO created either scheduled task or startup task to delete very old shopping list entries
    # TODO create user preference to define 'very old'
    if supermarket:
        supermarket_categories = SupermarketCategoryRelation.objects.filter(supermarket=supermarket, category=OuterRef('food__supermarket_category'))
        qs = qs.annotate(supermarket_order=Coalesce(Subquery(supermarket_categories.values('order')), Value(9999)))
        supermarket_order = ['supermarket_order'] + supermarket_order
    if checked in ['false', 0, '0']:
        qs = qs.filter(checked=False)
    elif checked in ['true', 1, '1']:
        qs = qs.filter(checked=True)
    elif checked in ['recent']:
        supermarket_order = ['checked'] + supermarket_order

    return qs.distinct().order_by(*supermarket_order).select_related('unit', 'food', 'ingredient', 'created_by', 'list_recipe', 'list_recipe__mealplan', 'list_recipe__recipe')


class RecipeShoppingEditor():
    def __init__(self, user, space, **kwargs):
        self.created_by = user
        self.space = space
        self._kwargs = {**kwargs}

        self.mealplan = self._kwargs.get('mealplan', None)
        if type(self.mealplan) in [int, float]:
            self.mealplan = MealPlan.objects.filter(id=self.mealplan, space=self.space)
        if isinstance(self.mealplan, dict):
            self.mealplan = MealPlan.objects.filter(id=self.mealplan['id'], space=self.space).first()
        self.id = self._kwargs.get('id', None)

        self._shopping_list_recipe = self.get_shopping_list_recipe(self.id, self.created_by, self.space)

        if self._shopping_list_recipe:
            # created_by needs to be sticky to original creator as it is 'their' shopping list
            # changing shopping list created_by can shift some items to new owner which may not share in the other direction
            self.created_by = getattr(self._shopping_list_recipe.entries.first(), 'created_by', self.created_by)

        self.recipe = getattr(self._shopping_list_recipe, 'recipe', None) or self._kwargs.get('recipe', None) or getattr(self.mealplan, 'recipe', None)
        if type(self.recipe) in [int, float]:
            self.recipe = Recipe.objects.filter(id=self.recipe, space=self.space)

        try:
            self.servings = float(self._kwargs.get('servings', None))
        except (ValueError, TypeError):
            self.servings = getattr(self._shopping_list_recipe, 'servings', None) or getattr(self.mealplan, 'servings', None) or getattr(self.recipe, 'servings', None)

    @property
    def _recipe_servings(self):
        return getattr(self.recipe, 'servings', None) or getattr(getattr(self.mealplan, 'recipe', None), 'servings',
                                                                 None) or getattr(getattr(self._shopping_list_recipe, 'recipe', None), 'servings', None)

    @property
    def _servings_factor(self):
        return Decimal(self.servings) / Decimal(self._recipe_servings)


    @staticmethod
    def get_shopping_list_recipe(id, user, space):
        # TODO this sucks since it wont find SLR's that no longer have any entries
        owner_user_space = user.userspace_set.filter(space=space).first()
        user_ids = get_household_user_ids(owner_user_space)

        return ShoppingListRecipe.objects.filter(id=id, space=space).filter(
            Q(entries__created_by=user)
            | Q(entries__created_by__in=user_ids)
        ).prefetch_related('entries').first()

    def get_recipe_ingredients(self, id, exclude_onhand=False):
        if exclude_onhand:
            queryset = Ingredient.objects.filter(step__recipe__id=id, food__ignore_shopping=False, space=self.space)
            owner_user_space = self.created_by.userspace_set.filter(space=self.space).first()
            queryset = queryset.exclude(food__onhand_users__id__in=get_household_user_ids(owner_user_space))
            return queryset
        else:
            return Ingredient.objects.filter(step__recipe__id=id, food__ignore_shopping=False, space=self.space)

    @property
    def _include_related(self):
        return self.created_by.userpreference.mealplan_autoinclude_related

    @property
    def _exclude_onhand(self):
        return self.created_by.userpreference.mealplan_autoexclude_onhand

    def create(self, **kwargs):
        ingredients = kwargs.get('ingredients', None)
        exclude_onhand = not ingredients and self._exclude_onhand
        if servings := kwargs.get('servings', None):
            self.servings = float(servings)

        if mealplan := kwargs.get('mealplan', None):
            if isinstance(mealplan, dict):
                self.mealplan = MealPlan.objects.filter(id=mealplan['id'], space=self.space).first()
            else:
                self.mealplan = mealplan
            self.recipe = mealplan.recipe
        elif recipe := kwargs.get('recipe', None):
            self.recipe = recipe

        if not self.servings:
            self.servings = getattr(self.mealplan, 'servings', None) or getattr(self.recipe, 'servings', 1.0)

        self._shopping_list_recipe = ShoppingListRecipe.objects.create(recipe=self.recipe, mealplan=self.mealplan, servings=self.servings, space=self.space, created_by=self.created_by)

        if ingredients:
            self._add_ingredients(ingredients=ingredients)
        else:
            if self._include_related:
                related = self.recipe.get_related_recipes()
                self._add_ingredients(self.get_recipe_ingredients(self.recipe.id, exclude_onhand=exclude_onhand).exclude(food__recipe__in=related))
                for r in related:
                    self._add_ingredients(self.get_recipe_ingredients(r.id, exclude_onhand=exclude_onhand).exclude(food__recipe__in=related))
            else:
                self._add_ingredients(self.get_recipe_ingredients(self.recipe.id, exclude_onhand=exclude_onhand))

        return True

    def add(self, **kwargs):
        return

    def edit(self, servings=None, ingredients=None, **kwargs):
        if servings:
            self.servings = servings

        self._delete_ingredients(ingredients=ingredients)
        # need to check if there is a SLR because its possible it cant be found if all entries are deleted
        if self._shopping_list_recipe and self.servings != self._shopping_list_recipe.servings:
            self.edit_servings()
        self._add_ingredients(ingredients=ingredients)
        return True

    def edit_servings(self, servings=None, **kwargs):
        if servings:
            self.servings = servings
        if id := kwargs.get('id', None):
            self._shopping_list_recipe = self.get_shopping_list_recipe(id, self.created_by, self.space)
        if not self.servings:
            raise ValueError(_("You must supply a servings size"))

        if self._shopping_list_recipe.servings == self.servings:
            return True

        for sle in ShoppingListEntry.objects.filter(list_recipe=self._shopping_list_recipe):
            if sle.ingredient: # TODO temporarily dont scale manual entries until ingredient_amount or some other base amount has been migrated to SLE
                sle.amount = sle.ingredient.amount * Decimal(self._servings_factor)
                sle.save()
        self._shopping_list_recipe.servings = self.servings
        self._shopping_list_recipe.save()
        return True

    def delete(self, **kwargs):
        try:
            self._shopping_list_recipe.delete()
            return True
        except BaseException:
            return False

    def _add_ingredients(self, ingredients=None):
        if not ingredients:
            return
        elif isinstance(ingredients, list):
            ingredients = Ingredient.objects.filter(id__in=ingredients, food__ignore_shopping=False)
        existing = self._shopping_list_recipe.entries.filter(ingredient__in=ingredients).values_list('ingredient__pk', flat=True)
        add_ingredients = ingredients.exclude(id__in=existing)

        entries = []
        for i in [x for x in add_ingredients if x.food]:
            entry =  ShoppingListEntry(
                list_recipe=self._shopping_list_recipe,
                food=i.food,
                unit=i.unit,
                ingredient=i,
                amount=i.amount * Decimal(self._servings_factor),
                created_by=self.created_by,
                space=self.space,
            )
            entries.append(entry)

        ShoppingListEntry.objects.bulk_create(entries)
        ConnectorManager.add_work(ActionType.CREATED, *entries)
        for e in entries:
            if e.food.shopping_lists.count() > 0:
                e.shopping_lists.set(e.food.shopping_lists.all())

    # deletes shopping list entries not in ingredients list
    def _delete_ingredients(self, ingredients=None):
        if not ingredients:
            return
        to_delete = self._shopping_list_recipe.entries.exclude(ingredient__in=ingredients)
        ShoppingListEntry.objects.filter(id__in=to_delete).delete()
        self._shopping_list_recipe = self.get_shopping_list_recipe(self.id, self.created_by, self.space)


CHANGE_TYPE_ADD = 'add'
CHANGE_TYPE_MERGE = 'merge'
CHANGE_TYPE_REMOVE = 'remove'


@dataclass
class ShoppingSyncChange:
    change_type: str
    mealplan: Optional[MealPlan] = None
    shopping_list_recipe: Optional[ShoppingListRecipe] = None
    recipe: Optional[Recipe] = None
    servings: Optional[Decimal] = None
    ingredients: List[Ingredient] = field(default_factory=list)
    merged_from: List[ShoppingListRecipe] = field(default_factory=list)

    def to_dict(self):
        return {
            'change_type': self.change_type,
            'mealplan_id': self.mealplan.id if self.mealplan else None,
            'mealplan_label': self.mealplan.get_label() if self.mealplan else None,
            'recipe_id': self.recipe.id if self.recipe else None,
            'recipe_name': self.recipe.name if self.recipe else None,
            'servings': float(self.servings) if self.servings else None,
            'ingredients': [
                {
                    'id': ing.id,
                    'food_name': ing.food.name,
                    'amount': float(ing.amount),
                    'unit_name': ing.unit.name if ing.unit else None
                } for ing in self.ingredients
            ],
            'shopping_list_recipe_id': self.shopping_list_recipe.id if self.shopping_list_recipe else None,
            'merged_from_ids': [slr.id for slr in self.merged_from]
        }


@dataclass
class ShoppingSyncPreview:
    changes: List[ShoppingSyncChange] = field(default_factory=list)
    summary: Dict[str, int] = field(default_factory=dict)
    sync_token: Optional[str] = None

    def calculate_summary(self):
        self.summary = {
            CHANGE_TYPE_ADD: len([c for c in self.changes if c.change_type == CHANGE_TYPE_ADD]),
            CHANGE_TYPE_MERGE: len([c for c in self.changes if c.change_type == CHANGE_TYPE_MERGE]),
            CHANGE_TYPE_REMOVE: len([c for c in self.changes if c.change_type == CHANGE_TYPE_REMOVE]),
        }

    def to_dict(self):
        self.calculate_summary()
        return {
            'changes': [c.to_dict() for c in self.changes],
            'summary': self.summary,
            'sync_token': self.sync_token
        }


class MealPlanShoppingSync:
    def __init__(self, user, space, from_date, to_date, **kwargs):
        self.user = user
        self.space = space
        self.from_date = from_date
        self.to_date = to_date
        self.kwargs = kwargs
        self.exclude_onhand = kwargs.get('exclude_onhand', False)
        self.include_related = kwargs.get('include_related', None)

        owner_user_space = user.userspace_set.filter(space=space).first()
        self.household_user_ids = get_household_user_ids(owner_user_space)

        if self.include_related is None:
            self.include_related = user.userpreference.mealplan_autoinclude_related

    def check_permissions(self) -> Tuple[bool, Optional[str]]:
        if not self.user.is_authenticated:
            return False, _('User must be authenticated')

        if not has_group_permission(self.user, ['user']):
            return False, _('You do not have the required permissions')

        return True, None

    def _get_meal_plans(self):
        return MealPlan.objects.filter(
            space=self.space,
            to_date__date__gte=self.from_date,
            from_date__date__lte=self.to_date,
            recipe__isnull=False
        ).filter(
            Q(created_by=self.user) | Q(created_by_id__in=self.household_user_ids)
        ).distinct().select_related('recipe', 'meal_type').prefetch_related('recipe__step_set__ingredients')

    def _get_all_shopping_recipes(self):
        return ShoppingListRecipe.objects.filter(
            space=self.space,
            mealplan__isnull=False
        ).filter(
            Q(entries__created_by=self.user)
            | Q(entries__created_by_id__in=self.household_user_ids)
            | Q(created_by=self.user)
            | Q(created_by_id__in=self.household_user_ids)
        ).distinct().prefetch_related('entries', 'entries__ingredient', 'mealplan')

    def _get_recipe_ingredients(self, recipe_id, exclude_onhand=False):
        if exclude_onhand:
            queryset = Ingredient.objects.filter(step__recipe__id=recipe_id, food__ignore_shopping=False, space=self.space)
            owner_user_space = self.user.userspace_set.filter(space=self.space).first()
            queryset = queryset.exclude(food__onhand_users__id__in=get_household_user_ids(owner_user_space))
            return queryset
        else:
            return Ingredient.objects.filter(step__recipe__id=recipe_id, food__ignore_shopping=False, space=self.space)

    def _get_all_ingredients_for_recipe(self, recipe):
        exclude_onhand = self.exclude_onhand or self.user.userpreference.mealplan_autoexclude_onhand

        if self.include_related:
            related = recipe.get_related_recipes()
            related_ids = [r.id for r in related]
            main_qs = self._get_recipe_ingredients(recipe.id, exclude_onhand=exclude_onhand)
            if related_ids:
                main_qs = main_qs.exclude(food__recipe__in=related_ids)
            ingredient_ids = set()
            result = []
            for ing in main_qs:
                if ing.id and ing.id not in ingredient_ids:
                    ingredient_ids.add(ing.id)
                    result.append(ing)
            for r in related:
                related_qs = self._get_recipe_ingredients(r.id, exclude_onhand=exclude_onhand)
                if related_ids:
                    related_qs = related_qs.exclude(food__recipe__in=related_ids)
                for ing in related_qs:
                    if ing.id and ing.id not in ingredient_ids:
                        ingredient_ids.add(ing.id)
                        result.append(ing)
            return result
        else:
            ingredient_ids = set()
            result = []
            for ing in self._get_recipe_ingredients(recipe.id, exclude_onhand=exclude_onhand):
                if ing.id and ing.id not in ingredient_ids:
                    ingredient_ids.add(ing.id)
                    result.append(ing)
            return result

    def _check_mealplan_permission(self, mealplan) -> bool:
        if is_object_owner(self.user, mealplan):
            return True
        if is_object_household(self.user, mealplan):
            return True
        return False

    def _check_slr_permission(self, slr) -> bool:
        first_entry = slr.entries.first()
        if first_entry:
            if is_object_owner(self.user, first_entry):
                return True
            if is_object_household(self.user, first_entry):
                return True
        if is_object_owner(self.user, slr):
            return True
        if slr.created_by_id in self.household_user_ids:
            return True
        return False

    def _get_existing_ingredient_ids(self, slr):
        return set(
            e.ingredient_id
            for e in slr.entries.all()
            if e.ingredient_id is not None
        )

    def _needs_merge(self, mp, existing_slr, ingredients):
        if not existing_slr:
            return False

        existing_ingredient_ids = self._get_existing_ingredient_ids(existing_slr)
        new_ingredient_ids = set(ing.id for ing in ingredients if ing.id)

        if existing_ingredient_ids != new_ingredient_ids:
            return True

        if existing_slr.servings != mp.servings:
            return True

        return False

    def _generate_sync_token(self) -> str:
        meal_plans = self._get_meal_plans()
        all_slrs = self._get_all_shopping_recipes()

        token_parts = []

        for mp in meal_plans.order_by('id'):
            token_parts.append(f"mp:{mp.id}:{mp.updated_at.isoformat() if mp.updated_at else ''}:{mp.servings}")

        for slr in all_slrs.order_by('id'):
            token_parts.append(f"slr:{slr.id}:{slr.updated_at.isoformat() if slr.updated_at else ''}:{slr.servings}")
            for entry in slr.entries.order_by('id').all():
                token_parts.append(f"sle:{entry.id}:{entry.updated_at.isoformat() if entry.updated_at else ''}")

        token_data = "|".join(token_parts)
        return hashlib.sha256(token_data.encode('utf-8')).hexdigest()

    def validate_sync_token(self, provided_token: Optional[str]) -> Tuple[bool, Optional[str]]:
        if not provided_token:
            return False, _('Sync token is required. Please generate a new preview.')

        current_token = self._generate_sync_token()
        if current_token != provided_token:
            return False, _('Data has been modified by another user. Please generate a new preview and try again.')

        return True, None

    def calculate_changes(self) -> ShoppingSyncPreview:
        preview = ShoppingSyncPreview()

        meal_plans = self._get_meal_plans()
        meal_plan_ids = list(meal_plans.values_list('id', flat=True))
        mp_id_set = set(meal_plan_ids)

        all_slrs = self._get_all_shopping_recipes()
        existing_slr_map = {}
        for slr in all_slrs:
            if slr.mealplan_id and slr.mealplan_id in mp_id_set:
                if slr.mealplan_id not in existing_slr_map or slr.created_by_id == self.user.id:
                    existing_slr_map[slr.mealplan_id] = slr

        food_to_slrs = {}
        for slr in all_slrs:
            if slr.mealplan_id in mp_id_set:
                for entry in slr.entries.all():
                    if entry.food_id:
                        food_to_slrs.setdefault(entry.food_id, []).append(slr)

        for mp in meal_plans:
            if not self._check_mealplan_permission(mp):
                continue

            if not mp.recipe:
                continue

            ingredients = self._get_all_ingredients_for_recipe(mp.recipe)
            existing_slr = existing_slr_map.get(mp.id)

            if existing_slr and self._check_slr_permission(existing_slr):
                if self._needs_merge(mp, existing_slr, ingredients):
                    existing_ingredient_ids = self._get_existing_ingredient_ids(existing_slr)
                    new_ingredient_ids = set(ing.id for ing in ingredients if ing.id)
                    added_ingredients = [ing for ing in ingredients if ing.id in (new_ingredient_ids - existing_ingredient_ids)]

                    preview.changes.append(ShoppingSyncChange(
                        change_type=CHANGE_TYPE_MERGE,
                        mealplan=mp,
                        shopping_list_recipe=existing_slr,
                        recipe=mp.recipe,
                        servings=mp.servings,
                        ingredients=added_ingredients,
                        merged_from=[existing_slr]
                    ))
            elif not existing_slr:
                merge_candidates = []
                for ing in ingredients:
                    if ing.food_id in food_to_slrs:
                        merge_candidates.extend(food_to_slrs[ing.food_id])

                merge_candidates = list(set(merge_candidates))
                merge_candidates = [slr for slr in merge_candidates if self._check_slr_permission(slr)]

                preview.changes.append(ShoppingSyncChange(
                    change_type=CHANGE_TYPE_ADD,
                    mealplan=mp,
                    recipe=mp.recipe,
                    servings=mp.servings,
                    ingredients=ingredients,
                    merged_from=merge_candidates
                ))

        for slr in all_slrs:
            if slr.mealplan_id and slr.mealplan_id not in mp_id_set:
                if self._check_slr_permission(slr):
                    preview.changes.append(ShoppingSyncChange(
                        change_type=CHANGE_TYPE_REMOVE,
                        shopping_list_recipe=slr,
                        recipe=slr.recipe,
                        servings=slr.servings,
                        ingredients=list({e.ingredient for e in slr.entries.all() if e.ingredient})
                    ))

        preview.calculate_summary()
        preview.sync_token = self._generate_sync_token()
        return preview

    def apply_changes(self, preview: ShoppingSyncPreview, selected_changes: Optional[List[int]] = None, sync_token: Optional[str] = None) -> Dict:
        results = {
            'added': 0,
            'merged': 0,
            'removed': 0,
            'failed': 0,
            'errors': [],
            'rolled_back': False,
            'token_invalid': False,
            'token_error': None
        }

        token_valid, token_error = self.validate_sync_token(sync_token)
        if not token_valid:
            results['token_invalid'] = True
            results['token_error'] = token_error
            results['failed'] = len(preview.changes)
            results['errors'].append(f"Sync token validation failed: {token_error}")
            return results

        changes_to_apply = preview.changes
        if selected_changes is not None:
            changes_to_apply = [preview.changes[i] for i in selected_changes if 0 <= i < len(preview.changes)]

        try:
            with transaction.atomic():
                for idx, change in enumerate(changes_to_apply):
                    if change.change_type == CHANGE_TYPE_ADD:
                        self._apply_add(change)
                        results['added'] += 1
                    elif change.change_type == CHANGE_TYPE_MERGE:
                        self._apply_merge(change)
                        results['merged'] += 1
                    elif change.change_type == CHANGE_TYPE_REMOVE:
                        self._apply_remove(change)
                        results['removed'] += 1
        except Exception as e:
            results['rolled_back'] = True
            results['failed'] = len(changes_to_apply)
            results['errors'].append(
                f"Atomic transaction rolled back due to error: {type(e).__name__}: {str(e)}"
            )
            results['added'] = 0
            results['merged'] = 0
            results['removed'] = 0

        return results

    def _apply_add(self, change: ShoppingSyncChange):
        if not change.mealplan or not change.recipe:
            return

        editor = RecipeShoppingEditor(user=self.user, space=self.space)
        editor.create(
            mealplan=change.mealplan,
            servings=change.servings,
            ingredients=[ing.id for ing in change.ingredients] if change.ingredients else None
        )

    def _apply_merge(self, change: ShoppingSyncChange):
        if not change.shopping_list_recipe:
            return

        editor = RecipeShoppingEditor(
            user=self.user,
            space=self.space,
            id=change.shopping_list_recipe.id,
            mealplan=change.mealplan
        )

        if change.mealplan and change.recipe:
            all_ingredients = self._get_all_ingredients_for_recipe(change.recipe)
            ingredient_ids = [ing.id for ing in all_ingredients if ing.id]
            editor.edit(
                servings=change.servings,
                ingredients=ingredient_ids if ingredient_ids else None
            )
        else:
            if change.servings is not None and change.servings != change.shopping_list_recipe.servings:
                editor.edit_servings(servings=change.servings)

    def _apply_remove(self, change: ShoppingSyncChange):
        if not change.shopping_list_recipe:
            return

        editor = RecipeShoppingEditor(
            user=self.user,
            space=self.space,
            id=change.shopping_list_recipe.id
        )
        editor.delete()
