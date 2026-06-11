import json
from dataclasses import dataclass
from io import BytesIO
from unittest.mock import MagicMock, patch, PropertyMock
from zipfile import ZipFile

import pytest
from django.contrib import auth
from django.test import RequestFactory
from django_scopes import scope

from cookbook.integration.integration import (
    Integration,
    ImportContext,
    ImportError,
    ERROR_SEPARATOR,
    ERROR_PREFIX,
    MSG_RECIPE_PROCESSED_TPL,
    MSG_DUPLICATES_IGNORED,
    MSG_EXPECTED_ZIP,
    MSG_UNEXPECTED_ERROR,
)
from cookbook.models import ImportLog, Keyword, Recipe


def _make_request(u1_s1):
    user = auth.get_user(u1_s1)
    space = user.userspace_set.first().space
    req = RequestFactory()
    req.user = user
    req.space = space
    return req


def _make_import_log(space, user):
    return ImportLog.objects.create(
        type='default',
        created_by=user,
        space=space,
        msg='',
    )


@dataclass
class RecipeSnapshot:
    name: str
    servings: int
    working_time: int
    waiting_time: int
    description: str
    source_url: str
    internal: bool
    step_count: int
    step_instructions: list
    ingredient_counts: list
    keyword_names: list
    has_image: bool


def _snapshot_recipe(recipe):
    steps = list(recipe.steps.all())
    return RecipeSnapshot(
        name=recipe.name,
        servings=recipe.servings,
        working_time=recipe.working_time,
        waiting_time=recipe.waiting_time,
        description=recipe.description,
        source_url=recipe.source_url,
        internal=recipe.internal,
        step_count=len(steps),
        step_instructions=[s.instruction for s in steps],
        ingredient_counts=[s.ingredients.count() for s in steps],
        keyword_names=[k.name for k in recipe.keywords.all()],
        has_image=bool(recipe.image),
    )


def _assert_recipe_match(snapshot, recipe):
    assert snapshot.name == recipe.name, f'name mismatch: {snapshot.name!r} != {recipe.name!r}'
    assert snapshot.servings == recipe.servings, f'servings mismatch'
    assert snapshot.working_time == recipe.working_time, f'working_time mismatch'
    assert snapshot.waiting_time == recipe.waiting_time, f'waiting_time mismatch'
    assert snapshot.description == recipe.description, f'description mismatch'
    assert snapshot.source_url == recipe.source_url, f'source_url mismatch'
    assert snapshot.internal == recipe.internal, f'internal mismatch'

    steps = list(recipe.steps.all())
    assert snapshot.step_count == len(steps), f'step_count mismatch: {snapshot.step_count} != {len(steps)}'
    for i, s in enumerate(steps):
        assert snapshot.step_instructions[i] == s.instruction, f'step[{i}] instruction mismatch'
        assert snapshot.ingredient_counts[i] == s.ingredients.count(), f'step[{i}] ingredient count mismatch'

    actual_kw = sorted([k.name for k in recipe.keywords.all()])
    assert sorted(snapshot.keyword_names) == actual_kw, f'keyword mismatch: {sorted(snapshot.keyword_names)} != {actual_kw}'


def _assert_error_structure(ctx, expected_error_count=None, expected_filenames=None):
    if expected_error_count is not None:
        assert len(ctx.errors) == expected_error_count, (
            f'error count mismatch: expected {expected_error_count}, got {len(ctx.errors)}'
        )
    if expected_filenames is not None:
        actual_filenames = [e.filename for e in ctx.errors]
        assert actual_filenames == expected_filenames, (
            f'error filenames mismatch: {actual_filenames} != {expected_filenames}'
        )
    for err in ctx.errors:
        formatted = err.format()
        assert formatted.startswith(ERROR_SEPARATOR), f'error must start with separator'
        assert ERROR_PREFIX in formatted, f'error must contain ERROR prefix'


def _zip_bytes(files_dict):
    buf = BytesIO()
    with ZipFile(buf, 'w') as zf:
        for name, content in files_dict.items():
            if isinstance(content, str):
                content = content.encode('utf-8')
            zf.writestr(name, content)
    buf.seek(0)
    return buf


# ========================================================================
# 1. Integration base class – unified strategy interface
# ========================================================================


