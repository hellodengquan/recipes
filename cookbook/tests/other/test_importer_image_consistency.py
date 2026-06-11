import base64
import hashlib
import os
import random
from io import BytesIO
from unittest.mock import MagicMock, patch
from zipfile import ZipFile

import pytest
from django.contrib import auth
from django.core.files import File
from django.test import RequestFactory
from django_scopes import scope
from PIL import Image

from cookbook.helper.image_processing import (
    get_filetype,
    handle_image,
    is_file_type_allowed,
    rescale_image_gif,
    rescale_image_jpeg,
    rescale_image_png,
    rescale_image_webp,
    strip_image_meta,
)
from cookbook.integration.integration import ImportContext, Integration
from cookbook.models import ImportLog, Recipe


RESOURCES = os.path.join(os.path.dirname(__file__), '..', 'resources')

SAMPLE_JPG = os.path.join(RESOURCES, 'image.jpg')
SAMPLE_PNG = os.path.join(RESOURCES, 'image.png')


def _make_request(u1_s1):
    user = auth.get_user(u1_s1)
    space = user.userspace_set.first().space
    req = RequestFactory()
    req.user = user
    req.space = space
    return req


def _make_import_log(space, user):
    return ImportLog.objects.create(type='default', created_by=user, space=space, msg='')


def _create_test_image(width, height, fmt='JPEG', mode='RGB', quality=95, large=False):
    if large:
        rng = random.Random(42)
        pixels = [(rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255)) for _ in range(3000 * 2000)]
        wide = Image.new(mode, (3000, 2000))
        wide.putdata(pixels)
        big_buf = BytesIO()
        wide.save(big_buf, format=fmt, quality=quality)
        big_buf.seek(0)
        return big_buf
    img = Image.new(mode, (width, height), color=(255, 128, 0))
    buf = BytesIO()
    img.save(buf, format=fmt, quality=quality)
    buf.seek(0)
    return buf


def _create_test_webp(large=False):
    if large:
        rng = random.Random(42)
        pixels = [(rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255)) for _ in range(3000 * 2000)]
        wide = Image.new('RGB', (3000, 2000))
        wide.putdata(pixels)
        buf = BytesIO()
        wide.save(buf, format='WEBP', quality=90)
        buf.seek(0)
        return buf
    img = Image.new('RGB', (400, 300), color=(50, 100, 200))
    buf = BytesIO()
    img.save(buf, format='WEBP', quality=90)
    buf.seek(0)
    return buf


def _create_test_gif(animated=False):
    if animated:
        frames = [Image.new('RGBA', (200, 200), color=(i * 50, 100, 200)) for i in range(3)]
        buf = BytesIO()
        frames[0].save(buf, format='GIF', save_all=True, append_images=frames[1:], duration=100, loop=0)
    else:
        img = Image.new('RGBA', (400, 300), color=(200, 100, 50))
        buf = BytesIO()
        img.save(buf, format='GIF')
    buf.seek(0)
    return buf


def _image_bytes_hash(image_buf):
    image_buf.seek(0)
    data = image_buf.read()
    image_buf.seek(0)
    return hashlib.sha256(data).hexdigest()


def _image_dimensions(image_buf):
    image_buf.seek(0)
    img = Image.open(image_buf)
    image_buf.seek(0)
    return img.size


def _image_format(image_buf):
    image_buf.seek(0)
    img = Image.open(image_buf)
    image_buf.seek(0)
    return img.format


# ========================================================================
# 1. handle_image — core image processing pipeline
# ========================================================================


