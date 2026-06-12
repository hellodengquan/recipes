import os
import shutil
import tempfile

import pytest
import yaml

from cookbook.helper.ingredient_normalizer import (
    load_canonical_names_from_yaml,
    save_canonical_names_to_yaml,
    add_alias_entry,
    update_alias_entry,
    delete_alias_entry,
    get_canonical_names,
    normalize_ingredient_name,
    get_canonical_name,
    get_all_supported_ingredients,
    reload_aliases,
    _invalidate_cache,
    _build_alias_map,
    CANONICAL_NAMES,
)


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
        'salt': {
            'languages': {'en': 'salt', 'zh': '盐', 'fr': 'sel'},
            'aliases': ['salt', '盐', 'sel'],
        },
    }


class TestYamlFileParsing:
    """Tests for YAML config file parsing."""

    def test_load_valid_yaml(self, temp_yaml_path, sample_yaml_data):
        with open(temp_yaml_path, 'w', encoding='utf-8') as f:
            yaml.dump(sample_yaml_data, f, allow_unicode=True)
        result = load_canonical_names_from_yaml(temp_yaml_path)
        assert 'tomato' in result
        assert 'egg' in result
        assert 'salt' in result
        assert result['tomato']['languages']['en'] == 'tomato'
        assert '西红柿' in result['tomato']['aliases']

    def test_load_nonexistent_file(self, temp_yaml_dir):
        path = os.path.join(temp_yaml_dir, 'nonexistent.yaml')
        result = load_canonical_names_from_yaml(path)
        assert result == {}

    def test_load_empty_yaml(self, temp_yaml_path):
        with open(temp_yaml_path, 'w', encoding='utf-8') as f:
            f.write('')
        result = load_canonical_names_from_yaml(temp_yaml_path)
        assert result == {}

    def test_load_invalid_yaml_syntax(self, temp_yaml_path):
        with open(temp_yaml_path, 'w', encoding='utf-8') as f:
            f.write('invalid: yaml: content:\n  - [broken')
        result = load_canonical_names_from_yaml(temp_yaml_path)
        assert isinstance(result, dict)

    def test_load_yaml_with_missing_languages(self, temp_yaml_path):
        data = {
            'tomato': {
                'aliases': ['tomato'],
            },
        }
        with open(temp_yaml_path, 'w', encoding='utf-8') as f:
            yaml.dump(data, f, allow_unicode=True)
        result = load_canonical_names_from_yaml(temp_yaml_path)
        assert 'tomato' not in result

    def test_load_yaml_with_missing_aliases_defaults_empty(self, temp_yaml_path):
        data = {
            'tomato': {
                'languages': {'en': 'tomato', 'zh': '番茄', 'fr': 'tomate'},
            },
        }
        with open(temp_yaml_path, 'w', encoding='utf-8') as f:
            yaml.dump(data, f, allow_unicode=True)
        result = load_canonical_names_from_yaml(temp_yaml_path)
        assert 'tomato' in result
        assert result['tomato']['aliases'] == []

    def test_load_yaml_with_non_dict_entry(self, temp_yaml_path):
        data = {
            'tomato': 'not a dict',
            'egg': {
                'languages': {'en': 'egg', 'zh': '鸡蛋', 'fr': 'œuf'},
                'aliases': ['egg'],
            },
        }
        with open(temp_yaml_path, 'w', encoding='utf-8') as f:
            yaml.dump(data, f, allow_unicode=True)
        result = load_canonical_names_from_yaml(temp_yaml_path)
        assert 'egg' in result

    def test_save_and_reload_yaml(self, temp_yaml_path, sample_yaml_data):
        save_canonical_names_to_yaml(sample_yaml_data, temp_yaml_path)
        result = load_canonical_names_from_yaml(temp_yaml_path)
        assert result['tomato']['languages']['en'] == 'tomato'
        assert result['egg']['aliases'] == sample_yaml_data['egg']['aliases']

    def test_save_preserves_unicode(self, temp_yaml_path):
        data = {
            'tomato': {
                'languages': {'en': 'tomato', 'zh': '番茄', 'fr': 'tomate'},
                'aliases': ['番茄', '西红柿', 'œuf'],
            },
        }
        save_canonical_names_to_yaml(data, temp_yaml_path)
        result = load_canonical_names_from_yaml(temp_yaml_path)
        assert '番茄' in result['tomato']['aliases']
        assert '西红柿' in result['tomato']['aliases']
        assert 'œuf' in result['tomato']['aliases']

    def test_load_default_yaml_file(self):
        result = load_canonical_names_from_yaml()
        assert isinstance(result, dict)
        assert len(result) >= 20
        assert 'tomato' in result
        assert 'egg' in result


