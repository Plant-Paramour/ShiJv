"""One persistent local profile, without passwords or account roles."""
import uuid

DEFAULT_NAME = '请输入用户名'


def ensure_identity(repository):
    with repository.database.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS local_identity (singleton INTEGER PRIMARY KEY CHECK(singleton=1), user_id TEXT NOT NULL UNIQUE REFERENCES users(id) ON DELETE RESTRICT)')
        row = db.execute('SELECT u.id FROM users u JOIN local_identity l ON l.user_id=u.id WHERE l.singleton=1').fetchone()
        if row:
            return row['id']
        existing = db.execute('SELECT id FROM users ORDER BY created_at,id').fetchall()
        if len(existing) > 1:
            raise ValueError('单机版只支持一个本地档案，请先转换旧数据副本')
        user_id = existing[0]['id'] if existing else str(uuid.uuid4())
        if not existing:
            db.execute('INSERT INTO users(id,public_id,username,display_name) VALUES (?,?,?,?)',
                       (user_id, repository._next_public_id(db), 'local-user', DEFAULT_NAME))
        db.execute('INSERT INTO local_identity(singleton,user_id) VALUES(1,?)', (user_id,))
        db.execute("CREATE TRIGGER IF NOT EXISTS local_one_user_insert BEFORE INSERT ON users WHEN EXISTS(SELECT 1 FROM users) BEGIN SELECT RAISE(ABORT,'单机版只能维护一个用户'); END")
        db.execute("CREATE TRIGGER IF NOT EXISTS local_one_user_delete BEFORE DELETE ON users WHEN OLD.id=(SELECT user_id FROM local_identity WHERE singleton=1) BEGIN SELECT RAISE(ABORT,'不能删除本地唯一用户'); END")
    return user_id