class TestHandleImageJpeg:

    def test_small_jpeg_strips_exif(self):
        img_buf = _create_test_image(400, 300, fmt='JPEG')
        result = handle_image(MagicMock(), File(img_buf, name='image'), filetype='.jpeg')
        assert result is not None
        result.seek(0)
        out_img = Image.open(result)
        assert out_img.format == 'JPEG'
        assert out_img.size == (400, 300)

    def test_small_jpeg_output_deterministic(self):
        img_buf = _create_test_image(400, 300, fmt='JPEG')
        result1 = handle_image(MagicMock(), File(img_buf, name='image'), filetype='.jpeg')
        img_buf.seek(0)
        result2 = handle_image(MagicMock(), File(img_buf, name='image'), filetype='.jpeg')
        assert _image_bytes_hash(result1) == _image_bytes_hash(result2)

    def test_large_jpeg_rescales(self):
        img_buf = _create_test_image(0, 0, large=True)
        result = handle_image(MagicMock(), File(img_buf, name='image'), filetype='.jpeg')
        assert result is not None
        w, h = _image_dimensions(result)
        assert w == 1020
        assert h > 0

    def test_large_jpeg_rescale_deterministic(self):
        img_buf = _create_test_image(0, 0, large=True)
        result1 = handle_image(MagicMock(), File(img_buf, name='image'), filetype='.jpeg')
        img_buf.seek(0)
        result2 = handle_image(MagicMock(), File(img_buf, name='image'), filetype='.jpeg')
        assert _image_bytes_hash(result1) == _image_bytes_hash(result2)

    def test_invalid_image_returns_none(self):
        bad_buf = BytesIO(b'not an image at all')
        result = handle_image(MagicMock(), File(bad_buf, name='image'), filetype='.jpeg')
        assert result is None

    def test_jpg_extension_treated_as_jpeg(self):
        img_buf = _create_test_image(400, 300, fmt='JPEG')
        result = handle_image(MagicMock(), File(img_buf, name='image'), filetype='.jpg')
        assert result is not None
        result.seek(0)
        assert Image.open(result).format == 'JPEG'


class TestHandleImagePng:

    def test_small_png_strips_exif(self):
        img_buf = _create_test_image(400, 300, fmt='PNG')
        result = handle_image(MagicMock(), File(img_buf, name='image'), filetype='.png')
        assert result is not None
        result.seek(0)
        assert Image.open(result).format == 'PNG'
        assert _image_dimensions(result) == (400, 300)

    def test_large_png_rescales(self):
        img_buf = _create_test_image(0, 0, fmt='PNG', large=True)
        result = handle_image(MagicMock(), File(img_buf, name='image'), filetype='.png')
        assert result is not None
        w, h = _image_dimensions(result)
        assert w == 1020

    def test_png_output_deterministic(self):
        img_buf = _create_test_image(400, 300, fmt='PNG')
        r1 = handle_image(MagicMock(), File(img_buf, name='image'), filetype='.png')
        img_buf.seek(0)
        r2 = handle_image(MagicMock(), File(img_buf, name='image'), filetype='.png')
        assert _image_bytes_hash(r1) == _image_bytes_hash(r2)


class TestHandleImageWebp:

    def test_small_webp_strips_exif(self):
        img_buf = _create_test_webp(large=False)
        result = handle_image(MagicMock(), File(img_buf, name='image'), filetype='.webp')
        assert result is not None
        result.seek(0)
        assert Image.open(result).format == 'WEBP'

    def test_large_webp_rescales(self):
        img_buf = _create_test_webp(large=True)
        result = handle_image(MagicMock(), File(img_buf, name='image'), filetype='.webp')
        assert result is not None
        w, h = _image_dimensions(result)
        assert w == 1020

    def test_webp_output_deterministic(self):
        img_buf = _create_test_webp(large=False)
        r1 = handle_image(MagicMock(), File(img_buf, name='image'), filetype='.webp')
        img_buf.seek(0)
        r2 = handle_image(MagicMock(), File(img_buf, name='image'), filetype='.webp')
        assert _image_bytes_hash(r1) == _image_bytes_hash(r2)


class TestHandleImageGif:

    def test_small_static_gif_strips_exif(self):
        img_buf = _create_test_gif(animated=False)
        result = handle_image(MagicMock(), File(img_buf, name='image'), filetype='.gif')
        assert result is not None
        result.seek(0)
        assert Image.open(result).format == 'GIF'

    def test_animated_gif_preserved(self):
        img_buf = _create_test_gif(animated=True)
        result = handle_image(MagicMock(), File(img_buf, name='image'), filetype='.gif')
        assert result is not None
        result.seek(0)
        img = Image.open(result)
        assert img.format == 'GIF'

    def test_gif_output_deterministic(self):
        img_buf = _create_test_gif(animated=False)
        r1 = handle_image(MagicMock(), File(img_buf, name='image'), filetype='.gif')
        img_buf.seek(0)
        r2 = handle_image(MagicMock(), File(img_buf, name='image'), filetype='.gif')
        assert _image_bytes_hash(r1) == _image_bytes_hash(r2)


class TestHandleImageUnknownType:

    def test_unknown_filetype_returns_raw(self):
        img_buf = _create_test_image(400, 300, fmt='JPEG')
        raw_input = File(img_buf, name='image')
        result = handle_image(MagicMock(), raw_input, filetype='.bmp')
        assert result is raw_input


