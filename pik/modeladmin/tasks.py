from datetime import datetime
import logging

from django.core.cache import cache


PROGRESS_KEY = 'progress_{task_id}'
CACHE_TIMEOUT = 7 * 24 * 60 * 60

logger = logging.getLogger('celery')


def get_progress(task_id):
    return cache.get(PROGRESS_KEY.format(task_id=task_id))


def register_progress(task_id, **kwargs):
    progress_defaults = {
        'started': datetime.now(),
        'current': 0,
        'successful': 0,
        'failed': 0,
        'total': 0,
        'error': None}
    progress = {**progress_defaults, **kwargs}
    logger.info(f'task_id: {task_id}, progress: {progress}')
    cache.set(
        PROGRESS_KEY.format(task_id=task_id), progress, timeout=CACHE_TIMEOUT)