class TestAliasCRUDOperations:
    """Tests for add, update, delete alias operations with temp YAML files."""

    @pytest.fixture(autouse=True)
    def setup_temp_yaml(self, temp_yaml_path, sample_yaml_data, monkeypatch):
        save_canonical_names_to_yaml(sample_yaml_data, temp_yaml_path)
        monkeypatch.setattr(
            'cookbook.helper.ingredient_normalizer.YAML_PATH', temp_yaml_path
        )
        _invalidate_cache()
        yield
        _invalidate_cache()

    def test_add_new_entry(self):
        success = add_alias_entry(
            canonical_key='basil',
            languages={'en': 'basil', 'zh': '罗勒', 'fr': 'basilic'},
            aliases=['basil', '罗勒', 'basilic', '九层塔'],
        )
        assert success is True
        data = get_canonical_names()
        assert 'basil' in data
        assert data['basil']['languages']['zh'] == '罗勒'
        assert '九层塔' in data['basil']['aliases']

    def test_add_entry_then_normalize_new_alias(self):
        add_alias_entry(
            canonical_key='basil',
            languages={'en': 'basil', 'zh': '罗勒', 'fr': 'basilic'},
            aliases=['basil', '罗勒', 'basilic', '九层塔'],
        )
        result = normalize_ingredient_name('九层塔')
        assert result['is_mapped'] is True
        assert result['canonical_name'] == 'basil'

    def test_add_entry_then_normalize_french(self):
        add_alias_entry(
            canonical_key='basil',
            languages={'en': 'basil', 'zh': '罗勒', 'fr': 'basilic'},
            aliases=['basil', '罗勒', 'basilic'],
        )
        result = normalize_ingredient_name('basilic')
        assert result['is_mapped'] is True
        assert result['canonical_name'] == 'basil'

    def test_update_aliases(self):
        success = update_alias_entry(
            canonical_key='salt',
            aliases=['salt', '盐', 'sel', '海盐', 'sel de mer'],
        )
        assert success is True
        data = get_canonical_names()
        assert '海盐' in data['salt']['aliases']
        assert 'sel de mer' in data['salt']['aliases']

    def test_update_languages_merge(self):
        success = update_alias_entry(
            canonical_key='salt',
            languages={'de': 'Salz'},
        )
        assert success is True
        data = get_canonical_names()
        assert data['salt']['languages']['en'] == 'salt'
        assert data['salt']['languages']['de'] == 'Salz'

    def test_update_then_normalize_new_alias(self):
        update_alias_entry(
            canonical_key='salt',
            aliases=['salt', '盐', 'sel', '海盐'],
        )
        result = normalize_ingredient_name('海盐')
        assert result['is_mapped'] is True
        assert result['canonical_name'] == 'salt'

    def test_update_nonexistent_key_raises(self):
        with pytest.raises(ValueError, match='does not exist'):
            update_alias_entry(canonical_key='nonexistent', aliases=['test'])

    def test_delete_entry(self):
        success = delete_alias_entry(canonical_key='salt')
        assert success is True
        data = get_canonical_names()
        assert 'salt' not in data

    def test_delete_entry_then_normalize_falls_back(self):
        delete_alias_entry(canonical_key='salt')
        result = normalize_ingredient_name('salt')
        assert result['is_mapped'] is False

    def test_delete_entry_removes_all_aliases(self):
        delete_alias_entry(canonical_key='salt')
        result = normalize_ingredient_name('盐')
        assert result['is_mapped'] is False
        result2 = normalize_ingredient_name('sel')
        assert result2['is_mapped'] is False

    def test_delete_nonexistent_key_raises(self):
        with pytest.raises(ValueError, match='does not exist'):
            delete_alias_entry(canonical_key='nonexistent')

    def test_add_overwrite_existing(self):
        add_alias_entry(
            canonical_key='tomato',
            languages={'en': 'tomato', 'zh': '番茄', 'fr': 'tomate'},
            aliases=['tomato', 'tomates', '番茄'],
        )
        data = get_canonical_names()
        assert '西红柿' not in data['tomato']['aliases']
        assert 'tomates' in data['tomato']['aliases']

    def test_other_entries_unchanged_after_add(self):
        add_alias_entry(
            canonical_key='basil',
            languages={'en': 'basil', 'zh': '罗勒', 'fr': 'basilic'},
            aliases=['basil'],
        )
        data = get_canonical_names()
        assert 'tomato' in data
        assert 'egg' in data
        assert data['tomato']['languages']['en'] == 'tomato'