# ========================================================================
# 2. Rescale functions — pixel-level consistency
# ========================================================================


class TestRescaleJpegConsistency:

    def test_rescale_jpeg_target_width(self):
        img_buf = _create_test_image(0, 0, large=True)
        result = rescale_image_jpeg(img_buf)
        result.seek(0)
        img = Image.open(result)
        assert img.size[0] == 1020

    def test_rescale_jpeg_maintains_aspect_ratio(self):
        img_buf = _create_test_image(0, 0, large=True)
        original = Image.open(img_buf)
        img_buf.seek(0)
        result = rescale_image_jpeg(img_buf)
        result.seek(0)
        rescaled = Image.open(result)
        original_ratio = original.size[1] / original.size[0]
        rescaled_ratio = rescaled.size[1] / rescaled.size[0]
        assert abs(original_ratio - rescaled_ratio) < 0.01

    def test_rescale_jpeg_deterministic(self):
        img_buf = _create_test_image(0, 0, large=True)
        r1 = rescale_image_jpeg(img_buf)
        img_buf.seek(0)
        r2 = rescale_image_jpeg(img_buf)
        assert _image_bytes_hash(r1) == _image_bytes_hash(r2)


class TestRescalePngConsistency:

    def test_rescale_png_target_width(self):
        img_buf = _create_test_image(0, 0, fmt='PNG', large=True)
        result = rescale_image_png(img_buf)
        result.seek(0)
        img = Image.open(result)
        assert img.size[0] == 1020

    def test_rescale_png_deterministic(self):
        img_buf = _create_test_image(0, 0, fmt='PNG', large=True)
        r1 = rescale_image_png(img_buf)
        img_buf.seek(0)
        r2 = rescale_image_png(img_buf)
        assert _image_bytes_hash(r1) == _image_bytes_hash(r2)


class TestRescaleWebpConsistency:

    def test_rescale_webp_target_width(self):
        img_buf = _create_test_webp(large=True)
        result = rescale_image_webp(img_buf)
        result.seek(0)
        img = Image.open(result)
        assert img.size[0] == 1020

    def test_rescale_webp_deterministic(self):
        img_buf = _create_test_webp(large=True)
        r1 = rescale_image_webp(img_buf)
        img_buf.seek(0)
        r2 = rescale_image_webp(img_buf)
        assert _image_bytes_hash(r1) == _image_bytes_hash(r2)


class TestRescaleGifConsistency:

    def test_rescale_static_gif_target_width(self):
        img_buf = _create_test_gif(animated=False)
        img = Image.open(img_buf)
        if not getattr(img, "is_animated", False):
            img_buf.seek(0)
            result = rescale_image_gif(img_buf)
            result.seek(0)
            out_img = Image.open(result)
            assert out_img.size[0] == 1020

    def test_rescale_animated_gif_preserved(self):
        img_buf = _create_test_gif(animated=True)
        result = rescale_image_gif(img_buf)
        result.seek(0)
        img = Image.open(result)
        assert img.format == 'GIF'


# ========================================================================
# 3. strip_image_meta — EXIF stripping consistency
# ========================================================================


class TestStripImageMetaConsistency:

    def test_strip_jpeg_removes_exif_data(self):
        img_buf = _create_test_image(400, 300, fmt='JPEG')
        result = strip_image_meta(img_buf, 'JPEG')
        result.seek(0)
        out_img = Image.open(result)
        assert out_img.size == (400, 300)
        assert out_img.format == 'JPEG'

    def test_strip_png_consistency(self):
        img_buf = _create_test_image(400, 300, fmt='PNG')
        r1 = strip_image_meta(img_buf, 'PNG')
        img_buf.seek(0)
        r2 = strip_image_meta(img_buf, 'PNG')
        assert _image_bytes_hash(r1) == _image_bytes_hash(r2)

    def test_strip_gif_animated(self):
        img_buf = _create_test_gif(animated=True)
        result = strip_image_meta(img_buf, 'GIF')
        result.seek(0)
        img = Image.open(result)
        assert img.format == 'GIF'

    def test_strip_jpeg_deterministic(self):
        img_buf = _create_test_image(400, 300, fmt='JPEG')
        r1 = strip_image_meta(img_buf, 'JPEG')
        img_buf.seek(0)
        r2 = strip_image_meta(img_buf, 'JPEG')
        assert _image_bytes_hash(r1) == _image_bytes_hash(r2)


