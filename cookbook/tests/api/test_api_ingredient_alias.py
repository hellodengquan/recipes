import copy
import json
import os
import shutil
import tempfile

import pytest
import yaml
from django.contrib.auth.models import Group
from django.urls import reverse
from django_scopes import scopes_disabled

from cookbook.helper.ingredient_normalizer import (
    _invalidate_cache,
    save_canonical_names_to_yaml,
)
from cookbook.models import UserSpace
from cookbook.tests.factories import UserFactory

LIST_URL = 'api:ingredient-alias-list'
DETAIL_URL = 'api:ingredient-alias-detail'


@pytest.fixture
def temp_yaml_dir():
    tmpdir = tempfile.mkdtemp()
    yield tmpdir
    shutil.rmtree(tmpdir)


@pytest.fixture
def temp_yaml_path(temp_yaml_dir):
    return os.path.join(temp_yaml_dir, 'test_aliases.yaml')


@pytest.fixture
def sample_yaml_data():
    return {
        'tomato': {
            'languages': {'en': 'tomato', 'zh': '番茄', 'fr': 'tomate'},
            'aliases': ['tomato', 'tomatoes', '番茄', '西红柿', 'tomate', 'tomates'],
        },
        'egg': {
            'languages': {'en': 'egg', 'zh': '鸡蛋', 'fr': 'œuf'},
            'aliases': ['egg', 'eggs', '鸡蛋', '蛋', 'œuf', 'œufs'],
        },
    }


@pytest.fixture(autouse=True)
def setup_temp_yaml(temp_yaml_path, sample_yaml_data, monkeypatch):
    save_canonical_names_to_yaml(sample_yaml_data, temp_yaml_path)
    monkeypatch.setattr(
        'cookbook.helper.ingredient_normalizer.YAML_PATH', temp_yaml_path
    )
    _invalidate_cache()
    yield
    _invalidate_cache()


@pytest.fixture
def ie1_s1(client, space_1):
    with scopes_disabled():
        Group.objects.get_or_create(name='ingredient-editor')
        user = UserFactory(space=space_1)
        us = UserSpace.objects.create(space=space_1, user=user, active=True)
        us.groups.add(Group.objects.get(name='ingredient-editor'))
        c = copy.deepcopy(client)
        c.force_login(user)
        return c


def _create_payload(**overrides):
    payload = {
        'canonical_key': 'basil',
        'languages': {'en': 'basil', 'zh': '罗勒', 'fr': 'basilic'},
        'aliases': ['basil', '罗勒', 'basilic', '九层塔'],
    }
    payload.update(overrides)
    return payload


class TestIngredientAliasAPIReadPermissions:
    def test_list_as_admin(self, a1_s1):
        r = a1_s1.get(reverse(LIST_URL))
        assert r.status_code == 200

    def test_list_as_ingredient_editor(self, ie1_s1):
        r = ie1_s1.get(reverse(LIST_URL))
        assert r.status_code == 200

    def test_list_as_user_allowed(self, u1_s1):
        r = u1_s1.get(reverse(LIST_URL))
        assert r.status_code == 200

    def test_list_as_guest_allowed(self, g1_s1):
        r = g1_s1.get(reverse(LIST_URL))
        assert r.status_code == 200

    def test_retrieve_as_admin(self, a1_s1):
        r = a1_s1.get(reverse(DETAIL_URL, kwargs={'pk': 'tomato'}))
        assert r.status_code == 200

    def test_retrieve_as_ingredient_editor(self, ie1_s1):
        r = ie1_s1.get(reverse(DETAIL_URL, kwargs={'pk': 'tomato'}))
        assert r.status_code == 200

    def test_retrieve_as_user_allowed(self, u1_s1):
        r = u1_s1.get(reverse(DETAIL_URL, kwargs={'pk': 'tomato'}))
        assert r.status_code == 200

    def test_retrieve_as_guest_allowed(self, g1_s1):
        r = g1_s1.get(reverse(DETAIL_URL, kwargs={'pk': 'tomato'}))
        assert r.status_code == 200