class TestImportErrorDataclass:

    def test_format_with_filename(self):
        err = ImportError(message='parse failed', filename='recipe.json')
        formatted = err.format()
        assert formatted.count(ERROR_SEPARATOR) == 2
        assert ERROR_PREFIX in formatted
        assert 'IMPORTING recipe.json' in formatted
        assert 'parse failed' in formatted

    def test_format_without_filename(self):
        err = ImportError(message='generic error')
        formatted = err.format()
        assert formatted.count(ERROR_SEPARATOR) == 2
        assert ERROR_PREFIX in formatted
        assert 'generic error' in formatted
        assert 'IMPORTING' not in formatted

    def test_exception_stored(self):
        exc = ValueError('bad')
        err = ImportError(message='msg', exception=exc)
        assert err.exception is exc


class TestImportContext:

    def test_defaults(self):
        ctx = ImportContext()
        assert ctx.errors == []
        assert ctx.ignored_recipes == []
        assert ctx.imported_recipes == 0
        assert ctx.import_duplicates is False

    def test_add_error(self):
        ctx = ImportContext()
        ctx.add_error('e1', filename='a.json')
        ctx.add_error('e2')
        assert len(ctx.errors) == 2
        assert ctx.errors[0].filename == 'a.json'
        assert ctx.errors[1].filename is None

    def test_get_error_messages(self):
        ctx = ImportContext()
        ctx.add_error('alpha')
        ctx.add_error('beta', filename='b.xml')
        msgs = ctx.get_error_messages()
        assert 'alpha' in msgs
        assert 'beta' in msgs
        assert 'IMPORTING b.xml' in msgs


class TestDetectFileCategory:

    @pytest.fixture
    def integration(self, u1_s1):
        req = _make_request(u1_s1)
        with scope(space=req.space):
            return Integration(req, 'export')

    @pytest.mark.parametrize('filename,expected', [
        ('RecipeKeeper_export.zip', 'recipekeeper_zip'),
        ('recipes.zip', 'generic_zip'),
        ('data.paprikarecipes', 'generic_zip'),
        ('export.mcb', 'generic_zip'),
        ('backup.rtk', 'rtk_zip'),
        ('recipes.json', 'single_data'),
        ('recipes.xml', 'single_data'),
        ('recipes.txt', 'single_data'),
        ('recipes.mmf', 'single_data'),
        ('recipes.rk', 'single_data'),
        ('recipes.melarecipe', 'single_data'),
        ('unknown.dat', 'raw_file'),
        ('noext', 'raw_file'),
    ])
    def test_category_detection(self, integration, filename, expected):
        assert integration._detect_file_category(filename) == expected


class TestFormatRecipeProcessed:

    @pytest.fixture
    def integration(self, u1_s1):
        req = _make_request(u1_s1)
        with scope(space=req.space):
            return Integration(req, 'export')

    def test_format(self, integration, space_1):
        with scope(space=space_1):
            recipe = Recipe.objects.create(
                name='Test Recipe', created_by=integration.request.user,
                internal=True, space=space_1,
            )
            result = integration._format_recipe_processed(recipe)
            assert result == MSG_RECIPE_PROCESSED_TPL.format(pk=recipe.pk, name=recipe.name)
            assert str(recipe.pk) in result
            assert 'Test Recipe' in result


class TestHandleDuplicates:

    @pytest.fixture
    def integration(self, u1_s1):
        req = _make_request(u1_s1)
        with scope(space=req.space):
            return Integration(req, 'export')

    def test_duplicate_ignored(self, integration, space_1):
        with scope(space=space_1):
            r1 = Recipe.objects.create(
                name='Dup', created_by=integration.request.user,
                internal=True, space=space_1,
            )
            r2 = Recipe.objects.create(
                name='Dup', created_by=integration.request.user,
                internal=True, space=space_1,
            )
            ctx = ImportContext()
            integration._ctx = ctx
            integration.handle_duplicates(r2, import_duplicates=False)
            assert 'Dup' in integration.ignored_recipes
            assert 'Dup' in ctx.ignored_recipes
            assert not Recipe.objects.filter(pk=r2.pk).exists()

    def test_duplicate_kept(self, integration, space_1):
        with scope(space=space_1):
            r1 = Recipe.objects.create(
                name='Dup', created_by=integration.request.user,
                internal=True, space=space_1,
            )
            r2 = Recipe.objects.create(
                name='Dup', created_by=integration.request.user,
                internal=True, space=space_1,
            )
            integration._ctx = ImportContext()
            integration.handle_duplicates(r2, import_duplicates=True)
            assert Recipe.objects.filter(pk=r2.pk).exists()