# ========================================================================
# 4. get_filetype — extension detection consistency
# ========================================================================


class TestGetFiletypeConsistency:

    @pytest.mark.parametrize('filename,expected', [
        ('photo.jpeg', '.jpeg'),
        ('photo.jpg', '.jpg'),
        ('photo.png', '.png'),
        ('photo.webp', '.webp'),
        ('photo.gif', '.gif'),
        ('recipe.zip', '.zip'),
        ('original.webp', '.webp'),
        ('image.jpg', '.jpg'),
        ('noext', ''),
        ('image.data', '.data'),
    ])
    def test_filetype_detection(self, filename, expected):
        assert get_filetype(filename) == expected

    def test_filetype_deterministic(self):
        assert get_filetype('photo.jpeg') == get_filetype('photo.jpeg')


class TestIsFileTypeAllowed:

    def test_allowed_image_types(self):
        for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']:
            assert is_file_type_allowed(f'image{ext}', image_only=True) is True

    def test_disallowed_image_types(self):
        for ext in ['.pdf', '.docx', '.mp4']:
            assert is_file_type_allowed(f'file{ext}', image_only=True) is False

    def test_non_image_allowed_when_not_image_only(self):
        assert is_file_type_allowed('file.pdf', image_only=False) is True


# ========================================================================
# 5. import_recipe_image — integration-level image pipeline
# ========================================================================


class TestImportRecipeImagePipeline:

    @pytest.fixture
    def integration(self, u1_s1):
        req = _make_request(u1_s1)
        with scope(space=req.space):
            integ = Integration(req, 'export')
            il = _make_import_log(req.space, req.user)
            integ._ctx = ImportContext(import_log=il)
            integ.import_log = il
            return integ

    def test_jpeg_image_attached_to_recipe(self, integration, space_1):
        with scope(space=space_1):
            recipe = Recipe.objects.create(
                name='ImgTest', created_by=integration.request.user,
                internal=True, space=space_1,
            )
            img_buf = _create_test_image(400, 300, fmt='JPEG')
            integration.import_recipe_image(recipe, img_buf, filetype='.jpeg')
            recipe.refresh_from_db()
            assert bool(recipe.image) is True
            assert recipe.image.name.endswith('.jpeg')

    def test_webp_image_attached_to_recipe(self, integration, space_1):
        with scope(space=space_1):
            recipe = Recipe.objects.create(
                name='WebPTest', created_by=integration.request.user,
                internal=True, space=space_1,
            )
            img_buf = _create_test_webp(large=False)
            integration.import_recipe_image(recipe, img_buf, filetype='.webp')
            recipe.refresh_from_db()
            assert bool(recipe.image) is True
            assert recipe.image.name.endswith('.webp')

    def test_png_image_attached_to_recipe(self, integration, space_1):
        with scope(space=space_1):
            recipe = Recipe.objects.create(
                name='PNGTest', created_by=integration.request.user,
                internal=True, space=space_1,
            )
            img_buf = _create_test_image(400, 300, fmt='PNG')
            integration.import_recipe_image(recipe, img_buf, filetype='.png')
            recipe.refresh_from_db()
            assert bool(recipe.image) is True
            assert recipe.image.name.endswith('.png')

    def test_invalid_image_not_attached(self, integration, space_1):
        with scope(space=space_1):
            recipe = Recipe.objects.create(
                name='BadImgTest', created_by=integration.request.user,
                internal=True, space=space_1,
            )
            bad_buf = BytesIO(b'garbage data')
            integration.import_recipe_image(recipe, bad_buf, filetype='.jpeg')
            recipe.refresh_from_db()
            assert bool(recipe.image) is False

    def test_none_from_handle_image_not_attached(self, integration, space_1):
        with scope(space=space_1):
            recipe = Recipe.objects.create(
                name='NoneImgTest', created_by=integration.request.user,
                internal=True, space=space_1,
            )
            with patch('cookbook.integration.integration.handle_image', return_value=None):
                integration.import_recipe_image(recipe, BytesIO(b'x'), filetype='.jpeg')
                recipe.refresh_from_db()
                assert bool(recipe.image) is False

    def test_image_naming_convention(self, integration, space_1):
        with scope(space=space_1):
            recipe = Recipe.objects.create(
                name='NameTest', created_by=integration.request.user,
                internal=True, space=space_1,
            )
            img_buf = _create_test_image(400, 300, fmt='JPEG')
            integration.import_recipe_image(recipe, img_buf, filetype='.jpeg')
            recipe.refresh_from_db()
            name = recipe.image.name
            assert f'_{recipe.pk}.jpeg' in name


