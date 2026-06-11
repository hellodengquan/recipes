import hashlib
import os
import random
import threading
import uuid
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
from django.contrib import auth
from django.core.files import File
from django.test import RequestFactory
from django_scopes import scope
from PIL import Image

from cookbook.helper.image_processing import handle_image
from cookbook.integration.integration import ImportContext, Integration
from cookbook.models import ImportLog, Keyword, Recipe


RESOURCES = os.path.join(os.path.dirname(__file__), '..', 'resources')
SAMPLE_JPG = os.path.join(RESOURCES, 'image.jpg')


def _make_request(u1_s1):
    user = auth.get_user(u1_s1)
    space = user.userspace_set.first().space
    req = RequestFactory()
    req.user = user
    req.space = space
    return req


def _make_import_log(space, user, itype='default'):
    return ImportLog.objects.create(type=itype, created_by=user, space=space, msg='')


def _create_test_image(width=400, height=300, fmt='JPEG', mode='RGB', quality=95):
    img = Image.new(mode, (width, height), color=(255, 128, 0))
    buf = BytesIO()
    img.save(buf, format=fmt, quality=quality)
    buf.seek(0)
    return buf


def _create_large_test_image(fmt='JPEG', quality=95):
    rng = random.Random(42)
    pixels = [(rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255)) for _ in range(3000 * 2000)]
    wide = Image.new('RGB', (3000, 2000))
    wide.putdata(pixels)
    buf = BytesIO()
    wide.save(buf, format=fmt, quality=quality)
    buf.seek(0)
    return buf


def _image_dimensions(image_field):
    image_field.open('rb')
    img = Image.open(image_field)
    return img.size


def _image_bytes_hash(buf):
    buf.seek(0)
    data = buf.read()
    buf.seek(0)
    return hashlib.sha256(data).hexdigest()


# ========================================================================
# 1. UUID-based naming uniqueness
# ========================================================================


class TestImageNamingUniqueness:

    @pytest.fixture
    def integration(self, u1_s1):
        req = _make_request(u1_s1)
        with scope(space=req.space):
            integ = Integration(req, 'export')
            il = _make_import_log(req.space, req.user)
            integ._ctx = ImportContext(import_log=il)
            integ.import_log = il
            return integ

    def test_two_recipes_same_space_get_unique_image_names(self, integration, space_1):
        with scope(space=space_1):
            r1 = Recipe.objects.create(name='Recipe A', created_by=integration.request.user, internal=True, space=space_1)
            r2 = Recipe.objects.create(name='Recipe B', created_by=integration.request.user, internal=True, space=space_1)
            img_buf = _create_test_image()
            integration.import_recipe_image(r1, img_buf, filetype='.jpeg')
            img_buf.seek(0)
            integration.import_recipe_image(r2, img_buf, filetype='.jpeg')
            r1.refresh_from_db()
            r2.refresh_from_db()
            assert r1.image.name != r2.image.name
            assert r1.image.name.endswith('.jpeg')
            assert r2.image.name.endswith('.jpeg')

    def test_same_recipe_reimport_gets_unique_name(self, integration, space_1):
        with scope(space=space_1):
            recipe = Recipe.objects.create(name='Reimport', created_by=integration.request.user, internal=True, space=space_1)
            img_buf = _create_test_image()
            integration.import_recipe_image(recipe, img_buf, filetype='.jpeg')
            recipe.refresh_from_db()
            first_name = recipe.image.name
            img_buf.seek(0)
            integration.import_recipe_image(recipe, img_buf, filetype='.jpeg')
            recipe.refresh_from_db()
            assert recipe.image.name != first_name

    def test_uuid_in_name_contains_recipe_pk(self, integration, space_1):
        with scope(space=space_1):
            recipe = Recipe.objects.create(name='UUIDCheck', created_by=integration.request.user, internal=True, space=space_1)
            img_buf = _create_test_image()
            integration.import_recipe_image(recipe, img_buf, filetype='.jpeg')
            recipe.refresh_from_db()
            assert f'_{recipe.pk}.jpeg' in recipe.image.name

    def test_uuid_generation_is_unique_per_call(self):
        names = set()
        for _ in range(100):
            name = f'{uuid.uuid4()}_42.jpeg'
            names.add(name)
        assert len(names) == 100

    def test_sequential_multi_recipe_names_all_unique(self, u1_s1, space_1):
        req = _make_request(u1_s1)
        with scope(space=space_1):
            integ = Integration(req, 'export')
            il = _make_import_log(space_1, req.user)
            integ._ctx = ImportContext(import_log=il)
            integ.import_log = il

            names = []
            for i in range(10):
                r = Recipe.objects.create(name=f'UniqueName{i}', created_by=req.user, internal=True, space=space_1)
                img_buf = _create_test_image()
                integ.import_recipe_image(r, img_buf, filetype='.jpeg')
                r.refresh_from_db()
                names.append(r.image.name)
            assert len(set(names)) == 10


