import json
import os
import shutil
import tempfile

import pytest
import yaml
from django.urls import reverse

from cookbook.helper.ingredient_normalizer import (
    _invalidate_cache,
    save_canonical_names_to_yaml,
    load_canonical_names_from_yaml,
)

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


class TestIngredientAliasAPIList:
    def test_list_as_admin(self, a1_s1):
        r = a1_s1.get(reverse(LIST_URL))
        assert r.status_code == 200
        data = json.loads(r.content)
        keys = [e['canonical_key'] for e in data]
        assert 'tomato' in keys
        assert 'egg' in keys

    def test_list_as_user_forbidden(self, u1_s1):
        r = u1_s1.get(reverse(LIST_URL))
        assert r.status_code == 403

    def test_list_as_guest_forbidden(self, g1_s1):
        r = g1_s1.get(reverse(LIST_URL))
        assert r.status_code == 403


class TestIngredientAliasAPIRetrieve:
    def test_retrieve_existing(self, a1_s1):
        r = a1_s1.get(reverse(DETAIL_URL, kwargs={'pk': 'tomato'}))
        assert r.status_code == 200
        data = json.loads(r.content)
        assert data['canonical_key'] == 'tomato'
        assert data['languages']['en'] == 'tomato'
        assert '西红柿' in data['aliases']

    def test_retrieve_nonexistent(self, a1_s1):
        r = a1_s1.get(reverse(DETAIL_URL, kwargs={'pk': 'nonexistent'}))
        assert r.status_code == 404


class TestIngredientAliasAPICreate:
    def test_create_new_entry(self, a1_s1):
        payload = {
            'canonical_key': 'basil',
            'languages': {'en': 'basil', 'zh': '罗勒', 'fr': 'basilic'},
            'aliases': ['basil', '罗勒', 'basilic', '九层塔'],
        }
        r = a1_s1.post(
            reverse(LIST_URL),
            data=json.dumps(payload),
            content_type='application/json',
        )
        assert r.status_code == 201
        from cookbook.helper.ingredient_normalizer import normalize_ingredient_name
        result = normalize_ingredient_name('九层塔')
        assert result['is_mapped'] is True
        assert result['canonical_name'] == 'basil'

    def test_create_then_normalize_french(self, a1_s1):
        payload = {
            'canonical_key': 'basil',
            'languages': {'en': 'basil', 'zh': '罗勒', 'fr': 'basilic'},
            'aliases': ['basil', '罗勒', 'basilic'],
        }
        a1_s1.post(
            reverse(LIST_URL),
            data=json.dumps(payload),
            content_type='application/json',
        )
        from cookbook.helper.ingredient_normalizer import normalize_ingredient_name
        result = normalize_ingredient_name('basilic')
        assert result['is_mapped'] is True
        assert result['canonical_name'] == 'basil'

    def test_create_missing_fields(self, a1_s1):
        r = a1_s1.post(
            reverse(LIST_URL),
            data=json.dumps({'canonical_key': 'basil'}),
            content_type='application/json',
        )
        assert r.status_code == 400

    def test_create_as_user_forbidden(self, u1_s1):
        payload = {
            'canonical_key': 'basil',
            'languages': {'en': 'basil', 'zh': '罗勒', 'fr': 'basilic'},
            'aliases': ['basil'],
        }
        r = u1_s1.post(
            reverse(LIST_URL),
            data=json.dumps(payload),
            content_type='application/json',
        )
        assert r.status_code == 403


class TestIngredientAliasAPIUpdate:
    def test_update_aliases(self, a1_s1):
        payload = {'aliases': ['tomato', 'tomatoes', '番茄', '西红柿', '小番茄']}
        r = a1_s1.patch(
            reverse(DETAIL_URL, kwargs={'pk': 'tomato'}),
            data=json.dumps(payload),
            content_type='application/json',
        )
        assert r.status_code == 200
        from cookbook.helper.ingredient_normalizer import normalize_ingredient_name
        result = normalize_ingredient_name('小番茄')
        assert result['is_mapped'] is True
        assert result['canonical_name'] == 'tomato'

    def test_update_languages_merge(self, a1_s1):
        payload = {'languages': {'de': 'Tomate'}}
        r = a1_s1.patch(
            reverse(DETAIL_URL, kwargs={'pk': 'tomato'}),
            data=json.dumps(payload),
            content_type='application/json',
        )
        assert r.status_code == 200
        from cookbook.helper.ingredient_normalizer import get_canonical_names
        data = get_canonical_names()
        assert data['tomato']['languages']['de'] == 'Tomate'
        assert data['tomato']['languages']['en'] == 'tomato'

    def test_update_nonexistent(self, a1_s1):
        payload = {'aliases': ['test']}
        r = a1_s1.patch(
            reverse(DETAIL_URL, kwargs={'pk': 'nonexistent'}),
            data=json.dumps(payload),
            content_type='application/json',
        )
        assert r.status_code == 404