class TestLogWarningAndError:

    @pytest.fixture
    def integration(self, u1_s1):
        req = _make_request(u1_s1)
        with scope(space=req.space):
            il = _make_import_log(req.space, req.user)
            integ = Integration(req, 'export')
            integ._ctx = ImportContext(import_log=il)
            integ.import_log = il
            return integ

    def test_log_warning_no_context(self, integration):
        integration._log_warning('something went wrong')
        assert 'WARN' in integration._ctx.import_log.msg
        assert 'something went wrong' in integration._ctx.import_log.msg

    def test_log_warning_with_context(self, integration):
        integration._log_warning('bad image', context='Pancakes')
        assert 'WARN [Pancakes] bad image' in integration._ctx.import_log.msg

    def test_log_error_with_exception(self, integration):
        integration._log_error('fatal', exception=ValueError('x'))
        assert 'ERROR' in integration._ctx.import_log.msg
        assert 'fatal' in integration._ctx.import_log.msg
        assert len(integration._ctx.errors) == 1


# ========================================================================
# 2. Domestica – JSON importer
# ========================================================================

class TestDomesticaRegression:

    SAMPLE = {
        'name': 'Domestica Test Recipe',
        'servings': 4,
        'timeCook': 30,
        'timePrep': 15,
        'directions': 'Mix and bake.',
        'source': 'https://example.com',
        'ingredients': '2 cups flour\n1 tsp salt\n3 eggs',
        'image': '',
    }

    def test_parse_produces_correct_recipe(self, u1_s1):
        from cookbook.integration.domestica import Domestica
        req = _make_request(u1_s1)
        space = req.space
        with scope(space=space):
            integ = Domestica(req, 'export')
            il = _make_import_log(space, req.user)
            integ._ctx = ImportContext(import_log=il)
            integ.import_log = il
            recipe = integ.get_recipe_from_file(self.SAMPLE)
            snap = _snapshot_recipe(recipe)
            assert snap.name == 'Domestica Test Recipe'
            assert snap.servings == 4
            assert snap.waiting_time == 30
            assert snap.working_time == 15
            assert snap.step_count == 1
            assert 'Mix and bake.' in snap.step_instructions[0]
            assert snap.ingredient_counts[0] == 3

    def test_empty_servings(self, u1_s1):
        from cookbook.integration.domestica import Domestica
        req = _make_request(u1_s1)
        space = req.space
        sample = {**self.SAMPLE, 'servings': '', 'timeCook': '', 'timePrep': ''}
        with scope(space=space):
            integ = Domestica(req, 'export')
            il = _make_import_log(space, req.user)
            integ._ctx = ImportContext(import_log=il)
            integ.import_log = il
            recipe = integ.get_recipe_from_file(sample)
            assert recipe.name == 'Domestica Test Recipe'


# ========================================================================
# 3. Saffron – text importer
# ========================================================================

class TestSaffronRegression:

    SAMPLE = b"""Title: Saffron Rice
Description: A simple rice dish
Yield: 4 servings
Prep: 10 mins
Cook: 20 mins
Ingredients:
2 cups rice
1 pinch saffron
3 cups water
Instructions:
Wash rice.
Add water and saffron.
Cook for 20 minutes.
"""

    def test_parse_produces_correct_recipe(self, u1_s1):
        from cookbook.integration.saffron import Saffron
        req = _make_request(u1_s1)
        space = req.space
        with scope(space=space):
            integ = Saffron(req, 'export')
            il = _make_import_log(space, req.user)
            integ._ctx = ImportContext(import_log=il)
            integ.import_log = il
            recipe = integ.get_recipe_from_file(BytesIO(self.SAMPLE))
            snap = _snapshot_recipe(recipe)
            assert snap.name == 'Saffron Rice'
            assert snap.description == 'A simple rice dish'
            assert snap.step_count == 1
            assert snap.ingredient_counts[0] == 3
            assert 'Wash rice.' in snap.step_instructions[0]


# ========================================================================
# 4. RecipeSage – JSON importer
# ========================================================================