class TestIngredientAliasAPICreatePermissions:
    def test_create_as_admin_success(self, a1_s1):
        r = a1_s1.post(
            reverse(LIST_URL),
            data=json.dumps(_create_payload()),
            content_type='application/json',
        )
        assert r.status_code == 201

    def test_create_as_ingredient_editor_success(self, ie1_s1):
        r = ie1_s1.post(
            reverse(LIST_URL),
            data=json.dumps(_create_payload()),
            content_type='application/json',
        )
        assert r.status_code == 201

    def test_create_as_user_forbidden(self, u1_s1):
        r = u1_s1.post(
            reverse(LIST_URL),
            data=json.dumps(_create_payload()),
            content_type='application/json',
        )
        assert r.status_code == 403

    def test_create_as_guest_forbidden(self, g1_s1):
        r = g1_s1.post(
            reverse(LIST_URL),
            data=json.dumps(_create_payload()),
            content_type='application/json',
        )
        assert r.status_code == 403


class TestIngredientAliasAPIUpdatePermissions:
    def test_update_as_admin_success(self, a1_s1):
        r = a1_s1.patch(
            reverse(DETAIL_URL, kwargs={'pk': 'tomato'}),
            data=json.dumps({'aliases': ['tomato', '小番茄']}),
            content_type='application/json',
        )
        assert r.status_code == 200

    def test_update_as_ingredient_editor_success(self, ie1_s1):
        r = ie1_s1.patch(
            reverse(DETAIL_URL, kwargs={'pk': 'tomato'}),
            data=json.dumps({'aliases': ['tomato', '小番茄']}),
            content_type='application/json',
        )
        assert r.status_code == 200

    def test_update_as_user_forbidden(self, u1_s1):
        r = u1_s1.patch(
            reverse(DETAIL_URL, kwargs={'pk': 'tomato'}),
            data=json.dumps({'aliases': ['tomato']}),
            content_type='application/json',
        )
        assert r.status_code == 403

    def test_update_as_guest_forbidden(self, g1_s1):
        r = g1_s1.patch(
            reverse(DETAIL_URL, kwargs={'pk': 'tomato'}),
            data=json.dumps({'aliases': ['tomato']}),
            content_type='application/json',
        )
        assert r.status_code == 403


class TestIngredientAliasAPIDeletePermissions:
    def test_delete_as_admin_success(self, a1_s1):
        r = a1_s1.delete(reverse(DETAIL_URL, kwargs={'pk': 'egg'}))
        assert r.status_code == 204

    def test_delete_as_ingredient_editor_success(self, ie1_s1):
        r = ie1_s1.delete(reverse(DETAIL_URL, kwargs={'pk': 'egg'}))
        assert r.status_code == 204

    def test_delete_as_user_forbidden(self, u1_s1):
        r = u1_s1.delete(reverse(DETAIL_URL, kwargs={'pk': 'egg'}))
        assert r.status_code == 403

    def test_delete_as_guest_forbidden(self, g1_s1):
        r = g1_s1.delete(reverse(DETAIL_URL, kwargs={'pk': 'egg'}))
        assert r.status_code == 403


class TestAdminWriteSuccess:
    def test_admin_create_and_normalize_hit(self, a1_s1):
        a1_s1.post(
            reverse(LIST_URL),
            data=json.dumps(_create_payload()),
            content_type='application/json',
        )
        from cookbook.helper.ingredient_normalizer import normalize_ingredient_name
        result = normalize_ingredient_name('九层塔')
        assert result['is_mapped'] is True
        assert result['canonical_name'] == 'basil'

    def test_admin_update_and_normalize_hit(self, a1_s1):
        a1_s1.patch(
            reverse(DETAIL_URL, kwargs={'pk': 'tomato'}),
            data=json.dumps({'aliases': ['tomato', 'tomatoes', '番茄', '西红柿', '圣女果', 'tomate', 'tomates']}),
            content_type='application/json',
        )
        from cookbook.helper.ingredient_normalizer import normalize_ingredient_name
        result = normalize_ingredient_name('圣女果')
        assert result['is_mapped'] is True
        assert result['canonical_name'] == 'tomato'

    def test_admin_delete_and_normalize_fallback(self, a1_s1):
        a1_s1.delete(reverse(DETAIL_URL, kwargs={'pk': 'egg'}))
        from cookbook.helper.ingredient_normalizer import normalize_ingredient_name
        assert normalize_ingredient_name('egg')['is_mapped'] is False
        assert normalize_ingredient_name('鸡蛋')['is_mapped'] is False
        assert normalize_ingredient_name('œufs')['is_mapped'] is False


