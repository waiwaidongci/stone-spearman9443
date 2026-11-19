import io
import csv
import math
import os
from redis import Redis
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from src.models.calibration import (
    CalibrationProject,
    CalibrationInstrument,
    CalibrationPair,
    CalibrationRun,
)
from src.services import calibration_solver
from src.services.audits import log
from src.utils.serializers import now, checksum


def _redis():
    return Redis.from_url(os.environ['REDIS_URL'])


def _enqueue(run_id):
    """Push a run onto the calibration queue; a failure leaves it queued.

    The record is already committed, so a later retry with the same
    idempotency key re-enqueues the *original* frozen snapshot instead of
    creating a second run.
    """
    try:
        _redis().rpush('calibration-queue', run_id)
    except Exception:
        pass


def _project_row(db, project_id, lock=False):
    stmt = select(CalibrationProject).where(CalibrationProject.id == project_id)
    if lock:
        stmt = stmt.with_for_update()
    project = db.scalar(stmt)
    if not project:
        raise HTTPException(404, '校准项目不存在')
    return project


def _instrument_name(value):
    name = str(value or '').strip()
    if not name:
        raise HTTPException(422, '仪器名称不能为空')
    return name


def _check_interval(lower, upper):
    try:
        lower = float(lower)
        upper = float(upper)
    except (TypeError, ValueError):
        raise HTTPException(422, '区间上下界必须是数值')
    if not (math.isfinite(lower) and math.isfinite(upper)):
        raise HTTPException(422, '区间上下界必须是有限数值')
    if abs(lower) > 1e9 or abs(upper) > 1e9:
        raise HTTPException(422, '区间数值超出允许范围')
    if lower > upper:
        raise HTTPException(422, '区间下界不得大于上界')
    return lower, upper


def _instruments(db, project_id, lock=False):
    stmt = select(CalibrationInstrument).where(
        CalibrationInstrument.project_id == project_id
    ).order_by(CalibrationInstrument.id)
    if lock:
        stmt = stmt.with_for_update()
    return list(db.scalars(stmt))


def _pairs(db, project_id, lock=False):
    stmt = select(CalibrationPair).where(
        CalibrationPair.project_id == project_id
    ).order_by(CalibrationPair.id)
    if lock:
        stmt = stmt.with_for_update()
    return list(db.scalars(stmt))


def _project_record(project):
    return {
        'id': project.id,
        'name': project.name,
        'source_filename': project.source_filename,
        'description': project.description or '',
        'baseline_instrument': project.baseline_instrument,
        'revision': project.revision,
        'created_at': project.created_at,
    }


def _instrument_record(row, baseline):
    return {
        'id': row.id,
        'name': row.name,
        'is_baseline': row.name == baseline,
    }


def _pair_record(row):
    return {
        'id': row.id,
        'a': row.instrument_a,
        'b': row.instrument_b,
        'lower': row.lower,
        'upper': row.upper,
        'basis': row.basis or '',
        'updated_at': row.updated_at,
    }


def _run_summary(run):
    return {
        'id': run.id,
        'name': run.name,
        'status': run.status,
        'snapshot_revision': run.snapshot_revision,
        'checksum': run.checksum,
        'published': run.published_at is not None,
        'created_at': run.created_at,
    }


def list_projects(db):
    data = []
    for project in db.scalars(select(CalibrationProject).order_by(CalibrationProject.id)):
        instruments = db.scalar(select(func.count()).select_from(CalibrationInstrument).where(
            CalibrationInstrument.project_id == project.id))
        pairs = db.scalar(select(func.count()).select_from(CalibrationPair).where(
            CalibrationPair.project_id == project.id))
        runs = db.scalar(select(func.count()).select_from(CalibrationRun).where(
            CalibrationRun.project_id == project.id))
        row = _project_record(project)
        row.update({'instrument_count': instruments or 0, 'pair_count': pairs or 0, 'run_count': runs or 0})
        data.append(row)
    return data


def get_project(db, project_id):
    project = _project_row(db, project_id)
    instruments = _instruments(db, project_id)
    pairs = _pairs(db, project_id)
    runs = list(db.scalars(select(CalibrationRun).where(
        CalibrationRun.project_id == project_id).order_by(CalibrationRun.id.desc())))
    data = _project_record(project)
    data['instruments'] = [_instrument_record(x, project.baseline_instrument) for x in instruments]
    data['pairs'] = [_pair_record(x) for x in pairs]
    data['runs'] = [_run_summary(x) for x in runs]
    data['latest_run'] = _run_summary(runs[0]) if runs else None
    return data


