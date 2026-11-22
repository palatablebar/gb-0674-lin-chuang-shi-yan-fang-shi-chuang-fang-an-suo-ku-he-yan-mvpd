"""澄清问题裁决。

锁库前未关闭的澄清问题分两类：
- key：质疑访视日期、检验值、单位或版本等关键事实，可能改变方案选版、
  访视窗、偏离与复检结论；未核实前必须阻断冻结。
- format：仅备注等格式待办，可在标注后随批冻结。

同一状态由 ``digest`` 统一标识，供方案、窗口、偏离、复检、锁库清单与
冻结凭证共同引用。原始提问、全部回复与更正均只追加，不覆盖；同一回复
重试得到同一摘要，不会新增处理。
"""
import hashlib
import json

from app.errors import BusinessError

FORMAT_FIELDS = {'remark', 'note', 'format', 'annotation', 'comment'}
ALLOWED_CORRECTIONS = {'at', 'value', 'unit', 'revision'}
VALID_STATUS = {'open', 'closed'}
VALID_KINDS = {'key', 'format'}
IDENTITY_FIELDS = ('field', 'kind', 'status', 'question')


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _canonical_queries(queries):
    """按问题编号归并，回复按作者+正文去重，使同一回复重试保持同一摘要。"""
    grouped = {}
    for query in queries or []:
        if not isinstance(query, dict) or not str(query.get('id', '') or '').strip():
            continue
        query_id = str(query['id']).strip()
        item = grouped.setdefault(query_id, {
            'id': query_id,
            'record_id': str(query.get('record_id', '') or ''),
            'subject': str(query.get('subject', '') or ''),
            'visit': str(query.get('visit', '') or ''),
            'field': str(query.get('field', '') or ''),
            'kind': str(query.get('kind', '') or ''),
            'status': str(query.get('status', 'open') or 'open'),
            'questions': set(),
            'replies': set(),
        })
        item['record_id'] = item['record_id'] or str(query.get('record_id', '') or '')
        item['subject'] = item['subject'] or str(query.get('subject', '') or '')
        item['visit'] = item['visit'] or str(query.get('visit', '') or '')
        item['field'] = item['field'] or str(query.get('field', '') or '')
        item['kind'] = item['kind'] or str(query.get('kind', '') or '')
        item['questions'].add(str(query.get('question', '') or ''))
        for reply in query.get('replies') or []:
            if isinstance(reply, dict):
                item['replies'].add(_json({'author': str(reply.get('author', '') or ''), 'text': str(reply.get('text', '') or '')}))
            else:
                item['replies'].add(_json({'author': '', 'text': str(reply)}))
    facts = []
    for query_id in sorted(grouped):
        item = grouped[query_id]
        facts.append({
            'id': item['id'],
            'record_id': item['record_id'],
            'subject': item['subject'],
            'visit': item['visit'],
            'field': item['field'],
            'kind': item['kind'],
            'status': item['status'],
            'questions': sorted(item['questions']),
            'replies': sorted(item['replies']),
        })
    return facts


def query_facts(queries, corrections=None):
    return {'queries': _canonical_queries(queries), 'corrections': corrections or {}}


def digest(queries, corrections=None):
    return hashlib.sha256(_json(query_facts(queries, corrections)).encode()).hexdigest()


def append_reply(queries, query_id, reply, close=False):
    """追加回复；相同作者与正文的重试不新增记录。返回 (新列表, 是否重复)。"""
    query_id = str(query_id or '').strip()
    text = str((reply or {}).get('text', '') or '').strip()
    author = str((reply or {}).get('author', '') or '').strip()
    if not query_id:
        raise BusinessError('BAD_QUERY_ID', '澄清问题编号不能为空')
    if not text:
        raise BusinessError('EMPTY_REPLY', '回复内容不能为空')
    matches = [query for query in queries if str(query.get('id', '') or '').strip() == query_id]
    if not matches:
        raise BusinessError('QUERY_NOT_FOUND', '澄清问题不存在', 404)

    duplicate = any(
        str(existing.get('text', '') or '').strip() == text and str(existing.get('author', '') or '').strip() == author
        for query in matches for existing in (query.get('replies') or [])
    )
    updated = [dict(query) for query in queries]
    if not duplicate:
        entry = {'text': text, 'author': author, 'at': (reply or {}).get('at', '')}
        for query in updated:
            if str(query.get('id', '') or '').strip() == query_id:
                query['replies'] = list(query.get('replies') or []) + [entry]
                break
    if close:
        for query in updated:
            if str(query.get('id', '') or '').strip() == query_id:
                query['status'] = 'closed'
    return updated, duplicate