class TestIngredientEditorWriteSuccess:
    def test_editor_create_and_normalize_hit(self, ie1_s1):
        ie1_s1.post(
            reverse(LIST_URL),
            data=json.dumps(_create_payload()),
            content_type='application/json',
        )
        from cookbook.helper.ingredient_normalizer import normalize_ingredient_name
        result = normalize_ingredient_name('九层塔')
        assert result['is_mapped'] is True
        assert result['canonical_name'] == 'basil'

    def test_editor_create_french_alias_hit(self, ie1_s1):
        ie1_s1.post(
            reverse(LIST_URL),
            data=json.dumps(_create_payload()),
            content_type='application/json',
        )
        from cookbook.helper.ingredient_normalizer import normalize_ingredient_name
        result = normalize_ingredient_name('basilic')
        assert result['is_mapped'] is True
        assert result['canonical_name'] == 'basil'

    def test_editor_update_add_alias_and_hit(self, ie1_s1):
        ie1_s1.patch(
            reverse(DETAIL_URL, kwargs={'pk': 'tomato'}),
            data=json.dumps({'aliases': ['tomato', 'tomatoes', '番茄', '西红柿', '圣女果', 'tomate', 'tomates']}),
            content_type='application/json',
        )
        from cookbook.helper.ingredient_normalizer import normalize_ingredient_name
        result = normalize_ingredient_name('圣女果')
        assert result['is_mapped'] is True
        assert result['canonical_name'] == 'tomato'

    def test_editor_update_languages_merge(self, ie1_s1):
        ie1_s1.patch(
            reverse(DETAIL_URL, kwargs={'pk': 'tomato'}),
            data=json.dumps({'languages': {'de': 'Tomate'}}),
            content_type='application/json',
        )
        from cookbook.helper.ingredient_normalizer import get_canonical_names
        data = get_canonical_names()
        assert data['tomato']['languages']['de'] == 'Tomate'
        assert data['tomato']['languages']['en'] == 'tomato'

    def test_editor_delete_and_normalize_fallback(self, ie1_s1):
        ie1_s1.delete(reverse(DETAIL_URL, kwargs={'pk': 'egg'}))
        from cookbook.helper.ingredient_normalizer import normalize_ingredient_name
        for name in ['egg', 'eggs', '鸡蛋', '蛋', 'œuf', 'œufs']:
            result = normalize_ingredient_name(name)
            assert result['is_mapped'] is False, f'{name} should not be mapped after delete'

    def test_editor_full_lifecycle(self, ie1_s1):
        from cookbook.helper.ingredient_normalizer import normalize_ingredient_name

        ie1_s1.post(
            reverse(LIST_URL),
            data=json.dumps({
                'canonical_key': 'cinnamon',
                'languages': {'en': 'cinnamon', 'zh': '肉桂', 'fr': 'cannelle'},
                'aliases': ['cinnamon', '肉桂'],
            }),
            content_type='application/json',
        )
        assert normalize_ingredient_name('肉桂')['canonical_name'] == 'cinnamon'
        assert normalize_ingredient_name('cannelle')['is_mapped'] is False

        ie1_s1.patch(
            reverse(DETAIL_URL, kwargs={'pk': 'cinnamon'}),
            data=json.dumps({'aliases': ['cinnamon', '肉桂', 'cannelle']}),
            content_type='application/json',
        )
        assert normalize_ingredient_name('cannelle')['canonical_name'] == 'cinnamon'

        ie1_s1.delete(reverse(DETAIL_URL, kwargs={'pk': 'cinnamon'}))
        assert normalize_ingredient_name('肉桂')['is_mapped'] is False
        assert normalize_ingredient_name('cannelle')['is_mapped'] is False


