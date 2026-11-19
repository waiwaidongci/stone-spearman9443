"""Tightest per-instrument deviation ranges from pairwise difference intervals.

Model
-----
Let ``d(i)`` be instrument ``i``'s deviation from the baseline instrument, so
``d(baseline) = 0``.  Each recorded pair gives

    lower <= d(a) - d(b) <= upper

which is a system of difference constraints:

    d(a) - d(b) <= upper      (edge b -> a, weight upper)
    d(b) - d(a) <= -lower     (edge a -> b, weight -lower)

For a feasible system the tightest possible range of ``d(i)`` is the projection
of the feasible polytope:

    lower_i = -shortest_path(i -> baseline)
    upper_i =  shortest_path(baseline -> i)

Shortest paths automatically combine direct and indirect calibrations, so the
result is the tightest range implied by *all* entered pairs.  A negative cycle
means the intervals contradict each other; we return a concrete closed loop of
constraints whose weights sum below zero so the conflict can be recomputed by
hand.  Instruments in a component not connected to the baseline have no finite
range relative to it and are reported separately.
"""

import math

EPS = 1e-9


class SnapshotError(ValueError):
    pass


def _number(value, label):
    if isinstance(value, bool) or value is None:
        raise SnapshotError(f'{label}必须是数值')
    try:
        x = float(value)
    except (TypeError, ValueError):
        raise SnapshotError(f'{label}必须是数值')
    if not math.isfinite(x):
        raise SnapshotError(f'{label}必须是有限数值')
    return x


def _rounded(x):
    r = round(float(x), 6)
    return 0.0 if r == 0 else r


def _sort_key(pair):
    return (str(pair.get('id', '')), str(pair.get('a', '')), str(pair.get('b', '')))


def prepare(snapshot):
    """Validate a frozen snapshot and return normalized nodes/pairs."""
    if not isinstance(snapshot, dict):
        raise SnapshotError('快照结构无效')
    baseline = str(snapshot.get('baseline') or '').strip()
    if not baseline:
        raise SnapshotError('缺少基准仪器')
    raw_instruments = snapshot.get('instruments') or []
    if not isinstance(raw_instruments, list):
        raise SnapshotError('仪器清单无效')
    instruments = []
    for name in raw_instruments:
        name = str(name).strip()
        if not name:
            raise SnapshotError('仪器名称不能为空')
        if name not in instruments:
            instruments.append(name)
    if baseline not in instruments:
        instruments.append(baseline)
    instruments = sorted(instruments, key=lambda s: (s != baseline, s))
    known = set(instruments)

    raw_pairs = snapshot.get('pairs') or []
    if not isinstance(raw_pairs, list):
        raise SnapshotError('成对区间清单无效')
    pairs = []
    for index, raw in enumerate(raw_pairs):
        if not isinstance(raw, dict):
            raise SnapshotError(f'第 {index + 1} 条成对区间结构无效')
        a = str(raw.get('a') or '').strip()
        b = str(raw.get('b') or '').strip()
        if not a or not b:
            raise SnapshotError(f'第 {index + 1} 条成对区间缺少仪器名称')
        if a == b:
            raise SnapshotError(f'第 {index + 1} 条成对区间的两台仪器不能相同')
        if a not in known or b not in known:
            raise SnapshotError(f'第 {index + 1} 条成对区间引用了清单外的仪器')
        lower = _number(raw.get('lower'), f'第 {index + 1} 条区间下界')
        upper = _number(raw.get('upper'), f'第 {index + 1} 条区间上界')
        if lower > upper + EPS:
            raise SnapshotError(f'第 {index + 1} 条区间下界大于上界')
        if lower > upper:
            lower = upper
        basis = str(raw.get('basis') or '').strip()
        pairs.append({
            'id': str(raw.get('id') or f'P{index + 1}'),
            'a': a,
            'b': b,
            'lower': lower,
            'upper': upper,
            'basis': basis,
            'updated_at': raw.get('updated_at') or '',
        })
    pairs.sort(key=_sort_key)

    revision = snapshot.get('revision')
    try:
        revision = int(revision)
    except (TypeError, ValueError):
        revision = 0
    return baseline, instruments, pairs, revision