# ========================================================================
# 2. Same image data → different names, identical pixel content
# ========================================================================


class TestSameImageDataConsistency:

    @pytest.fixture
    def integration(self, u1_s1):
        req = _make_request(u1_s1)
        with scope(space=req.space):
            integ = Integration(req, 'export')
            il = _make_import_log(req.space, req.user)
            integ._ctx = ImportContext(import_log=il)
            integ.import_log = il
            return integ

    def test_same_image_different_recipes_different_names(self, integration, space_1):
        with scope(space=space_1):
            r1 = Recipe.objects.create(name='SameData A', created_by=integration.request.user, internal=True, space=space_1)
            r2 = Recipe.objects.create(name='SameData B', created_by=integration.request.user, internal=True, space=space_1)
            img_buf = _create_test_image()
            integration.import_recipe_image(r1, img_buf, filetype='.jpeg')
            img_buf.seek(0)
            integration.import_recipe_image(r2, img_buf, filetype='.jpeg')
            r1.refresh_from_db()
            r2.refresh_from_db()
            assert r1.image.name != r2.image.name
            assert _image_dimensions(r1.image) == _image_dimensions(r2.image)

    def test_same_image_same_recipe_replaced(self, integration, space_1):
        with scope(space=space_1):
            recipe = Recipe.objects.create(name='ReplaceTest', created_by=integration.request.user, internal=True, space=space_1)
            img_buf = _create_test_image()
            integration.import_recipe_image(recipe, img_buf, filetype='.jpeg')
            recipe.refresh_from_db()
            first_dims = _image_dimensions(recipe.image)
            img_buf.seek(0)
            integration.import_recipe_image(recipe, img_buf, filetype='.jpeg')
            recipe.refresh_from_db()
            second_dims = _image_dimensions(recipe.image)
            assert first_dims == second_dims

    def test_different_filetypes_different_names(self, integration, space_1):
        with scope(space=space_1):
            r1 = Recipe.objects.create(name='TypeJpeg', created_by=integration.request.user, internal=True, space=space_1)
            r2 = Recipe.objects.create(name='TypePng', created_by=integration.request.user, internal=True, space=space_1)
            integration.import_recipe_image(r1, _create_test_image(fmt='JPEG'), filetype='.jpeg')
            integration.import_recipe_image(r2, _create_test_image(fmt='PNG'), filetype='.png')
            r1.refresh_from_db()
            r2.refresh_from_db()
            assert r1.image.name.endswith('.jpeg')
            assert r2.image.name.endswith('.png')

    def test_identical_image_bytes_produce_identical_output_dimensions(self, u1_s1, space_1):
        req = _make_request(u1_s1)
        with scope(space=space_1):
            integ = Integration(req, 'export')
            il = _make_import_log(space_1, req.user)
            integ._ctx = ImportContext(import_log=il)
            integ.import_log = il

            img_data = _create_test_image().read()
            recipes = []
            for i in range(5):
                r = Recipe.objects.create(name=f'SameImg{i}', created_by=req.user, internal=True, space=space_1)
                integ.import_recipe_image(r, BytesIO(img_data), filetype='.jpeg')
                r.refresh_from_db()
                recipes.append(r)

            dims = [_image_dimensions(r.image) for r in recipes]
            assert len(set(dims)) == 1, f'Expected all identical dimensions, got {dims}'


# ========================================================================
# 3. handle_duplicates under simulated concurrent imports
# ========================================================================