def apply_corrections(state):
    """把已关闭澄清中的更正叠加到采信记录上；失败则整批重算失败，旧批保留。"""
    corrections = state.get('corrections') or {}
    by_id = {}
    for record in state.get('records') or []:
        by_id.setdefault(record.get('id'), []).append(record)

    authorized = {}
    for query in state['clarify']['queries']:
        if query['status'] == 'closed' and query['kind'] == 'key' and query['freeze'] == '已关闭':
            for record_id in query['record_ids']:
                authorized.setdefault(record_id, set()).add(query['id'])

    applied = []
    for record_id in sorted(corrections):
        fields = corrections[record_id]
        if not isinstance(fields, dict) or not fields:
            raise BusinessError('BAD_CORRECTION', f'记录 {record_id} 的澄清更正格式无效')
        if not authorized.get(record_id):
            raise BusinessError('CORRECTION_UNAUTHORIZED', f'记录 {record_id} 没有已关闭的关键澄清，不能应用更正')
        matches = by_id.get(record_id, [])
        if len(matches) != 1:
            raise BusinessError('CORRECTION_TARGET_MISSING', f'记录 {record_id} 不在采信结果中，无法应用澄清更正')
        for field in sorted(fields):
            value = fields[field]
            if field not in ALLOWED_CORRECTIONS:
                raise BusinessError('BAD_CORRECTION', f'字段 {field} 不允许通过澄清更正')
            if str(matches[0].get(field)) == str(value):
                continue
            matches[0][field] = value
            for view in (state.get('views') or {}).values():
                for view_row in view.get('rows') or []:
                    if isinstance(view_row, dict) and view_row.get('id') == record_id:
                        view_row[field] = value
            applied.append({'record_id': record_id, 'field': field, 'value': value, 'query_id': sorted(authorized[record_id])[0]})
    state['corrections_applied'] = applied


def _visit_key(subject, visit):
    return f'{subject}|{visit}'


def _entry(query):
    return {
        'id': query['id'], 'freeze': query['freeze'], 'code': query['code'],
        'kind': query['kind'], 'field': query['field'], 'status': query['status'],
        'reason': query['reason'],
    }


def _targets(query, records, quarantined):
    record_id = str(query.get('record_id', '') or '').strip()
    subject = str(query.get('subject', '') or '').strip()
    visit = str(query.get('visit', '') or '').strip()
    if record_id:
        if record_id in records:
            return [record_id], None
        if record_id in quarantined:
            return [], ('QUERY_TARGET_QUARANTINED', '澄清目标已被采信隔离，关键事实未核实')
        return [], ('QUERY_TARGET_MISSING', '澄清目标记录不存在，无法核实')
    if subject:
        matches = [r for r in records.values() if r.get('subject') == subject and (not visit or r.get('visit') == visit)]
        if matches:
            return sorted(r['id'] for r in matches), None
        return [], ('QUERY_TARGET_MISSING', '澄清目标访视不存在，无法核实')
    return [], ('QUERY_TARGET_MISSING', '澄清问题缺少目标记录或受试者访视')


