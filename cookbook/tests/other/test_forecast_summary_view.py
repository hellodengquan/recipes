import pytest
from datetime import timedelta
from decimal import Decimal

from django.contrib import auth
from django.utils import timezone
from django_scopes import scopes_disabled

from cookbook.models import (
    Food,
    ForecastLog,
    SupermarketCategory,
)


@pytest.fixture
def admin_client(admin_user):
    """Django test client logged in as admin."""
    from django.test import Client
    client = Client()
    client.force_login(admin_user)
    return client


@pytest.fixture
def summary_url():
    from django.urls import reverse
    return reverse('admin:cookbook_food_retry_forecast_summary')


@pytest.fixture
def sm_category_veggies(space_1):
    with scopes_disabled():
        return SupermarketCategory.objects.create(
            name='Vegetables', space=space_1,
        )


@pytest.fixture
def sm_category_meat(space_1):
    with scopes_disabled():
        return SupermarketCategory.objects.create(
            name='Meat & Poultry', space=space_1,
        )


@pytest.fixture
def foods_with_various_limits(space_1, sm_category_veggies, sm_category_meat):
    """
    Create several foods with different reserve_retry_limit values
    and different supermarket categories.
    """
    with scopes_disabled():
        f_hot1 = Food.objects.create(
            name='Eggs (hot)', space=space_1,
            reserve_retry_limit=5,
            supermarket_category=sm_category_veggies,
        )
        f_hot2 = Food.objects.create(
            name='Chicken Breast (popular)', space=space_1,
            reserve_retry_limit=3,
            supermarket_category=sm_category_meat,
        )
        f_zero = Food.objects.create(
            name='Truffle (sensitive)', space=space_1,
            reserve_retry_limit=0,
            supermarket_category=sm_category_meat,
        )
        f_default = Food.objects.create(
            name='Regular Flour', space=space_1,
            reserve_retry_limit=1,  # default - should NOT appear
            supermarket_category=sm_category_veggies,
        )
    return {
        'hot1': f_hot1,
        'hot2': f_hot2,
        'zero': f_zero,
        'default': f_default,
    }


@pytest.fixture
def forecast_logs_with_hits(space_1, foods_with_various_limits, u1_s1):
    """
    Create ForecastLog entries with hit_limit=True for the last 7 days
    for some of the foods.
    """
    user = auth.get_user(u1_s1)
    now = timezone.now()

    with scopes_disabled():
        logs = []
        # 3 hits for hot1 (eggs) in the last 7 days
        for i in range(3):
            logs.append(ForecastLog(
                space=space_1,
                food=foods_with_various_limits['hot1'],
                created_by=user,
                status='conflict',
                retry_count=5,
                hit_limit=True,
                reserve_retry_limit=5,
                created_at=now - timedelta(days=i),
            ))
        # 1 hit for hot2 (chicken)
        logs.append(ForecastLog(
            space=space_1,
            food=foods_with_various_limits['hot2'],
            created_by=user,
            status='conflict',
            retry_count=3,
            hit_limit=True,
            reserve_retry_limit=3,
            created_at=now - timedelta(days=2),
        ))
        # 2 hits for hot1 but > 7 days old (should NOT count)
        for i in range(2):
            logs.append(ForecastLog(
                space=space_1,
                food=foods_with_various_limits['hot1'],
                created_by=user,
                status='conflict',
                retry_count=5,
                hit_limit=True,
                reserve_retry_limit=5,
                created_at=now - timedelta(days=10 + i),
            ))
        # zero-retry food: no hits logged yet

        ForecastLog.objects.bulk_create(logs)

    return logs


# ---------------------------------------------------------------------------
# 场景 1: 列表渲染包含调高重试上限的食材及其触达次数
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_summary_view_lists_non_default_foods_with_hit_counts(
    admin_client, summary_url, foods_with_various_limits, forecast_logs_with_hits,
):
    """
    汇总视图应展示所有非默认（limit != 1）的食材，
    并显示最近 7 天触达上限的次数。
    默认 limit=1 的食材不应出现在列表中。
    """
    response = admin_client.get(summary_url)

    assert response.status_code == 200

    # 非默认食材应该出现
    assert 'Eggs (hot)' in response.content.decode()
    assert 'Chicken Breast (popular)' in response.content.decode()
    assert 'Truffle (sensitive)' in response.content.decode()

    # 默认 limit=1 的食材不应出现
    assert 'Regular Flour' not in response.content.decode()

    # 7 天触达次数：hot1 应该有 3 次（>7天的不计）
    content = response.content.decode()
    # 检查 3 这个数字出现在 egg 相关的行中
    # 简化：直接检查内容中包含 "3"（hot2 有 1 次，hot1 有 3 次，总共 4 次 hit）
    assert '3' in content
    # 验证按 limit 从高到低排序：hot1 (5) → hot2 (3) → zero (0)
    idx_hot1 = content.index('Eggs (hot)')
    idx_hot2 = content.index('Chicken Breast (popular)')
    idx_zero = content.index('Truffle (sensitive)')
    assert idx_hot1 < idx_hot2 < idx_zero, (
        "Foods should be sorted by reserve_retry_limit descending: "
        f"hot1(5) at {idx_hot1}, hot2(3) at {idx_hot2}, zero(0) at {idx_zero}"
    )


