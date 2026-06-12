import pytest
from datetime import timedelta
from decimal import Decimal

from django.contrib import auth
from django.db.models import Sum
from django.utils import timezone
from django_scopes import scopes_disabled

from cookbook.helper.meal_plan_forecast_helper import (
    FORECAST_LOG_ARCHIVE_DAYS,
    aggregate_forecast_logs_for_date,
    archive_forecast_logs,
    get_forecast_log_archive_cutoff,
    get_hit_limit_count_for_foods,
)
from cookbook.models import (
    Food,
    ForecastDailySummary,
    ForecastLog,
    SupermarketCategory,
)


@pytest.fixture
def log_space(space_1):
    return space_1


@pytest.fixture
def log_user(u1_s1):
    return auth.get_user(u1_s1)


@pytest.fixture
def log_food_factory(log_space):
    def _make(name, reserve_retry_limit=1, supermarket_category=None):
        with scopes_disabled():
            return Food.objects.create(
                name=name, space=log_space,
                reserve_retry_limit=reserve_retry_limit,
                supermarket_category=supermarket_category,
            )
    return _make


@pytest.fixture
def sm_category_veggies(log_space):
    with scopes_disabled():
        return SupermarketCategory.objects.create(
            name='TestVeg', space=log_space,
        )


@pytest.fixture
def forecast_logs_across_dates(log_space, log_user, log_food_factory, sm_category_veggies):
    """
    Create ForecastLog entries across a wide date range for testing archive and cross-source queries.
    """
    from cookbook.models import ForecastLog

    now = timezone.now()

    with scopes_disabled():
        f_hot = log_food_factory('HotFood', reserve_retry_limit=5, supermarket_category=sm_category_veggies)
        f_normal = log_food_factory('NormalFood', reserve_retry_limit=3)

        logs = []

        def make_logs(days_ago, food, count, hit_count):
            created_at = now - timedelta(days=days_ago)
            for i in range(count):
                logs.append(ForecastLog(
                    space=log_space,
                    food=food,
                    created_by=log_user,
                    status='conflict',
                    retry_count=food.reserve_retry_limit,
                    hit_limit=(i < hit_count),
                    reserve_retry_limit=food.reserve_retry_limit,
                    created_at=created_at,
                ))
                if not (i < hit_count):
                    logs[-1].note = f"Hit limit"

        # D-20: hot 2 logs, 1 hit
        make_logs(20, f_hot, 2, 1)
        # D-15: hot 3 logs, 2 hits
        make_logs(15, f_hot, 3, 2)
        # D-10: hot 4 logs, 1 hit
        make_logs(10, f_hot, 4, 1)
        # D-8: normal 6 logs, 4 hits
        make_logs(8, f_normal, 6, 4)
        # D-3: hot 2 logs, 1 hit; normal 1 log, 0 hits
        make_logs(3, f_hot, 2, 1)
        make_logs(3, f_normal, 1, 0)
        # D-1: hot 3 logs, 2 hits; normal 2 logs, 1 hit
        make_logs(1, f_hot, 3, 2)
        make_logs(1, f_normal, 2, 1)

        ForecastLog.objects.bulk_create(logs)

    return {
        'f_hot': f_hot,
        'f_normal': f_normal,
    }


# ---------------------------------------------------------------------------
# 场景 1: 归档执行后明细数据消失但日聚合数据可读取
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_archive_deletes_old_logs_and_creates_summaries(
    log_space, forecast_logs_across_dates,
):
    """
    Archiving should delete old logs, create summaries, and keep recent logs intact.
    """
    from cookbook.models import ForecastLog, ForecastDailySummary

    f_hot = forecast_logs_across_dates['f_hot']
    f_normal = forecast_logs_across_dates['f_normal']

    with scopes_disabled():
        all_logs_before = ForecastLog.objects.count()
        assert all_logs_before >= 23

    with scopes_disabled():
        result = archive_forecast_logs()

    assert len(result['days_processed']) >= 4

    with scopes_disabled():
        recent_logs = ForecastLog.objects.filter(
            created_at__gte=timezone.now() - timedelta(days=FORECAST_LOG_ARCHIVE_DAYS)
        ).count()
        old_logs = ForecastLog.objects.filter(
            created_at__lt=timezone.now() - timedelta(days=FORECAST_LOG_ARCHIVE_DAYS)
        ).count()

    assert old_logs == 0, f"All old logs should be deleted, got {old_logs}"
    assert recent_logs > 0, "Recent logs should remain"

    with scopes_disabled():
        summaries = ForecastDailySummary.objects.count()
        assert summaries >= 4

        hot_summary = ForecastDailySummary.objects.filter(food=f_hot).aggregate(
            total=Sum('hit_limit_count')
        )['total'] or 0
        normal_summary = ForecastDailySummary.objects.filter(food=f_normal).aggregate(
            total=Sum('hit_limit_count')
        )['total'] or 0

    assert hot_summary == 4, f"Expected 4 archived hits for hot food, got {hot_summary}"
    assert normal_summary == 4, f"Expected 4 archived hits for normal food, got {normal_summary}"


