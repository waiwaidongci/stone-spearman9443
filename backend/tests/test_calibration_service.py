import os

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault('REDIS_URL', 'redis://localhost:6379/15')
os.environ.setdefault('JWT_SECRET', 'test-secret')

from src.config.database import Base
from src.models import calibration as calibration_models  # noqa: F401  registers tables
from src.models.user import User
from src.services import calibration
from src.services import calibration_solver as solver
from src.schemas.calibration import (
    CalibrationProjectInput,
    InstrumentInput,
    PairInput,
    CalibrationRunInput,
    PublishInput,
    CalibrationProjectUpdate,
)


class FakeRedis:
    def __init__(self):
        self.queues = {}
        self.fail = False

    def rpush(self, key, value):
        if self.fail:
            raise ConnectionError('redis down')
        self.queues.setdefault(key, []).append(int(value))

    def exists(self, key):
        return int(key in self.queues and bool(self.queues[key]))


@pytest.fixture()
def env(monkeypatch):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    fake = FakeRedis()
    monkeypatch.setattr(calibration, '_redis', lambda: fake)
    db = Session()
    user = User(email='analyst@demo.local', role='analyst', password_hash='x')
    admin = User(email='admin@demo.local', role='admin', password_hash='x')
    viewer = User(email='viewer@demo.local', role='viewer', password_hash='x')
    db.add_all([user, admin, viewer])
    db.commit()
    yield db, fake, admin, viewer
    db.close()


def make_project(db, user, baseline='M0'):
    return calibration.create_project(db, CalibrationProjectInput(
        name=f'项目-{baseline}', source_filename='cal.csv',
        baseline_instrument=baseline), user)


def add(db, pid, user, *names):
    for name in names:
        calibration.add_instrument(db, pid, InstrumentInput(name=name), user)


def pair(db, pid, user, a, b, lo, hi, basis='同批标准样'):
    return calibration.save_pair(db, pid, PairInput(
        a=a, b=b, lower=lo, upper=hi, basis=basis), user)


def run(db, pid, user, key='key-0000001', name='校准核算'):
    return calibration.create_run(db, CalibrationRunInput(
        project_id=pid, name=name, idempotency_key=key), user)


def drain(db, fake):
    """Simulate the worker consuming the calibration queue."""
    fake.queues.setdefault('calibration-queue', [])
    while fake.queues['calibration-queue']:
        run_id = fake.queues['calibration-queue'].pop(0)
        row = db.get(calibration_models.CalibrationRun, run_id)
        if not row or row.status in ('completed', 'failed'):
            continue
        row.result = solver.solve(row.snapshot)
        row.status = 'completed'
        db.commit()


def test_snapshot_freezes_intervals_and_revision(env):
    db, fake, admin, viewer = env
    p = make_project(db, admin)
    add(db, p['id'], admin, 'M1')
    pair(db, p['id'], admin, 'M1', 'M0', -0.5, 0.4)
    r = run(db, p['id'], admin)
    assert r['snapshot_revision'] == 2
    assert r['snapshot']['baseline'] == 'M0'
    assert len(r['snapshot']['pairs']) == 1
    # a later edit does not touch the frozen snapshot
    pair(db, p['id'], admin, 'M1', 'M0', -0.1, 0.1)
    again = calibration.get_run(db, r['id'])
    assert again['snapshot']['pairs'][0]['lower'] == -0.5
    assert again['checksum'] == r['checksum']


def test_run_requires_locked_project_not_live_rows(env, monkeypatch):
    """create_run must read instruments/pairs after locking the project row."""
    db, fake, admin, viewer = env
    p = make_project(db, admin)
    add(db, p['id'], admin, 'M1')
    pair(db, p['id'], admin, 'M1', 'M0', -0.5, 0.4)
    locked = []
    original = calibration._project_row

    def spy(db_, pid, lock=False):
        row = original(db_, pid, lock=lock)
        if lock:
            locked.append(pid)
        return row

    monkeypatch.setattr(calibration, '_project_row', spy)
    run(db, p['id'], admin)
    assert locked


