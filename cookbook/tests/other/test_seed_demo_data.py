from io import StringIO

import pytest
from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django_scopes import scopes_disabled

from cookbook.management.commands.seed_demo_data import DEMO_SPACES
from cookbook.models import (
    Food,
    Keyword,
    MealPlan,
    MealType,
    Recipe,
    RecipeBook,
    RecipeBookEntry,
    Space,
    Unit,
    UserSpace,
)

pytestmark = pytest.mark.django_db


EXPECTED_COUNTS = {}
for space_config in DEMO_SPACES:
    recipe_book_count = len(space_config['recipe_books'])
    recipe_book_entry_count = sum(
        len(book['recipes']) for book in space_config['recipe_books']
    )
    total_steps = sum(
        len(recipe['steps']) for recipe in space_config['recipes']
    )
    total_ingredients = sum(
        sum(
            len(step['ingredients']) for step in recipe['steps']
        )
        for recipe in space_config['recipes']
    )

    EXPECTED_COUNTS[space_config['name']] = {
        'users': len(space_config['users']),
        'keywords': len(space_config['keywords']),
        'units': len(space_config['units']),
        'foods': len(space_config['foods']),
        'meal_types': len(space_config['meal_types']),
        'recipes': len(space_config['recipes']),
        'recipe_books': recipe_book_count,
        'recipe_book_entries': recipe_book_entry_count,
        'steps': total_steps,
        'ingredients': total_ingredients,
    }


def _get_space_counts(space):
    with scopes_disabled():
        return {
            'users': UserSpace.objects.filter(space=space).count(),
            'keywords': Keyword.objects.filter(space=space).count(),
            'units': Unit.objects.filter(space=space).count(),
            'foods': Food.objects.filter(space=space).count(),
            'meal_types': MealType.objects.filter(space=space).count(),
            'recipes': Recipe.objects.filter(space=space).count(),
            'recipe_books': RecipeBook.objects.filter(space=space).count(),
            'recipe_book_entries': RecipeBookEntry.objects.filter(book__space=space).count(),
        }


def _get_global_counts():
    with scopes_disabled():
        return {
            'spaces': Space.objects.count(),
            'users': User.objects.filter(username__in=[
                u['username'] for s in DEMO_SPACES for u in s['users']
            ]).count(),
            'meal_plans': MealPlan.objects.count(),
            'groups': Group.objects.count(),
        }


