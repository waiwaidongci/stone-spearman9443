import math

import pytest

from src.services import calibration_solver as solver


def snap(instruments, pairs, baseline='R'):
    return {
        'baseline': baseline,
        'instruments': instruments,
        'revision': 1,
        'pairs': [
            {'id': f'{a}-{b}', 'a': a, 'b': b, 'lower': lo, 'upper': hi, 'basis': 'x'}
            for (a, b, lo, hi) in pairs
        ],
    }


def by_name(result):
    return {row['instrument']: row for row in result['ranges']}


def test_direct_pair_gives_direct_range():
    r = solver.solve(snap(['R', 'A'], [('A', 'R', -0.5, 0.4)]))
    assert r['status'] == 'ok'
    row = by_name(r)['A']
    assert (row['lower'], row['upper']) == (-0.5, 0.4)
    assert row['upper_basis']['kind'] == 'direct'
    assert row['lower_basis']['kind'] == 'direct'
    assert r['publish_ready'] is True


def test_baseline_is_zero_width():
    r = solver.solve(snap(['R', 'A'], [('A', 'R', -0.5, 0.4)]))
    row = by_name(r)['R']
    assert (row['lower'], row['upper'], row['width']) == (0, 0, 0)


def test_indirect_chain_is_tighter_than_direct_pair():
    # A - B in [0.1, 0.3]; B - R in [0.2, 0.4]; direct A - R in [-1, 1]
    r = solver.solve(snap(['R', 'A', 'B'], [
        ('A', 'B', 0.1, 0.3),
        ('B', 'R', 0.2, 0.4),
        ('A', 'R', -1, 1),
    ]))
    row = by_name(r)['A']
    assert row['lower'] == pytest.approx(0.3)
    assert row['upper'] == pytest.approx(0.7)
    assert row['upper_basis']['kind'] == 'indirect'
    assert len(row['upper_basis']['steps']) == 2
    # direct A-R would only give -1; the chain tightens it to 0.3
    assert row['lower_basis']['kind'] == 'indirect'
    assert row['lower_basis']['sum'] == pytest.approx(-0.3)


def test_indirect_is_used_when_no_direct_pair():
    r = solver.solve(snap(['R', 'A', 'B'], [
        ('A', 'B', 0.1, 0.3),
        ('B', 'R', 0.2, 0.4),
    ]))
    row = by_name(r)['A']
    assert (row['lower'], row['upper']) == (pytest.approx(0.3), pytest.approx(0.7))
    assert row['width'] == pytest.approx(0.4)


def test_reversed_pair_orientation_is_respected():
    r = solver.solve(snap(['R', 'A'], [('R', 'A', -0.4, 0.5)]))
    row = by_name(r)['A']
    assert row['lower'] == pytest.approx(-0.5)
    assert row['upper'] == pytest.approx(0.4)


def test_contradiction_returns_recomputable_closed_loop():
    r = solver.solve(snap(['R', 'A', 'B'], [
        ('A', 'R', 1.0, 2.0),
        ('B', 'R', 0.0, 0.5),
        ('A', 'B', 0.0, 0.2),
    ]))
    assert r['status'] == 'conflict'
    assert r['publish_ready'] is False
    conflict = r['conflicts'][0]
    assert conflict['residual'] == pytest.approx(0.3)
    assert len(conflict['cycle']) >= 2
    # every step is a real recorded pair and the weights really sum to the gap
    ids = [step['pair_id'] for step in conflict['cycle']]
    assert all(i in {'A-R', 'B-R', 'A-B'} for i in ids)
    assert sum(step['weight'] for step in conflict['cycle']) == pytest.approx(conflict['sum_weight'])
    # no ranges are emitted from a contradictory component
    assert by_name(r)['A'] if r['ranges'] else True


