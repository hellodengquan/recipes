import json
import pytest
import time
import uuid
from decimal import Decimal

from django.contrib import auth
from django.core.cache import caches
from django.urls import reverse
from django_scopes import scopes_disabled

from cookbook.helper.async_cache_refresher import AsyncCacheRefresher, RefreshWork
from cookbook.helper.cache_helper import CacheHelper
from cookbook.helper.unit_conversion_helper import UnitConversionHelper
from cookbook.models import CacheRefreshStatus, CacheRefreshTask, CacheRefreshType, Food, Ingredient, ShoppingListEntry, Unit, UnitConversion

LIST_URL = 'api:unit-list'
DETAIL_URL = 'api:unit-detail'
MERGE_URL = 'api:unit-merge'


def random_food(space_1, u1_s1):
    return Food.objects.get_or_create(name=str(uuid.uuid4()), space=space_1)[0]


@pytest.fixture()
def obj_1(space_1):
    return Unit.objects.get_or_create(name='test_1', space=space_1)[0]


@pytest.fixture
def obj_2(space_1):
    return Unit.objects.get_or_create(name='test_2', space=space_1)[0]


@pytest.fixture
def obj_3(space_2):
    return Unit.objects.get_or_create(name='test_3', space=space_2)[0]


@pytest.fixture()
def ing_1_s1(obj_1, space_1, u1_s1):
    return Ingredient.objects.create(unit=obj_1, food=random_food(space_1, u1_s1), space=space_1)


@pytest.fixture()
def ing_2_s1(obj_2, space_1, u1_s1):
    return Ingredient.objects.create(unit=obj_2, food=random_food(space_1, u1_s1), space=space_1)


@pytest.fixture()
def ing_3_s2(obj_3, space_2, u2_s2):
    return Ingredient.objects.create(unit=obj_3, food=random_food(space_2, u2_s2), space=space_2)


@pytest.fixture()
def sle_1_s1(obj_1, u1_s1, space_1):
    e = ShoppingListEntry.objects.create(unit=obj_1, food=random_food(space_1, u1_s1), created_by=auth.get_user(u1_s1), space=space_1,)
    return e


@pytest.fixture()
def sle_2_s1(obj_2, u1_s1, space_1):
    return ShoppingListEntry.objects.create(unit=obj_2, food=random_food(space_1, u1_s1), created_by=auth.get_user(u1_s1), space=space_1,)


@pytest.fixture()
def sle_3_s2(obj_3, u2_s2, space_2):
    e = ShoppingListEntry.objects.create(unit=obj_3, food=random_food(space_2, u2_s2), created_by=auth.get_user(u2_s2), space=space_2)
    return e


@pytest.mark.parametrize("arg", [
    ['a_u', 403],
    ['g1_s1', 403],
    ['u1_s1', 200],
    ['a1_s1', 200],
])
def test_list_permission(arg, request):
    c = request.getfixturevalue(arg[0])
    assert c.get(reverse(LIST_URL)).status_code == arg[1]


def test_list_space(obj_1, obj_2, u1_s1, u1_s2, space_2):
    assert json.loads(u1_s1.get(reverse(LIST_URL)).content)['count'] == 2
    assert json.loads(u1_s2.get(reverse(LIST_URL)).content)['count'] == 0

    obj_1.space = space_2
    obj_1.save()

    assert json.loads(u1_s1.get(reverse(LIST_URL)).content)['count'] == 1
    assert json.loads(u1_s2.get(reverse(LIST_URL)).content)['count'] == 1


def test_list_filter(obj_1, obj_2, u1_s1):
    r = u1_s1.get(reverse(LIST_URL))
    assert r.status_code == 200
    response = json.loads(r.content)
    assert response['count'] == 2

    response = json.loads(u1_s1.get(f'{reverse(LIST_URL)}?limit=1').content)
    assert response['count'] == 1

    response = json.loads(u1_s1.get(f'{reverse(LIST_URL)}?query=chicken').content)
    assert response['count'] == 0

    response = json.loads(u1_s1.get(f'{reverse(LIST_URL)}?query={obj_1.name[4:]}').content)
    assert response['count'] == 1
    assert response['results'][0]['name'] == obj_1.name


@pytest.mark.parametrize("arg", [
    ['a_u', 403],
    ['g1_s1', 403],
    ['u1_s1', 200],
    ['a1_s1', 200],
    ['g1_s2', 403],
    ['u1_s2', 404],
    ['a1_s2', 404],
])
def test_update(arg, request, obj_1):
    c = request.getfixturevalue(arg[0])
    r = c.patch(
        reverse(
            DETAIL_URL,
            args={obj_1.id}
        ),
        {'name': 'new'},
        content_type='application/json'
    )
    response = json.loads(r.content)
    assert r.status_code == arg[1]
    if r.status_code == 200:
        assert response['name'] == 'new'