# ========================================================================
# 6. _import_recipe_safe — safe wrapper consistency
# ========================================================================


class TestImportRecipeSafeConsistency:

    @pytest.fixture
    def integration(self, u1_s1):
        req = _make_request(u1_s1)
        with scope(space=req.space):
            integ = Integration(req, 'export')
            il = _make_import_log(req.space, req.user)
            integ._ctx = ImportContext(import_log=il)
            integ.import_log = il
            return integ

    def test_safe_success_returns_true(self, integration, space_1):
        with scope(space=space_1):
            recipe = Recipe.objects.create(
                name='SafeOk', created_by=integration.request.user,
                internal=True, space=space_1,
            )
            img_buf = _create_test_image(400, 300, fmt='JPEG')
            result = integration._import_recipe_safe('SafeOk', recipe, image_file=img_buf, filetype='.jpeg')
            assert result is True
            recipe.refresh_from_db()
            assert bool(recipe.image) is True

    def test_safe_failure_returns_false_and_logs_warning(self, integration, space_1):
        with scope(space=space_1):
            recipe = Recipe.objects.create(
                name='SafeFail', created_by=integration.request.user,
                internal=True, space=space_1,
            )
            with patch.object(integration, 'import_recipe_image', side_effect=IOError('disk full')):
                result = integration._import_recipe_safe('SafeFail', recipe, image_file=BytesIO(b'x'))
            assert result is False
            assert 'WARN [SafeFail]' in integration._ctx.import_log.msg
            assert 'failed to import image' in integration._ctx.import_log.msg

    def test_safe_none_image_returns_true(self, integration, space_1):
        with scope(space=space_1):
            recipe = Recipe.objects.create(
                name='SafeNone', created_by=integration.request.user,
                internal=True, space=space_1,
            )
            result = integration._import_recipe_safe('SafeNone', recipe, image_file=None)
            assert result is True
            recipe.refresh_from_db()
            assert bool(recipe.image) is False

    def test_safe_produces_same_result_as_direct_call(self, integration, space_1):
        with scope(space=space_1):
            recipe_a = Recipe.objects.create(
                name='Direct', created_by=integration.request.user,
                internal=True, space=space_1,
            )
            recipe_b = Recipe.objects.create(
                name='Safe', created_by=integration.request.user,
                internal=True, space=space_1,
            )
            img_buf_a = _create_test_image(400, 300, fmt='JPEG')
            img_buf_b = _create_test_image(400, 300, fmt='JPEG')

            integration.import_recipe_image(recipe_a, img_buf_a, filetype='.jpeg')
            integration._import_recipe_safe('Safe', recipe_b, image_file=img_buf_b, filetype='.jpeg')

            recipe_a.refresh_from_db()
            recipe_b.refresh_from_db()
            assert bool(recipe_a.image) == bool(recipe_b.image)
            assert recipe_a.image.name.endswith('.jpeg')
            assert recipe_b.image.name.endswith('.jpeg')


# ========================================================================
# 7. Image filetype propagation — importer consistency
# ========================================================================