def test_same_idempotency_key_returns_same_run(env):
    db, fake, admin, viewer = env
    p = make_project(db, admin)
    add(db, p['id'], admin, 'M1')
    pair(db, p['id'], admin, 'M1', 'M0', -0.5, 0.4)
    first = run(db, p['id'], admin, key='retry-key-1')
    second = run(db, p['id'], admin, key='retry-key-1')
    assert first['id'] == second['id']
    assert len(calibration.list_runs(db, p['id'])) == 1


def test_retry_after_interrupted_write_requeues_original_snapshot(env):
    db, fake, admin, viewer = env
    p = make_project(db, admin)
    add(db, p['id'], admin, 'M1')
    pair(db, p['id'], admin, 'M1', 'M0', -0.5, 0.4)
    fake.fail = True
    first = run(db, p['id'], admin, key='retry-key-2')  # enqueue raised, swallowed
    assert first['status'] == 'queued'
    fake.fail = False
    # inputs change before the retry; the retry must still use the frozen data
    pair(db, p['id'], admin, 'M1', 'M0', -0.05, 0.05)
    second = run(db, p['id'], admin, key='retry-key-2')
    assert second['id'] == first['id']
    assert second['snapshot']['pairs'][0]['lower'] == -0.5
    assert fake.queues['calibration-queue'] == [first['id']]
    assert len(calibration.list_runs(db, p['id'])) == 1


def test_completed_run_is_not_duplicated_by_retry(env):
    db, fake, admin, viewer = env
    p = make_project(db, admin)
    add(db, p['id'], admin, 'M1')
    pair(db, p['id'], admin, 'M1', 'M0', -0.5, 0.4)
    first = run(db, p['id'], admin, key='retry-key-3')
    drain(db, fake)
    fake.queues['calibration-queue'] = []
    second = run(db, p['id'], admin, key='retry-key-3')
    assert second['id'] == first['id']
    assert second['status'] == 'completed'
    assert calibration.list_runs(db, p['id'])[0]['id'] == first['id']


def test_retry_requeues_a_run_left_running(env):
    db, fake, admin, viewer = env
    p = make_project(db, admin)
    add(db, p['id'], admin, 'M1')
    pair(db, p['id'], admin, 'M1', 'M0', -0.5, 0.4)
    first = run(db, p['id'], admin, key='retry-key-4')
    row = db.get(calibration_models.CalibrationRun, first['id'])
    row.status = 'running'   # worker dequeued, then died before writing a result
    db.commit()
    fake.queues['calibration-queue'] = []
    second = run(db, p['id'], admin, key='retry-key-4')
    assert second['id'] == first['id']
    assert fake.queues['calibration-queue'] == [first['id']]
    drain(db, fake)
    again = calibration.get_run(db, first['id'])
    assert again['status'] == 'completed'
    assert len(calibration.list_runs(db, p['id'])) == 1


def test_revision_bumps_on_every_calibration_edit(env):
    db, fake, admin, viewer = env
    p = make_project(db, admin)
    assert calibration.get_project(db, p['id'])['revision'] == 0
    add(db, p['id'], admin, 'M1')
    assert calibration.get_project(db, p['id'])['revision'] == 1
    pr = pair(db, p['id'], admin, 'M1', 'M0', -0.5, 0.4)
    assert calibration.get_project(db, p['id'])['revision'] == 2
    calibration.save_pair(db, p['id'], PairInput(
        a='M1', b='M0', lower=-0.4, upper=0.3, basis='更新'), admin, pair_id=pr['id'])
    assert calibration.get_project(db, p['id'])['revision'] == 3
    calibration.update_project(db, p['id'], CalibrationProjectUpdate(
        baseline_instrument='M1'), admin)
    assert calibration.get_project(db, p['id'])['revision'] == 4


def test_publish_requires_ready_result_and_is_immutable(env):
    db, fake, admin, viewer = env
    p = make_project(db, admin)
    add(db, p['id'], admin, 'M1')
    pair(db, p['id'], admin, 'M1', 'M0', -0.5, 0.4)
    r = run(db, p['id'], admin)
    with pytest.raises(HTTPException) as exc:
        calibration.publish(db, r['id'], PublishInput(note='不能发布未完成'), admin)
    assert exc.value.status_code == 409
    drain(db, fake)
    published = calibration.publish(db, r['id'], PublishInput(
        note='按冻结修订发布', scope='吸水率'), admin)
    assert published['published_at']
    with pytest.raises(HTTPException) as exc:
        calibration.publish(db, r['id'], PublishInput(note='再次发布尝试'), admin)
    assert exc.value.status_code == 409