@pytest.mark.parametrize("arg", [
    ['a_u', 403],
    ['g1_s1', 403],
    ['u1_s1', 201],
    ['a1_s1', 201],
])
def test_add(arg, request, u1_s2):
    c = request.getfixturevalue(arg[0])
    r = c.post(
        reverse(LIST_URL),
        {'name': 'test'},
        content_type='application/json'
    )
    response = json.loads(r.content)
    assert r.status_code == arg[1]
    if r.status_code == 201:
        assert response['name'] == 'test'
        r = c.get(reverse(DETAIL_URL, args={response['id']}))
        assert r.status_code == 200
        r = u1_s2.get(reverse(DETAIL_URL, args={response['id']}))
        assert r.status_code == 404


def test_add_duplicate(u1_s1, u1_s2, obj_1):
    r = u1_s1.post(
        reverse(LIST_URL),
        {'name': obj_1.name},
        content_type='application/json'
    )
    response = json.loads(r.content)
    assert r.status_code == 201
    assert response['id'] == obj_1.id

    r = u1_s2.post(
        reverse(LIST_URL),
        {'name': obj_1.name},
        content_type='application/json'
    )
    response = json.loads(r.content)
    assert r.status_code == 201
    assert response['id'] != obj_1.id


def test_delete(u1_s1, u1_s2, obj_1):
    r = u1_s2.delete(
        reverse(
            DETAIL_URL,
            args={obj_1.id}
        )
    )
    assert r.status_code == 404

    r = u1_s1.delete(
        reverse(
            DETAIL_URL,
            args={obj_1.id}
        )
    )

    assert r.status_code == 204
    with scopes_disabled():
        assert Food.objects.count() == 0


def test_merge(
    u1_s1,
    obj_1, obj_2, obj_3,
    ing_1_s1, ing_2_s1, ing_3_s2,
    sle_1_s1, sle_2_s1, sle_3_s2,
    space_1
):
    with scopes_disabled():
        assert Unit.objects.filter(space=space_1).count() == 2
        assert obj_1.ingredient_set.count() == 1
        assert obj_2.ingredient_set.count() == 1
        assert obj_3.ingredient_set.count() == 1
        assert obj_1.shoppinglistentry_set.count() == 1
        assert obj_2.shoppinglistentry_set.count() == 1
        assert obj_3.shoppinglistentry_set.count() == 1

    # merge Unit with ingredient/shopping list entry with another Unit, only HTTP put method should work
    url = reverse(MERGE_URL, args=[obj_1.id, obj_2.id])
    r = u1_s1.get(url)
    assert r.status_code == 405
    r = u1_s1.post(url)
    assert r.status_code == 405
    r = u1_s1.delete(url)
    assert r.status_code == 405
    r = u1_s1.put(url)
    assert r.status_code == 200
    with scopes_disabled():
        assert Unit.objects.filter(pk=obj_1.id).count() == 0
        assert obj_2.ingredient_set.count() == 2
        assert obj_3.ingredient_set.count() == 1

        assert obj_2.shoppinglistentry_set.count() == 2
        assert obj_3.shoppinglistentry_set.count() == 1

    # attempt to merge with non-existent parent
    r = u1_s1.put(
        reverse(MERGE_URL, args=[obj_2.id, 9999])
    )
    assert r.status_code == 404

    # attempt to move to wrong space
    r = u1_s1.put(
        reverse(MERGE_URL, args=[obj_2.id, obj_3.id])
    )
    assert r.status_code == 404

    # attempt to merge with self
    r = u1_s1.put(
        reverse(MERGE_URL, args=[obj_2.id, obj_2.id])
    )
    assert r.status_code == 403

    # run diagnostic to find problems - none should be found
    with scopes_disabled():
        assert Food.find_problems() == ([], [], [], [], [])


@pytest.fixture()
def unit_gram(space_1):
    return Unit.objects.get_or_create(name='gram', base_unit='g', space=space_1)[0]


@pytest.fixture()
def unit_kg(space_1):
    return Unit.objects.get_or_create(name='kilogram', base_unit='kg', space=space_1)[0]


@pytest.fixture()
def unit_pcs(space_1):
    return Unit.objects.get_or_create(name='pieces', base_unit='', space=space_1)[0]


@pytest.fixture()
def unit_custom_1(space_1):
    return Unit.objects.get_or_create(name='custom_1', base_unit='', space=space_1)[0]


@pytest.fixture()
def unit_custom_2(space_1):
    return Unit.objects.get_or_create(name='custom_2', base_unit='', space=space_1)[0]


