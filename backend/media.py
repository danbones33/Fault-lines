"""
Async media queue: the sim never waits for an image, clip or voice. Jobs are closures; each one
writes its asset under runs/<id>/media/... and patches the feed/timeline through the engine's lock.
A failed job logs and moves on. One worker keeps paid calls serialized (easy to reason about spend).
"""
import threading
import time
import traceback


class MediaQueue:
    def __init__(self, workers=2):
        self.q = []
        self.lock = threading.Lock()
        self.active = 0
        self.done = 0
        self.failed = 0
        self.labels = []     # jobs in flight, by label
        self.recent = []     # the last finished jobs: {t, label, ok, secs}
        for _ in range(workers):
            threading.Thread(target=self._loop, daemon=True).start()

    def submit(self, fn, label=""):
        with self.lock:
            self.q.append((fn, label))

    def pending(self):
        with self.lock:
            return len(self.q) + self.active

    def pending_other(self, prefix):
        """Jobs queued or running whose label does not start with prefix (e.g. everything but other episode builds)."""
        with self.lock:
            return sum(1 for _, lb in self.q if not lb.startswith(prefix)) + sum(1 for lb in self.labels if not lb.startswith(prefix))

    def active_labels(self):
        with self.lock:
            return list(self.labels) + [lb for _, lb in self.q[:6]]

    def recent_done(self, n=10):
        with self.lock:
            return list(self.recent[-n:])

    def _loop(self):
        while True:
            job = None
            with self.lock:
                if self.q:
                    job = self.q.pop(0)
                    self.active += 1
            if not job:
                time.sleep(0.4)
                continue
            fn, label = job
            t0 = time.time()
            with self.lock:
                self.labels.append(label)
            ok = True
            try:
                fn()
                with self.lock:
                    self.done += 1
            except Exception as e:
                ok = False
                with self.lock:
                    self.failed += 1
                print(f"[media] {label} failed: {type(e).__name__}: {e}")
                traceback.print_exc()
            finally:
                with self.lock:
                    self.active -= 1
                    if label in self.labels:
                        self.labels.remove(label)
                    self.recent.append({"t": time.time(), "label": label, "ok": ok, "secs": round(time.time() - t0)})
                    del self.recent[:-40]