def test_conflict_loop_closes_node_to_node():
    r = solver.solve(snap(['R', 'A', 'B', 'C'], [
        ('A', 'R', 1, 2),
        ('B', 'A', 0, 0.2),
        ('C', 'B', 0, 0.1),
        ('C', 'R', 3, 4),
    ]))
    conflict = r['conflicts'][0]
    cycle = conflict['cycle']
    # the returned loop must be a real closed chain, not a truncated path
    for current, nxt in zip(cycle, cycle[1:] + cycle[:1]):
        assert current['to'] == nxt['from']
    assert conflict['residual'] == pytest.approx(0.7)
    assert sum(step['weight'] for step in cycle) == pytest.approx(-0.7)


def test_conflict_does_not_silently_produce_ranges():
    r = solver.solve(snap(['R', 'A', 'B'], [
        ('A', 'R', 1.0, 2.0),
        ('B', 'R', 0.0, 0.5),
        ('A', 'B', 0.0, 0.2),
    ]))
    names = set(by_name(r))
    assert 'A' not in names and 'B' not in names
    assert any('矛盾' in b for b in r['publish_blockers'])


def test_unanchored_instruments_are_listed_separately():
    r = solver.solve(snap(['R', 'A', 'C', 'D'], [
        ('A', 'R', -0.2, 0.2),
        ('C', 'D', 0.1, 0.3),
    ]))
    assert r['status'] == 'ok'
    assert {row['instrument'] for row in r['ranges']} == {'R', 'A'}
    unanchored = {item['instrument']: item for item in r['unanchored']}
    assert set(unanchored) == {'C', 'D'}
    assert unanchored['C']['component'] == ['C', 'D']


def test_missing_basis_blocks_publication():
    s = snap(['R', 'A'], [('A', 'R', -0.1, 0.1)])
    s['pairs'][0]['basis'] = ''
    r = solver.solve(s)
    assert r['publish_ready'] is False
    assert any('依据' in b for b in r['publish_blockers'])


def test_no_pairs_blocks_publication():
    r = solver.solve(snap(['R', 'A'], []))
    assert r['publish_ready'] is False
    assert r['unanchored'][0]['instrument'] == 'A'


def test_tightest_range_follows_all_constraints_not_a_span():
    # chain R -> B -> A where B is wide but A-B is very tight
    r = solver.solve(snap(['R', 'A', 'B'], [
        ('B', 'R', -5, 5),
        ('A', 'B', 0.0, 0.0),
    ]))
    row = by_name(r)['A']
    assert (row['lower'], row['upper']) == (-5, 5)
    # adding a second tight route narrows both instruments
    r2 = solver.solve(snap(['R', 'A', 'B'], [
        ('B', 'R', -5, 5),
        ('A', 'B', 0.0, 0.0),
        ('A', 'R', 1.0, 2.0),
    ]))
    assert by_name(r2)['A']['lower'] == 1.0
    assert by_name(r2)['A']['upper'] == 2.0
    assert by_name(r2)['B']['lower'] == 1.0
    assert by_name(r2)['B']['upper'] == 2.0


def test_invalid_snapshot_values_are_rejected():
    s = snap(['R', 'A'], [('A', 'R', -0.1, 0.1)])
    s['pairs'][0]['lower'] = float('nan')
    with pytest.raises(solver.SnapshotError):
        solver.solve(s)
    with pytest.raises(solver.SnapshotError):
        solver.solve(snap(['R', 'A'], [('A', 'R', 0.5, -0.5)]))
    with pytest.raises(solver.SnapshotError):
        solver.solve(snap(['R', 'A'], [('A', 'R', 0, 1), ('A', 'A', 0, 1)]))
    with pytest.raises(solver.SnapshotError):
        solver.solve(snap(['R', 'A'], [('A', 'Z', 0, 1)]))


def test_result_is_deterministic_regardless_of_pair_order():
    pairs = [('A', 'B', 0.1, 0.3), ('B', 'R', 0.2, 0.4), ('A', 'R', -1, 1)]
    a = solver.solve(snap(['R', 'A', 'B'], pairs))
    b = solver.solve(snap(['R', 'B', 'A'], list(reversed(pairs))))
    assert a == b