def test_merge_with_base_unit_conversion(u1_s1, space_1, unit_gram, unit_kg):
    with scopes_disabled():
        u1_s1_user = auth.get_user(u1_s1)
        food = Food.objects.create(name='Test Food', space=space_1)
        ingredient = Ingredient.objects.create(
            food=food,
            unit=unit_gram,
            amount=Decimal('1000'),
            space=space_1,
        )
        shopping_entry = ShoppingListEntry.objects.create(
            food=food,
            unit=unit_gram,
            amount=Decimal('500'),
            created_by=u1_s1_user,
            space=space_1,
        )

        UnitConversionHelper._base_units_cache.pop(space_1.id, None)
        caches['default'].delete(CacheHelper(space_1).BASE_UNITS_CACHE_KEY)

    url = reverse(MERGE_URL, args=[unit_gram.id, unit_kg.id])
    r = u1_s1.put(url)
    assert r.status_code == 200

    with scopes_disabled():
        ingredient.refresh_from_db()
        shopping_entry.refresh_from_db()

        assert ingredient.unit == unit_kg
        assert abs(ingredient.amount - Decimal('1')) < Decimal('0.0001')

        assert shopping_entry.unit == unit_kg
        assert abs(shopping_entry.amount - Decimal('0.5')) < Decimal('0.0001')

        assert Unit.objects.filter(pk=unit_gram.id).count() == 0


def test_merge_with_custom_unit_conversion(u1_s1, space_1, unit_custom_1, unit_custom_2):
    with scopes_disabled():
        u1_s1_user = auth.get_user(u1_s1)
        UnitConversion.objects.create(
            base_amount=Decimal('1'),
            base_unit=unit_custom_1,
            converted_amount=Decimal('1000'),
            converted_unit=unit_custom_2,
            space=space_1,
            created_by=u1_s1_user,
        )

        food = Food.objects.create(name='Test Food 2', space=space_1)
        ingredient = Ingredient.objects.create(
            food=food,
            unit=unit_custom_1,
            amount=Decimal('5'),
            space=space_1,
        )
        shopping_entry = ShoppingListEntry.objects.create(
            food=food,
            unit=unit_custom_1,
            amount=Decimal('2.5'),
            created_by=u1_s1_user,
            space=space_1,
        )

    url = reverse(MERGE_URL, args=[unit_custom_1.id, unit_custom_2.id])
    r = u1_s1.put(url)
    assert r.status_code == 200

    with scopes_disabled():
        ingredient.refresh_from_db()
        shopping_entry.refresh_from_db()

        assert ingredient.unit == unit_custom_2
        assert abs(ingredient.amount - Decimal('5000')) < Decimal('0.0001')

        assert shopping_entry.unit == unit_custom_2
        assert abs(shopping_entry.amount - Decimal('2500')) < Decimal('0.0001')


def test_merge_without_conversion(u1_s1, space_1, unit_pcs):
    with scopes_disabled():
        u1_s1_user = auth.get_user(u1_s1)
        unit_pcs_2 = Unit.objects.create(name='pieces_2', base_unit='', space=space_1)
        food = Food.objects.create(name='Test Food 3', space=space_1)
        ingredient = Ingredient.objects.create(
            food=food,
            unit=unit_pcs,
            amount=Decimal('10'),
            space=space_1,
        )
        shopping_entry = ShoppingListEntry.objects.create(
            food=food,
            unit=unit_pcs,
            amount=Decimal('5'),
            created_by=u1_s1_user,
            space=space_1,
        )

    url = reverse(MERGE_URL, args=[unit_pcs.id, unit_pcs_2.id])
    r = u1_s1.put(url)
    assert r.status_code == 200

    with scopes_disabled():
        ingredient.refresh_from_db()
        shopping_entry.refresh_from_db()

        assert ingredient.unit == unit_pcs_2
        assert ingredient.amount == Decimal('10')

        assert shopping_entry.unit == unit_pcs_2
        assert shopping_entry.amount == Decimal('5')


def test_merge_clears_cache(u1_s1, space_1, unit_gram, unit_kg):
    with scopes_disabled():
        cache_helper = CacheHelper(space_1)
        caches['default'].set(cache_helper.BASE_UNITS_CACHE_KEY, ['test_data'], 60)
        UnitConversionHelper._base_units_cache[space_1.id] = ['test_data']

        assert caches['default'].get(cache_helper.BASE_UNITS_CACHE_KEY) is not None
        assert space_1.id in UnitConversionHelper._base_units_cache

    url = reverse(MERGE_URL, args=[unit_gram.id, unit_kg.id])
    r = u1_s1.put(url)
    assert r.status_code == 200

    with scopes_disabled():
        assert caches['default'].get(cache_helper.BASE_UNITS_CACHE_KEY) is None
        assert space_1.id not in UnitConversionHelper._base_units_cache