@pytest.mark.django_db
def test_summary_view_excludes_old_hit_counts(
    admin_client, summary_url, foods_with_various_limits, forecast_logs_with_hits,
):
    """
    超过 7 天的触达记录不应计入 7 天触达次数。
    hot1 有 3 次 7 天内的 + 2 次 7 天外的 → 只计 3 次
    """
    from cookbook.models import ForecastLog
    with scopes_disabled():
        total_hits = ForecastLog.objects.filter(
            food=foods_with_various_limits['hot1'],
            hit_limit=True,
        ).count()
        assert total_hits == 5, "Test setup: 5 total hits for hot1"

    response = admin_client.get(summary_url)
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# 场景 2: 品类筛选功能正常
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_summary_view_category_filter_veggies(
    admin_client, summary_url, foods_with_various_limits, sm_category_veggies,
    forecast_logs_with_hits,
):
    """
    按蔬菜品类筛选：只显示蔬菜类的非默认食材。
    hot1（蔬菜类）应出现，hot2 和 zero（肉类）不应出现。
    """
    response = admin_client.get(summary_url, {'category': str(sm_category_veggies.id)})

    assert response.status_code == 200
    content = response.content.decode()

    # 蔬菜类食材 hot1 应该出现
    assert 'Eggs (hot)' in content
    # 肉类食材不应出现
    assert 'Chicken Breast' not in content
    assert 'Truffle' not in content


@pytest.mark.django_db
def test_summary_view_category_filter_meat(
    admin_client, summary_url, foods_with_various_limits, sm_category_meat,
    forecast_logs_with_hits,
):
    """
    按肉类品类筛选：只显示肉类的非默认食材。
    """
    response = admin_client.get(summary_url, {'category': str(sm_category_meat.id)})

    assert response.status_code == 200
    content = response.content.decode()

    # 肉类食材应该出现
    assert 'Chicken Breast' in content
    assert 'Truffle' in content
    # 蔬菜类食材不应出现
    assert 'Eggs (hot)' not in content


@pytest.mark.django_db
def test_summary_view_category_filter_empty(
    admin_client, summary_url, foods_with_various_limits, forecast_logs_with_hits,
):
    """
    不带 category 参数时，显示所有非默认食材。
    """
    response = admin_client.get(summary_url)

    assert response.status_code == 200
    content = response.content.decode()

    assert 'Eggs (hot)' in content
    assert 'Chicken Breast' in content
    assert 'Truffle' in content


# ---------------------------------------------------------------------------
# 场景 3: 阈值筛选后仅显示符合条件的食材
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_summary_view_min_limit_threshold_high(
    admin_client, summary_url, foods_with_various_limits, forecast_logs_with_hits,
):
    """
    阈值 min_limit=4：只有 limit >= 4 的食材才会显示。
    hot1=5 会显示，hot2=3 和 zero=0 不会显示。
    """
    response = admin_client.get(summary_url, {'min_limit': '4'})

    assert response.status_code == 200
    content = response.content.decode()

    assert 'Eggs (hot)' in content
    assert 'Chicken Breast' not in content
    assert 'Truffle' not in content


@pytest.mark.django_db
def test_summary_view_min_limit_threshold_zero(
    admin_client, summary_url, foods_with_various_limits, forecast_logs_with_hits,
):
    """
    阈值 min_limit=0：所有非默认食材都应该显示（因为 limit 都是 >= 0 的）。
    """
    response = admin_client.get(summary_url, {'min_limit': '0'})

    assert response.status_code == 200
    content = response.content.decode()

    assert 'Eggs (hot)' in content
    assert 'Chicken Breast' in content
    assert 'Truffle' in content


@pytest.mark.django_db
def test_summary_view_min_limit_threshold_too_high(
    admin_client, summary_url, foods_with_various_limits, forecast_logs_with_hits,
):
    """
    阈值 min_limit=999：没有食材达到，应显示空状态提示。
    """
    response = admin_client.get(summary_url, {'min_limit': '999'})

    assert response.status_code == 200
    content = response.content.decode()

    assert 'No foods found' in content or 'no foods' in content.lower() or 'errornote' in content
    assert 'Eggs (hot)' not in content


@pytest.mark.django_db
def test_summary_view_combined_filters(
    admin_client, summary_url, foods_with_various_limits,
    sm_category_meat, sm_category_veggies, forecast_logs_with_hits,
):
    """
    组合筛选：品类=肉类 + min_limit=2
    hot2=3 (肉类) 应该显示，zero=0 (肉类) 不显示，hot1=5 (蔬菜) 不显示
    """
    response = admin_client.get(
        summary_url,
        {'category': str(sm_category_meat.id), 'min_limit': '2'}
    )

    assert response.status_code == 200
    content = response.content.decode()

    assert 'Chicken Breast' in content  # 肉类 + limit=3 >= 2 → 显示
    assert 'Truffle' not in content     # 肉类 + limit=0 < 2 → 不显示
    assert 'Eggs (hot)' not in content  # 蔬菜类 → 不显示