class TestHotReload:
    """Tests for hot reload when YAML file changes."""

    @pytest.fixture(autouse=True)
    def setup_temp_yaml(self, temp_yaml_path, sample_yaml_data, monkeypatch):
        save_canonical_names_to_yaml(sample_yaml_data, temp_yaml_path)
        monkeypatch.setattr(
            'cookbook.helper.ingredient_normalizer.YAML_PATH', temp_yaml_path
        )
        _invalidate_cache()
        yield
        _invalidate_cache()

    def test_reload_aliases_picks_up_new_entry(self):
        data = get_canonical_names()
        assert 'pepper' not in data
        data['pepper'] = {
            'languages': {'en': 'pepper', 'zh': '胡椒', 'fr': 'poivre'},
            'aliases': ['pepper', '胡椒', 'poivre'],
        }
        save_canonical_names_to_yaml(data)
        reload_aliases()
        result = normalize_ingredient_name('胡椒')
        assert result['is_mapped'] is True
        assert result['canonical_name'] == 'pepper'

    def test_reload_aliases_picks_up_deleted_entry(self):
        data = get_canonical_names()
        assert 'salt' in data
        del data['salt']
        save_canonical_names_to_yaml(data)
        reload_aliases()
        result = normalize_ingredient_name('salt')
        assert result['is_mapped'] is False

    def test_reload_aliases_picks_up_modified_aliases(self):
        data = get_canonical_names()
        data['tomato']['aliases'].append('小番茄')
        save_canonical_names_to_yaml(data)
        reload_aliases()
        result = normalize_ingredient_name('小番茄')
        assert result['is_mapped'] is True
        assert result['canonical_name'] == 'tomato'

    def test_canonical_names_proxy_reflects_yaml(self):
        assert 'tomato' in CANONICAL_NAMES
        assert len(CANONICAL_NAMES) >= 3

    def test_get_all_supported_ingredients_reflects_yaml(self):
        ingredients = get_all_supported_ingredients()
        keys = [i['canonical_key'] for i in ingredients]
        assert 'tomato' in keys
        assert 'egg' in keys
        assert 'salt' in keys


class TestBuildAliasMap:
    """Tests for the alias map builder."""

    def test_build_alias_map_basic(self):
        data = {
            'tomato': {
                'languages': {'en': 'tomato', 'zh': '番茄'},
                'aliases': ['tomato', '番茄'],
            },
        }
        alias_map = _build_alias_map(data)
        assert 'tomato' in alias_map
        assert '番茄' in alias_map
        assert alias_map['tomato']['canonical_key'] == 'tomato'

    def test_build_alias_map_first_alias_wins(self):
        data = {
            'tomato': {
                'languages': {'en': 'tomato', 'zh': '番茄'},
                'aliases': ['tomato', 'tomato'],
            },
        }
        alias_map = _build_alias_map(data)
        assert alias_map['tomato']['canonical_name'] == 'tomato'

    def test_build_alias_map_missing_aliases(self):
        data = {
            'tomato': {
                'languages': {'en': 'tomato', 'zh': '番茄'},
            },
        }
        alias_map = _build_alias_map(data)
        assert len(alias_map) == 0