def _build_query(query_id, variants, records, quarantined):
    base = dict(variants[0])
    replies = []
    for variant in variants:
        for reply in variant.get('replies') or []:
            if reply not in replies:
                replies.append(reply)
    status = str(base.get('status', 'open') or 'open').strip()
    field = str(base.get('field', '') or '').strip()
    kind = str(base.get('kind', '') or '').strip() or ('format' if field in FORMAT_FIELDS else 'key')
    question = str(base.get('question', '') or '').strip()

    conflicts = []
    distinct = {_json({k: variant.get(k) for k in IDENTITY_FIELDS}) for variant in variants}
    if len(distinct) > 1:
        conflicts.append(('QUERY_STATUS_CONFLICT', '同一编号的澄清问题状态或内容冲突'))
    if status not in VALID_STATUS:
        conflicts.append(('QUERY_STATUS_CONFLICT', '澄清问题状态无效'))
    if kind not in VALID_KINDS:
        conflicts.append(('QUERY_STATUS_CONFLICT', '澄清问题级别无效'))
    expected_kind = 'format' if field in FORMAT_FIELDS else 'key'
    if kind in VALID_KINDS and kind != expected_kind:
        conflicts.append(('QUERY_STATUS_CONFLICT', '问题级别与所涉字段不匹配'))
    if status == 'closed' and not replies:
        conflicts.append(('QUERY_STATUS_CONFLICT', '问题已关闭但缺少原始回复记录'))
    targets, target_issue = _targets(base, records, quarantined)
    if target_issue:
        conflicts.append(target_issue)

    if conflicts:
        freeze, code = '阻断', conflicts[0][0]
        reason = '；'.join(dict.fromkeys(text for _, text in conflicts))
    elif status == 'open' and expected_kind == 'key':
        freeze, code = '阻断', 'KEY_FACT_UNVERIFIED'
        reason = '关键事实未核实，须关闭澄清后才能冻结'
    elif status == 'open':
        freeze, code = '随批冻结', 'FORMAT_PENDING'
        reason = '仅备注格式待办，可随批冻结'
    else:
        freeze, code = '已关闭', 'CLOSED'
        reason = '澄清问题已关闭'

    subject = str(base.get('subject', '') or '').strip()
    visit = str(base.get('visit', '') or '').strip()
    if not subject or not visit:
        for record_id in targets:
            record = records.get(record_id) or {}
            subject = subject or str(record.get('subject', ''))
            visit = visit or str(record.get('visit', ''))
            if subject and visit:
                break

    return {
        'id': query_id,
        'target': ','.join(targets) or (subject + ('/' + visit if visit else '')),
        'record_ids': targets,
        'subject': subject,
        'visit': visit,
        'field': field,
        'kind': kind,
        'status': status,
        'question': question,
        'replies': replies,
        'variants': [{k: variant.get(k) for k in IDENTITY_FIELDS} for variant in variants]
        if len(distinct) > 1 else [],
        'freeze': freeze,
        'code': code,
        'conflicts': list(dict.fromkeys(code for code, _ in conflicts)),
        'reason': reason,
    }


def adjudicate(state):
    """计算逐条澄清裁决与统一摘要。可在任意模块入口重复调用。"""
    raw_queries = state.get('query_input') or []
    corrections = state.get('corrections') or {}
    records = {r['id']: r for r in state.get('records') or []}
    quarantined = {q.get('id') for q in state.get('quarantine') or []}

    groups, order, invalid = {}, [], []
    for index, query in enumerate(raw_queries):
        if not isinstance(query, dict) or not str(query.get('id', '') or '').strip():
            invalid.append({
                'id': f'INVALID-{index + 1}', 'target': '', 'field': '', 'kind': 'key',
                'status': 'open', 'question': _json(query)[:300], 'replies': [],
                'record_ids': [], 'subject': '', 'visit': '',
                'freeze': '阻断', 'code': 'QUERY_INVALID', 'conflicts': ['QUERY_INVALID'],
                'reason': '澄清记录格式无效，无法核实', 'variants': [],
            })
            continue
        query_id = str(query['id']).strip()
        groups.setdefault(query_id, []).append(query)
        if query_id not in order:
            order.append(query_id)

    queries = [_build_query(query_id, groups[query_id], records, quarantined) for query_id in order]

    by_record, by_subject_visit = {}, {}
    for query in queries + invalid:
        entry = _entry(query)
        for record_id in query['record_ids']:
            by_record.setdefault(record_id, []).append(entry)
        if query.get('subject') and query.get('visit'):
            by_subject_visit.setdefault(_visit_key(query['subject'], query['visit']), []).append(entry)

    blocking = [q for q in queries + invalid if q['freeze'] == '阻断']
    with_batch = [q for q in queries + invalid if q['freeze'] == '随批冻结']
    status_value = '待处理' if blocking else ('可随批冻结' if with_batch else '已澄清')

    state['clarify'] = {
        'queries': queries + invalid,
        'by_record': by_record,
        'by_subject_visit': by_subject_visit,
        'blocking': [q['id'] for q in blocking],
        'with_batch': [q['id'] for q in with_batch],
        'status': status_value,
        'digest': digest(raw_queries, corrections),
        'corrections': corrections,
    }
    return state['clarify']