def test_delete_clears_cache(u1_s1, space_1, unit_gram):
    with scopes_disabled():
        cache_helper = CacheHelper(space_1)
        caches['default'].set(cache_helper.BASE_UNITS_CACHE_KEY, ['test_data'], 60)
        UnitConversionHelper._base_units_cache[space_1.id] = ['test_data']

        assert caches['default'].get(cache_helper.BASE_UNITS_CACHE_KEY) is not None
        assert space_1.id in UnitConversionHelper._base_units_cache

    url = reverse(DETAIL_URL, args=[unit_gram.id])
    r = u1_s1.delete(url)
    assert r.status_code == 204

    with scopes_disabled():
        assert caches['default'].get(cache_helper.BASE_UNITS_CACHE_KEY) is None
        assert space_1.id not in UnitConversionHelper._base_units_cache


def test_convert_from_to_precision():
    result = UnitConversionHelper.convert_from_to('g', 'kg', 1234)
    assert abs(result - Decimal('1.234')) < Decimal('0.0001')

    result = UnitConversionHelper.convert_from_to('kg', 'g', 1)
    assert result == Decimal('1000')

    result = UnitConversionHelper.convert_from_to('kg', 'pound', 2)
    assert abs(result - Decimal('4.40924')) < Decimal('0.00001')


def test_merge_clears_property_type_cache(u1_s1, space_1, unit_gram, unit_kg):
    with scopes_disabled():
        cache_helper = CacheHelper(space_1)
        caches['default'].set(cache_helper.PROPERTY_TYPE_CACHE_KEY, ['test_property_data'], 60)

        assert caches['default'].get(cache_helper.PROPERTY_TYPE_CACHE_KEY) is not None

    url = reverse(MERGE_URL, args=[unit_gram.id, unit_kg.id])
    r = u1_s1.put(url)
    assert r.status_code == 200

    with scopes_disabled():
        assert caches['default'].get(cache_helper.PROPERTY_TYPE_CACHE_KEY) is None


def test_delete_clears_property_type_cache(u1_s1, space_1, unit_gram):
    with scopes_disabled():
        cache_helper = CacheHelper(space_1)
        caches['default'].set(cache_helper.PROPERTY_TYPE_CACHE_KEY, ['test_property_data'], 60)

        assert caches['default'].get(cache_helper.PROPERTY_TYPE_CACHE_KEY) is not None

    url = reverse(DETAIL_URL, args=[unit_gram.id])
    r = u1_s1.delete(url)
    assert r.status_code == 204

    with scopes_disabled():
        assert caches['default'].get(cache_helper.PROPERTY_TYPE_CACHE_KEY) is None


def test_save_clears_all_unit_related_caches(u1_s1, space_1, unit_gram):
    with scopes_disabled():
        cache_helper = CacheHelper(space_1)
        caches['default'].set(cache_helper.BASE_UNITS_CACHE_KEY, ['test_data'], 60)
        caches['default'].set(cache_helper.PROPERTY_TYPE_CACHE_KEY, ['test_property_data'], 60)
        UnitConversionHelper._base_units_cache[space_1.id] = ['test_data']

        assert caches['default'].get(cache_helper.BASE_UNITS_CACHE_KEY) is not None
        assert caches['default'].get(cache_helper.PROPERTY_TYPE_CACHE_KEY) is not None
        assert space_1.id in UnitConversionHelper._base_units_cache

    url = reverse(DETAIL_URL, args=[unit_gram.id])
    r = u1_s1.patch(url, {'name': 'gram_updated'}, content_type='application/json')
    assert r.status_code == 200

    with scopes_disabled():
        assert caches['default'].get(cache_helper.BASE_UNITS_CACHE_KEY) is None
        assert caches['default'].get(cache_helper.PROPERTY_TYPE_CACHE_KEY) is None
        assert space_1.id not in UnitConversionHelper._base_units_cache


def test_merge_backfills_ingredient_amounts(u1_s1, space_1, unit_gram, unit_kg, recipe_1_s1):
    with scopes_disabled():
        food = random_food(space_1, u1_s1)
        step = recipe_1_s1.steps.first()
        ingredient = Ingredient.objects.create(
            food=food,
            unit=unit_gram,
            amount=Decimal('2000'),
            step=step,
            space=space_1
        )

        assert ingredient.unit == unit_gram
        assert ingredient.amount == Decimal('2000')

    url = reverse(MERGE_URL, args=[unit_gram.id, unit_kg.id])
    r = u1_s1.put(url)
    assert r.status_code == 200

    with scopes_disabled():
        ingredient.refresh_from_db()
        assert ingredient.unit == unit_kg
        assert abs(ingredient.amount - Decimal('2')) < Decimal('0.0001')