class TestNormalUserWriteReturns403:
    def test_user_create_returns_403(self, u1_s1):
        r = u1_s1.post(
            reverse(LIST_URL),
            data=json.dumps(_create_payload()),
            content_type='application/json',
        )
        assert r.status_code == 403

    def test_user_update_returns_403(self, u1_s1):
        r = u1_s1.patch(
            reverse(DETAIL_URL, kwargs={'pk': 'tomato'}),
            data=json.dumps({'aliases': ['tomato']}),
            content_type='application/json',
        )
        assert r.status_code == 403

    def test_user_delete_returns_403(self, u1_s1):
        r = u1_s1.delete(reverse(DETAIL_URL, kwargs={'pk': 'egg'}))
        assert r.status_code == 403

    def test_user_put_returns_403(self, u1_s1):
        r = u1_s1.put(
            reverse(DETAIL_URL, kwargs={'pk': 'tomato'}),
            data=json.dumps({
                'languages': {'en': 'tomato', 'zh': '番茄', 'fr': 'tomate'},
                'aliases': ['tomato'],
            }),
            content_type='application/json',
        )
        assert r.status_code == 403

    def test_user_read_still_works(self, u1_s1):
        r = u1_s1.get(reverse(LIST_URL))
        assert r.status_code == 200
        r = u1_s1.get(reverse(DETAIL_URL, kwargs={'pk': 'tomato'}))
        assert r.status_code == 200

    def test_guest_create_returns_403(self, g1_s1):
        r = g1_s1.post(
            reverse(LIST_URL),
            data=json.dumps(_create_payload()),
            content_type='application/json',
        )
        assert r.status_code == 403

    def test_guest_update_returns_403(self, g1_s1):
        r = g1_s1.patch(
            reverse(DETAIL_URL, kwargs={'pk': 'tomato'}),
            data=json.dumps({'aliases': ['tomato']}),
            content_type='application/json',
        )
        assert r.status_code == 403

    def test_guest_delete_returns_403(self, g1_s1):
        r = g1_s1.delete(reverse(DETAIL_URL, kwargs={'pk': 'egg'}))
        assert r.status_code == 403

    def test_guest_read_still_works(self, g1_s1):
        r = g1_s1.get(reverse(LIST_URL))
        assert r.status_code == 200
        r = g1_s1.get(reverse(DETAIL_URL, kwargs={'pk': 'tomato'}))
        assert r.status_code == 200


class TestIngredientEditorNotInAdminHierarchy:
    """
    Verify that ingredient-editor is NOT automatically granted by admin hierarchy.
    The has_group_permission function with ['ingredient-editor'] should only
    match users explicitly in the ingredient-editor group, not admin users
    through hierarchy. However, the permission class explicitly checks both
    admin AND ingredient-editor, so admin users should still pass.
    """

    def test_admin_can_write_because_permission_checks_both(self, a1_s1):
        r = a1_s1.post(
            reverse(LIST_URL),
            data=json.dumps(_create_payload()),
            content_type='application/json',
        )
        assert r.status_code == 201

    def test_editor_can_write_explicitly(self, ie1_s1):
        r = ie1_s1.post(
            reverse(LIST_URL),
            data=json.dumps(_create_payload()),
            content_type='application/json',
        )
        assert r.status_code == 201

    def test_regular_user_cannot_write_even_though_authenticated(self, u1_s1):
        r = u1_s1.post(
            reverse(LIST_URL),
            data=json.dumps(_create_payload()),
            content_type='application/json',
        )
        assert r.status_code == 403