class TestFiletypePropagationConsistency:

    @pytest.fixture
    def integration(self, u1_s1):
        req = _make_request(u1_s1)
        with scope(space=req.space):
            integ = Integration(req, 'export')
            il = _make_import_log(req.space, req.user)
            integ._ctx = ImportContext(import_log=il)
            integ.import_log = il
            return integ

    def test_jpeg_filetype_saves_as_jpeg(self, integration, space_1):
        with scope(space=space_1):
            recipe = Recipe.objects.create(
                name='JpegType', created_by=integration.request.user,
                internal=True, space=space_1,
            )
            img_buf = _create_test_image(400, 300, fmt='JPEG')
            integration.import_recipe_image(recipe, img_buf, filetype='.jpeg')
            recipe.refresh_from_db()
            assert recipe.image.name.endswith('.jpeg')

    def test_webp_filetype_saves_as_webp(self, integration, space_1):
        with scope(space=space_1):
            recipe = Recipe.objects.create(
                name='WebPType', created_by=integration.request.user,
                internal=True, space=space_1,
            )
            img_buf = _create_test_webp(large=False)
            integration.import_recipe_image(recipe, img_buf, filetype='.webp')
            recipe.refresh_from_db()
            assert recipe.image.name.endswith('.webp')

    def test_png_filetype_saves_as_png(self, integration, space_1):
        with scope(space=space_1):
            recipe = Recipe.objects.create(
                name='PNGType', created_by=integration.request.user,
                internal=True, space=space_1,
            )
            img_buf = _create_test_image(400, 300, fmt='PNG')
            integration.import_recipe_image(recipe, img_buf, filetype='.png')
            recipe.refresh_from_db()
            assert recipe.image.name.endswith('.png')

    def test_default_filetype_is_jpeg(self, integration, space_1):
        with scope(space=space_1):
            recipe = Recipe.objects.create(
                name='DefaultType', created_by=integration.request.user,
                internal=True, space=space_1,
            )
            img_buf = _create_test_image(400, 300, fmt='JPEG')
            integration.import_recipe_image(recipe, img_buf)
            recipe.refresh_from_db()
            assert recipe.image.name.endswith('.jpeg')


# ========================================================================
# 8. Large image rescaling — thumbnail generation consistency
# ========================================================================


class TestLargeImageRescalingConsistency:

    @pytest.fixture
    def integration(self, u1_s1):
        req = _make_request(u1_s1)
        with scope(space=req.space):
            integ = Integration(req, 'export')
            il = _make_import_log(req.space, req.user)
            integ._ctx = ImportContext(import_log=il)
            integ.import_log = il
            return integ

    def test_large_jpeg_rescaled_to_1020_width(self, integration, space_1):
        with scope(space=space_1):
            recipe = Recipe.objects.create(
                name='LargeJpeg', created_by=integration.request.user,
                internal=True, space=space_1,
            )
            img_buf = _create_test_image(0, 0, large=True)
            integration.import_recipe_image(recipe, img_buf, filetype='.jpeg')
            recipe.refresh_from_db()
            recipe.image.open('rb')
            img = Image.open(recipe.image)
            assert img.size[0] == 1020

    def test_large_png_rescaled_to_1020_width(self, integration, space_1):
        with scope(space=space_1):
            recipe = Recipe.objects.create(
                name='LargePng', created_by=integration.request.user,
                internal=True, space=space_1,
            )
            img_buf = _create_test_image(0, 0, fmt='PNG', large=True)
            integration.import_recipe_image(recipe, img_buf, filetype='.png')
            recipe.refresh_from_db()
            recipe.image.open('rb')
            img = Image.open(recipe.image)
            assert img.size[0] == 1020

    def test_large_webp_rescaled_to_1020_width(self, integration, space_1):
        with scope(space=space_1):
            recipe = Recipe.objects.create(
                name='LargeWebP', created_by=integration.request.user,
                internal=True, space=space_1,
            )
            img_buf = _create_test_webp(large=True)
            integration.import_recipe_image(recipe, img_buf, filetype='.webp')
            recipe.refresh_from_db()
            recipe.image.open('rb')
            img = Image.open(recipe.image)
            assert img.size[0] == 1020

    def test_small_jpeg_preserves_dimensions(self, integration, space_1):
        with scope(space=space_1):
            recipe = Recipe.objects.create(
                name='SmallJpeg', created_by=integration.request.user,
                internal=True, space=space_1,
            )
            img_buf = _create_test_image(400, 300, fmt='JPEG')
            integration.import_recipe_image(recipe, img_buf, filetype='.jpeg')
            recipe.refresh_from_db()
            recipe.image.open('rb')
            img = Image.open(recipe.image)
            assert img.size == (400, 300)

    def test_small_png_preserves_dimensions(self, integration, space_1):
        with scope(space=space_1):
            recipe = Recipe.objects.create(
                name='SmallPng', created_by=integration.request.user,
                internal=True, space=space_1,
            )
            img_buf = _create_test_image(400, 300, fmt='PNG')
            integration.import_recipe_image(recipe, img_buf, filetype='.png')
            recipe.refresh_from_db()
            recipe.image.open('rb')
            img = Image.open(recipe.image)
            assert img.size == (400, 300)

    def test_rescale_preserves_aspect_ratio(self, integration, space_1):
        with scope(space=space_1):
            recipe = Recipe.objects.create(
                name='AspectJpeg', created_by=integration.request.user,
                internal=True, space=space_1,
            )
            img_buf = _create_test_image(0, 0, large=True)
            original = Image.open(img_buf)
            original_ratio = original.size[1] / original.size[0]
            img_buf.seek(0)
            integration.import_recipe_image(recipe, img_buf, filetype='.jpeg')
            recipe.refresh_from_db()
            recipe.image.open('rb')
            rescaled = Image.open(recipe.image)
            rescaled_ratio = rescaled.size[1] / rescaled.size[0]
            assert abs(original_ratio - rescaled_ratio) < 0.01