def annotate_row(row, clarify):
    """把同一澄清状态投影到任意结论行上。"""
    ids = {row.get('id'), row.get('primary_id'), row.get('parent')}
    ids.discard('')
    entries = []
    for record_id in ids:
        entries.extend(clarify['by_record'].get(record_id, []))
    if row.get('subject') and row.get('visit'):
        entries.extend(clarify['by_subject_visit'].get(_visit_key(row['subject'], row['visit']), []))
    unique = {}
    for entry in entries:
        unique[(entry['id'], entry['code'], entry['freeze'])] = entry
    values = list(unique.values())
    row['clarify_digest'] = clarify['digest']
    if not values:
        # 没有关联问题时也带摘要，保证同一状态可追溯；不伪造澄清结论。
        return
    blocked = any(entry['freeze'] == '阻断' for entry in values)
    batch = any(entry['freeze'] == '随批冻结' for entry in values)
    row['clarify_status'] = '阻断' if blocked else ('随批冻结' if batch else '已关闭')
    row['clarify_queries'] = ','.join(sorted({entry['id'] for entry in values}))
    if blocked:
        row['clarify_reason'] = '；'.join(sorted({entry['code'] for entry in values if entry['freeze'] == '阻断'}))
    elif batch:
        row['clarify_reason'] = '格式待办'
    else:
        row['clarify_reason'] = '已澄清'


def _annotate_rows(rows, clarify):
    for row in rows or []:
        if isinstance(row, dict):
            annotate_row(row, clarify)


def _visit_rows(state, clarify):
    """每条访视一个冻结状态，带关键问题与所有阻断原因。"""
    context = state.get('context') or {}
    subjects = list(context.get('subjects') or {})
    visits = list(context.get('required_visits') or [])
    completed = {(row.get('subject'), row.get('visit')) for row in state.get('completed') or []}
    windows = {}
    for row in state.get('windows') or []:
        if row.get('kind') == 'PRIMARY':
            windows.setdefault((row.get('subject'), row.get('visit')), row)
    deviation_by_key = {}
    for row in state.get('deviations') or []:
        deviation_by_key.setdefault((row.get('subject'), row.get('visit')), []).append(row.get('code', ''))
    retest_by_parent = {}
    for row in state.get('retest_issues') or []:
        retest_by_parent.setdefault(row.get('parent', ''), []).append(row.get('code', ''))

    rows = []
    for subject in subjects:
        for visit in visits:
            key = (subject, visit)
            entries = clarify['by_subject_visit'].get(_visit_key(subject, visit), [])
            key_entries = [e for e in entries if e['kind'] == 'key']
            blocking_entries = [e for e in entries if e['freeze'] == '阻断']
            window = windows.get(key) or {}
            reasons = list(deviation_by_key.get(key, []))
            if window.get('id'):
                reasons.extend(retest_by_parent.get(window['id'], []))
            reasons.extend(e['code'] for e in blocking_entries)
            reasons = list(dict.fromkeys(reason for reason in reasons if reason))

            if reasons:
                freeze, state_text = '阻断', '阻断'
                clarify_state = '澄清阻断' if blocking_entries else '数据阻断'
            elif any(e['freeze'] == '随批冻结' for e in entries):
                freeze, state_text, clarify_state = '随批冻结', '可随批冻结', '可随批冻结'
            elif key in completed:
                freeze, state_text, clarify_state = '', '可锁库', '无未关闭问题'
            elif entries:
                freeze, state_text, clarify_state = '已关闭', '待查', '已澄清'
            else:
                freeze, state_text, clarify_state = '', '待查', '无未关闭问题'

            rows.append({
                'subject': subject,
                'visit': visit,
                'freeze': freeze,
                'state': state_text,
                'clarify_state': clarify_state,
                'key_queries': ','.join(sorted({e['id'] for e in key_entries})),
                'query_ids': ','.join(sorted({e['id'] for e in entries})),
                'blocking_reasons': ','.join(reasons),
                'window_code': window.get('code', ''),
                'protocol': window.get('selected_protocol', ''),
                'completed': '是' if key in completed else '否',
            })

    rows.sort(key=lambda row: (0 if row['state'] == '阻断' else 1, row['subject'], row['visit']))
    return rows