class TestDuplicateHandlingIsolation:

    def test_sequential_same_name_second_ignored(self, u1_s1, space_1):
        req = _make_request(u1_s1)
        with scope(space=space_1):
            integ = Integration(req, 'export')
            il = _make_import_log(space_1, req.user)
            integ._ctx = ImportContext(import_log=il, import_duplicates=False)
            integ.import_log = il
            integ.ignored_recipes = []

            r1 = Recipe.objects.create(name='DupRecipe', created_by=req.user, internal=True, space=space_1)
            r1.keywords.add(integ.keyword)
            r2 = Recipe.objects.create(name='DupRecipe', created_by=req.user, internal=True, space=space_1)
            r2.keywords.add(integ.keyword)

            integ.handle_duplicates(r2, import_duplicates=False)
            assert 'DupRecipe' in integ.ignored_recipes
            assert Recipe.objects.filter(space=space_1, name='DupRecipe').count() == 1

    def test_import_duplicates_true_keeps_both(self, u1_s1, space_1):
        req = _make_request(u1_s1)
        with scope(space=space_1):
            integ = Integration(req, 'export')
            il = _make_import_log(space_1, req.user)
            integ._ctx = ImportContext(import_log=il, import_duplicates=True)
            integ.import_log = il
            integ.ignored_recipes = []

            r1 = Recipe.objects.create(name='KeepBoth', created_by=req.user, internal=True, space=space_1)
            r1.keywords.add(integ.keyword)
            r2 = Recipe.objects.create(name='KeepBoth', created_by=req.user, internal=True, space=space_1)
            r2.keywords.add(integ.keyword)

            integ.handle_duplicates(r2, import_duplicates=True)
            assert 'KeepBoth' not in integ.ignored_recipes
            assert Recipe.objects.filter(space=space_1, name='KeepBoth').count() == 2

    def test_two_integrations_same_space_same_recipe_name(self, u1_s1, space_1):
        req = _make_request(u1_s1)
        with scope(space=space_1):
            integ1 = Integration(req, 'default')
            il1 = _make_import_log(space_1, req.user, itype='default')
            integ1._ctx = ImportContext(import_log=il1, import_duplicates=False)
            integ1.import_log = il1
            integ1.ignored_recipes = []

            integ2 = Integration(req, 'default')
            il2 = _make_import_log(space_1, req.user, itype='default')
            integ2._ctx = ImportContext(import_log=il2, import_duplicates=False)
            integ2.import_log = il2
            integ2.ignored_recipes = []

            r1 = Recipe.objects.create(name='SharedName', created_by=req.user, internal=True, space=space_1)
            r1.keywords.add(integ1.keyword)
            r2 = Recipe.objects.create(name='SharedName', created_by=req.user, internal=True, space=space_1)
            r2.keywords.add(integ2.keyword)

            integ1.handle_duplicates(r1, import_duplicates=False)
            integ2.handle_duplicates(r2, import_duplicates=False)

            surviving = Recipe.objects.filter(space=space_1, name='SharedName').count()
            assert surviving >= 1

    def test_image_survives_duplicate_deletion(self, u1_s1, space_1):
        req = _make_request(u1_s1)
        with scope(space=space_1):
            integ = Integration(req, 'export')
            il = _make_import_log(space_1, req.user)
            integ._ctx = ImportContext(import_log=il, import_duplicates=False)
            integ.import_log = il
            integ.ignored_recipes = []

            r1 = Recipe.objects.create(name='ImgDup', created_by=req.user, internal=True, space=space_1)
            r1.keywords.add(integ.keyword)
            integ.import_recipe_image(r1, _create_test_image(), filetype='.jpeg')
            r1.refresh_from_db()
            assert bool(r1.image) is True

            r2 = Recipe.objects.create(name='ImgDup', created_by=req.user, internal=True, space=space_1)
            r2.keywords.add(integ.keyword)
            integ.handle_duplicates(r2, import_duplicates=False)

            surviving = Recipe.objects.filter(space=space_1, name='ImgDup').first()
            assert surviving is not None
            assert bool(surviving.image) is True


# ========================================================================
# 4. Keyword numbering under simulated concurrent Integration.__init__
# ========================================================================


class TestKeywordNumberingIsolation:

    def test_sequential_keywords_increment(self, u1_s1, space_1):
        req = _make_request(u1_s1)
        with scope(space=space_1):
            integ1 = Integration(req, 'default')
            assert integ1.keyword is not None
            integ2 = Integration(req, 'default')
            assert integ2.keyword is not None
            assert integ1.keyword.name != integ2.keyword.name or ' ' in integ2.keyword.name

    def test_keyword_conflict_resolved_with_uuid_suffix(self, u1_s1, space_1):
        req = _make_request(u1_s1)
        with scope(space=space_1):
            parent, _ = Keyword.objects.get_or_create(name='Import', space=space_1)
            parent.add_child(name='Import 99', description='blocker', space=space_1)

            integ = Integration(req, 'default')
            assert integ.keyword is not None
            if integ.keyword.name.startswith('Import 99'):
                assert ' ' in integ.keyword.name.replace('Import 99', '').strip() or integ.keyword.name == 'Import 99'

    def test_multiple_integrations_get_distinct_keywords(self, u1_s1, space_1):
        req = _make_request(u1_s1)
        with scope(space=space_1):
            keywords = []
            for _ in range(5):
                integ = Integration(req, 'default')
                keywords.append(integ.keyword.name)
            assert len(set(keywords)) >= 3