class TestSeedDemoDataFirstRun:
    """Test that seed_demo_data creates all expected data correctly on first run."""

    def test_command_runs_successfully(self):
        out = StringIO()
        call_command('seed_demo_data', stdout=out)
        output = out.getvalue()

        for space_config in DEMO_SPACES:
            assert f'Processing space: {space_config["name"]}' in output

        assert 'Demo data seeding completed successfully' in output

    def test_spaces_created(self):
        call_command('seed_demo_data')

        with scopes_disabled():
            space_names = list(Space.objects.values_list('name', flat=True))

        for space_config in DEMO_SPACES:
            assert space_config['name'] in space_names

        assert len(space_names) == len(DEMO_SPACES)

    def test_default_groups_created(self):
        call_command('seed_demo_data')

        with scopes_disabled():
            group_names = list(Group.objects.values_list('name', flat=True))

        assert 'admin' in group_names
        assert 'user' in group_names
        assert 'guest' in group_names

    def test_users_created_with_correct_attributes(self):
        call_command('seed_demo_data')

        with scopes_disabled():
            for space_config in DEMO_SPACES:
                for user_config in space_config['users']:
                    user = User.objects.get(username=user_config['username'])
                    assert user.email == f"{user_config['username']}@example.com"
                    assert user.first_name == user_config.get('first_name', '')
                    assert user.last_name == user_config.get('last_name', '')
                    assert user.check_password(user_config['password'])

    def test_user_space_assignments_and_groups(self):
        call_command('seed_demo_data')

        with scopes_disabled():
            for space_config in DEMO_SPACES:
                space = Space.objects.get(name=space_config['name'])

                for user_config in space_config['users']:
                    user = User.objects.get(username=user_config['username'])
                    user_space = UserSpace.objects.get(user=user, space=space)

                    assert user_space.active is True
                    assert user_space.groups.filter(name=user_config['group']).exists()
                    assert user_space.groups.count() == 1

    def test_space_data_counts_match_expected(self):
        call_command('seed_demo_data')

        with scopes_disabled():
            for space_config in DEMO_SPACES:
                space = Space.objects.get(name=space_config['name'])
                actual = _get_space_counts(space)
                expected = EXPECTED_COUNTS[space_config['name']]

                for key in expected:
                    assert actual[key] == expected[key], (
                        f"Space '{space.name}' {key} count mismatch: "
                        f"expected {expected[key]}, got {actual[key]}"
                    )

    def test_recipes_belong_to_correct_space(self):
        call_command('seed_demo_data')

        with scopes_disabled():
            for space_config in DEMO_SPACES:
                space = Space.objects.get(name=space_config['name'])
                recipe_names = [r['name'] for r in space_config['recipes']]

                space_recipes = Recipe.objects.filter(space=space)
                assert space_recipes.count() == len(recipe_names)

                for recipe in space_recipes:
                    assert recipe.name in recipe_names
                    assert recipe.created_by is not None
                    assert recipe.internal is False

    def test_recipe_keywords_assigned(self):
        call_command('seed_demo_data')

        with scopes_disabled():
            for space_config in DEMO_SPACES:
                space = Space.objects.get(name=space_config['name'])

                for recipe_config in space_config['recipes']:
                    recipe = Recipe.objects.get(name=recipe_config['name'], space=space)
                    expected_keywords = recipe_config['keywords']

                    assert recipe.keywords.count() == len(expected_keywords)
                    for kw_name in expected_keywords:
                        assert recipe.keywords.filter(name=kw_name, space=space).exists()

    def test_recipe_steps_and_ingredients(self):
        call_command('seed_demo_data')

        with scopes_disabled():
            for space_config in DEMO_SPACES:
                space = Space.objects.get(name=space_config['name'])

                for recipe_config in space_config['recipes']:
                    recipe = Recipe.objects.get(name=recipe_config['name'], space=space)

                    assert recipe.steps.count() == len(recipe_config['steps'])

                    for i, step_config in enumerate(recipe_config['steps']):
                        step = recipe.steps.get(order=i)
                        assert step.name == step_config['name']
                        assert step.instruction == step_config['instruction']
                        assert step.ingredients.count() == len(step_config['ingredients'])

    def test_recipe_books_and_entries(self):
        call_command('seed_demo_data')

        with scopes_disabled():
            for space_config in DEMO_SPACES:
                space = Space.objects.get(name=space_config['name'])

                for book_config in space_config['recipe_books']:
                    book = RecipeBook.objects.get(name=book_config['name'], space=space)
                    assert book.description == book_config['description']
                    assert book.created_by is not None

                    entry_recipes = [
                        entry.recipe.name for entry in RecipeBookEntry.objects.filter(book=book)
                    ]
                    assert sorted(entry_recipes) == sorted(book_config['recipes'])

    def test_meal_plans_created(self, settings):
        settings.USE_TZ = True
        call_command('seed_demo_data', '--mealplan-days', '7')

        with scopes_disabled():
            for space_config in DEMO_SPACES:
                space = Space.objects.get(name=space_config['name'])
                meal_type_count = min(len(space_config['meal_types']), 3)
                expected_meal_plans = 7 * meal_type_count

                actual = MealPlan.objects.filter(space=space).count()
                assert actual == expected_meal_plans, (
                    f"Space '{space.name}' meal plan count mismatch: "
                    f"expected {expected_meal_plans}, got {actual}"
                )

                for mp in MealPlan.objects.filter(space=space):
                    assert mp.meal_type.space == space
                    assert mp.recipe.space == space
                    assert mp.created_by is not None


