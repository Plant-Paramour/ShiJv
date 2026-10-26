"""Track local running tasks, without account quotas or remote coordination."""
import time
import uuid


class TaskReservations:
    def __init__(self, database, settings=None, *, clock=time.time):
        self.database, self.clock = database, clock
        self.timeout = getattr(settings, 'agent_turn_timeout_seconds', 900)

    def reserve_in_transaction(self, connection, user_id, category, *, job_id=None):
        now = self.clock()
        connection.execute('DELETE FROM generation_admissions WHERE expires_at<=? AND job_id IS NULL', (now,))
        reservation = str(uuid.uuid4())
        connection.execute('INSERT INTO generation_admissions(id,identity,category,created_at,expires_at,job_id) VALUES (?,?,?,?,?,?)',
                           (reservation, user_id or 'local', category, now, now + self.timeout + 60, job_id))
        return reservation

    def reserve(self, user_id, category):
        with self.database.connect() as db:
            return self.reserve_in_transaction(db, user_id, category)

    def release(self, reservation):
        with self.database.connect() as db:
            db.execute('DELETE FROM generation_admissions WHERE id=? AND job_id IS NULL', (reservation,))

    def start_heartbeat(self, reservation, *, interval=30):
        import threading
        stop = threading.Event()
        def renew():
            while not stop.wait(interval):
                with self.database.connect() as db:
                    db.execute('UPDATE generation_admissions SET expires_at=? WHERE id=?',
                               (self.clock() + self.timeout + 60, reservation))
        thread = threading.Thread(target=renew, daemon=True)
        thread.start()
        def finish():
            stop.set()
            thread.join(timeout=2)
        return finish