class TestIngredientAliasAPIDelete:
    def test_delete_entry(self, a1_s1):
        r = a1_s1.delete(reverse(DETAIL_URL, kwargs={'pk': 'egg'}))
        assert r.status_code == 204
        from cookbook.helper.ingredient_normalizer import normalize_ingredient_name
        result = normalize_ingredient_name('鸡蛋')
        assert result['is_mapped'] is False

    def test_delete_then_normalize_falls_back_all_languages(self, a1_s1):
        a1_s1.delete(reverse(DETAIL_URL, kwargs={'pk': 'egg'}))
        from cookbook.helper.ingredient_normalizer import normalize_ingredient_name
        for name in ['egg', 'eggs', '鸡蛋', '蛋', 'œuf', 'œufs']:
            result = normalize_ingredient_name(name)
            assert result['is_mapped'] is False, f'{name} should not be mapped after delete'

    def test_delete_nonexistent(self, a1_s1):
        r = a1_s1.delete(reverse(DETAIL_URL, kwargs={'pk': 'nonexistent'}))
        assert r.status_code == 404

    def test_delete_as_user_forbidden(self, u1_s1):
        r = u1_s1.delete(reverse(DETAIL_URL, kwargs={'pk': 'egg'}))
        assert r.status_code == 403


class TestAPICRUDAndCanonicalHit:
    """Integration tests: API CRUD operations followed by canonical name hit verification."""

    def test_create_new_then_hit_in_all_three_languages(self, a1_s1):
        payload = {
            'canonical_key': 'cinnamon',
            'languages': {'en': 'cinnamon', 'zh': '肉桂', 'fr': 'cannelle'},
            'aliases': ['cinnamon', '肉桂', '桂皮', 'cannelle'],
        }
        a1_s1.post(
            reverse(LIST_URL),
            data=json.dumps(payload),
            content_type='application/json',
        )
        from cookbook.helper.ingredient_normalizer import normalize_ingredient_name
        assert normalize_ingredient_name('cinnamon')['canonical_name'] == 'cinnamon'
        assert normalize_ingredient_name('肉桂')['canonical_name'] == 'cinnamon'
        assert normalize_ingredient_name('cannelle')['canonical_name'] == 'cinnamon'

    def test_update_add_alias_then_hit(self, a1_s1):
        payload = {'aliases': ['tomato', 'tomatoes', '番茄', '西红柿', '圣女果', 'tomate', 'tomates']}
        a1_s1.patch(
            reverse(DETAIL_URL, kwargs={'pk': 'tomato'}),
            data=json.dumps(payload),
            content_type='application/json',
        )
        from cookbook.helper.ingredient_normalizer import normalize_ingredient_name
        result = normalize_ingredient_name('圣女果')
        assert result['is_mapped'] is True
        assert result['canonical_name'] == 'tomato'

    def test_delete_then_add_same_key_again(self, a1_s1):
        a1_s1.delete(reverse(DETAIL_URL, kwargs={'pk': 'egg'}))
        from cookbook.helper.ingredient_normalizer import normalize_ingredient_name
        assert normalize_ingredient_name('egg')['is_mapped'] is False

        payload = {
            'canonical_key': 'egg',
            'languages': {'en': 'egg', 'zh': '鸡蛋', 'fr': 'œuf'},
            'aliases': ['egg', 'eggs', '鸡蛋', 'œuf', 'œufs'],
        }
        a1_s1.post(
            reverse(LIST_URL),
            data=json.dumps(payload),
            content_type='application/json',
        )
        result = normalize_ingredient_name('鸡蛋')
        assert result['is_mapped'] is True
        assert result['canonical_name'] == 'egg'

    def test_full_lifecycle_create_update_delete(self, a1_s1):
        from cookbook.helper.ingredient_normalizer import normalize_ingredient_name

        payload = {
            'canonical_key': 'turmeric',
            'languages': {'en': 'turmeric', 'zh': '姜黄', 'fr': 'curcuma'},
            'aliases': ['turmeric', '姜黄'],
        }
        a1_s1.post(
            reverse(LIST_URL),
            data=json.dumps(payload),
            content_type='application/json',
        )
        assert normalize_ingredient_name('姜黄')['canonical_name'] == 'turmeric'
        assert normalize_ingredient_name('curcuma')['is_mapped'] is False

        payload = {'aliases': ['turmeric', '姜黄', 'curcuma']}
        a1_s1.patch(
            reverse(DETAIL_URL, kwargs={'pk': 'turmeric'}),
            data=json.dumps(payload),
            content_type='application/json',
        )
        assert normalize_ingredient_name('curcuma')['canonical_name'] == 'turmeric'

        a1_s1.delete(reverse(DETAIL_URL, kwargs={'pk': 'turmeric'}))
        assert normalize_ingredient_name('姜黄')['is_mapped'] is False
        assert normalize_ingredient_name('curcuma')['is_mapped'] is False