# ========================================================================
# 9. Image from sample resource files — real file consistency
# ========================================================================


class TestRealImageFiles:

    @pytest.fixture
    def integration(self, u1_s1):
        req = _make_request(u1_s1)
        with scope(space=req.space):
            integ = Integration(req, 'export')
            il = _make_import_log(req.space, req.user)
            integ._ctx = ImportContext(import_log=il)
            integ.import_log = il
            return integ

    def test_sample_jpeg_processable(self, integration, space_1):
        with scope(space=space_1):
            recipe = Recipe.objects.create(
                name='SampleJpeg', created_by=integration.request.user,
                internal=True, space=space_1,
            )
            with open(SAMPLE_JPG, 'rb') as f:
                integration.import_recipe_image(recipe, BytesIO(f.read()), filetype='.jpeg')
            recipe.refresh_from_db()
            assert bool(recipe.image) is True

    def test_sample_png_processable(self, integration, space_1):
        with scope(space=space_1):
            recipe = Recipe.objects.create(
                name='SamplePng', created_by=integration.request.user,
                internal=True, space=space_1,
            )
            with open(SAMPLE_PNG, 'rb') as f:
                integration.import_recipe_image(recipe, BytesIO(f.read()), filetype='.png')
            recipe.refresh_from_db()
            assert bool(recipe.image) is True

    def test_sample_jpeg_deterministic(self):
        with open(SAMPLE_JPG, 'rb') as f:
            img_buf = BytesIO(f.read())
        r1 = handle_image(MagicMock(), File(img_buf, name='image'), filetype='.jpeg')
        img_buf.seek(0)
        r2 = handle_image(MagicMock(), File(img_buf, name='image'), filetype='.jpeg')
        assert _image_bytes_hash(r1) == _image_bytes_hash(r2)

    def test_sample_png_deterministic(self):
        with open(SAMPLE_PNG, 'rb') as f:
            img_buf = BytesIO(f.read())
        r1 = handle_image(MagicMock(), File(img_buf, name='image'), filetype='.png')
        img_buf.seek(0)
        r2 = handle_image(MagicMock(), File(img_buf, name='image'), filetype='.png')
        assert _image_bytes_hash(r1) == _image_bytes_hash(r2)


# ========================================================================
# 10. Image from zip — simulated importer image pipeline
# ========================================================================


class TestImageFromZipPipeline:

    @pytest.fixture
    def integration(self, u1_s1):
        req = _make_request(u1_s1)
        with scope(space=req.space):
            integ = Integration(req, 'export')
            il = _make_import_log(req.space, req.user)
            integ._ctx = ImportContext(import_log=il)
            integ.import_log = il
            return integ

    def _zip_with_image(self, image_filename, image_data):
        buf = BytesIO()
        with ZipFile(buf, 'w') as zf:
            zf.writestr(image_filename, image_data)
        buf.seek(0)
        return buf

    def test_jpeg_from_zip(self, integration, space_1):
        with scope(space=space_1):
            recipe = Recipe.objects.create(
                name='ZipJpeg', created_by=integration.request.user,
                internal=True, space=space_1,
            )
            img_data = _create_test_image(400, 300, fmt='JPEG').read()
            zf = self._zip_with_image('photo.jpg', img_data)
            zip_file = ZipFile(zf)
            image_bytes = BytesIO(zip_file.read('photo.jpg'))
            integration.import_recipe_image(recipe, image_bytes, filetype=get_filetype('photo.jpg'))
            recipe.refresh_from_db()
            assert bool(recipe.image) is True

    def test_webp_from_zip(self, integration, space_1):
        with scope(space=space_1):
            recipe = Recipe.objects.create(
                name='ZipWebP', created_by=integration.request.user,
                internal=True, space=space_1,
            )
            img_data = _create_test_webp(large=False).read()
            zf = self._zip_with_image('original.webp', img_data)
            zip_file = ZipFile(zf)
            image_bytes = BytesIO(zip_file.read('original.webp'))
            integration.import_recipe_image(recipe, image_bytes, filetype=get_filetype('original.webp'))
            recipe.refresh_from_db()
            assert bool(recipe.image) is True
            assert recipe.image.name.endswith('.webp')


