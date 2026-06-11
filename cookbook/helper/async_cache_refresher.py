import logging
import queue
import threading
from dataclasses import dataclass
from typing import Optional

from django.conf import settings
from django.db import connection
from django_scopes import scope, scopes_disabled

from cookbook.helper.cache_helper import CacheHelper
from cookbook.models import CacheRefreshStatus, CacheRefreshTask, CacheRefreshType, Space, User


@dataclass
class RefreshWork:
    task: CacheRefreshTask
    cache_patterns: list[str]


class Singleton(type):
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super(Singleton, cls).__call__(*args, **kwargs)
        return cls._instances[cls]


class AsyncCacheRefresher(metaclass=Singleton):
    _logger: logging.Logger
    _queue: queue.Queue
    _worker: threading.Thread
    _lock_manager_lock: threading.Lock
    _unit_locks: dict
    _active_unit_tasks: dict

    def __init__(self):
        self._logger = logging.getLogger("recipes.cache_refresher")
        self._logger.debug("AsyncCacheRefresher initializing")
        queue_size = getattr(settings, 'CACHE_REFRESH_QUEUE_SIZE', 1000)
        self._queue = queue.Queue(maxsize=queue_size)
        self._lock_manager_lock = threading.Lock()
        self._unit_locks = {}
        self._active_unit_tasks = {}
        self._worker = threading.Thread(target=self.worker, args=(0, self._queue,), daemon=True)
        self._worker.start()

    @classmethod
    def is_initialized(cls):
        return cls in cls._instances

    def stop(self):
        self._queue.put(None)
        self._worker.join(timeout=5)

    def _get_unit_lock_key(self, space_id: int, unit_id: Optional[int]) -> Optional[tuple]:
        if unit_id is None:
            return None
        return (space_id, unit_id)

    def _acquire_unit_lock(self, space_id: int, unit_id: Optional[int]) -> bool:
        key = self._get_unit_lock_key(space_id, unit_id)
        if key is None:
            return True
        with self._lock_manager_lock:
            if key not in self._unit_locks:
                self._unit_locks[key] = threading.Lock()
            lock = self._unit_locks[key]
        acquired = lock.acquire(blocking=False)
        if acquired:
            with self._lock_manager_lock:
                self._active_unit_tasks[key] = True
        return acquired

    def _release_unit_lock(self, space_id: int, unit_id: Optional[int]):
        key = self._get_unit_lock_key(space_id, unit_id)
        if key is None:
            return
        with self._lock_manager_lock:
            if key in self._active_unit_tasks:
                del self._active_unit_tasks[key]
            if key in self._unit_locks:
                lock = self._unit_locks[key]
                try:
                    lock.release()
                except RuntimeError:
                    pass

    def _is_unit_locked(self, space_id: int, unit_id: Optional[int]) -> bool:
        key = self._get_unit_lock_key(space_id, unit_id)
        if key is None:
            return False
        with self._lock_manager_lock:
            return key in self._active_unit_tasks

    @staticmethod
    def _find_conflicting_task(space_id: int, unit_id: Optional[int], exclude_task_id: int = None):
        if unit_id is None:
            return None
        active_statuses = [
            CacheRefreshStatus.PENDING.value,
            CacheRefreshStatus.RUNNING.value,
            CacheRefreshStatus.RETRYING.value,
        ]
        with scopes_disabled():
            qs = CacheRefreshTask.objects.filter(
                space_id=space_id,
                source_unit_id=unit_id,
                status__in=active_statuses,
            )
            if exclude_task_id is not None:
                qs = qs.exclude(pk=exclude_task_id)
            return qs.order_by('-created_at').first()

    def _add_work(self, work: RefreshWork):
        try:
            self._queue.put_nowait(work)
            self._logger.debug(f"Queued cache refresh task {work.task.id} for space {work.task.space.id}")
        except queue.Full:
            self._logger.warning(f"Cache refresh queue was full, skipping task {work.task.id}")
            with scopes_disabled():
                work.task.mark_failed("Queue full")

    @staticmethod
    def _save_cache_patterns(task: CacheRefreshTask, cache_patterns: list[str]):
        with scopes_disabled():
            t = CacheRefreshTask.objects.get(pk=task.id)
            t.cache_patterns = cache_patterns
            t.save(update_fields=['cache_patterns'])

    @staticmethod
    def _get_unit_ids_for_task(task: CacheRefreshTask) -> list:
        unit_ids = []
        if task.source_unit_id:
            unit_ids.append(task.source_unit_id)
        if task.target_unit_id and task.target_unit_id != task.source_unit_id:
            unit_ids.append(task.target_unit_id)
        return unit_ids

    @staticmethod
    def _check_and_handle_conflict(task: CacheRefreshTask) -> Optional[CacheRefreshTask]:
        unit_ids = AsyncCacheRefresher._get_unit_ids_for_task(task)
        for unit_id in unit_ids:
            existing = AsyncCacheRefresher._find_conflicting_task(
                space_id=task.space_id,
                unit_id=unit_id,
                exclude_task_id=task.id,
            )
            if existing is not None:
                with scopes_disabled():
                    t = CacheRefreshTask.objects.get(pk=task.id)
                    t.mark_skipped(
                        message=f"Skipped due to conflicting task {existing.id} "
                                f"({existing.task_type}, unit={unit_id})",
                        merged_into_task=existing,
                    )
                return existing
        return None

    @staticmethod
    def submit_unit_merge_refresh(space: Space, source_unit_id: int, target_unit_id: int,
                                  created_by: Optional[User] = None, batch_size: int = 100):
        cache_helper = CacheHelper(space)
        cache_patterns = [
            cache_helper.BASE_UNITS_CACHE_KEY,
            cache_helper.PROPERTY_TYPE_CACHE_KEY,
            f'{cache_helper.DELETE_COLLECTOR_CACHE_PREFIX}*',
        ]

        with scopes_disabled():
            task = CacheRefreshTask.objects.create(
                task_type=CacheRefreshType.UNIT_MERGE.value,
                source_unit_id=source_unit_id,
                target_unit_id=target_unit_id,
                batch_size=batch_size,
                created_by=created_by,
                space=space,
                cache_patterns=cache_patterns,
            )

        conflicting = AsyncCacheRefresher._check_and_handle_conflict(task)
        if conflicting is not None:
            logging.getLogger("recipes.cache_refresher").info(
                f"Task {task.id} skipped due to conflict with {conflicting.id}"
            )
            return task

        work = RefreshWork(task=task, cache_patterns=cache_patterns)

        if not AsyncCacheRefresher.is_initialized():
            AsyncCacheRefresher()._add_work(work)
        else:
            AsyncCacheRefresher()._add_work(work)

        return task

    @staticmethod
    def submit_unit_delete_refresh(space: Space, unit_id: int,
                                   created_by: Optional[User] = None, batch_size: int = 100):
        cache_helper = CacheHelper(space)
        cache_patterns = [
            cache_helper.BASE_UNITS_CACHE_KEY,
            cache_helper.PROPERTY_TYPE_CACHE_KEY,
            f'{cache_helper.DELETE_COLLECTOR_CACHE_PREFIX}*',
        ]

        with scopes_disabled():
            task = CacheRefreshTask.objects.create(
                task_type=CacheRefreshType.UNIT_DELETE.value,
                source_unit_id=unit_id,
                batch_size=batch_size,
                created_by=created_by,
                space=space,
                cache_patterns=cache_patterns,
            )

        conflicting = AsyncCacheRefresher._check_and_handle_conflict(task)
        if conflicting is not None:
            logging.getLogger("recipes.cache_refresher").info(
                f"Task {task.id} skipped due to conflict with {conflicting.id}"
            )
            return task

        work = RefreshWork(task=task, cache_patterns=cache_patterns)

        if not AsyncCacheRefresher.is_initialized():
            AsyncCacheRefresher()._add_work(work)
        else:
            AsyncCacheRefresher()._add_work(work)

        return task

    @staticmethod
    def submit_unit_save_refresh(space: Space, unit_id: int,
                                 created_by: Optional[User] = None, batch_size: int = 100):
        cache_helper = CacheHelper(space)
        cache_patterns = [
            cache_helper.BASE_UNITS_CACHE_KEY,
            cache_helper.PROPERTY_TYPE_CACHE_KEY,
            f'{cache_helper.DELETE_COLLECTOR_CACHE_PREFIX}*',
        ]

        with scopes_disabled():
            task = CacheRefreshTask.objects.create(
                task_type=CacheRefreshType.UNIT_SAVE.value,
                source_unit_id=unit_id,
                batch_size=batch_size,
                created_by=created_by,
                space=space,
                cache_patterns=cache_patterns,
            )

        conflicting = AsyncCacheRefresher._check_and_handle_conflict(task)
        if conflicting is not None:
            logging.getLogger("recipes.cache_refresher").info(
                f"Task {task.id} skipped due to conflict with {conflicting.id}"
            )
            return task

        work = RefreshWork(task=task, cache_patterns=cache_patterns)

        if not AsyncCacheRefresher.is_initialized():
            AsyncCacheRefresher()._add_work(work)
        else:
            AsyncCacheRefresher()._add_work(work)

        return task

    @staticmethod
    def submit_retry_task(task_id: int):
        with scopes_disabled():
            try:
                task = CacheRefreshTask.objects.get(pk=task_id)
            except CacheRefreshTask.DoesNotExist:
                logging.getLogger("recipes.cache_refresher").warning(f"Task {task_id} not found for retry")
                return None

            if not task.can_retry():
                logging.getLogger("recipes.cache_refresher").warning(
                    f"Task {task_id} cannot retry (retry_count={task.retry_count}, max_retries={task.max_retries})"
                )
                return None

            task.mark_retrying()

        conflicting = AsyncCacheRefresher._check_and_handle_conflict(task)
        if conflicting is not None:
            logging.getLogger("recipes.cache_refresher").info(
                f"Retry task {task_id} skipped due to conflict with {conflicting.id}"
            )
            return task

        cache_patterns = task.cache_patterns if task.cache_patterns else []

        work = RefreshWork(task=task, cache_patterns=cache_patterns)

        if not AsyncCacheRefresher.is_initialized():
            AsyncCacheRefresher()._add_work(work)
        else:
            AsyncCacheRefresher()._add_work(work)

        return task

    @staticmethod
    def worker(worker_id: int, worker_queue: queue.Queue):
        logger = logging.getLogger("recipes.cache_refresher.worker")
        refresher = AsyncCacheRefresher()

        logger.info(f"started AsyncCacheRefresher worker {worker_id}")

        while True:
            try:
                item: Optional[RefreshWork] = worker_queue.get()
            except KeyboardInterrupt:
                break

            if item is None:
                break

            logger.debug(f"received cache refresh task {item.task.id} for space {item.task.space.id}")

            space_id = item.task.space_id
            task_unit_ids = AsyncCacheRefresher._get_unit_ids_for_task(item.task)

            locks_acquired = []
            skip_due_to_lock = False

            for unit_id in task_unit_ids:
                if refresher._acquire_unit_lock(space_id, unit_id):
                    locks_acquired.append(unit_id)
                else:
                    skip_due_to_lock = True
                    logger.warning(f"Task {item.task.id} cannot acquire lock for unit {unit_id}, re-queuing")
                    break

            if skip_due_to_lock:
                for unit_id in locks_acquired:
                    refresher._release_unit_lock(space_id, unit_id)
                import time
                time.sleep(0.1)
                try:
                    refresher._queue.put_nowait(item)
                except queue.Full:
                    with scopes_disabled():
                        try:
                            item.task.mark_failed("Queue full after lock conflict retry")
                        except BaseException:
                            pass
                worker_queue.task_done()
                continue

            try:
                with scopes_disabled():
                    current = CacheRefreshTask.objects.get(pk=item.task.id)
                if current.status == CacheRefreshStatus.SKIPPED.value:
                    logger.info(f"Task {item.task.id} already marked as SKIPPED, bypassing execution")
                else:
                    AsyncCacheRefresher.process_refresh_work(item, logger)
            except BaseException as e:
                logger.exception(f"Error processing cache refresh task {item.task.id}")
                with scopes_disabled():
                    try:
                        task = CacheRefreshTask.objects.get(pk=item.task.id)
                        if task.can_retry():
                            task.mark_retrying()
                            AsyncCacheRefresher.submit_retry_task(task.id)
                        else:
                            task.mark_failed(str(e))
                    except BaseException:
                        logger.exception(f"Failed to handle task {item.task.id} failure")
            finally:
                for unit_id in task_unit_ids:
                    refresher._release_unit_lock(space_id, unit_id)
                worker_queue.task_done()

        logger.info(f"terminating AsyncCacheRefresher worker {worker_id}")
        connection.close()

    @staticmethod
    def _get_cache_keys(cache_backend, pattern):
        import fnmatch

        if '*' in pattern:
            if hasattr(cache_backend, 'keys'):
                try:
                    return list(cache_backend.keys(pattern))
                except Exception:
                    pass

            if hasattr(cache_backend, '_cache'):
                all_keys = list(cache_backend._cache.keys())
                return fnmatch.filter(all_keys, pattern)

            return []
        else:
            return [pattern]

    @staticmethod
    def process_refresh_work(work: RefreshWork, logger: logging.Logger):
        from cookbook.helper.unit_conversion_helper import UnitConversionHelper
        from django.core.cache import caches
        from django.db import connection as db_connection

        try:
            db_connection.close()
        except Exception:
            pass

        try:
            with scopes_disabled():
                task = CacheRefreshTask.objects.get(pk=work.task.id)

            if task.status == CacheRefreshStatus.SKIPPED.value:
                logger.info(f"Task {task.id} is SKIPPED, bypassing execution")
                return

            cache_patterns = work.cache_patterns if work.cache_patterns else task.cache_patterns
            is_resuming = task.status == CacheRefreshStatus.RETRYING.value or task.last_processed_index > 0

            task.mark_running()

            space = work.task.space
            cache_backend = caches['default']

            all_cache_keys = []
            for pattern in cache_patterns:
                matched = AsyncCacheRefresher._get_cache_keys(cache_backend, pattern)
                all_cache_keys.extend(matched)
                logger.debug(f"Pattern {pattern} matched {len(matched)} keys")

            if not is_resuming or task.total_items == 0:
                task.total_items = len(all_cache_keys)
                task.save(update_fields=['total_items'])

            start_index = task.last_processed_index if is_resuming else 0

            if is_resuming and start_index > 0:
                logger.info(f"Resuming task {task.id} from index {start_index}/{len(all_cache_keys)}")

            logger.debug(f"Processing cache refresh task {task.id}: "
                         f"{'resuming' if is_resuming else 'new'}, "
                         f"start_index={start_index}, total={len(all_cache_keys)}")

            with scope(space=space):
                batch_size = task.batch_size
                for i in range(start_index, len(all_cache_keys), batch_size):
                    batch = all_cache_keys[i:i + batch_size]
                    batch_count = len(batch)

                    for key in batch:
                        cache_backend.delete(key)

                    UnitConversionHelper._base_units_cache.pop(space.id, None)

                    try:
                        db_connection.close()
                    except Exception:
                        pass

                    processed_end_index = min(i + batch_count, len(all_cache_keys))
                    task.update_progress(processed_batch_count=batch_count,
                                         processed_index=processed_end_index)

                    logger.debug(f"Task {task.id}: Processed batch {task.batch_count}, "
                                 f"{task.processed_items}/{task.total_items} keys, "
                                 f"index={processed_end_index}/{len(all_cache_keys)}")

                    if i + batch_size < len(all_cache_keys):
                        import time
                        time.sleep(0.01)

            try:
                db_connection.close()
            except Exception:
                pass

            task.mark_completed(f"Successfully refreshed {task.total_items} cache keys")
            logger.info(f"Completed cache refresh task {task.id}: "
                        f"{task.processed_items}/{task.total_items} keys in {task.batch_count} batches")

        except BaseException as e:
            logger.exception(f"Failed to process cache refresh task {work.task.id}")
            try:
                db_connection.close()
            except Exception:
                pass
            try:
                with scopes_disabled():
                    task = CacheRefreshTask.objects.get(pk=work.task.id)
                    if task.can_retry():
                        task.mark_retrying()
                        logger.info(f"Auto-retrying task {task.id}: "
                                    f"retry_count={task.retry_count}/{task.max_retries}, "
                                    f"last_processed_index={task.last_processed_index}")
                        AsyncCacheRefresher.submit_retry_task(task.id)
                    else:
                        task.mark_failed(str(e))
                        logger.error(f"Task {task.id} permanently failed after {task.retry_count} retries: {e}")
            except BaseException:
                logger.exception(f"Failed to handle task {work.task.id} failure")
            raise
