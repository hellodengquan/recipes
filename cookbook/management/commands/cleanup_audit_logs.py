import csv
import sys
from datetime import timedelta
from io import StringIO

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_scopes import scopes_disabled

from cookbook.models import PermissionAuditLog


class Command(BaseCommand):
    help = _('Clean up expired permission audit log entries based on retention policy.')

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=None,
            help=_('Retention period in days. Overrides PERMISSION_AUDIT_LOG_RETENTION_DAYS setting.'),
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            default=False,
            help=_('Do not actually delete anything, just report what would be deleted.'),
        )
        parser.add_argument(
            '--archive',
            type=str,
            default=None,
            metavar='FILE',
            help=_('Export expired entries to a CSV file before deletion.'),
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=None,
            help=_('Number of rows to delete per batch. Overrides PERMISSION_AUDIT_LOG_BATCH_SIZE setting.'),
        )

    def handle(self, *args, **options):
        retention_days = options.get('days')
        if retention_days is None:
            retention_days = settings.PERMISSION_AUDIT_LOG_RETENTION_DAYS

        batch_size = options.get('batch_size')
        if batch_size is None:
            batch_size = settings.PERMISSION_AUDIT_LOG_BATCH_SIZE

        dry_run = options.get('dry_run')
        archive_path = options.get('archive')

        if retention_days <= 0:
            self.stdout.write(self.style.WARNING(
                _('Retention period is disabled (≤ 0 days). No cleanup will be performed.')
            ))
            return

        cutoff = timezone.now() - timedelta(days=retention_days)

        with scopes_disabled():
            total_expired = PermissionAuditLog.objects.filter(
                created_at__lt=cutoff
            ).count()

        self.stdout.write(
            _('Retention window: %(days)d days') % {'days': retention_days}
        )
        self.stdout.write(
            _('Cutoff time: %(cutoff)s') % {'cutoff': cutoff.isoformat()}
        )
        self.stdout.write(
            _('Expired entries found: %(count)d') % {'count': total_expired}
        )

        if total_expired == 0:
            self.stdout.write(self.style.SUCCESS(_('Nothing to clean up.')))
            return

        if dry_run and not archive_path:
            self.stdout.write(self.style.WARNING(
                _('Dry run: no entries will be deleted.')
            ))
            return

        if archive_path:
            self._archive_expired(cutoff, archive_path, dry_run)
            if dry_run:
                return

        deleted = self._batch_delete(cutoff, batch_size)

        self.stdout.write(self.style.SUCCESS(
            _('Cleanup complete. Deleted %(count)d entries.') % {'count': deleted}
        ))

    def _archive_expired(self, cutoff, archive_path, dry_run):
        self.stdout.write(
            _('Archiving expired entries to %(path)s...') % {'path': archive_path}
        )

        field_names = [
            'id', 'action', 'space_id', 'target_user_id', 'target_username',
            'actor_user_id', 'actor_username', 'old_groups', 'new_groups',
            'old_household_id', 'new_household_id', 'message', 'created_at',
        ]

        count = 0
        with scopes_disabled():
            queryset = PermissionAuditLog.objects.filter(
                created_at__lt=cutoff
            ).order_by('created_at')

            try:
                if archive_path == '-':
                    f = sys.stdout
                else:
                    f = open(archive_path, 'w', newline='', encoding='utf-8')

                writer = csv.DictWriter(f, fieldnames=field_names)
                writer.writeheader()

                for log in queryset.iterator(chunk_size=1000):
                    row = {}
                    for fname in field_names:
                        val = getattr(log, fname)
                        if hasattr(val, 'isoformat'):
                            val = val.isoformat()
                        elif isinstance(val, (list, dict)):
                            import json
                            val = json.dumps(val)
                        row[fname] = val
                    writer.writerow(row)
                    count += 1

                if archive_path != '-':
                    f.close()
            except Exception as e:
                if archive_path != '-' and 'f' in locals() and not f.closed:
                    try:
                        f.close()
                    except Exception:
                        pass
                raise

        self.stdout.write(self.style.SUCCESS(
            _('Archived %(count)d entries.') % {'count': count}
        ))

    def _batch_delete(self, cutoff, batch_size):
        deleted_total = 0

        with scopes_disabled():
            while True:
                pks = list(
                    PermissionAuditLog.objects.filter(
                        created_at__lt=cutoff
                    ).order_by('created_at').values_list('pk', flat=True)[:batch_size]
                )

                if not pks:
                    break

                count, _ = PermissionAuditLog.objects.filter(pk__in=pks).delete()
                deleted_total += count

                if count < batch_size:
                    break

        return deleted_total