@pytest.mark.django_db(transaction=True)
def test_get_hit_limit_count_reads_from_summary_after_archive(
    log_space, forecast_logs_across_dates,
):
    """
    After archiving, get_hit_limit_count_for_foods should combine summary + detail data.
    """
    from cookbook.models import ForecastLog, ForecastDailySummary

    f_hot = forecast_logs_across_dates['f_hot']
    f_normal = forecast_logs_across_dates['f_normal']

    with scopes_disabled():
        archive_forecast_logs()

    now = timezone.now()

    with scopes_disabled():
        counts = get_hit_limit_count_for_foods(
            food_ids=[f_hot.id, f_normal.id],
            from_date=(now - timedelta(days=20)).date(),
            to_date=now.date(),
        )

    assert counts.get(f_hot.id, 0) == 7
    assert counts.get(f_normal.id, 0) == 5


# ---------------------------------------------------------------------------
# 场景 2: 汇总视图横跨明细和聚合两段查询
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_summary_view_across_detail_and_summary(
    admin_client, log_space, forecast_logs_across_dates,
):
    """
    Admin summary view with date range spanning both zones should show combined counts.
    """
    from django.urls import reverse

    url = reverse('admin:cookbook_food_retry_forecast_summary')
    f_hot = forecast_logs_across_dates['f_hot']
    f_normal = forecast_logs_across_dates['f_normal']

    now = timezone.now()

    with scopes_disabled():
        archive_forecast_logs()

    from_date = (now - timedelta(days=20)).date()
    to_date = now.date()

    response = admin_client.get(
        url, {
            'from_date': from_date.strftime('%Y-%m-%d'),
            'to_date': to_date.strftime('%Y-%m-%d'),
        }
    )

    assert response.status_code == 200
    content = response.content.decode()

    assert 'HotFood' in content
    assert 'NormalFood' in content
    assert '7' in content
    assert '5' in content