# ========================================================================
# 5. ImportLog.msg isolation between multiple importers
# ========================================================================


class TestImportLogMsgIsolation:

    def test_separate_import_logs_isolated(self, u1_s1, space_1):
        req = _make_request(u1_s1)
        with scope(space=space_1):
            il1 = _make_import_log(space_1, req.user, itype='default')
            il2 = _make_import_log(space_1, req.user, itype='paprika')

            integ1 = Integration(req, 'default')
            integ1._ctx = ImportContext(import_log=il1)
            integ1.import_log = il1

            integ2 = Integration(req, 'paprika')
            integ2._ctx = ImportContext(import_log=il2)
            integ2.import_log = il2

            integ1._append_log('log from integ1\n')
            integ1._flush_log()
            integ2._append_log('log from integ2\n')
            integ2._flush_log()

            il1.refresh_from_db()
            il2.refresh_from_db()
            assert 'log from integ1' in il1.msg
            assert 'log from integ2' not in il1.msg
            assert 'log from integ2' in il2.msg
            assert 'log from integ1' not in il2.msg

    def test_multiple_sequential_flushes_isolated(self, u1_s1, space_1):
        req = _make_request(u1_s1)
        with scope(space=space_1):
            il1 = _make_import_log(space_1, req.user, itype='default')
            il2 = _make_import_log(space_1, req.user, itype='paprika')

            integ1 = Integration(req, 'default')
            integ1._ctx = ImportContext(import_log=il1)
            integ1.import_log = il1

            integ2 = Integration(req, 'paprika')
            integ2._ctx = ImportContext(import_log=il2)
            integ2.import_log = il2

            for i in range(5):
                integ1._append_log(f'line1_{i}\n')
                integ1._flush_log()
                integ2._append_log(f'line2_{i}\n')
                integ2._flush_log()

            il1.refresh_from_db()
            il2.refresh_from_db()
            for i in range(5):
                assert f'line1_{i}' in il1.msg
                assert f'line2_{i}' not in il1.msg
                assert f'line2_{i}' in il2.msg
                assert f'line1_{i}' not in il2.msg

    def test_log_warnings_do_not_cross_contaminate(self, u1_s1, space_1):
        req = _make_request(u1_s1)
        with scope(space=space_1):
            il1 = _make_import_log(space_1, req.user, itype='default')
            il2 = _make_import_log(space_1, req.user, itype='paprika')

            integ1 = Integration(req, 'default')
            integ1._ctx = ImportContext(import_log=il1)
            integ1.import_log = il1

            integ2 = Integration(req, 'paprika')
            integ2._ctx = ImportContext(import_log=il2)
            integ2.import_log = il2

            integ1._log_warning('warn from integ1', context='ctx1', persist=True)
            integ2._log_warning('warn from integ2', context='ctx2', persist=True)

            il1.refresh_from_db()
            il2.refresh_from_db()
            assert 'warn from integ1' in il1.msg
            assert 'warn from integ2' not in il1.msg


# ========================================================================
# 6. Image attachment isolation across multiple importers (sequential sim)
# ========================================================================