class TestRecipeSageRegression:

    SAMPLE = {
        'name': 'Sage Cookies',
        'recipeYield': '12 cookies',
        'totalTime': 'PT45M',
        'prepTime': 'PT15M',
        'timePrep': 'PT15M',
        'isBasedOn': 'https://sage.example.com',
        'description': 'Delicious cookies from RecipeSage',
        'recipeInstructions': [
            {'@type': 'HowToStep', 'text': 'Mix dry ingredients'},
            {'@type': 'HowToStep', 'text': 'Add wet ingredients and bake'},
        ],
        'recipeIngredient': [
            '2 cups flour',
            '1 cup sugar',
            '2 eggs',
        ],
        'image': [],
        'recipeCategory': ['Dessert', 'Baking'],
    }

    def test_parse_produces_correct_recipe(self, u1_s1):
        from cookbook.integration.recipesage import RecipeSage
        req = _make_request(u1_s1)
        space = req.space
        with scope(space=space):
            integ = RecipeSage(req, 'export')
            il = _make_import_log(space, req.user)
            integ._ctx = ImportContext(import_log=il)
            integ.import_log = il
            recipe = integ.get_recipe_from_file(self.SAMPLE)
            snap = _snapshot_recipe(recipe)
            assert snap.name == 'Sage Cookies'
            assert snap.step_count == 2
            assert snap.ingredient_counts[0] == 3
            assert snap.keyword_names == ['Dessert', 'Baking']

    def test_split_recipe_file_list(self, u1_s1):
        from cookbook.integration.recipesage import RecipeSage
        req = _make_request(u1_s1)
        with scope(space=req.space):
            integ = RecipeSage(req, 'export')
            data = json.dumps({'recipes': [self.SAMPLE, self.SAMPLE]}).encode('utf-8')
            result = integ.split_recipe_file(BytesIO(data))
            assert len(result) == 2

    def test_split_recipe_file_single(self, u1_s1):
        from cookbook.integration.recipesage import RecipeSage
        req = _make_request(u1_s1)
        with scope(space=req.space):
            integ = RecipeSage(req, 'export')
            data = json.dumps(self.SAMPLE).encode('utf-8')
            result = integ.split_recipe_file(BytesIO(data))
            assert isinstance(result, dict)

    def test_split_raises_on_invalid(self, u1_s1):
        from cookbook.integration.recipesage import RecipeSage
        req = _make_request(u1_s1)
        with scope(space=req.space):
            integ = RecipeSage(req, 'export')
            il = _make_import_log(req.space, req.user)
            integ._ctx = ImportContext(import_log=il)
            integ.import_log = il
            with pytest.raises(Exception):
                integ.split_recipe_file(BytesIO(b'not json at all'))


# ========================================================================
# 5. RecetteTek – JSON/RTK importer
# ========================================================================

class TestRecetteTekRegression:

    SAMPLE_JSON = [
        {
            'title': 'RTK Quiche',
            'description': 'A quiche recipe',
            'instructions': 'Mix and bake',
            'url': 'https://rtk.example.com',
            'ingredients': '3 eggs\n200ml cream\n1 pie crust',
            'quantity': '4 servings',
            'totalTime': '45',
            'preparationTime': '15',
            'cookingTime': '30',
            'keywords': 'baking;french',
            'pictures': [''],
            'originalPicture': '',
        }
    ]

    def test_parse_produces_correct_recipe(self, u1_s1):
        from cookbook.integration.recettetek import RecetteTek
        req = _make_request(u1_s1)
        space = req.space
        with scope(space=space):
            integ = RecetteTek(req, 'export')
            il = _make_import_log(space, req.user)
            integ._ctx = ImportContext(import_log=il)
            integ.import_log = il
            integ.files = []
            recipe = integ.get_recipe_from_file(self.SAMPLE_JSON[0])
            snap = _snapshot_recipe(recipe)
            assert snap.name == 'RTK Quiche'
            assert snap.step_count == 1
            assert snap.ingredient_counts[0] == 3
            assert snap.working_time == 15
            assert snap.waiting_time == 30
            assert snap.keyword_names == ['baking', 'french']

    def test_empty_fields_use_defaults(self, u1_s1):
        from cookbook.integration.recettetek import RecetteTek
        req = _make_request(u1_s1)
        space = req.space
        minimal = {
            'title': 'Minimal',
            'description': '',
            'instructions': '',
            'url': '',
            'ingredients': '',
            'quantity': '',
            'totalTime': '',
            'preparationTime': '',
            'cookingTime': '',
            'keywords': '',
            'pictures': [],
            'originalPicture': '',
        }
        with scope(space=space):
            integ = RecetteTek(req, 'export')
            il = _make_import_log(space, req.user)
            integ._ctx = ImportContext(import_log=il)
            integ.import_log = il
            integ.files = []
            recipe = integ.get_recipe_from_file(minimal)
            assert recipe.name == 'Minimal'
            assert recipe.working_time == 0
            assert recipe.waiting_time == 0