def _graph(pairs):
    """Build constraint edges.

    Each edge is a statement ``d(v) - d(u) <= w``.  ``kind`` records whether the
    bound came from the upper end or the lower end of the recorded interval.
    """
    edges = []
    for pair in pairs:
        edges.append({
            'u': pair['b'], 'v': pair['a'], 'w': pair['upper'],
            'kind': 'upper', 'pair': pair,
            'statement': f"d({pair['a']}) − d({pair['b']}) ≤ {_rounded(pair['upper'])}",
        })
        edges.append({
            'u': pair['a'], 'v': pair['b'], 'w': -pair['lower'],
            'kind': 'lower', 'pair': pair,
            'statement': f"d({pair['b']}) − d({pair['a']}) ≤ {_rounded(-pair['lower'])}",
        })
    return edges


def _adjacency(nodes, edges):
    adj = {name: [] for name in nodes}
    for edge in edges:
        adj[edge['u']].append(edge)
    for name in adj:
        adj[name].sort(key=lambda e: (_sort_key(e['pair']), e['kind']))
    return adj


def _shortest_path(start, adj):
    """Bellman-Ford on a graph known to have no negative cycle."""
    dist = {start: 0.0}
    pred = {}
    nodes = list(adj)
    for _ in range(len(nodes) + 1):
        changed = False
        for u in nodes:
            if u not in dist:
                continue
            for edge in adj[u]:
                nd = dist[u] + edge['w']
                v = edge['v']
                if v not in dist or nd < dist[v] - EPS:
                    dist[v] = nd
                    pred[v] = edge
                    changed = True
        if not changed:
            break
    return dist, pred


def _path_edges(start, target, pred):
    if start == target:
        return []
    edges = []
    cur = target
    seen = set()
    while cur != start:
        if cur in seen or cur not in pred:
            return None
        seen.add(cur)
        edge = pred[cur]
        edges.append(edge)
        cur = edge['u']
    edges.reverse()
    return edges