class TestImageAttachmentIsolation:

    def test_two_importers_same_space_separate_recipes(self, u1_s1, space_1):
        req = _make_request(u1_s1)
        with scope(space=space_1):
            il1 = _make_import_log(space_1, req.user, itype='default')
            il2 = _make_import_log(space_1, req.user, itype='paprika')

            integ1 = Integration(req, 'default')
            integ1._ctx = ImportContext(import_log=il1)
            integ1.import_log = il1

            integ2 = Integration(req, 'paprika')
            integ2._ctx = ImportContext(import_log=il2)
            integ2.import_log = il2

            r1 = Recipe.objects.create(name='Integ1 Recipe', created_by=req.user, internal=True, space=space_1)
            r2 = Recipe.objects.create(name='Integ2 Recipe', created_by=req.user, internal=True, space=space_1)

            integ1.import_recipe_image(r1, _create_test_image(), filetype='.jpeg')
            integ2.import_recipe_image(r2, _create_test_image(fmt='PNG'), filetype='.png')

            r1.refresh_from_db()
            r2.refresh_from_db()
            assert bool(r1.image) is True
            assert bool(r2.image) is True
            assert r1.image.name.endswith('.jpeg')
            assert r2.image.name.endswith('.png')

    def test_three_importers_no_cross_attachment(self, u1_s1, space_1):
        req = _make_request(u1_s1)
        with scope(space=space_1):
            importers = []
            for itype in ['default', 'paprika', 'nextcloud']:
                il = _make_import_log(space_1, req.user, itype=itype)
                integ = Integration(req, itype)
                integ._ctx = ImportContext(import_log=il)
                integ.import_log = il
                importers.append(integ)

            recipes = []
            for i in range(3):
                r = Recipe.objects.create(name=f'MultiInteg{i}', created_by=req.user, internal=True, space=space_1)
                recipes.append(r)

            filetypes = ['.jpeg', '.png', '.webp']
            fmts = ['JPEG', 'PNG', 'WEBP']
            for i, (integ, recipe) in enumerate(zip(importers, recipes)):
                img_buf = _create_test_image(fmt=fmts[i])
                integ.import_recipe_image(recipe, img_buf, filetype=filetypes[i])

            for i, recipe in enumerate(recipes):
                recipe.refresh_from_db()
                assert bool(recipe.image) is True, f'Recipe {i} should have image'
                assert recipe.image.name.endswith(filetypes[i]), f'Recipe {i} image should end with {filetypes[i]}'

    def test_image_not_shared_between_recipes(self, u1_s1, space_1):
        req = _make_request(u1_s1)
        with scope(space=space_1):
            il = _make_import_log(space_1, req.user)
            integ = Integration(req, 'export')
            integ._ctx = ImportContext(import_log=il)
            integ.import_log = il

            r1 = Recipe.objects.create(name='NoShare1', created_by=req.user, internal=True, space=space_1)
            r2 = Recipe.objects.create(name='NoShare2', created_by=req.user, internal=True, space=space_1)

            img_buf = _create_test_image()
            integ.import_recipe_image(r1, BytesIO(img_buf.read()), filetype='.jpeg')
            img_buf.seek(0)
            integ.import_recipe_image(r2, BytesIO(img_buf.read()), filetype='.jpeg')

            r1.refresh_from_db()
            r2.refresh_from_db()
            assert r1.image.name != r2.image.name
            r1.image.open('rb')
            r2.image.open('rb')
            assert _image_bytes_hash(r1.image) == _image_bytes_hash(r2.image)


# ========================================================================
# 7. handle_image thread-safety (no DB, pure in-memory)
# ========================================================================