def test_merge_backfills_shopping_list_entry_amounts(u1_s1, space_1, unit_gram, unit_kg):
    with scopes_disabled():
        food = random_food(space_1, u1_s1)
        user = auth.get_user(u1_s1)
        entry = ShoppingListEntry.objects.create(
            food=food,
            unit=unit_gram,
            amount=Decimal('1500'),
            created_by=user,
            space=space_1
        )

        assert entry.unit == unit_gram
        assert entry.amount == Decimal('1500')

    url = reverse(MERGE_URL, args=[unit_gram.id, unit_kg.id])
    r = u1_s1.put(url)
    assert r.status_code == 200

    with scopes_disabled():
        entry.refresh_from_db()
        assert entry.unit == unit_kg
        assert abs(entry.amount - Decimal('1.5')) < Decimal('0.0001')


def test_cache_helper_clear_unit_related_caches(space_1):
    with scopes_disabled():
        cache_helper = CacheHelper(space_1)
        caches['default'].set(cache_helper.BASE_UNITS_CACHE_KEY, ['test_data'], 60)
        caches['default'].set(cache_helper.PROPERTY_TYPE_CACHE_KEY, ['test_property_data'], 60)
        UnitConversionHelper._base_units_cache[space_1.id] = ['test_data']
        caches['default'].set(f'{cache_helper.DELETE_COLLECTOR_CACHE_PREFIX}PROTECTING_Unit_1', ['test'], 60)
        caches['default'].set(f'{cache_helper.DELETE_COLLECTOR_CACHE_PREFIX}CASCADING_Ingredient_5', ['test'], 60)

        assert caches['default'].get(cache_helper.BASE_UNITS_CACHE_KEY) is not None
        assert caches['default'].get(cache_helper.PROPERTY_TYPE_CACHE_KEY) is not None
        assert space_1.id in UnitConversionHelper._base_units_cache

        cache_helper.clear_unit_related_caches()

        assert caches['default'].get(cache_helper.BASE_UNITS_CACHE_KEY) is None
        assert caches['default'].get(cache_helper.PROPERTY_TYPE_CACHE_KEY) is None
        assert space_1.id not in UnitConversionHelper._base_units_cache


def wait_for_async_task(task_id, timeout=10):
    start_time = time.time()
    while time.time() - start_time < timeout:
        with scopes_disabled():
            task = CacheRefreshTask.objects.filter(pk=task_id).first()
            if task and task.status in [CacheRefreshStatus.COMPLETED.value, CacheRefreshStatus.FAILED.value]:
                return task
        time.sleep(0.1)
    return None


def test_merge_creates_async_refresh_task(u1_s1, space_1, unit_gram, unit_kg):
    with scopes_disabled():
        initial_task_count = CacheRefreshTask.objects.filter(space=space_1).count()

    url = reverse(MERGE_URL, args=[unit_gram.id, unit_kg.id])
    r = u1_s1.put(url)
    assert r.status_code == 200

    with scopes_disabled():
        task = CacheRefreshTask.objects.filter(
            space=space_1,
            task_type=CacheRefreshType.UNIT_MERGE.value
        ).order_by('-created_at').first()

        assert task is not None
        assert task.source_unit_id == unit_gram.id
        assert task.target_unit_id == unit_kg.id
        assert CacheRefreshTask.objects.filter(space=space_1).count() == initial_task_count + 1


def test_merge_backfills_and_async_refreshes(u1_s1, space_1, unit_gram, unit_kg, recipe_1_s1):
    with scopes_disabled():
        user = auth.get_user(u1_s1)
        food = random_food(space_1, u1_s1)
        step = recipe_1_s1.steps.first()
        ingredient = Ingredient.objects.create(
            food=food,
            unit=unit_gram,
            amount=Decimal('3000'),
            step=step,
            space=space_1
        )
        shopping_entry = ShoppingListEntry.objects.create(
            food=food,
            unit=unit_gram,
            amount=Decimal('500'),
            created_by=user,
            space=space_1
        )

        cache_helper = CacheHelper(space_1)
        caches['default'].set(cache_helper.BASE_UNITS_CACHE_KEY, ['old_data'], 60)
        caches['default'].set(cache_helper.PROPERTY_TYPE_CACHE_KEY, ['old_property_data'], 60)
        UnitConversionHelper._base_units_cache[space_1.id] = ['old_data']

    url = reverse(MERGE_URL, args=[unit_gram.id, unit_kg.id])
    r = u1_s1.put(url)
    assert r.status_code == 200

    with scopes_disabled():
        ingredient.refresh_from_db()
        shopping_entry.refresh_from_db()

        assert ingredient.unit == unit_kg
        assert abs(ingredient.amount - Decimal('3')) < Decimal('0.0001')
        assert shopping_entry.unit == unit_kg
        assert abs(shopping_entry.amount - Decimal('0.5')) < Decimal('0.0001')

        task = CacheRefreshTask.objects.filter(
            space=space_1,
            task_type=CacheRefreshType.UNIT_MERGE.value
        ).order_by('-created_at').first()

        assert task is not None
        completed_task = wait_for_async_task(task.id, timeout=15)
        assert completed_task is not None
        assert completed_task.status == CacheRefreshStatus.COMPLETED.value
        assert completed_task.processed_items >= 2
        assert completed_task.batch_count >= 1

        assert caches['default'].get(cache_helper.BASE_UNITS_CACHE_KEY) is None
        assert caches['default'].get(cache_helper.PROPERTY_TYPE_CACHE_KEY) is None
        assert space_1.id not in UnitConversionHelper._base_units_cache