def _tarjan(nodes, adj):
    index = {}
    low = {}
    on_stack = set()
    stack = []
    result = []
    counter = 0

    def strong(v):
        nonlocal counter
        index[v] = low[v] = counter
        counter += 1
        stack.append(v)
        on_stack.add(v)
        for edge in adj[v]:
            w = edge['v']
            if w not in index:
                strong(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            component = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                component.append(w)
                if w == v:
                    break
            result.append(component)

    for node in nodes:
        if node not in index:
            strong(node)
    return result


def _negative_cycle(component, edges):
    """Return one negative cycle inside an SCC, or None."""
    inside = set(component)
    local = [e for e in edges if e['u'] in inside and e['v'] in inside]
    if not local:
        return None
    dist = {node: 0.0 for node in component}
    pred = {}
    count = len(component)
    for _ in range(count):
        changed = False
        for edge in local:
            nd = dist[edge['u']] + edge['w']
            if nd < dist[edge['v']] - EPS:
                dist[edge['v']] = nd
                pred[edge['v']] = edge
                changed = True
        if not changed:
            return None
    changed_node = None
    for edge in local:
        if dist[edge['u']] + edge['w'] < dist[edge['v']] - EPS:
            changed_node = edge['v']
    if changed_node is None:
        return None
    node = changed_node
    for _ in range(count):
        if node not in pred:
            return None
        node = pred[node]['u']
    # ``node`` lies on a negative cycle.  Follow predecessors until a node
    # repeats, then keep the loop itself (it may not start at ``node``).
    walk = []
    index = {}
    cur = node
    while cur not in index:
        index[cur] = len(walk)
        walk.append(cur)
        if cur not in pred:
            return None
        cur = pred[cur]['u']
    loop_nodes = walk[index[cur]:]
    cycle = [pred[n] for n in loop_nodes]
    cycle.reverse()
    # cycle[i] has v == loop_nodes[i]; orient as a single chain
    ordered = []
    by_from = {e['u']: e for e in cycle}
    start = loop_nodes[0]
    cur = start
    for _ in range(len(loop_nodes)):
        edge = by_from.get(cur)
        if edge is None:
            return None
        ordered.append(edge)
        cur = edge['v']
    if cur != start:
        return None
    total = sum(e['w'] for e in ordered)
    if total >= -EPS:
        return None
    return ordered


def _edge_step(edge):
    pair = edge['pair']
    if edge['kind'] == 'upper':
        source = f"{pair['a']} − {pair['b']} ≤ {_rounded(pair['upper'])}"
        bound = f"{pair['a']} − {pair['b']} ∈ [{_rounded(pair['lower'])}, {_rounded(pair['upper'])}]"
    else:
        source = f"{pair['a']} − {pair['b']} ≥ {_rounded(pair['lower'])}"
        bound = f"{pair['a']} − {pair['b']} ∈ [{_rounded(pair['lower'])}, {_rounded(pair['upper'])}]"
    return {
        'pair_id': pair['id'],
        'statement': edge['statement'],
        'source': source,
        'interval': bound,
        'basis': pair['basis'],
        'from': edge['u'],
        'to': edge['v'],
        'weight': _rounded(edge['w']),
    }


def _finite(value):
    return value is not None and math.isfinite(value)


def solve(snapshot):
    baseline, instruments, pairs, revision = prepare(snapshot)
    edges = _graph(pairs)
    adj = _adjacency(instruments, edges)
    components = _tarjan(instruments, adj)

    conflicts = []
    conflicting_nodes = set()
    for comp in components:
        cycle = _negative_cycle(comp, edges)
        if not cycle:
            continue
        conflicting_nodes.update(comp)
        steps = [_edge_step(e) for e in cycle]
        total = sum(e['w'] for e in cycle)
        conflicts.append({
            'component': sorted(comp, key=lambda s: (s != baseline, s)),
            'cycle': steps,
            'sum_weight': _rounded(total),
            'residual': _rounded(-total),
            'statement': (
                '闭环同向相加：' + ' + '.join(f"({s['statement']})" for s in steps)
                + f" ⇒ 0 ≤ {_rounded(total)}，但 0 ≤ {_rounded(total)} 不成立，缺口 {_rounded(-total)}"
            ),
            'check': (
                '复核方法：把闭环每一段按箭头方向写成“左 − 右 ≤ 上界”，同向相加后'
                '左边相邻项两两抵消为 0，右边之和即闭环保留的缺口；缺口大于 0 说明区间互相矛盾。'
            ),
        })

    forward, forward_pred = _shortest_path(baseline, adj)
    # Lower bounds need shortest paths *into* the baseline, i.e. d(baseline) −
    # d(i) ≤ … .  Searching the edge-reversed graph from the baseline finds
    # those; each reversed edge keeps a pointer to the original constraint so
    # the emitted explanation is still stated in the recorded orientation.
    reverse_adj = {name: [] for name in instruments}
    for edge in edges:
        reverse_adj[edge['v']].append({
            'u': edge['v'], 'v': edge['u'], 'w': edge['w'],
            'kind': edge['kind'], 'pair': edge['pair'],
            'statement': edge['statement'], 'orig': edge,
        })
    for name in reverse_adj:
        reverse_adj[name].sort(key=lambda e: (_sort_key(e['pair']), e['kind']))
    backward, backward_pred = _shortest_path(baseline, reverse_adj)

    anchored = [n for n in instruments if n in forward and n in backward]

    # Weakly connected components, for labelling unanchored instruments.
    parent = {n: n for n in instruments}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx

    for edge in edges:
        union(edge['u'], edge['v'])
    groups = {}
    for node in instruments:
        groups.setdefault(find(node), []).append(node)

    unanchored = []
    for node in instruments:
        if node in anchored:
            continue
        group = sorted(groups[find(node)], key=lambda s: (s != baseline, s))
        unanchored.append({
            'instrument': node,
            'component': group,
            'reason': '该组仪器没有与基准仪器相连的成对区间，无法相对基准给出范围',
        })

    anchored_conflict = bool(conflicting_nodes & set(anchored))
    ranges = []
    if not anchored_conflict:
        for node in anchored:
            if node == baseline:
                ranges.append({
                    'instrument': node,
                    'lower': 0.0,
                    'upper': 0.0,
                    'width': 0.0,
                    'kind': 'baseline',
                    'lower_basis': {'kind': 'baseline', 'steps': [], 'statement': '基准仪器，偏差定义为 0'},
                    'upper_basis': {'kind': 'baseline', 'steps': [], 'statement': '基准仪器，偏差定义为 0'},
                })
                continue
            upper = forward[node]
            lower = -backward[node]
            if lower > upper and lower - upper <= EPS:
                mid = (lower + upper) / 2
                lower = upper = mid
            upper_path = _path_edges(baseline, node, forward_pred) or []
            lower_path = _path_edges(baseline, node, backward_pred) or []
            upper_steps = [_edge_step(e) for e in upper_path]
            # Reversed-graph edges are walked baseline→node; the matching
            # original constraints are summed node→baseline.
            lower_steps = [_edge_step(e['orig']) for e in reversed(lower_path)]
            ranges.append({
                'instrument': node,
                'lower': _rounded(lower),
                'upper': _rounded(upper),
                'width': _rounded(upper - lower),
                'kind': 'anchored',
                'upper_basis': {
                    'kind': 'direct' if len(upper_steps) == 1 else 'indirect',
                    'steps': upper_steps,
                    'sum': _rounded(upper),
                    'statement': (
                        '直接区间' if len(upper_steps) == 1 else f'{len(upper_steps)} 条区间相加'
                    ) + f"，得 d({node}) − d({baseline}) ≤ {_rounded(upper)}",
                },
                'lower_basis': {
                    'kind': 'direct' if len(lower_steps) == 1 else 'indirect',
                    'steps': lower_steps,
                    'sum': _rounded(-lower),
                    'statement': (
                        '直接区间' if len(lower_steps) == 1 else f'{len(lower_steps)} 条区间相加'
                    ) + f"，由 d({baseline}) − d({node}) ≤ {_rounded(-lower)} 得下界 {_rounded(lower)}",
                },
            })

    blockers = []
    if conflicts:
        blockers.append('存在约束冲突：' + '；'.join(
            f"闭环 {', '.join(c['component'])} 缺口 {c['residual']}" for c in conflicts))
    if anchored_conflict:
        blockers.append('基准所在连通分量存在矛盾，无法形成相对基准的范围')
    if not pairs:
        blockers.append('尚无成对偏差区间，没有可发布的校准范围')
    if unanchored:
        blockers.append('以下仪器接不到基准，范围不齐备：' + '、'.join(
            f"{u['instrument']}（同组 {'/'.join(u['component'])}）" for u in unanchored))
    missing_basis = [p['id'] for p in pairs if not p['basis']]
    if missing_basis:
        blockers.append('以下成对区间缺少校准依据：' + ', '.join(missing_basis))
    if not anchored_conflict and len(ranges) != len(anchored):
        blockers.append('部分锚定仪器缺少完整上下界')
    for item in ranges:
        if item['kind'] == 'anchored':
            if not _finite(item['lower']) or not _finite(item['upper']):
                blockers.append(f"{item['instrument']} 的边界不是有限数")
            if not item['upper_basis']['steps'] or not item['lower_basis']['steps']:
                blockers.append(f"{item['instrument']} 的边界依据链不完整")

    publish_ready = not blockers
    status = 'conflict' if conflicts else 'ok'

    direct_pairs = sum(1 for p in pairs if p['a'] == baseline or p['b'] == baseline)
    metrics = {
        '仪器总数': len(instruments),
        '成对区间数': len(pairs),
        '锚定仪器数': len(anchored),
        '接不到基准的仪器数': len(unanchored),
        '直接区间数': direct_pairs,
        '间接区间数': len(pairs) - direct_pairs,
        '矛盾闭环数': len(conflicts),
        '快照修订号': revision,
    }

    return {
        'schema': 'calibration-range-v1',
        'status': status,
        'baseline': baseline,
        'revision': revision,
        'instrument_count': len(instruments),
        'ranges': ranges,
        'unanchored': unanchored,
        'conflicts': conflicts,
        'metrics': metrics,
        'publish_ready': publish_ready,
        'publish_blockers': blockers,
        'caveat': (
            '范围是全部成对区间（直接与间接）共同约束下、该仪器相对基准偏移的可行投影，'
            '即最紧范围；它不是统计置信区间。基准仪器自身偏移定义为 0。'
        ),
        'interpretation': (
            f"以 {baseline} 为基准，{len(anchored)} 台仪器可锚定、"
            f"{len(unanchored)} 台接不到基准、{len(conflicts)} 个矛盾闭环。"
        ),
    }