def test_publish_blocked_when_ranges_incomplete(env):
    db, fake, admin, viewer = env
    p = make_project(db, admin)
    add(db, p['id'], admin, 'M1', 'X1', 'X2')
    pair(db, p['id'], admin, 'M1', 'M0', -0.5, 0.4)
    pair(db, p['id'], admin, 'X1', 'X2', 0.1, 0.2)
    r = run(db, p['id'], admin)
    drain(db, fake)
    result = calibration.get_run(db, r['id'])['result']
    assert result['publish_ready'] is False
    assert any('基准' in b or '依据' in b for b in result['publish_blockers'])
    with pytest.raises(HTTPException) as exc:
        calibration.publish(db, r['id'], PublishInput(note='强行发布尝试'), admin)
    assert exc.value.status_code == 409


def test_publish_blocked_when_basis_missing(env):
    db, fake, admin, viewer = env
    p = make_project(db, admin)
    add(db, p['id'], admin, 'M1')
    pair(db, p['id'], admin, 'M1', 'M0', -0.5, 0.4, basis='')
    r = run(db, p['id'], admin)
    drain(db, fake)
    result = calibration.get_run(db, r['id'])['result']
    assert result['publish_ready'] is False


def test_viewer_can_only_export_published(env):
    db, fake, admin, viewer = env
    p = make_project(db, admin)
    add(db, p['id'], admin, 'M1')
    pair(db, p['id'], admin, 'M1', 'M0', -0.5, 0.4)
    r = run(db, p['id'], admin)
    drain(db, fake)
    with pytest.raises(HTTPException) as exc:
        calibration.export(db, r['id'], viewer)
    assert exc.value.status_code == 403
    calibration.publish(db, r['id'], PublishInput(note='发布后导出'), admin)
    text = calibration.export(db, r['id'], viewer)
    assert 'M1' in text and '-0.5' in text


def test_create_run_rejects_project_without_pairs(env):
    db, fake, admin, viewer = env
    p = make_project(db, admin)
    add(db, p['id'], admin, 'M1')
    with pytest.raises(HTTPException) as exc:
        run(db, p['id'], admin)
    assert exc.value.status_code == 422


def test_invalid_pair_and_instrument_inputs_rejected(env):
    db, fake, admin, viewer = env
    p = make_project(db, admin)
    add(db, p['id'], admin, 'M1')
    with pytest.raises(HTTPException):
        calibration.add_instrument(db, p['id'], InstrumentInput(name='M1'), admin)
    with pytest.raises(HTTPException):
        pair(db, p['id'], admin, 'M1', 'ZZ', 0, 1)
    with pytest.raises(HTTPException):
        pair(db, p['id'], admin, 'M1', 'M0', 1, 0)
    with pytest.raises(HTTPException):
        pair(db, p['id'], admin, 'M1', 'M1', 0, 1)
    with pytest.raises(HTTPException):
        calibration.remove_instrument(db, p['id'], 1, admin)  # baseline


def test_cannot_remove_referenced_instrument(env):
    db, fake, admin, viewer = env
    p = make_project(db, admin)
    add(db, p['id'], admin, 'M1')
    pair(db, p['id'], admin, 'M1', 'M0', -0.5, 0.4)
    rows = calibration.get_project(db, p['id'])['instruments']
    m1 = next(x for x in rows if x['name'] == 'M1')
    with pytest.raises(HTTPException) as exc:
        calibration.remove_instrument(db, p['id'], m1['id'], admin)
    assert exc.value.status_code == 409


def test_baseline_change_must_be_in_roster(env):
    db, fake, admin, viewer = env
    p = make_project(db, admin)
    with pytest.raises(HTTPException):
        calibration.update_project(db, p['id'], CalibrationProjectUpdate(
            baseline_instrument='NOPE'), admin)