# ========================================================================
# 6. Rezeptsuitede – XML importer
# ========================================================================

class TestRezeptsuitedeRegression:

    SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rezeptesammlung>
  <rezept>
    <head title="Kartoffelsuppe" servingtype="4 Portionen"/>
    <remark><line>Ein einfaches Suppenrezept</line></remark>
    <preparation><step>Die Kartoffeln schälen und kochen.</step></preparation>
    <part>
      <ingredient item="Kartoffeln" unit="kg" qty="1"/>
      <ingredient item="Zwiebeln" unit="Stück" qty="2"/>
      <ingredient item="Brühe" unit="ml" qty="500"/>
    </part>
    <head><cat>Suppen</cat></head>
  </rezept>
</rezeptesammlung>"""

    def test_split_returns_list(self, u1_s1):
        from cookbook.integration.rezeptsuitede import Rezeptsuitede
        req = _make_request(u1_s1)
        with scope(space=req.space):
            integ = Rezeptsuitede(req, 'export')
            result = integ.split_recipe_file(BytesIO(self.SAMPLE_XML.encode('utf-8')))
            assert len(result) >= 1

    def test_parse_produces_correct_recipe(self, u1_s1):
        from cookbook.integration.rezeptsuitede import Rezeptsuitede
        req = _make_request(u1_s1)
        space = req.space
        with scope(space=space):
            integ = Rezeptsuitede(req, 'export')
            il = _make_import_log(space, req.user)
            integ._ctx = ImportContext(import_log=il)
            integ.import_log = il
            split = integ.split_recipe_file(BytesIO(self.SAMPLE_XML.encode('utf-8')))
            recipe = integ.get_recipe_from_file(split[0])
            assert recipe.name == 'Kartoffelsuppe'
            assert recipe.servings == 4
            assert recipe.description is not None


# ========================================================================
# 7. Plantoeat – JSON importer
# ========================================================================

class TestPlantoeatRegression:

    SAMPLE = (
        'Title: "PTE Pasta"\n'
        'Description: Easy weeknight dinner\n'
        'Serves: 4\n'
        'Source: https://plantoeat.example.com\n'
        'Prep Time: 15 minutes\n'
        'Cook Time: 20 minutes\n'
        'Photo Url: \n'
        'Tags: pasta^dinner\n'
        'Ingredients:\n'
        '400g pasta\n'
        '2 cloves garlic\n'
        '1 can tomatoes\n'
        'Directions:\n'
        'Boil pasta.\n'
        'Make sauce.\n'
        'Combine.\n'
    )

    def test_parse_produces_correct_recipe(self, u1_s1):
        from cookbook.integration.plantoeat import Plantoeat
        req = _make_request(u1_s1)
        space = req.space
        with scope(space=space):
            integ = Plantoeat(req, 'export')
            il = _make_import_log(space, req.user)
            integ._ctx = ImportContext(import_log=il)
            integ.import_log = il
            recipe = integ.get_recipe_from_file(self.SAMPLE)
            snap = _snapshot_recipe(recipe)
            assert snap.name == 'PTE Pasta'
            assert snap.step_count == 1
            assert snap.ingredient_counts[0] == 3


# ========================================================================
# 8. MealMaster – MMF text importer
# ========================================================================

class TestMealMasterRegression:

    SAMPLE = """MMMMM----- Recipe via Meal-Master (tm)

      Title: MMF Apple Pie
 Categories: Desserts, Pies
      Yield: 8 servings

  2 1/2 c  flour
    1 ts  salt
  6  tb  butter
  4      apples

  Mix flour and salt. Cut in butter. Add apples.
  Bake at 375F for 45 minutes.

MMMMM
"""

    def test_parse_produces_correct_recipe(self, u1_s1):
        from cookbook.integration.mealmaster import MealMaster
        req = _make_request(u1_s1)
        space = req.space
        with scope(space=space):
            integ = MealMaster(req, 'export')
            il = _make_import_log(space, req.user)
            integ._ctx = ImportContext(import_log=il)
            integ.import_log = il
            split = integ.split_recipe_file(BytesIO(self.SAMPLE.encode('utf-8')))
            assert len(split) >= 1
            recipe = integ.get_recipe_from_file(split[0])
            assert recipe.name == 'MMF Apple Pie'
            assert str(recipe.servings) == '8'

    def test_failed_servings_uses_default(self, u1_s1):
        from cookbook.integration.mealmaster import MealMaster
        req = _make_request(u1_s1)
        space = req.space
        with scope(space=space):
            integ = MealMaster(req, 'export')
            il = _make_import_log(space, req.user)
            integ._ctx = ImportContext(import_log=il)
            integ.import_log = il
            split = integ.split_recipe_file(BytesIO(self.SAMPLE.encode('utf-8')))
            recipe = integ.get_recipe_from_file(split[0])
            assert recipe.name == 'MMF Apple Pie'


# ========================================================================
# 9. ChefTap – text/zip importer
# ========================================================================

class TestChefTapRegression:

    SAMPLE = b"""ChefTap Recipe