class TestSeedDemoDataIdempotency:
    """Test that running seed_demo_data twice produces identical results."""

    def test_counts_unchanged_after_second_run(self):
        call_command('seed_demo_data', '--mealplan-days', '7')

        with scopes_disabled():
            before_spaces = _get_global_counts()

            before_space_data = {}
            for space_config in DEMO_SPACES:
                space = Space.objects.get(name=space_config['name'])
                before_space_data[space.name] = {
                    'counts': _get_space_counts(space),
                    'meal_plans': MealPlan.objects.filter(space=space).count(),
                    'recipe_ids': sorted(Recipe.objects.filter(space=space).values_list('id', flat=True)),
                    'user_ids': sorted(UserSpace.objects.filter(space=space).values_list('user_id', flat=True)),
                    'keyword_ids': sorted(Keyword.objects.filter(space=space).values_list('id', flat=True)),
                }

        call_command('seed_demo_data', '--mealplan-days', '7')

        with scopes_disabled():
            after_spaces = _get_global_counts()
            assert after_spaces == before_spaces

            for space_config in DEMO_SPACES:
                space = Space.objects.get(name=space_config['name'])
                after_counts = _get_space_counts(space)
                assert after_counts == before_space_data[space.name]['counts']

                after_meal_plans = MealPlan.objects.filter(space=space).count()
                assert after_meal_plans == before_space_data[space.name]['meal_plans']

                after_recipe_ids = sorted(Recipe.objects.filter(space=space).values_list('id', flat=True))
                assert after_recipe_ids == before_space_data[space.name]['recipe_ids']

                after_user_ids = sorted(UserSpace.objects.filter(space=space).values_list('user_id', flat=True))
                assert after_user_ids == before_space_data[space.name]['user_ids']

                after_keyword_ids = sorted(Keyword.objects.filter(space=space).values_list('id', flat=True))
                assert after_keyword_ids == before_space_data[space.name]['keyword_ids']

    def test_recipe_content_unchanged_after_second_run(self):
        call_command('seed_demo_data')

        with scopes_disabled():
            before_recipes = {}
            for space_config in DEMO_SPACES:
                space = Space.objects.get(name=space_config['name'])
                for recipe in Recipe.objects.filter(space=space):
                    before_recipes[recipe.id] = {
                        'name': recipe.name,
                        'description': recipe.description,
                        'servings': recipe.servings,
                        'working_time': recipe.working_time,
                        'waiting_time': recipe.waiting_time,
                        'keywords_count': recipe.keywords.count(),
                        'steps_count': recipe.steps.count(),
                    }

        call_command('seed_demo_data')

        with scopes_disabled():
            for recipe_id, before_data in before_recipes.items():
                recipe = Recipe.objects.get(id=recipe_id)
                assert recipe.name == before_data['name']
                assert recipe.description == before_data['description']
                assert recipe.servings == before_data['servings']
                assert recipe.working_time == before_data['working_time']
                assert recipe.waiting_time == before_data['waiting_time']
                assert recipe.keywords.count() == before_data['keywords_count']
                assert recipe.steps.count() == before_data['steps_count']

    def test_no_duplicate_entries_created(self):
        call_command('seed_demo_data')
        call_command('seed_demo_data')

        with scopes_disabled():
            for space_config in DEMO_SPACES:
                space = Space.objects.get(name=space_config['name'])

                recipe_names = Recipe.objects.filter(space=space).values_list('name', flat=True)
                assert len(recipe_names) == len(set(recipe_names)), (
                    f"Duplicate recipe names found in space '{space.name}'"
                )

                book_names = RecipeBook.objects.filter(space=space).values_list('name', flat=True)
                assert len(book_names) == len(set(book_names)), (
                    f"Duplicate recipe book names found in space '{space.name}'"
                )

                keyword_names = Keyword.objects.filter(space=space).values_list('name', flat=True)
                assert len(keyword_names) == len(set(keyword_names)), (
                    f"Duplicate keyword names found in space '{space.name}'"
                )

                food_names = Food.objects.filter(space=space).values_list('name', flat=True)
                assert len(food_names) == len(set(food_names)), (
                    f"Duplicate food names found in space '{space.name}'"
                )

                unit_names = Unit.objects.filter(space=space).values_list('name', flat=True)
                assert len(unit_names) == len(set(unit_names)), (
                    f"Duplicate unit names found in space '{space.name}'"
                )

    def test_meal_plans_not_duplicated(self):
        call_command('seed_demo_data', '--mealplan-days', '3')

        with scopes_disabled():
            before_count = MealPlan.objects.count()

        call_command('seed_demo_data', '--mealplan-days', '3')

        with scopes_disabled():
            after_count = MealPlan.objects.count()
            assert after_count == before_count, (
                f"Meal plan count changed: {before_count} -> {after_count}"
            )


