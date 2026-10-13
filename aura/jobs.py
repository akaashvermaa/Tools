"""
Registry for background jobs (downloads, video conversions...).

Each job owns a folder in the user cache dir. Finished jobs are removed after an
hour; leftovers from crashed runs are removed at startup, and a job's own folder
at exit. A job only needs `.id`, `.dir` and `.finished_at` (None while running).
"""

import atexit
import os
import shutil
import threading
import time
import uuid
from typing import Dict, Generic, Optional, TypeVar

from .paths import cache_dir

TTL_SECONDS = 60 * 60       # finished jobs (and their files) are removed after an hour
STALE_SECONDS = 6 * 3600    # folders left by a crashed run; newer ones may belong to another running instance

J = TypeVar("J")


class JobRegistry(Generic[J]):
    def __init__(self, folder: str):
        self.root = os.path.join(cache_dir(), folder)
        self._jobs: Dict[str, J] = {}
        self._lock = threading.Lock()
        self._remove_stale_dirs()
        atexit.register(self._remove_own_dirs)

    def new_job_dir(self):
        """A fresh (id, folder) pair for a job about to be created."""
        job_id = uuid.uuid4().hex
        path = os.path.join(self.root, job_id)
        os.makedirs(path)
        return job_id, path

    def add(self, job: J):
        self.cleanup_old()
        with self._lock:
            self._jobs[job.id] = job

    def get(self, job_id: str) -> Optional[J]:
        with self._lock:
            return self._jobs.get(job_id)

    def cleanup_old(self):
        now = time.time()
        with self._lock:
            expired = [j for j in self._jobs.values() if j.finished_at and now - j.finished_at > TTL_SECONDS]
            for job in expired:
                del self._jobs[job.id]
        for job in expired:
            shutil.rmtree(job.dir, ignore_errors=True)

    def _remove_stale_dirs(self):
        os.makedirs(self.root, exist_ok=True)
        now = time.time()
        for name in os.listdir(self.root):
            path = os.path.join(self.root, name)
            try:
                if now - os.path.getmtime(path) > STALE_SECONDS:
                    shutil.rmtree(path, ignore_errors=True)
            except OSError:
                pass

    def _remove_own_dirs(self):
        for job in list(self._jobs.values()):
            shutil.rmtree(job.dir, ignore_errors=True)