https://cheftap.example.com/recipe

2 cups flour
1 tsp salt
3 eggs

Mix everything together and bake at 350F.
"""

    def test_import_file_name_filter(self, u1_s1):
        from cookbook.integration.cheftap import ChefTap
        req = _make_request(u1_s1)
        with scope(space=req.space):
            integ = ChefTap(req, 'export')
            zf = _zip_bytes({'cheftap_export/My Recipe.txt': 'title'})
            info = ZipFile(zf).infolist()[0]
            assert integ.import_file_name_filter(info) is not None

    def test_parse_produces_recipe(self, u1_s1):
        from cookbook.integration.cheftap import ChefTap
        req = _make_request(u1_s1)
        space = req.space
        with scope(space=space):
            integ = ChefTap(req, 'export')
            il = _make_import_log(space, req.user)
            integ._ctx = ImportContext(import_log=il)
            integ.import_log = il
            recipe = integ.get_recipe_from_file(BytesIO(self.SAMPLE))
            assert recipe.name == 'ChefTap Recipe'


# ========================================================================
# 10. CopyMeThat – HTML/zip importer
# ========================================================================

class TestCopyMeThatRegression:

    def test_import_file_name_filter(self, u1_s1):
        from cookbook.integration.copymethat import CopyMeThat
        req = _make_request(u1_s1)
        with scope(space=req.space):
            integ = CopyMeThat(req, 'export')
            zf = _zip_bytes({'recipes.html': '<html></html>'})
            info = ZipFile(zf).infolist()[0]
            assert integ.import_file_name_filter(info) is True
            zf2 = _zip_bytes({'other.txt': ''})
            info2 = ZipFile(zf2).infolist()[0]
            assert integ.import_file_name_filter(info2) is False


# ========================================================================
# 11. Cookmate – XML/zip importer
# ========================================================================

class TestCookmateRegression:

    def test_import_file_name_filter(self, u1_s1):
        from cookbook.integration.cookmate import Cookmate
        req = _make_request(u1_s1)
        with scope(space=req.space):
            integ = Cookmate(req, 'export')
            zf = _zip_bytes({'recipes.xml': '<recipes/>'})
            info = ZipFile(zf).infolist()[0]
            assert integ.import_file_name_filter(info) is True


# ========================================================================
# 12. Cooklang – .cook file importer (existing tests cover more detail)
# ========================================================================

class TestCooklangRegression:

    def test_no_split_recipe_file_override(self, u1_s1):
        from cookbook.integration.cooklang import Cooklang
        req = _make_request(u1_s1)
        with scope(space=req.space):
            integ = Cooklang(req, 'export')
            with pytest.raises(NotImplementedError):
                integ.split_recipe_file(BytesIO(b'data'))

    def test_cooklang_empty_file_creates_recipe_with_fallback_name(self, u1_s1):
        from cookbook.integration.cooklang import Cooklang
        req = _make_request(u1_s1)
        space = req.space
        with scope(space=space):
            integ = Cooklang(req, 'export')
            il = _make_import_log(space, req.user)
            integ._ctx = ImportContext(import_log=il)
            integ.import_log = il
            buf = BytesIO(b'')
            buf.name = 'test_recipe.cook'
            recipe = integ.get_recipe_from_file(buf)
            assert recipe is not None
            assert 'test_recipe' in recipe.name


# ========================================================================
# 13. CookBookApp – JSON importer
# ========================================================================

class TestCookBookAppRegression:

    def test_import_file_name_filter_yml(self, u1_s1):
        from cookbook.integration.cookbookapp import CookBookApp
        req = _make_request(u1_s1)
        with scope(space=req.space):
            integ = CookBookApp(req, 'export')
            zf = _zip_bytes({'recipe.yml': 'name: Test'})
            info = ZipFile(zf).infolist()[0]
            assert integ.import_file_name_filter(info) is True

    def test_import_file_name_filter_rejects_json(self, u1_s1):
        from cookbook.integration.cookbookapp import CookBookApp
        req = _make_request(u1_s1)
        with scope(space=req.space):
            integ = CookBookApp(req, 'export')
            zf = _zip_bytes({'recipes.json': '[]'})
            info = ZipFile(zf).infolist()[0]
            assert integ.import_file_name_filter(info) is False


# ========================================================================
# 14. Gourmet – HTML/zip importer
# ========================================================================

class TestGourmetRegression:

    def test_import_file_name_filter(self, u1_s1):
        from cookbook.integration.gourmet import Gourmet
        req = _make_request(u1_s1)
        with scope(space=req.space):
            integ = Gourmet(req, 'export')
            zf = _zip_bytes({'recipe.htm': '<html></html>'})
            info = ZipFile(zf).infolist()[0]
            result = integ.import_file_name_filter(info)
            assert isinstance(result, bool)


# ========================================================================
# 15. RecipeKeeper – HTML/zip importer
# ========================================================================

class TestRecipeKeeperRegression:

    def test_import_file_name_filter(self, u1_s1):
        from cookbook.integration.recipekeeper import RecipeKeeper
        req = _make_request(u1_s1)
        with scope(space=req.space):
            integ = RecipeKeeper(req, 'export')
            zf = _zip_bytes({'recipes.html': '<html></html>'})
            info = ZipFile(zf).infolist()[0]
            assert integ.import_file_name_filter(info) is not None


# ========================================================================
# 16. Error structure consistency across importers
# ========================================================================

class TestErrorStructureConsistency:

    def test_import_error_format_is_deterministic(self):
        err = ImportError(message='boom', filename='x.json')
        first = err.format()
        second = err.format()
        assert first == second

    def test_import_error_separator_count(self):
        err = ImportError(message='test')
        assert err.format().count(ERROR_SEPARATOR) == 2

    def test_context_error_aggregation(self):
        ctx = ImportContext()
        ctx.add_error('e1', filename='a.json')
        ctx.add_error('e2', filename='b.xml')
        ctx.add_error('e3')
        msgs = ctx.get_error_messages()
        assert 'IMPORTING a.json' in msgs
        assert 'IMPORTING b.xml' in msgs
        assert 'e3' in msgs
        _assert_error_structure(ctx, expected_error_count=3,
                                expected_filenames=['a.json', 'b.xml', None])

    def test_log_warning_format(self, u1_s1):
        req = _make_request(u1_s1)
        space = req.space
        with scope(space=space):
            integ = Integration(req, 'export')
            il = _make_import_log(space, req.user)
            integ._ctx = ImportContext(import_log=il)
            integ.import_log = il
            integ._log_warning('image failed', context='Pancake')
            msg = il.msg
            assert 'WARN [Pancake] image failed' in msg
            assert msg.endswith('\n')

    def test_log_error_format(self, u1_s1):
        req = _make_request(u1_s1)
        space = req.space
        with scope(space=space):
            integ = Integration(req, 'export')
            il = _make_import_log(space, req.user)
            integ._ctx = ImportContext(import_log=il)
            integ.import_log = il
            integ._log_error('fatal', exception=RuntimeError('crash'), recipe_name='Soup')
            msg = il.msg
            assert 'ERROR [Soup] fatal' in msg
            assert 'Exception: crash' in msg
            assert len(integ._ctx.errors) == 1
            assert integ._ctx.errors[0].filename == 'Soup'


# ========================================================================
# 17. do_import end-to-end with BadZipFile and generic exception
# ========================================================================

class TestDoImportErrorPaths:

    def test_bad_zip_file_error(self, u1_s1):
        req = _make_request(u1_s1)
        space = req.space
        with scope(space=space):
            integ = Integration(req, 'export')
            il = _make_import_log(space, req.user)
            bad_file = BytesIO(b'not a zip file')
            bad_file.name = 'recipes.zip'
            files = [{'file': bad_file, 'name': 'recipes.zip'}]
            integ.do_import(files, il, import_duplicates=False)
            il.refresh_from_db()
            assert 'ERROR' in il.msg
            assert not il.running

    def test_unexpected_exception_in_import(self, u1_s1):
        req = _make_request(u1_s1)
        space = req.space
        with scope(space=space):
            integ = Integration(req, 'export')
            il = _make_import_log(space, req.user)
            with patch.object(integ, '_detect_file_category', side_effect=RuntimeError('surprise')):
                files = [{'file': BytesIO(b''), 'name': 'x.json'}]
                integ.do_import(files, il, import_duplicates=False)
            il.refresh_from_db()
            assert 'ERROR' in il.msg
            assert not il.running

    def test_single_data_file_import(self, u1_s1):
        from cookbook.integration.domestica import Domestica
        req = _make_request(u1_s1)
        space = req.space
        with scope(space=space):
            integ = Domestica(req, 'export')
            il = _make_import_log(space, req.user)
            data = json.dumps([{
                'name': 'E2E Test',
                'servings': 2,
                'timeCook': '',
                'timePrep': '',
                'directions': 'Cook it.',
                'source': '',
                'ingredients': '1 cup rice',
                'image': '',
            }]).encode('utf-8')
            files = [{'file': BytesIO(data), 'name': 'recipes.json'}]
            integ.do_import(files, il, import_duplicates=False)
            il.refresh_from_db()
            assert not il.running
            assert il.imported_recipes >= 1
            assert 'E2E Test' in il.msg


# ========================================================================
# 18. ImportContext state tracking
# ========================================================================

class TestImportContextStateTracking:

    def test_imported_recipes_increment(self, u1_s1):
        req = _make_request(u1_s1)
        space = req.space
        with scope(space=space):
            integ = Integration(req, 'export')
            il = _make_import_log(space, req.user)
            integ._init_import_context([], il, False, True, True, False)
            assert integ._ctx.imported_recipes == 0

    def test_ignored_recipes_tracking(self, u1_s1):
        req = _make_request(u1_s1)
        space = req.space
        with scope(space=space):
            integ = Integration(req, 'export')
            il = _make_import_log(space, req.user)
            integ._init_import_context([], il, False, True, True, False)
            r1 = Recipe.objects.create(
                name='Dup', created_by=req.user, internal=True, space=space,
            )
            r2 = Recipe.objects.create(
                name='Dup', created_by=req.user, internal=True, space=space,
            )
            integ.handle_duplicates(r2, import_duplicates=False)
            assert 'Dup' in integ._ctx.ignored_recipes
            assert 'Dup' in integ.ignored_recipes


# ========================================================================
# 19. import_recipe_image null safety
# ========================================================================

class TestImportRecipeImageSafety:

    def test_import_recipe_image_handles_none(self, u1_s1):
        req = _make_request(u1_s1)
        space = req.space
        with scope(space=space):
            integ = Integration(req, 'export')
            il = _make_import_log(space, req.user)
            integ._ctx = ImportContext(import_log=il)
            integ.import_log = il
            recipe = Recipe.objects.create(
                name='ImageTest', created_by=req.user, internal=True, space=space,
            )
            with patch('cookbook.integration.integration.handle_image', return_value=None):
                integ.import_recipe_image(recipe, BytesIO(b'fake'), filetype='.jpeg')
                assert not recipe.image


# ========================================================================
# 20. ImportLog final state
# ========================================================================

class TestImportLogFinalState:

    def test_do_import_sets_running_false(self, u1_s1):
        from cookbook.integration.domestica import Domestica
        req = _make_request(u1_s1)
        space = req.space
        with scope(space=space):
            integ = Domestica(req, 'export')
            il = _make_import_log(space, req.user)
            data = json.dumps([{
                'name': 'Final State',
                'servings': 1,
                'timeCook': '',
                'timePrep': '',
                'directions': 'Done.',
                'source': '',
                'ingredients': '',
                'image': '',
            }]).encode('utf-8')
            files = [{'file': BytesIO(data), 'name': 'test.json'}]
            integ.do_import(files, il, import_duplicates=False)
            il.refresh_from_db()
            assert il.running is False

    def test_do_import_sets_keyword(self, u1_s1):
        from cookbook.integration.domestica import Domestica
        req = _make_request(u1_s1)
        space = req.space
        with scope(space=space):
            integ = Domestica(req, 'export')
            il = _make_import_log(space, req.user)
            data = json.dumps([{
                'name': 'KW Test',
                'servings': 1,
                'timeCook': '',
                'timePrep': '',
                'directions': 'Done.',
                'source': '',
                'ingredients': '',
                'image': '',
            }]).encode('utf-8')
            files = [{'file': BytesIO(data), 'name': 'test.json'}]
            integ.do_import(files, il, import_duplicates=False)
            il.refresh_from_db()
            assert il.keyword is not None
            assert il.keyword.name.startswith('Import')