class TestSeedDemoDataSpaceIsolation:
    """Test that data is properly isolated between different spaces."""

    def test_keywords_isolated_between_spaces(self):
        call_command('seed_demo_data')

        with scopes_disabled():
            space1 = Space.objects.get(name=DEMO_SPACES[0]['name'])
            space2 = Space.objects.get(name=DEMO_SPACES[1]['name'])

            kw1 = set(Keyword.objects.filter(space=space1).values_list('name', flat=True))
            kw2 = set(Keyword.objects.filter(space=space2).values_list('name', flat=True))

            common = kw1 & kw2
            for name in common:
                kw_in_space1 = Keyword.objects.get(name=name, space=space1)
                kw_in_space2 = Keyword.objects.get(name=name, space=space2)
                assert kw_in_space1.id != kw_in_space2.id, (
                    f"Keyword '{name}' is shared between spaces"
                )

    def test_foods_isolated_between_spaces(self):
        call_command('seed_demo_data')

        with scopes_disabled():
            space1 = Space.objects.get(name=DEMO_SPACES[0]['name'])
            space2 = Space.objects.get(name=DEMO_SPACES[1]['name'])

            foods1 = set(Food.objects.filter(space=space1).values_list('name', flat=True))
            foods2 = set(Food.objects.filter(space=space2).values_list('name', flat=True))

            common = foods1 & foods2
            for name in common:
                food_in_space1 = Food.objects.get(name=name, space=space1)
                food_in_space2 = Food.objects.get(name=name, space=space2)
                assert food_in_space1.id != food_in_space2.id, (
                    f"Food '{name}' is shared between spaces"
                )

    def test_units_isolated_between_spaces(self):
        call_command('seed_demo_data')

        with scopes_disabled():
            space1 = Space.objects.get(name=DEMO_SPACES[0]['name'])
            space2 = Space.objects.get(name=DEMO_SPACES[1]['name'])

            units1 = set(Unit.objects.filter(space=space1).values_list('name', flat=True))
            units2 = set(Unit.objects.filter(space=space2).values_list('name', flat=True))

            common = units1 & units2
            for name in common:
                unit_in_space1 = Unit.objects.get(name=name, space=space1)
                unit_in_space2 = Unit.objects.get(name=name, space=space2)
                assert unit_in_space1.id != unit_in_space2.id, (
                    f"Unit '{name}' is shared between spaces"
                )

    def test_recipes_isolated_between_spaces(self):
        call_command('seed_demo_data')

        with scopes_disabled():
            all_recipes = Recipe.objects.all()
            space_ids = set(all_recipes.values_list('space_id', flat=True))
            expected_space_ids = set(
                Space.objects.filter(name__in=[s['name'] for s in DEMO_SPACES]).values_list('id', flat=True)
            )
            assert space_ids == expected_space_ids

            for space_config in DEMO_SPACES:
                space = Space.objects.get(name=space_config['name'])
                other_spaces = Space.objects.exclude(id=space.id)

                for recipe in Recipe.objects.filter(space=space):
                    assert recipe.space == space
                    assert not Recipe.objects.filter(
                        name=recipe.name, space__in=other_spaces
                    ).exists(), (
                        f"Recipe '{recipe.name}' appears in multiple spaces"
                    )

    def test_recipe_books_isolated_between_spaces(self):
        call_command('seed_demo_data')

        with scopes_disabled():
            for space_config in DEMO_SPACES:
                space = Space.objects.get(name=space_config['name'])
                other_spaces = Space.objects.exclude(id=space.id)

                for book in RecipeBook.objects.filter(space=space):
                    assert book.space == space
                    assert not RecipeBook.objects.filter(
                        name=book.name, space__in=other_spaces
                    ).exists(), (
                        f"Recipe book '{book.name}' appears in multiple spaces"
                    )

    def test_meal_plans_isolated_between_spaces(self):
        call_command('seed_demo_data', '--mealplan-days', '3')

        with scopes_disabled():
            all_meal_plans = MealPlan.objects.all()
            space_ids = set(all_meal_plans.values_list('space_id', flat=True))
            expected_space_ids = set(
                Space.objects.filter(name__in=[s['name'] for s in DEMO_SPACES]).values_list('id', flat=True)
            )
            assert space_ids == expected_space_ids

            for mp in MealPlan.objects.all():
                assert mp.space == mp.recipe.space
                assert mp.space == mp.meal_type.space

    def test_users_can_access_only_assigned_spaces(self):
        call_command('seed_demo_data')

        with scopes_disabled():
            for space_config in DEMO_SPACES:
                space = Space.objects.get(name=space_config['name'])
                other_space = Space.objects.exclude(id=space.id).first()

                for user_config in space_config['users']:
                    user = User.objects.get(username=user_config['username'])

                    user_spaces = set(
                        UserSpace.objects.filter(user=user).values_list('space_id', flat=True)
                    )

                    assert space.id in user_spaces
                    if other_space:
                        assert other_space.id not in user_spaces, (
                            f"User '{user.username}' has access to space '{other_space.name}' "
                            f"which they should not be assigned to"
                        )