def test_async_refresh_batched_execution(space_1):
    import logging
    with scopes_disabled():
        cache_helper = CacheHelper(space_1)
        test_cache_keys = []
        for i in range(50):
            key = f'{cache_helper.DELETE_COLLECTOR_CACHE_PREFIX}PROTECTING_Unit_{i}'
            caches['default'].set(key, f'data_{i}', 60)
            test_cache_keys.append(key)

        task = CacheRefreshTask.objects.create(
            task_type=CacheRefreshType.UNIT_SAVE.value,
            source_unit_id=1,
            batch_size=10,
            space=space_1,
        )

        cache_patterns = test_cache_keys

        work = RefreshWork(task=task, cache_patterns=cache_patterns)
        AsyncCacheRefresher.process_refresh_work(work, logging.getLogger('test'))

        task.refresh_from_db()
        assert task.status == CacheRefreshStatus.COMPLETED.value
        assert task.total_items >= 50
        assert task.processed_items >= 50
        assert task.batch_count >= 5

        for key in test_cache_keys:
            assert caches['default'].get(key) is None


def test_cache_refresh_task_progress_tracking(space_1):
    with scopes_disabled():
        task = CacheRefreshTask.objects.create(
            task_type=CacheRefreshType.UNIT_SAVE.value,
            source_unit_id=1,
            total_items=100,
            batch_size=20,
            space=space_1,
        )

        assert task.status == CacheRefreshStatus.PENDING.value
        assert task.processed_items == 0
        assert task.batch_count == 0
        assert task.started_at is None
        assert task.completed_at is None

        task.mark_running()
        task.refresh_from_db()
        assert task.status == CacheRefreshStatus.RUNNING.value
        assert task.started_at is not None

        task.update_progress(processed_batch_count=20)
        task.refresh_from_db()
        assert task.processed_items == 20
        assert task.batch_count == 1

        task.update_progress(processed_batch_count=20)
        task.refresh_from_db()
        assert task.processed_items == 40
        assert task.batch_count == 2

        task.mark_completed('test completed')
        task.refresh_from_db()
        assert task.status == CacheRefreshStatus.COMPLETED.value
        assert task.completed_at is not None
        assert task.message == 'test completed'


def test_cache_refresh_task_error_handling(space_1):
    with scopes_disabled():
        task = CacheRefreshTask.objects.create(
            task_type=CacheRefreshType.UNIT_SAVE.value,
            source_unit_id=1,
            space=space_1,
        )

        task.mark_running()
        task.refresh_from_db()
        assert task.status == CacheRefreshStatus.RUNNING.value

        task.mark_failed('test error')
        task.refresh_from_db()
        assert task.status == CacheRefreshStatus.FAILED.value
        assert task.error_message == 'test error'
        assert task.completed_at is not None


def test_async_refresh_with_small_batch_size(space_1):
    import logging
    with scopes_disabled():
        cache_helper = CacheHelper(space_1)
        test_cache_keys = []
        for i in range(25):
            key = f'{cache_helper.DELETE_COLLECTOR_CACHE_PREFIX}CASCADING_Ingredient_{i}'
            caches['default'].set(key, f'data_{i}', 60)
            test_cache_keys.append(key)

        task = CacheRefreshTask.objects.create(
            task_type=CacheRefreshType.UNIT_DELETE.value,
            source_unit_id=1,
            batch_size=5,
            space=space_1,
        )

        cache_patterns = test_cache_keys

        work = RefreshWork(task=task, cache_patterns=cache_patterns)
        AsyncCacheRefresher.process_refresh_work(work, logging.getLogger('test'))

        task.refresh_from_db()
        assert task.status == CacheRefreshStatus.COMPLETED.value
        assert task.total_items >= 25
        assert task.batch_count == 5

        for key in test_cache_keys:
            assert caches['default'].get(key) is None