def refresh(state):
    """重算澄清裁决并同步所有结论行、页面视图与锁库清单。"""
    clarify = adjudicate(state)
    for key in ('windows', 'deviations', 'selected_results', 'retest_issues'):
        _annotate_rows(state.get(key), clarify)
    views = state.get('views') or {}
    for view in views.values():
        if isinstance(view.get('rows'), list):
            _annotate_rows(view['rows'], clarify)

    balance = state.get('balance')
    if balance:
        balance['clarify_digest'] = clarify['digest']
        balance['clarify_status'] = clarify['status']
        balance['clarify_blocked'] = len(clarify['blocking'])
        balance['with_batch'] = len(clarify['with_batch'])
    archive = state.get('archive')
    if archive:
        archive['clarify_digest'] = clarify['digest']
        archive['clarify_status'] = clarify['status']
        archive['clarify_blocked'] = len(clarify['blocking'])
        archive['with_batch'] = len(clarify['with_batch'])
    manifest = state.get('manifest')
    if manifest:
        manifest['clarify_digest'] = clarify['digest']
        manifest['clarify_status'] = clarify['status']

    visits = _visit_rows(state, clarify)
    clarify['visits'] = visits
    by_visit = {f"{row['subject']}|{row['visit']}": row for row in visits}
    clarify['visit_map'] = by_visit
    blocking_visits = [row for row in visits if row['state'] == '阻断']

    if not balance:
        if blocking_visits or clarify['blocking']:
            lock_status = '待处理'
        elif clarify['with_batch']:
            lock_status = '可随批冻结'
        else:
            lock_status = '计算中'
    elif balance.get('status') == '待处理' or blocking_visits or clarify['blocking']:
        lock_status = '待处理'
    elif clarify['with_batch']:
        lock_status = '可随批冻结'
    else:
        lock_status = '可锁库'

    state['lockable'] = {
        'digest': clarify['digest'],
        'clarify_status': clarify['status'],
        'status': lock_status,
        'visits': visits,
        'visit_map': by_visit,
        'blocking_visits': blocking_visits,
        'blocking_queries': [q for q in clarify['queries'] if q['freeze'] == '阻断'],
        'with_batch_queries': [q for q in clarify['queries'] if q['freeze'] == '随批冻结'],
        'blocking_items': _blocking_items(state, clarify),
        'corrections_applied': state.get('corrections_applied') or [],
        'summary': {
            'visits': len(visits),
            'blocked': len(blocking_visits),
            'query_blocked': len(clarify['blocking']),
            'query_with_batch': len(clarify['with_batch']),
        },
    }
    state['views']['clarify'] = {
        'title': '澄清裁决',
        'queries': clarify['queries'],
        'visits': visits,
        'lockable': state['lockable'],
    }
    return clarify


def _blocking_items(state, clarify):
    items = []
    for row in state.get('deviations') or []:
        items.append({'scope': '偏离', 'subject': row.get('subject', ''), 'visit': row.get('visit', ''),
                      'record': row.get('id', ''), 'reason': row.get('code', ''),
                      'clarify_digest': clarify['digest'], 'clarify_status': row.get('clarify_status', '')})
    for row in state.get('retest_issues') or []:
        items.append({'scope': '复检', 'subject': '', 'visit': '', 'record': row.get('id', ''),
                      'reason': row.get('code', ''), 'clarify_digest': clarify['digest'],
                      'clarify_status': row.get('clarify_status', '')})
    for row in state.get('quarantine') or []:
        items.append({'scope': '采信', 'subject': '', 'visit': '', 'record': row.get('id', ''),
                      'reason': row.get('code', ''), 'clarify_digest': clarify['digest'], 'clarify_status': ''})
    for query in clarify['queries']:
        if query['freeze'] == '阻断':
            items.append({'scope': '澄清', 'subject': query.get('subject', ''), 'visit': query.get('visit', ''),
                          'record': query.get('target', ''), 'reason': query['code'],
                          'clarify_digest': clarify['digest'], 'clarify_status': '阻断'})
    return items