def create_project(db, data, user):
    name = data.name.strip()
    baseline = _instrument_name(data.baseline_instrument)
    if db.scalar(select(CalibrationProject).where(CalibrationProject.name == name)):
        raise HTTPException(409, '校准项目名称已存在')
    stamp = now()
    project = CalibrationProject(
        name=name,
        source_filename=data.source_filename.strip(),
        description=data.description or '',
        baseline_instrument=baseline,
        revision=0,
        created_at=stamp,
    )
    db.add(project)
    db.flush()
    db.add(CalibrationInstrument(project_id=project.id, name=baseline, created_at=stamp))
    log(db, user, 'calibration.project.create', f'{project.id}: {name} / 基准 {baseline}')
    db.commit()
    return _project_record(project)


def update_project(db, project_id, data, user):
    project = _project_row(db, project_id, lock=True)
    changed = []
    if data.baseline_instrument is not None:
        baseline = _instrument_name(data.baseline_instrument)
        roster = {x.name for x in _instruments(db, project_id, lock=True)}
        if baseline not in roster:
            raise HTTPException(422, '基准仪器必须在仪器清单内')
        if baseline != project.baseline_instrument:
            project.baseline_instrument = baseline
            changed.append(f'基准→{baseline}')
    if data.description is not None and data.description != (project.description or ''):
        project.description = data.description
        changed.append('说明')
    if changed:
        project.revision = (project.revision or 0) + 1
        log(db, user, 'calibration.project.update', f'{project_id}: ' + '，'.join(changed))
        db.commit()
    return _project_record(project)


def add_instrument(db, project_id, data, user):
    project = _project_row(db, project_id, lock=True)
    name = _instrument_name(data.name)
    roster = _instruments(db, project_id, lock=True)
    if any(x.name == name for x in roster):
        raise HTTPException(409, '仪器已在清单中')
    if name == project.baseline_instrument:
        raise HTTPException(409, '仪器已在清单中')
    db.add(CalibrationInstrument(project_id=project_id, name=name, created_at=now()))
    project.revision = (project.revision or 0) + 1
    log(db, user, 'calibration.instrument.add', f'{project_id}: {name}')
    db.commit()
    return get_project(db, project_id)


def remove_instrument(db, project_id, instrument_id, user):
    project = _project_row(db, project_id, lock=True)
    row = db.get(CalibrationInstrument, instrument_id)
    if not row or row.project_id != project_id:
        raise HTTPException(404, '仪器不存在')
    if row.name == project.baseline_instrument:
        raise HTTPException(409, '基准仪器不能移除，请先切换基准')
    used = db.scalar(select(func.count()).select_from(CalibrationPair).where(
        CalibrationPair.project_id == project_id,
        ((CalibrationPair.instrument_a == row.name) | (CalibrationPair.instrument_b == row.name)),
    ))
    if used:
        raise HTTPException(409, f'该仪器仍被 {used} 条成对区间引用，请先删除区间')
    db.delete(row)
    project.revision = (project.revision or 0) + 1
    log(db, user, 'calibration.instrument.remove', f'{project_id}: {row.name}')
    db.commit()
    return get_project(db, project_id)


def _validate_pair_names(db, project, data):
    a = _instrument_name(data.a)
    b = _instrument_name(data.b)
    if a == b:
        raise HTTPException(422, '成对区间的两台仪器不能相同')
    roster = {x.name for x in _instruments(db, project.id, lock=True)}
    if a not in roster or b not in roster:
        raise HTTPException(422, '两台仪器都必须先在仪器清单内')
    lower, upper = _check_interval(data.lower, data.upper)
    basis = (data.basis or '').strip()
    if len(basis) > 1000:
        raise HTTPException(422, '依据说明最多 1000 字')
    return a, b, lower, upper, basis