def test_unit_save_triggers_async_refresh(u1_s1, space_1, unit_gram):
    with scopes_disabled():
        initial_task_count = CacheRefreshTask.objects.filter(space=space_1).count()

    url = reverse(DETAIL_URL, args=[unit_gram.id])
    r = u1_s1.patch(url, {'name': 'gram_updated_v2'}, content_type='application/json')
    assert r.status_code == 200

    with scopes_disabled():
        task = CacheRefreshTask.objects.filter(
            space=space_1,
            task_type=CacheRefreshType.UNIT_SAVE.value
        ).order_by('-created_at').first()

        assert task is not None
        assert CacheRefreshTask.objects.filter(space=space_1).count() > initial_task_count

        completed_task = wait_for_async_task(task.id, timeout=15)
        assert completed_task is not None
        assert completed_task.status == CacheRefreshStatus.COMPLETED.value


def test_cache_refresh_task_resume_from_checkpoint(space_1):
    import logging
    with scopes_disabled():
        cache_helper = CacheHelper(space_1)
        test_cache_keys = []
        for i in range(30):
            key = f'{cache_helper.DELETE_COLLECTOR_CACHE_PREFIX}RESUME_TEST_{i}'
            caches['default'].set(key, f'data_{i}', 60)
            test_cache_keys.append(key)

        task = CacheRefreshTask.objects.create(
            task_type=CacheRefreshType.UNIT_MERGE.value,
            source_unit_id=1,
            target_unit_id=2,
            batch_size=10,
            space=space_1,
            cache_patterns=test_cache_keys,
        )

        cache_backend = caches['default']
        for i in range(12):
            cache_backend.delete(test_cache_keys[i])

        task.total_items = 30
        task.processed_items = 12
        task.batch_count = 2
        task.last_processed_index = 12
        task.status = CacheRefreshStatus.FAILED.value
        task.retry_count = 1
        task.save()

        work = RefreshWork(task=task, cache_patterns=test_cache_keys)
        AsyncCacheRefresher.process_refresh_work(work, logging.getLogger('test'))

        task.refresh_from_db()
        assert task.status == CacheRefreshStatus.COMPLETED.value
        assert task.last_processed_index == 30

        for key in test_cache_keys:
            assert caches['default'].get(key) is None


def test_cache_refresh_task_resume_from_mid_batch(space_1):
    import logging
    with scopes_disabled():
        cache_helper = CacheHelper(space_1)
        test_cache_keys = []
        for i in range(50):
            key = f'{cache_helper.DELETE_COLLECTOR_CACHE_PREFIX}MID_BATCH_{i}'
            caches['default'].set(key, f'data_{i}', 60)
            test_cache_keys.append(key)

        task = CacheRefreshTask.objects.create(
            task_type=CacheRefreshType.UNIT_DELETE.value,
            source_unit_id=5,
            batch_size=10,
            space=space_1,
            cache_patterns=test_cache_keys,
        )

        cache_backend = caches['default']
        checkpoint = 35
        for i in range(checkpoint):
            cache_backend.delete(test_cache_keys[i])

        task.total_items = 50
        task.processed_items = checkpoint
        task.batch_count = 3
        task.last_processed_index = checkpoint
        task.status = CacheRefreshStatus.RETRYING.value
        task.retry_count = 1
        task.save()

        work = RefreshWork(task=task, cache_patterns=test_cache_keys)
        AsyncCacheRefresher.process_refresh_work(work, logging.getLogger('test'))

        task.refresh_from_db()
        assert task.status == CacheRefreshStatus.COMPLETED.value
        assert task.total_items == 50
        assert task.last_processed_index == 50

        for key in test_cache_keys:
            assert caches['default'].get(key) is None


def test_cache_refresh_task_retry_method(space_1):
    with scopes_disabled():
        task = CacheRefreshTask.objects.create(
            task_type=CacheRefreshType.UNIT_SAVE.value,
            source_unit_id=1,
            max_retries=3,
            space=space_1,
        )

        assert task.can_retry() is True
        assert task.retry_count == 0

        task.mark_retrying()
        task.refresh_from_db()
        assert task.retry_count == 1
        assert task.status == CacheRefreshStatus.RETRYING.value
        assert task.can_retry() is True

        task.mark_retrying()
        task.mark_retrying()
        task.refresh_from_db()
        assert task.retry_count == 3
        assert task.can_retry() is False

        task.mark_failed('final failure')
        task.refresh_from_db()
        assert task.status == CacheRefreshStatus.FAILED.value
        assert task.error_message == 'final failure'