class TestSeedDemoDataOptions:
    """Test command-line options for seed_demo_data."""

    def test_spaces_option_filters_spaces(self):
        target_space = DEMO_SPACES[0]['name']
        call_command('seed_demo_data', '--spaces', target_space)

        with scopes_disabled():
            assert Space.objects.count() == 1
            assert Space.objects.first().name == target_space

            for space_config in DEMO_SPACES:
                if space_config['name'] == target_space:
                    expected_users = [u['username'] for u in space_config['users']]
                    actual_users = list(User.objects.values_list('username', flat=True))
                    assert sorted(actual_users) == sorted(expected_users)

    def test_reset_option_clears_and_recreates(self):
        call_command('seed_demo_data')

        with scopes_disabled():
            before_space_ids = sorted(Space.objects.values_list('id', flat=True))
            before_user_ids = sorted(User.objects.values_list('id', flat=True))

        call_command('seed_demo_data', '--reset')

        with scopes_disabled():
            after_space_ids = sorted(Space.objects.values_list('id', flat=True))
            after_user_ids = sorted(User.objects.values_list('id', flat=True))

            assert len(after_space_ids) == len(before_space_ids)
            assert len(after_user_ids) == len(before_user_ids)
            assert before_space_ids != after_space_ids, "Space IDs should be different after reset"
            assert before_user_ids != after_user_ids, "User IDs should be different after reset"

    def test_mealplan_days_option(self):
        call_command('seed_demo_data', '--mealplan-days', '2')

        with scopes_disabled():
            for space_config in DEMO_SPACES:
                space = Space.objects.get(name=space_config['name'])
                meal_type_count = min(len(space_config['meal_types']), 3)
                expected = 2 * meal_type_count
                actual = MealPlan.objects.filter(space=space).count()
                assert actual == expected, (
                    f"Expected {expected} meal plans, got {actual}"
                )