def save_pair(db, project_id, data, user, pair_id=None):
    project = _project_row(db, project_id, lock=True)
    a, b, lower, upper, basis = _validate_pair_names(db, project, data)
    stamp = now()
    if pair_id:
        row = db.get(CalibrationPair, pair_id)
        if not row or row.project_id != project_id:
            raise HTTPException(404, '成对区间不存在')
        row.instrument_a = a
        row.instrument_b = b
        row.lower = lower
        row.upper = upper
        row.basis = basis
        row.updated_at = stamp
        action = 'calibration.pair.update'
        detail = f'{project_id}/{pair_id}: {a}−{b} ∈ [{lower}, {upper}]'
    else:
        row = CalibrationPair(
            project_id=project_id, instrument_a=a, instrument_b=b,
            lower=lower, upper=upper, basis=basis,
            created_at=stamp, updated_at=stamp,
        )
        db.add(row)
        action = 'calibration.pair.create'
        detail = f'{project_id}: {a}−{b} ∈ [{lower}, {upper}]'
    project.revision = (project.revision or 0) + 1
    log(db, user, action, detail)
    db.commit()
    return _pair_record(row)


def remove_pair(db, project_id, pair_id, user):
    project = _project_row(db, project_id, lock=True)
    row = db.get(CalibrationPair, pair_id)
    if not row or row.project_id != project_id:
        raise HTTPException(404, '成对区间不存在')
    db.delete(row)
    project.revision = (project.revision or 0) + 1
    log(db, user, 'calibration.pair.remove', f'{project_id}/{pair_id}')
    db.commit()
    return {'deleted': True}


def _snapshot(db, project, stamp):
    """Read the roster, baseline and intervals at one consistent revision."""
    instruments = [x.name for x in _instruments(db, project.id, lock=True)]
    pairs = _pairs(db, project.id, lock=True)
    return {
        'project_id': project.id,
        'project_name': project.name,
        'baseline': project.baseline_instrument,
        'revision': project.revision,
        'instruments': instruments,
        'pairs': [
            {
                'id': str(p.id),
                'a': p.instrument_a,
                'b': p.instrument_b,
                'lower': p.lower,
                'upper': p.upper,
                'basis': p.basis or '',
                'updated_at': p.updated_at,
            }
            for p in pairs
        ],
        'frozen_at': stamp,
    }


def _run_record(run):
    return {
        'id': run.id,
        'project_id': run.project_id,
        'name': run.name,
        'status': run.status,
        'snapshot': run.snapshot,
        'checksum': run.checksum,
        'snapshot_revision': run.snapshot_revision,
        'idempotency_key': run.idempotency_key,
        'result': run.result,
        'error': run.error or '',
        'created_at': run.created_at,
        'published_at': run.published_at,
        'published_by': run.published_by,
        'publication': run.publication,
    }


def create_run(db, data, user):
    project_id = data.project_id
    key = (data.idempotency_key or '').strip()
    if not key:
        raise HTTPException(422, '缺少幂等键，无法安全重试')
    # Serialize against concurrent edits: after this lock is granted every
    # roster/interval change waits, so the snapshot below is one point in time.
    project = _project_row(db, project_id, lock=True)
    existing = db.scalar(select(CalibrationRun).where(
        CalibrationRun.project_id == project_id,
        CalibrationRun.idempotency_key == key,
    ))
    if existing:
        # A retry after an interrupted write reuses the frozen snapshot; it
        # never creates a second run or re-reads current calibration data.
        # A run left queued (enqueue failed) or running (worker died) is
        # re-queued so the original snapshot is still computed exactly once.
        if existing.status in ('queued', 'running'):
            _enqueue(existing.id)
        log(db, user, 'calibration.run.retry', f'{project_id}/{existing.id}: 幂等键复用原快照')
        db.commit()
        return _run_record(existing)
    stamp = now()
    snapshot = _snapshot(db, project, stamp)
    if not snapshot['pairs']:
        raise HTTPException(422, '尚无成对偏差区间，无法冻结校准输入')
    run = CalibrationRun(
        project_id=project_id,
        name=data.name.strip(),
        status='queued',
        snapshot=snapshot,
        checksum=checksum(snapshot),
        snapshot_revision=snapshot['revision'],
        idempotency_key=key,
        created_at=stamp,
    )
    db.add(run)
    try:
        db.flush()
    except IntegrityError:
        # A concurrent request with the same key won the unique constraint;
        # its frozen snapshot is the one and only run for this key.
        db.rollback()
        existing = db.scalar(select(CalibrationRun).where(
            CalibrationRun.project_id == project_id,
            CalibrationRun.idempotency_key == key,
        ))
        if not existing:
            raise
        return _run_record(existing)
    log(db, user, 'calibration.run.queue', f'{run.id}: {data.name} / 修订 {snapshot["revision"]}')
    db.commit()
    _enqueue(run.id)
    return _run_record(run)


