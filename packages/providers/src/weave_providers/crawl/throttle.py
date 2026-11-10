"""Cross-worker request spacing and concurrency limits."""
import time
from contextlib import contextmanager
from datetime import datetime, timezone

import sqlalchemy as sa


class DomainThrottle:
    def __init__(self, engine, min_interval_for=None, max_parallel_for=None,
                 clock=lambda: datetime.now(timezone.utc), sleep=time.sleep):
        self.engine = engine
        self.min_interval_for = min_interval_for
        self.max_parallel_for = max_parallel_for
        self.clock = clock
        self.sleep = sleep

    @contextmanager
    def acquire(self, domain):
        with self.engine.connect() as conn:
            settings = conn.execute(sa.text(
                'SELECT min_interval_seconds, max_parallel FROM sites WHERE domain = :domain'),
                {'domain': domain}).first()
            interval = self.min_interval_for(domain) if self.min_interval_for else (settings[0] if settings else 2.0)
            parallel = self.max_parallel_for(domain) if self.max_parallel_for else (settings[1] if settings else 1)
            conn.commit()
            slot = None
            try:
                while slot is None:
                    for k in range(parallel):
                        if conn.scalar(sa.text('SELECT pg_try_advisory_lock(hashtext(:domain), :k)'), {'domain': domain, 'k': k}):
                            slot = k
                            break
                    conn.commit()
                    if slot is None:
                        self.sleep(0.05)
                reserved = conn.scalar(sa.text('''
                    INSERT INTO domain_throttle (domain, next_request_at)
                    VALUES (:domain, now() + :seconds * interval '1 second')
                    ON CONFLICT (domain) DO UPDATE SET next_request_at =
                        greatest(domain_throttle.next_request_at, now()) + :seconds * interval '1 second'
                    RETURNING next_request_at - :seconds * interval '1 second'
                '''), {'domain': domain, 'seconds': interval})
                conn.commit()
                delay = (reserved - self.clock()).total_seconds()
                if delay > 0:
                    self.sleep(delay)
                yield
            finally:
                if slot is not None:
                    conn.rollback()
                    conn.execute(sa.text('SELECT pg_advisory_unlock(hashtext(:domain), :k)'), {'domain': domain, 'k': slot})
                    conn.commit()