def test_cache_refresh_task_update_progress_with_index(space_1):
    with scopes_disabled():
        task = CacheRefreshTask.objects.create(
            task_type=CacheRefreshType.UNIT_MERGE.value,
            source_unit_id=10,
            target_unit_id=20,
            total_items=100,
            batch_size=25,
            space=space_1,
        )

        task.mark_running()

        task.update_progress(processed_batch_count=25, processed_index=25)
        task.refresh_from_db()
        assert task.processed_items == 25
        assert task.last_processed_index == 25
        assert task.batch_count == 1

        task.update_progress(processed_batch_count=25, processed_index=50)
        task.refresh_from_db()
        assert task.processed_items == 50
        assert task.last_processed_index == 50
        assert task.batch_count == 2

        task.mark_completed('done')
        task.refresh_from_db()
        assert task.last_processed_index == 100


def test_submit_retry_task_success(space_1):
    with scopes_disabled():
        cache_helper = CacheHelper(space_1)
        test_cache_keys = []
        for i in range(5):
            key = f'{cache_helper.DELETE_COLLECTOR_CACHE_PREFIX}RETRY_SUBMIT_{i}'
            caches['default'].set(key, f'data_{i}', 60)
            test_cache_keys.append(key)

        task = CacheRefreshTask.objects.create(
            task_type=CacheRefreshType.UNIT_DELETE.value,
            source_unit_id=1,
            batch_size=10,
            max_retries=2,
            retry_count=1,
            space=space_1,
            cache_patterns=test_cache_keys,
            status=CacheRefreshStatus.FAILED.value,
            error_message='simulated failure',
        )

        assert task.can_retry() is True

        retried_task = AsyncCacheRefresher.submit_retry_task(task.id)
        assert retried_task is not None
        assert retried_task.retry_count == 2
        assert retried_task.status == CacheRefreshStatus.RETRYING.value


def test_submit_retry_task_exceeds_max_retries(space_1):
    with scopes_disabled():
        task = CacheRefreshTask.objects.create(
            task_type=CacheRefreshType.UNIT_MERGE.value,
            source_unit_id=1,
            target_unit_id=2,
            max_retries=3,
            retry_count=3,
            space=space_1,
            status=CacheRefreshStatus.FAILED.value,
            error_message='too many retries',
        )

        assert task.can_retry() is False

        retried_task = AsyncCacheRefresher.submit_retry_task(task.id)
        assert retried_task is None


def test_submit_retry_task_not_exist(space_1):
    retried_task = AsyncCacheRefresher.submit_retry_task(999999)
    assert retried_task is None


def test_cache_refresh_task_patterns_saved_on_creation(space_1):
    with scopes_disabled():
        cache_helper = CacheHelper(space_1)
        task = AsyncCacheRefresher.submit_unit_save_refresh(
            space=space_1,
            unit_id=42,
        )

        task.refresh_from_db()
        assert len(task.cache_patterns) > 0
        assert cache_helper.BASE_UNITS_CACHE_KEY in task.cache_patterns
        assert cache_helper.PROPERTY_TYPE_CACHE_KEY in task.cache_patterns
        assert any(p.startswith(cache_helper.DELETE_COLLECTOR_CACHE_PREFIX) for p in task.cache_patterns)


def test_process_refresh_work_simulated_failure_then_resume(space_1):
    import logging
    logger = logging.getLogger('test_failure')

    with scopes_disabled():
        cache_helper = CacheHelper(space_1)
        test_cache_keys = []
        total_keys = 20
        for i in range(total_keys):
            key = f'{cache_helper.DELETE_COLLECTOR_CACHE_PREFIX}FAIL_TEST_{i}'
            caches['default'].set(key, f'data_{i}', 60)
            test_cache_keys.append(key)

        task = CacheRefreshTask.objects.create(
            task_type=CacheRefreshType.UNIT_MERGE.value,
            source_unit_id=1,
            target_unit_id=3,
            batch_size=7,
            space=space_1,
            cache_patterns=test_cache_keys,
        )

        checkpoint = 7
        cache_backend = caches['default']
        for i in range(checkpoint):
            cache_backend.delete(test_cache_keys[i])

        task.total_items = total_keys
        task.processed_items = checkpoint
        task.batch_count = 1
        task.last_processed_index = checkpoint
        task.status = CacheRefreshStatus.FAILED.value
        task.error_message = 'Simulated network error'
        task.retry_count = 1
        task.max_retries = 3
        task.save()

        remaining_before = sum(1 for k in test_cache_keys if caches['default'].get(k) is not None)
        assert remaining_before == total_keys - checkpoint

        work = RefreshWork(task=task, cache_patterns=test_cache_keys)
        AsyncCacheRefresher.process_refresh_work(work, logger)

        task.refresh_from_db()
        assert task.status == CacheRefreshStatus.COMPLETED.value
        assert task.last_processed_index == total_keys
        assert task.processed_items >= total_keys

        remaining_after = sum(1 for k in test_cache_keys if caches['default'].get(k) is not None)
        assert remaining_after == 0

        for key in test_cache_keys:
            assert caches['default'].get(key) is None