def get_run(db, run_id):
    run = db.get(CalibrationRun, run_id)
    if not run:
        raise HTTPException(404, '校准运行不存在')
    return _run_record(run)


def list_runs(db, project_id=None):
    stmt = select(CalibrationRun).order_by(CalibrationRun.id.desc())
    if project_id:
        stmt = stmt.where(CalibrationRun.project_id == project_id)
    return [_run_record(x) for x in db.scalars(stmt)]


def publish(db, run_id, data, user):
    run = db.scalar(select(CalibrationRun).where(
        CalibrationRun.id == run_id).with_for_update())
    if not run:
        raise HTTPException(404, '校准运行不存在')
    if run.status != 'completed':
        raise HTTPException(409, '只能发布已完成的校准运行')
    if run.published_at:
        raise HTTPException(409, '已发布，记录不可覆盖')
    result = run.result or {}
    blockers = result.get('publish_blockers') or []
    if not result.get('publish_ready') or blockers:
        raise HTTPException(409, '范围或依据不齐备，不能发布：' + '；'.join(blockers or ['结果未就绪']))
    if checksum(run.snapshot) != run.checksum:
        raise HTTPException(409, '冻结快照校验失败，不能发布')
    stamp = now()
    run.publication = {
        'note': data.note.strip(),
        'scope': (data.scope or '').strip(),
        'author': user.email,
        'created_at': stamp,
        'checksum': run.checksum,
        'snapshot_revision': run.snapshot_revision,
        'baseline': result.get('baseline'),
        'range_count': len([r for r in result.get('ranges', []) if r.get('kind') == 'anchored']),
        'unanchored': [u['instrument'] for u in result.get('unanchored', [])],
    }
    run.published_at = stamp
    run.published_by = user.email
    log(db, user, 'calibration.run.publish', f'{run_id}: {data.note.strip()}')
    db.commit()
    return _run_record(run)


def execute(db, run_id):
    """Compute a queued run from its frozen snapshot exactly once."""
    run = db.get(CalibrationRun, run_id)
    if not run or run.status in ('completed', 'failed'):
        return run
    run.status = 'running'
    db.commit()
    try:
        if checksum(run.snapshot) != run.checksum:
            raise ValueError('输入快照校验失败')
        result = calibration_solver.solve(run.snapshot)
        result['input_sha256'] = run.checksum
        run.result = result
        run.status = 'completed'
    except Exception as exc:
        run.status = 'failed'
        run.error = str(exc)
    db.commit()
    return run


def export(db, run_id, user):
    run = get_run(db, run_id)
    if run['status'] != 'completed' or not run['result']:
        raise HTTPException(409, '尚无完成结果')
    if user.role == 'viewer' and not run['published_at']:
        raise HTTPException(403, '观察员只能导出已发布结果')
    result = run['result']
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(['校准运行', run_id, '输入校验SHA256', run['checksum'], '冻结修订', run['snapshot_revision']])
    writer.writerow(['基准仪器', result.get('baseline', '')])
    writer.writerow([])
    writer.writerow(['仪器', '相对基准下界', '相对基准上界', '宽度', '下界依据类型', '下界依据', '上界依据类型', '上界依据'])
    for row in result.get('ranges', []):
        lower_basis = row.get('lower_basis') or {}
        upper_basis = row.get('upper_basis') or {}
        writer.writerow([
            row['instrument'], row['lower'], row['upper'], row['width'],
            lower_basis.get('kind', ''), lower_basis.get('statement', ''),
            upper_basis.get('kind', ''), upper_basis.get('statement', ''),
        ])
    writer.writerow([])
    writer.writerow(['接不到基准的仪器', '同组仪器', '原因'])
    for item in result.get('unanchored', []):
        writer.writerow([item['instrument'], ' / '.join(item['component']), item['reason']])
    writer.writerow([])
    writer.writerow(['矛盾闭环', '缺口', '闭环'])
    for item in result.get('conflicts', []):
        writer.writerow([', '.join(item['component']), item['residual'], item['statement']])
    log(db, user, 'calibration.run.export', run_id)
    db.commit()
    return '\ufeff' + stream.getvalue()