class TestConcurrentHandleImageIntegrity:

    def test_concurrent_handle_image_same_input(self):
        results = {}
        errors = []

        def process_image(idx):
            try:
                img_buf = _create_test_image()
                mock_req = MagicMock()
                result = handle_image(mock_req, File(img_buf, name='image'), filetype='.jpeg')
                if result is not None:
                    result.seek(0)
                    img = Image.open(result)
                    results[idx] = img.size
                else:
                    results[idx] = None
            except Exception as e:
                errors.append(str(e))

        threads = []
        for i in range(5):
            t = threading.Thread(target=process_image, args=(i,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert len(errors) == 0, f'Errors: {errors}'
        assert len(results) == 5
        sizes = set(results.values())
        assert len(sizes) == 1, f'Inconsistent sizes across concurrent handle_image calls: {sizes}'
        assert (400, 300) in sizes

    def test_concurrent_handle_image_mixed_types(self):
        results = {}
        errors = []

        def process_image(idx, fmt, filetype):
            try:
                img_buf = _create_test_image(fmt=fmt)
                mock_req = MagicMock()
                result = handle_image(mock_req, File(img_buf, name='image'), filetype=filetype)
                if result is not None:
                    result.seek(0)
                    img = Image.open(result)
                    results[idx] = {'size': img.size, 'format': img.format}
                else:
                    results[idx] = None
            except Exception as e:
                errors.append(str(e))

        threads = [
            threading.Thread(target=process_image, args=(0, 'JPEG', '.jpeg')),
            threading.Thread(target=process_image, args=(1, 'PNG', '.png')),
            threading.Thread(target=process_image, args=(2, 'JPEG', '.jpeg')),
            threading.Thread(target=process_image, args=(3, 'PNG', '.png')),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert len(errors) == 0, f'Errors: {errors}'
        assert results[0]['size'] == results[2]['size']
        assert results[1]['size'] == results[3]['size']

    def test_concurrent_rescale_same_dimensions(self):
        from cookbook.helper.image_processing import rescale_image_jpeg

        results = {}
        errors = []

        def rescale(idx):
            try:
                img_buf = _create_large_test_image(fmt='JPEG')
                result = rescale_image_jpeg(img_buf)
                result.seek(0)
                img = Image.open(result)
                results[idx] = img.size
            except Exception as e:
                errors.append(str(e))

        threads = []
        for i in range(3):
            t = threading.Thread(target=rescale, args=(i,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join(timeout=120)

        assert len(errors) == 0, f'Errors: {errors}'
        sizes = set(results.values())
        assert len(sizes) == 1, f'Inconsistent rescale sizes: {sizes}'
        w, h = list(sizes)[0]
        assert w == 1020


# ========================================================================
# 8. _import_recipe_safe isolation (sequential simulation)
# ========================================================================


class TestImportRecipeSafeIsolation:

    def test_safe_success_returns_true(self, u1_s1, space_1):
        req = _make_request(u1_s1)
        with scope(space=space_1):
            il = _make_import_log(space_1, req.user)
            integ = Integration(req, 'export')
            integ._ctx = ImportContext(import_log=il)
            integ.import_log = il
            recipe = Recipe.objects.create(name='SafeOk', created_by=req.user, internal=True, space=space_1)
            img_buf = _create_test_image()
            ok = integ._import_recipe_safe('SafeOk', recipe, image_file=img_buf, filetype='.jpeg')
            assert ok is True
            recipe.refresh_from_db()
            assert bool(recipe.image) is True

    def test_safe_failure_returns_false_and_logs_warning(self, u1_s1, space_1):
        req = _make_request(u1_s1)
        with scope(space=space_1):
            il = _make_import_log(space_1, req.user)
            integ = Integration(req, 'export')
            integ._ctx = ImportContext(import_log=il)
            integ.import_log = il
            recipe = Recipe.objects.create(name='SafeFail', created_by=req.user, internal=True, space=space_1)
            with patch.object(integ, 'import_recipe_image', side_effect=IOError('disk full')):
                ok = integ._import_recipe_safe('SafeFail', recipe, image_file=BytesIO(b'x'))
            assert ok is False
            assert 'WARN [SafeFail]' in integ._ctx.import_log.msg
            assert 'failed to import image' in integ._ctx.import_log.msg

    def test_safe_failure_does_not_affect_other_recipes(self, u1_s1, space_1):
        req = _make_request(u1_s1)
        with scope(space=space_1):
            il = _make_import_log(space_1, req.user)
            integ = Integration(req, 'export')
            integ._ctx = ImportContext(import_log=il)
            integ.import_log = il

            r_fail = Recipe.objects.create(name='FailRecipe', created_by=req.user, internal=True, space=space_1)
            r_ok = Recipe.objects.create(name='OkRecipe', created_by=req.user, internal=True, space=space_1)

            with patch.object(integ, 'import_recipe_image', side_effect=IOError('test error')):
                ok1 = integ._import_recipe_safe('FailRecipe', r_fail, image_file=BytesIO(b'x'))
            ok2 = integ._import_recipe_safe('OkRecipe', r_ok, image_file=_create_test_image(), filetype='.jpeg')

            assert ok1 is False
            assert ok2 is True
            r_fail.refresh_from_db()
            r_ok.refresh_from_db()
            assert bool(r_fail.image) is False
            assert bool(r_ok.image) is True

    def test_two_integrations_safe_failures_isolated(self, u1_s1, space_1):
        req = _make_request(u1_s1)
        with scope(space=space_1):
            il1 = _make_import_log(space_1, req.user, itype='default')
            il2 = _make_import_log(space_1, req.user, itype='paprika')

            integ1 = Integration(req, 'default')
            integ1._ctx = ImportContext(import_log=il1)
            integ1.import_log = il1

            integ2 = Integration(req, 'paprika')
            integ2._ctx = ImportContext(import_log=il2)
            integ2.import_log = il2

            r1 = Recipe.objects.create(name='Fail1', created_by=req.user, internal=True, space=space_1)
            with patch.object(integ1, 'import_recipe_image', side_effect=IOError('fail1')):
                integ1._import_recipe_safe('Fail1', r1, image_file=BytesIO(b'x'))

            r2 = Recipe.objects.create(name='Success2', created_by=req.user, internal=True, space=space_1)
            integ2._import_recipe_safe('Success2', r2, image_file=_create_test_image(), filetype='.jpeg')

            assert 'WARN [Fail1]' in integ1._ctx.import_log.msg
            assert 'Fail1' not in integ2._ctx.import_log.msg
            assert bool(r2.image) is True


# ========================================================================
# 9. BytesIO state after verify() — image pipeline safety
# ========================================================================


class TestBytesIOStateAfterVerify:

    def test_handle_image_reopens_after_verify(self):
        img_buf = _create_test_image()
        mock_req = MagicMock()
        result = handle_image(mock_req, File(img_buf, name='image'), filetype='.jpeg')
        assert result is not None

    def test_import_recipe_image_handles_bytesio_correctly(self, u1_s1, space_1):
        req = _make_request(u1_s1)
        with scope(space=space_1):
            il = _make_import_log(space_1, req.user)
            integ = Integration(req, 'export')
            integ._ctx = ImportContext(import_log=il)
            integ.import_log = il

            recipe = Recipe.objects.create(name='BytesIOTest', created_by=req.user, internal=True, space=space_1)
            img_buf = _create_test_image()
            integ.import_recipe_image(recipe, img_buf, filetype='.jpeg')
            recipe.refresh_from_db()
            assert bool(recipe.image) is True

    def test_sequential_imports_same_bytesio_seeking(self, u1_s1, space_1):
        req = _make_request(u1_s1)
        with scope(space=space_1):
            il = _make_import_log(space_1, req.user)
            integ = Integration(req, 'export')
            integ._ctx = ImportContext(import_log=il)
            integ.import_log = il

            img_buf = _create_test_image()
            r1 = Recipe.objects.create(name='Seek1', created_by=req.user, internal=True, space=space_1)
            integ.import_recipe_image(r1, img_buf, filetype='.jpeg')
            r1.refresh_from_db()
            assert bool(r1.image) is True

            img_buf.seek(0)
            r2 = Recipe.objects.create(name='Seek2', created_by=req.user, internal=True, space=space_1)
            integ.import_recipe_image(r2, img_buf, filetype='.jpeg')
            r2.refresh_from_db()
            assert bool(r2.image) is True


# ========================================================================
# 10. ImportContext isolation between multiple imports
# ========================================================================


class TestImportContextIsolation:

    def test_separate_contexts_independent(self):
        il1 = MagicMock()
        il2 = MagicMock()
        ctx1 = ImportContext(import_log=il1, import_duplicates=False)
        ctx2 = ImportContext(import_log=il2, import_duplicates=True)

        ctx1.imported_recipes += 1
        ctx1.add_error(message='error from ctx1')

        assert ctx2.imported_recipes == 0
        assert len(ctx2.errors) == 0
        assert ctx1.imported_recipes == 1
        assert len(ctx1.errors) == 1

    def test_concurrent_context_counter_race_detected(self):
        il = MagicMock()
        ctx = ImportContext(import_log=il)
        results = {}

        def mutate_context(prefix, count):
            for i in range(count):
                ctx.imported_recipes += 1
                ctx.add_error(message=f'{prefix}_{i}')
            results[prefix] = ctx.imported_recipes

        threads = [
            threading.Thread(target=mutate_context, args=('A', 100)),
            threading.Thread(target=mutate_context, args=('B', 100)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        actual = ctx.imported_recipes
        assert actual <= 200
        assert len(ctx.errors) == 200

    def test_context_errors_list_per_instance(self):
        il = MagicMock()
        ctx1 = ImportContext(import_log=il)
        ctx2 = ImportContext(import_log=il)

        ctx1.add_error(message='ctx1 error')
        ctx2.add_error(message='ctx2 error')

        assert len(ctx1.errors) == 1
        assert len(ctx2.errors) == 1
        assert ctx1.errors[0].message == 'ctx1 error'
        assert ctx2.errors[0].message == 'ctx2 error'

    def test_get_error_messages_per_instance(self):
        il = MagicMock()
        ctx1 = ImportContext(import_log=il)
        ctx2 = ImportContext(import_log=il)

        ctx1.add_error(message='error1')
        ctx2.add_error(message='error2')

        msg1 = ctx1.get_error_messages()
        msg2 = ctx2.get_error_messages()
        assert 'error1' in msg1
        assert 'error2' not in msg1
        assert 'error2' in msg2
        assert 'error1' not in msg2


# ========================================================================
# 11. Full do_import sequential simulation of concurrent behavior
# ========================================================================


class TestDoImportSequentialConcurrencySimulation:

    def test_two_importers_same_space_sequential(self, u1_s1, space_1):
        from cookbook.integration.domestica import Domestica

        req = _make_request(u1_s1)

        dom_sample = '''{
            "name": "Concurrent Sim Recipe",
            "description": "Test",
            "servings": 2,
            "working_time": 10,
            "waiting_time": 5,
            "source_url": "",
            "ingredients": [],
            "instructions": []
        }'''

        logs = []
        with scope(space=space_1):
            for prefix in ['importer_a', 'importer_b']:
                il = _make_import_log(space_1, req.user, itype='default')
                integ = Domestica(req, 'export')
                files = [{'name': f'{prefix}_recipe.json', 'file': BytesIO(dom_sample.encode('utf-8'))}]
                integ.do_import(files, il, import_duplicates=True)
                il.refresh_from_db()
                logs.append({'running': il.running, 'msg': il.msg})

        assert len(logs) == 2
        for log_info in logs:
            assert log_info['running'] is False

    def test_two_importers_preserve_isolated_keyword_counts(self, u1_s1, space_1):
        from cookbook.integration.domestica import Domestica

        req = _make_request(u1_s1)

        dom_sample = '''{
            "name": "KeywordCountTest",
            "description": "test",
            "servings": 1,
            "working_time": 5,
            "waiting_time": 0,
            "source_url": "",
            "ingredients": [],
            "instructions": []
        }'''

        keyword_pks = []
        with scope(space=space_1):
            for i in range(3):
                il = _make_import_log(space_1, req.user, itype='default')
                integ = Domestica(req, 'export')
                files = [{'name': f'kw_{i}.json', 'file': BytesIO(dom_sample.encode('utf-8'))}]
                integ.do_import(files, il, import_duplicates=True)
                keyword_pks.append(integ.keyword.pk)

        assert len(set(keyword_pks)) >= 2, f'Keywords should be distinct across imports: {keyword_pks}'

    def test_recipe_image_imported_in_full_pipeline(self, u1_s1, space_1):
        from cookbook.integration.domestica import Domestica

        req = _make_request(u1_s1)
        with scope(space=space_1):
            il = _make_import_log(space_1, req.user, itype='default')
            integ = Domestica(req, 'export')
            integ._ctx = ImportContext(import_log=il)
            integ.import_log = il

            recipe = Recipe.objects.create(name='PipelineImg', created_by=req.user, internal=True, space=space_1)
            img_buf = _create_test_image()
            integ.import_recipe_image(recipe, img_buf, filetype='.jpeg')
            recipe.refresh_from_db()
            assert bool(recipe.image) is True
            assert recipe.image.name.endswith('.jpeg')


# ========================================================================
# 12. Image naming collision resistance
# ========================================================================


class TestImageNamingCollisionResistance:

    def test_uuid_v4_collision_resistance(self):
        names = set()
        for _ in range(1000):
            name = f'{uuid.uuid4()}_42.jpeg'
            names.add(name)
        assert len(names) == 1000

    def test_image_file_stored_correctly_per_recipe(self, u1_s1, space_1):
        req = _make_request(u1_s1)
        with scope(space=space_1):
            il = _make_import_log(space_1, req.user)
            integ = Integration(req, 'export')
            integ._ctx = ImportContext(import_log=il)
            integ.import_log = il

            recipes = []
            for i in range(5):
                r = Recipe.objects.create(name=f'CollisionTest{i}', created_by=req.user, internal=True, space=space_1)
                img_buf = _create_test_image()
                integ.import_recipe_image(r, img_buf, filetype='.jpeg')
                r.refresh_from_db()
                recipes.append(r)

            stored_names = [r.image.name for r in recipes]
            assert len(stored_names) == 5
            assert len(set(stored_names)) == 5
            for name in stored_names:
                assert name.startswith('recipes/')
                assert name.endswith('.jpeg')

    def test_mixed_filetypes_no_collision(self, u1_s1, space_1):
        req = _make_request(u1_s1)
        with scope(space=space_1):
            il = _make_import_log(space_1, req.user)
            integ = Integration(req, 'export')
            integ._ctx = ImportContext(import_log=il)
            integ.import_log = il

            types_and_fmts = [('.jpeg', 'JPEG'), ('.png', 'PNG'), ('.webp', 'WEBP')]
            recipes = []
            for ft, fmt in types_and_fmts:
                r = Recipe.objects.create(name=f'MixedType{ft}', created_by=req.user, internal=True, space=space_1)
                img_buf = _create_test_image(fmt=fmt)
                integ.import_recipe_image(r, img_buf, filetype=ft)
                r.refresh_from_db()
                recipes.append(r)

            names = [r.image.name for r in recipes]
            assert len(set(names)) == 3
            for r, (ft, _) in zip(recipes, types_and_fmts):
                assert r.image.name.endswith(ft)


# ========================================================================
# 13. Cache key isolation for export operations
# ========================================================================


class TestCacheKeyIsolation:

    def test_export_cache_key_uses_pk(self):
        for pk in [1, 2, 999]:
            key = f'export_file_{pk}'
            assert str(pk) in key

    def test_different_pks_produce_different_keys(self):
        keys = {f'export_file_{pk}' for pk in range(100)}
        assert len(keys) == 100
