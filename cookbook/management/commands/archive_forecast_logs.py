from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_scopes import scopes_disabled

from cookbook.helper.meal_plan_forecast_helper import (
    FORECAST_LOG_ARCHIVE_DAYS,
    FORECAST_LOG_ARCHIVE_HOUR,
    archive_forecast_logs,
)


class Command(BaseCommand):
    help = _(
        f'Archive ForecastLog entries older than {FORECAST_LOG_ARCHIVE_DAYS} days '
        'into ForecastDailySummary and delete the original entries. '
        'Intended to be run daily via cron (at hour 02:00 recommended).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '-d', '--dry-run',
            action='store_true',
            help='Do not modify database, just print what would happen',
        )
        parser.add_argument(
            '--as-of',
            type=str,
            help='Reference date for cutoff (ISO format YYYY-MM-DD). Defaults to now.',
        )
        parser.add_argument(
            '--space-ids',
            type=str,
            help='Comma-separated list of space IDs to restrict operation to',
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        as_of_str = options.get('as_of')
        space_ids_str = options.get('space_ids')

        as_of = timezone.now()
        if as_of_str:
            try:
                as_of = timezone.make_aware(
                    timezone.datetime.strptime(as_of_str, '%Y-%m-%d')
                )
            except ValueError:
                self.stderr.write(f'Invalid --as-of format: {as_of_str}. Use YYYY-MM-DD.')
                return

        space_ids = None
        if space_ids_str:
            try:
                space_ids = [int(s.strip()) for s in space_ids_str.split(',') if s.strip()]
            except ValueError:
                self.stderr.write(f'Invalid --space-ids: {space_ids_str}. Use comma-separated integers.')
                return

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f'DRY RUN mode: no changes will be made to the database.'
            ))

        self.stdout.write(
            f'Archiving ForecastLog entries older than {FORECAST_LOG_ARCHIVE_DAYS} days '
            f'(cutoff: {as_of - timezone.timedelta(days=FORECAST_LOG_ARCHIVE_DAYS):%Y-%m-%d})'
        )

        with scopes_disabled():
            result = archive_forecast_logs(
                as_of=as_of,
                dry_run=dry_run,
                space_ids=space_ids,
            )

        days_processed = result['days_processed']
        created = result['total_summaries_created']
        updated = result['total_summaries_updated']
        deleted = result['total_logs_deleted']

        if not days_processed:
            self.stdout.write(self.style.SUCCESS(
                'No ForecastLog entries found to archive.'
            ))
            return

        self.stdout.write(self.style.SUCCESS(
            f'Successfully processed {len(days_processed)} day(s): '
            f'{", ".join(d.strftime("%Y-%m-%d") for d in days_processed)}'
        ))
        self.stdout.write(
            f'  - {created} new daily summaries created\n'
            f'  - {updated} existing daily summaries updated\n'
            f'  - {deleted} raw ForecastLog entries {"would be" if dry_run else ""} deleted'
        )

        if dry_run:
            self.stdout.write(self.style.WARNING(
                'This was a dry run. Re-run without --dry-run to actually archive.'
            ))
