"""End-to-end API exercise against a throwaway SQLite DB and in-process fakes.

Run with::

    PYTHONPATH=backend python3 backend/scripts/calibration_smoke.py

It only uses local temp files; no Docker, MySQL or Redis is required.
"""
import os
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix='calibration-smoke-')
os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(TMP, 'smoke.db')
os.environ['REDIS_URL'] = 'redis://localhost:6399/0'
os.environ['JWT_SECRET'] = 'smoke-secret'

from fastapi.testclient import TestClient  # noqa: E402
from src.config.database import Base, engine, Session  # noqa: E402
from src.models import calibration as calibration_models  # noqa: E402
from src.models.user import User  # noqa: E402
from src.services import calibration as calibration_service  # noqa: E402
from src.utils.security import hash_password  # noqa: E402


class FakeRedis:
    """Stands in for Redis: records enqueued ids, never connects."""

    def __init__(self):
        self.queues = {}

    def rpush(self, key, value):
        self.queues.setdefault(key, []).append(int(value))
        return len(self.queues[key])

    def incr(self, key):
        return 1

    def expire(self, key, seconds):
        return True

    def exists(self, key):
        return int(bool(self.queues.get(key)))

    def ping(self):
        return True


fake_redis = FakeRedis()
import src.middlewares.rate_limit as rate_limit  # noqa: E402
import src.services.calibration as calibration_module  # noqa: E402
rate_limit.redis = fake_redis
calibration_module._redis = lambda: fake_redis

import src.main as main  # noqa: E402

Base.metadata.create_all(engine)
with Session() as db:
    for role in ('admin', 'analyst', 'viewer'):
        db.add(User(email=role + '@demo.local', role=role,
                    password_hash=hash_password('Research2026!')))
    db.commit()

BASE = '/calibration'


def login(client, role):
    r = client.post('/auth/login', json={'email': role + '@demo.local',
                                         'password': 'Research2026!'})
    r.raise_for_status()
    return {'Authorization': 'Bearer ' + r.json()['token']}


def drain():
    from src.config.database import Session as S
    with S() as db:
        from sqlalchemy import select
        ids = list(db.scalars(select(calibration_models.CalibrationRun.id).where(
            calibration_models.CalibrationRun.status == 'queued')))
        for run_id in ids:
            calibration_service.execute(db, run_id)


def main_():
    client = TestClient(main.app)
    admin = login(client, 'admin')
    viewer = login(client, 'viewer')

    r = client.post(BASE + '/projects', headers=admin, json={
        'name': '吸水率仪器比对 2026-11', 'source_filename': 'cal-202611.csv',
        'baseline_instrument': 'M0'})
    assert r.status_code == 200, r.text
    pid = r.json()['id']
    for name in ('M1', 'M2', 'M3'):
        r = client.post(f'{BASE}/projects/{pid}/instruments', headers=admin, json={'name': name})
        assert r.status_code == 200, r.text
    pairs = [
        ('M1', 'M0', -0.6, 0.4, '证书 A-11'),
        ('M2', 'M1', -0.2, 0.3, '证书 A-12'),
        ('M3', 'M2', 0.1, 0.5, '复测批次 B-2'),
    ]
    for a, b, lo, hi, basis in pairs:
        r = client.post(f'{BASE}/projects/{pid}/pairs', headers=admin,
                        json={'a': a, 'b': b, 'lower': lo, 'upper': hi, 'basis': basis})
        assert r.status_code == 200, r.text

    r = client.get(f'{BASE}/projects/{pid}', headers=admin)
    assert r.json()['revision'] == 6, r.text

    r = client.post(BASE + '/runs', headers=admin, json={
        'project_id': pid, 'name': '十一月核算', 'idempotency_key': 'smoke-key-0001'})
    assert r.status_code == 200, r.text
    run = r.json()
    revision = r.json()['snapshot_revision']
    # editing after the freeze must not change the frozen snapshot
    client.post(f'{BASE}/projects/{pid}/pairs', headers=admin,
                json={'a': 'M3', 'b': 'M0', 'lower': 0, 'upper': 9, 'basis': '晚到证书'})
    r = client.get(f"{BASE}/runs/{run['id']}", headers=admin)
    assert r.json()['snapshot_revision'] == revision
    assert len(r.json()['snapshot']['pairs']) == 3

    # retrying the same request returns the same run, no duplicate
    r = client.post(BASE + '/runs', headers=admin, json={
        'project_id': pid, 'name': '十一月核算', 'idempotency_key': 'smoke-key-0001'})
    assert r.json()['id'] == run['id']
    assert len(client.get(BASE + '/runs', headers=admin).json()) == 1

    drain()
    r = client.get(f"{BASE}/runs/{run['id']}", headers=admin)
    result = r.json()['result']
    ranges = {x['instrument']: x for x in result['ranges']}
    assert round(ranges['M1']['lower'], 6) == -0.6
    assert round(ranges['M1']['upper'], 6) == 0.4
    assert round(ranges['M2']['lower'], 6) == -0.8
    assert round(ranges['M2']['upper'], 6) == 0.7
    assert round(ranges['M3']['lower'], 6) == -0.7
    assert round(ranges['M3']['upper'], 6) == 1.2
    assert result['publish_ready'] is True, result['publish_blockers']
    assert ranges['M2']['upper_basis']['kind'] == 'indirect'

    r = client.get(f"{BASE}/runs/{run['id']}/export", headers=viewer)
    assert r.status_code == 403
    r = client.post(f"{BASE}/runs/{run['id']}/publish", headers=admin,
                    json={'note': '按冻结修订发布，供复测排期', 'scope': '吸水率'})
    assert r.status_code == 200, r.text
    r = client.get(f"{BASE}/runs/{run['id']}/export", headers=viewer)
    assert r.status_code == 200 and 'M3' in r.text

    # a contradictory edit produces a recomputable multi-hop closed loop:
    # M2-M1 in [-0.2,0.3] and M1-M0 in [-0.6,0.4] imply M2-M0 <= 0.7,
    # but the new record says M2-M0 >= 3.
    r = client.post(f'{BASE}/projects/{pid}/pairs', headers=admin,
                    json={'a': 'M2', 'b': 'M0', 'lower': 3, 'upper': 4, 'basis': '矛盾记录'})
    assert r.status_code == 200, r.text
    r = client.post(BASE + '/runs', headers=admin, json={
        'project_id': pid, 'name': '矛盾检查', 'idempotency_key': 'smoke-key-0002'})
    drain()
    result = client.get(f"{BASE}/runs/{r.json()['id']}", headers=admin).json()['result']
    assert result['status'] == 'conflict'
    assert result['conflicts'][0]['residual'] > 0
    assert result['publish_ready'] is False
    print('conflict loop:', result['conflicts'][0]['statement'])
    print('residual:', result['conflicts'][0]['residual'])
    print('SMOKE OK')


if __name__ == '__main__':
    main_()
