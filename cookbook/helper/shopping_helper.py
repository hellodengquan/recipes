
from decimal import Decimal

from django.db.models import F, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils.translation import gettext as _

from cookbook.connectors.connector_manager import ActionType, ConnectorManager
from cookbook.helper.permission_helper import get_household_user_ids
from cookbook.helper.unit_conversion_helper import ConversionException, UnitConversionHelper
from cookbook.models import Ingredient, InventoryEntry, MealPlan, Recipe, ShoppingListEntry, ShoppingListRecipe, SupermarketCategoryRelation, Household


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


def get_household_inventory_qs(user, space):
    """
    Get a queryset of InventoryEntry objects for the user's household (or just the user if no household).
    """
    owner_user_space = user.userspace_set.filter(space=space).first()
    if not owner_user_space:
        return InventoryEntry.objects.none()
    user_ids = get_household_user_ids(owner_user_space)
    household_ids = list(
        Household.objects.filter(
            userspace__user_id__in=user_ids,
            userspace__space=space
        ).values_list('id', flat=True)
    )
    if len(household_ids) > 0:
        return InventoryEntry.objects.filter(
            inventory_location__household_id__in=household_ids,
            space=space,
            amount__gt=0
        ).select_related('food', 'unit')
    else:
        return InventoryEntry.objects.none()


def get_food_inventory_total(food, target_unit, user, space):
    """
    Calculate the total inventory amount for a given food, converted to the target unit.
    Uses UnitConversionHelper for conversion.
    Returns (total_amount, inventory_details) where inventory_details is a list of (amount, unit) tuples.
    """
    if not food:
        return Decimal('0'), []

    uch = UnitConversionHelper(space)
    inventory_entries = get_household_inventory_qs(user, space).filter(food=food)

    total = Decimal('0')
    details = []

    for entry in inventory_entries:
        entry_amount = entry.amount or Decimal('0')
        if entry_amount <= 0:
            continue

        details.append((entry_amount, entry.unit))

        # If no target unit, just sum up (unlikely case but handle it)
        if not target_unit:
            if not entry.unit:
                total += entry_amount
            continue

        # If same unit, add directly
        if entry.unit and entry.unit.id == target_unit.id:
            total += entry_amount
            continue

        # Try to convert using UnitConversionHelper
        if entry.unit:
            try:
                temp_ingredient = Ingredient(
                    amount=entry_amount,
                    unit=entry.unit,
                    food=food,
                    space=space
                )
                conversions = uch.get_conversions(temp_ingredient)
                # Find a conversion matching the target unit
                for conv in conversions:
                    if conv.unit and conv.unit.id == target_unit.id:
                        total += conv.amount
                        break
            except (ConversionException, Exception):
                pass

    return total, details


def calculate_inventory_deduction(food, required_amount, required_unit, user, space):
    """
    Calculate how much of a food's required amount can be deducted from inventory.
    Returns:
      - amount_needed: amount that still needs to be purchased (after deduction)
      - amount_deducted: amount that was covered by inventory
      - inventory_total: total inventory available (in required_unit if convertible)
      - inventory_details: list of (amount, unit) for each stock entry
    """
    inventory_total, inventory_details = get_food_inventory_total(food, required_unit, user, space)

    if required_amount is None:
        required_amount = Decimal('0')

    if inventory_total <= 0:
        return required_amount, Decimal('0'), Decimal('0'), inventory_details

    amount_deducted = min(required_amount, inventory_total)
    amount_needed = required_amount - amount_deducted

    if amount_needed < 0:
        amount_needed = Decimal('0')

    return amount_needed, amount_deducted, inventory_total, inventory_details


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

    @property
    def _use_inventory_deduction(self):
        return self.created_by.userpreference.shopping_use_inventory_deduction

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
            entry_amount = i.amount * Decimal(self._servings_factor)

            inventory_deducted = Decimal('0')
            inventory_available = Decimal('0')

            if self._use_inventory_deduction:
                entry_amount, inventory_deducted, inventory_available, _ = calculate_inventory_deduction(
                    i.food,
                    entry_amount,
                    i.unit,
                    self.created_by,
                    self.space
                )

            if entry_amount > 0 or inventory_deducted > 0:
                entry = ShoppingListEntry(
                    list_recipe=self._shopping_list_recipe,
                    food=i.food,
                    unit=i.unit,
                    ingredient=i,
                    amount=entry_amount,
                    created_by=self.created_by,
                    space=self.space,
                )
                entry._inventory_deducted_amount = inventory_deducted
                entry._inventory_available_amount = inventory_available
                entries.append(entry)

        created_entries = ShoppingListEntry.objects.bulk_create(entries)
        ConnectorManager.add_work(ActionType.CREATED, *created_entries)
        for e in created_entries:
            if e.food.shopping_lists.count() > 0:
                e.shopping_lists.set(e.food.shopping_lists.all())

    # deletes shopping list entries not in ingredients list
    def _delete_ingredients(self, ingredients=None):
        if not ingredients:
            return
        to_delete = self._shopping_list_recipe.entries.exclude(ingredient__in=ingredients)
        ShoppingListEntry.objects.filter(id__in=to_delete).delete()
        self._shopping_list_recipe = self.get_shopping_list_recipe(self.id, self.created_by, self.space)