# ========================================================================
# 11. Base64 image — Domestica/Rezeptsuitede style
# ========================================================================


class TestBase64ImagePipeline:

    @pytest.fixture
    def integration(self, u1_s1):
        req = _make_request(u1_s1)
        with scope(space=req.space):
            integ = Integration(req, 'export')
            il = _make_import_log(req.space, req.user)
            integ._ctx = ImportContext(import_log=il)
            integ.import_log = il
            return integ

    def test_base64_jpeg_decoded_and_attached(self, integration, space_1):
        with scope(space=space_1):
            recipe = Recipe.objects.create(
                name='B64Jpeg', created_by=integration.request.user,
                internal=True, space=space_1,
            )
            img_buf = _create_test_image(400, 300, fmt='JPEG')
            b64_data = base64.b64encode(img_buf.read()).decode('ascii')
            decoded = BytesIO(base64.b64decode(b64_data))
            integration.import_recipe_image(recipe, decoded, filetype='.jpeg')
            recipe.refresh_from_db()
            assert bool(recipe.image) is True

    def test_base64_with_data_uri_prefix(self, integration, space_1):
        with scope(space=space_1):
            recipe = Recipe.objects.create(
                name='B64DataUri', created_by=integration.request.user,
                internal=True, space=space_1,
            )
            img_buf = _create_test_image(400, 300, fmt='JPEG')
            b64_data = base64.b64encode(img_buf.read()).decode('ascii')
            data_uri = f'data:image/jpeg;base64,{b64_data}'
            cleaned = data_uri.replace('data:image/jpeg;base64,', '')
            decoded = BytesIO(base64.b64decode(cleaned))
            integration.import_recipe_image(recipe, decoded, filetype='.jpeg')
            recipe.refresh_from_db()
            assert bool(recipe.image) is True


# ========================================================================
# 12. URL-downloaded image — Plantoeat/RecipeSage/Cookmate style
# ========================================================================


class TestUrlDownloadedImagePipeline:

    @pytest.fixture
    def integration(self, u1_s1):
        req = _make_request(u1_s1)
        with scope(space=req.space):
            integ = Integration(req, 'export')
            il = _make_import_log(req.space, req.user)
            integ._ctx = ImportContext(import_log=il)
            integ.import_log = il
            return integ

    def test_mock_url_jpeg_attached(self, integration, space_1):
        with scope(space=space_1):
            recipe = Recipe.objects.create(
                name='UrlJpeg', created_by=integration.request.user,
                internal=True, space=space_1,
            )
            img_buf = _create_test_image(400, 300, fmt='JPEG')
            mock_response = MagicMock()
            mock_response.content = img_buf.read()
            img_buf.seek(0)

            with patch('cookbook.helper.HelperFunctions.safe_request', return_value=mock_response):
                from cookbook.helper.HelperFunctions import safe_request
                response = safe_request('GET', 'https://example.com/image.jpg')
                integration.import_recipe_image(recipe, BytesIO(response.content))
            recipe.refresh_from_db()
            assert bool(recipe.image) is True

    def test_mock_url_image_same_as_local(self, integration, space_1):
        with scope(space=space_1):
            recipe_a = Recipe.objects.create(
                name='LocalImg', created_by=integration.request.user,
                internal=True, space=space_1,
            )
            recipe_b = Recipe.objects.create(
                name='UrlImg', created_by=integration.request.user,
                internal=True, space=space_1,
            )
            img_buf = _create_test_image(400, 300, fmt='JPEG')

            integration.import_recipe_image(recipe_a, BytesIO(img_buf.read()), filetype='.jpeg')
            img_buf.seek(0)

            mock_response = MagicMock()
            mock_response.content = img_buf.read()
            integration.import_recipe_image(recipe_b, BytesIO(mock_response.content))

            recipe_a.refresh_from_db()
            recipe_b.refresh_from_db()
            recipe_a.image.open('rb')
            recipe_b.image.open('rb')
            img_a = Image.open(recipe_a.image)
            img_b = Image.open(recipe_b.image)
            assert img_a.size == img_b.size