@pytest.mark.django_db(transaction=True)
def test_summary_view_detail_zone_only(
    admin_client, log_space, forecast_logs_across_dates,
):
    """
    Query only the last 7 days (detail zone) should return correct hit counts from ForecastLog.
    """
    from django.urls import reverse

    url = reverse('admin:cookbook_food_retry_forecast_summary')
    f_hot = forecast_logs_across_dates['f_hot']

    now = timezone.now()

    with scopes_disabled():
        archive_forecast_logs()

    from_date = (now - timedelta(days=3)).date()
    to_date = now.date()

    response = admin_client.get(
        url, {
            'from_date': from_date.strftime('%Y-%m-%d'),
            'to_date': to_date.strftime('%Y-%m-%d'),
        }
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert '3' in content


@pytest.mark.django_db(transaction=True)
def test_summary_view_summary_zone_only(
    admin_client, log_space, forecast_logs_across_dates,
):
    """
    Query only the archived zone should return hit counts from ForecastDailySummary.
    """
    from django.urls import reverse

    url = reverse('admin:cookbook_food_retry_forecast_summary')
    f_hot = forecast_logs_across_dates['f_hot']

    now = timezone.now()

    with scopes_disabled():
        archive_forecast_logs()

    from_date = (now - timedelta(days=20)).date()
    to_date = (now - timedelta(days=8)).date()

    response = admin_client.get(
        url, {
            'from_date': from_date.strftime('%Y-%m-%d'),
            'to_date': to_date.strftime('%Y-%m-%d'),
        }
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert '4' in content


# ---------------------------------------------------------------------------
# 场景 3: 未到归档窗口的明细数据不被误删
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_recent_logs_not_deleted(
    log_space, forecast_logs_across_dates,
):
    """
    ForecastLog entries within archive window should NOT be deleted.
    """
    from cookbook.models import ForecastLog

    f_hot = forecast_logs_across_dates['f_hot']
    f_normal = forecast_logs_across_dates['f_normal']

    now = timezone.now()
    cutoff = get_forecast_log_archive_cutoff()

    with scopes_disabled():
        recent_count_before = ForecastLog.objects.filter(
            created_at__gte=cutoff
        ).count()

    with scopes_disabled():
        archive_forecast_logs()

    with scopes_disabled():
        recent_count_after = ForecastLog.objects.filter(
            created_at__gte=cutoff
        ).count()

    assert recent_count_before == recent_count_after, (
        f"Recent logs should not be deleted: {recent_count_before} before vs {recent_count_after} after"
    )
    assert recent_count_after > 0


@pytest.mark.django_db(transaction=True)
def test_dry_run_does_not_delete_anything(
    log_space, forecast_logs_across_dates,
):
    """
    With dry_run=True, no data should be created, deleted, or modified.
    """
    from cookbook.models import ForecastLog, ForecastDailySummary

    f_hot = forecast_logs_across_dates['f_hot']

    with scopes_disabled():
        count_before = ForecastLog.objects.count()
        summary_before = ForecastDailySummary.objects.count()

    with scopes_disabled():
        result = archive_forecast_logs(dry_run=True)

    with scopes_disabled():
        count_after = ForecastLog.objects.count()
        summary_after = ForecastDailySummary.objects.count()

    assert count_before == count_after
    assert summary_before == summary_after
    assert result['total_logs_deleted'] > 0
    assert len(result['days_processed']) > 0


@pytest.mark.django_db(transaction=True)
def test_logs_exactly_at_archive_boundary(
    log_space, log_user, log_food_factory,
):
    """
    Logs exactly 7 days old should be archived.
    Logs 6 days old should remain as detail.
    """
    from cookbook.models import ForecastLog, ForecastDailySummary

    f_hot = log_food_factory('BoundaryFood', reserve_retry_limit=5)

    now = timezone.now()

    with scopes_disabled():
        old_log = ForecastLog.objects.create(
            space=log_space,
            food=f_hot,
            created_by=log_user,
            status='conflict',
            retry_count=5,
            hit_limit=True,
            reserve_retry_limit=5,
            created_at=now - timedelta(days=7),
        )
        new_log = ForecastLog.objects.create(
            space=log_space,
            food=f_hot,
            created_by=log_user,
            status='conflict',
            retry_count=3,
            hit_limit=True,
            reserve_retry_limit=5,
            created_at=now - timedelta(days=6),
        )

    with scopes_disabled():
        archive_forecast_logs()

    with scopes_disabled():
        assert not ForecastLog.objects.filter(pk=old_log.pk).exists()
        assert ForecastLog.objects.filter(pk=new_log.pk).exists()

    with scopes_disabled():
        summaries = ForecastDailySummary.objects.filter(food=f_hot).count()
        assert summaries == 1


# ---------------------------------------------------------------------------
# 辅助: 检查聚合数据的正确性
# ---------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_aggregate_values_correctly(
    log_space, log_user, log_food_factory,
):
    """
    Verify that aggregate_forecast_logs_for_date correctly computes all aggregated fields.
    """
    from cookbook.models import ForecastDailySummary, ForecastLog

    f1 = log_food_factory('AggTest', reserve_retry_limit=5)

    now = timezone.now()
    test_date = (now - timedelta(days=10)).date()

    with scopes_disabled():
        for i in range(5):
            ForecastLog.objects.create(
                space=log_space,
                food=f1,
                created_by=log_user,
                status='success' if i < 3 else 'conflict',
                retry_count=i,
                hit_limit=(i == 4),
                reserve_retry_limit=5,
                created_at=now - timedelta(days=10),
            )

    with scopes_disabled():
        created, updated = aggregate_forecast_logs_for_date(test_date)

    assert created == 1
    assert updated == 0

    with scopes_disabled():
        summary = ForecastDailySummary.objects.get(food=f1, date=test_date)

    assert summary.total_attempts == 5
    assert summary.success_count == 3
    assert summary.conflict_count == 2
    assert summary.hit_limit_count == 1
    assert summary.total_retry_count == 10
    assert summary.max_retry_count == 4
    assert float(summary.avg_retry_limit) == 5.0
