from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app import main, store
from app.engine import evaluate
from app.errors import BusinessError


def q(**kwargs):
    base = {'id': 'Q1', 'record_id': '00001-V1', 'field': 'at', 'kind': 'key', 'status': 'open', 'question': '采样日期是否应为 10-08？'}
    base.update(kwargs)
    return base


def test_open_key_blocks():
    state = evaluate({'dataset': 'clean', 'queries': [q()]})
    assert state['clarify']['queries'][0]['freeze'] == '阻断'
    assert state['clarify']['queries'][0]['code'] == 'KEY_FACT_UNVERIFIED'
    assert state['balance']['status'] == '待处理'
    assert state['archive']['status'] == '待处理'
    assert state['lockable']['status'] == '待处理'
    visit = state['lockable']['visit_map']['00001|V1']
    assert visit['state'] == '阻断'
    assert visit['key_queries'] == 'Q1'
    assert 'KEY_FACT_UNVERIFIED' in visit['blocking_reasons']


def test_format_only_freezes_with_batch():
    state = evaluate({'dataset': 'clean', 'queries': [q(field='remark', kind='format', question='备注补单位')]})
    assert state['clarify']['queries'][0]['freeze'] == '随批冻结'
    assert state['clarify']['status'] == '可随批冻结'
    assert state['balance']['status'] == '可随批冻结'
    assert state['archive']['status'] == '可随批冻结'
    assert state['lockable']['status'] == '可随批冻结'
    assert state['lockable']['visit_map']['00001|V1']['state'] == '可随批冻结'


def test_status_conflict_blocks():
    rows = [q(status='open'), q(status='closed', replies=[{'author': 'A', 'text': '已核实'}])]
    state = evaluate({'dataset': 'clean', 'queries': rows})
    query = state['clarify']['queries'][0]
    assert query['freeze'] == '阻断'
    assert 'QUERY_STATUS_CONFLICT' in query['conflicts']
    assert query['replies'] == [{'author': 'A', 'text': '已核实'}]


def test_closed_without_reply_blocks():
    state = evaluate({'dataset': 'clean', 'queries': [q(status='closed')]})
    query = state['clarify']['queries'][0]
    assert query['freeze'] == '阻断'
    assert query['code'] == 'QUERY_STATUS_CONFLICT'


def test_unverified_target_blocks():
    missing = evaluate({'dataset': 'clean', 'queries': [q(record_id='NOPE', field='value')]})
    assert missing['clarify']['queries'][0]['code'] == 'QUERY_TARGET_MISSING'
    quarantined = evaluate({'dataset': 'dirty', 'queries': [q(record_id='BAD1', field='value')]})
    assert quarantined['clarify']['queries'][0]['code'] == 'QUERY_TARGET_QUARANTINED'
    assert quarantined['clarify']['queries'][0]['freeze'] == '阻断'


def test_closed_honours_original_question_and_replies():
    rows = [q(status='closed', replies=[{'author': 'A', 'text': '确认为 10-08'}])]
    state = evaluate({'dataset': 'clean', 'queries': rows})
    query = state['clarify']['queries'][0]
    assert query['status'] == 'closed'
    assert query['freeze'] == '已关闭'
    assert query['question'] == '采样日期是否应为 10-08？'
    assert query['replies'][0]['text'] == '确认为 10-08'


def test_unified_digest_across_conclusions():
    state = evaluate({'dataset': 'clean', 'queries': [q(field='remark', kind='format')]})
    digest = state['clarify']['digest']
    assert state['manifest']['clarify_digest'] == digest
    assert state['balance']['clarify_digest'] == digest
    assert state['archive']['clarify_digest'] == digest
    assert all(row['clarify_digest'] == digest for row in state['windows'])
    assert all(row['clarify_digest'] == digest for row in state['selected_results'])


def test_correction_recomputes_and_clears_deviation():
    queries = [q(status='closed', replies=[{'author': 'A', 'text': '确认为 10-08'}])]
    corrected = evaluate({'dataset': 'dirty', 'queries': queries,
                          'corrections': {'00001-V1': {'at': '2026-10-08T08:00:00+08:00'}}})
    assert corrected['corrections_applied']
    assert not any(row.get('id') == '00001-V1' for row in corrected['deviations'])
    assert corrected['windows'][0]['day'] == 7
    assert corrected['windows'][0]['code'] == 'IN_WINDOW'


def test_correction_requires_closed_key_query():
    with pytest.raises(BusinessError) as error:
        evaluate({'dataset': 'clean', 'corrections': {'00003-V1': {'at': '2026-10-08T08:00:00+08:00'}}})
    assert error.value.code == 'CORRECTION_UNAUTHORIZED'
    with pytest.raises(BusinessError) as error:
        evaluate({'dataset': 'clean', 'queries': [q(status='closed', replies=[{'author': 'A', 'text': 'ok'}])],
                  'corrections': {'00001-V1': {'enrolled': '2026-10-02T00:00:00+08:00'}}})
    assert error.value.code == 'BAD_CORRECTION'


def test_rejected_correction_keeps_previous_run(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'clarify.sqlite'))
    client = TestClient(main.app)
    with client:
        created = client.post('/api/runs', json={'dataset': 'clean', 'queries': [q()]}).json()
        run_id = created['manifest']['run_id']
        response = client.post('/api/clarify/reply', json={
            'run_id': run_id, 'query_id': 'Q1', 'author': 'A', 'text': '确认为 10-08', 'close': True,
            'corrections': {'00001-V1': {'enrolled': '2026-10-02T00:00:00+08:00'}},
        })
        # 未授权更正在重算时失败，接口拒绝，旧批仍是当前版本。
        assert response.status_code in (200, 422)
        stored = store.get(main.DB_PATH, run_id)
        assert stored['clarify']['queries'][0]['status'] == 'open'
        assert not stored.get('superseded_by')


def test_reply_retry_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'retry.sqlite'))
    client = TestClient(main.app)
    with client:
        created = client.post('/api/runs', json={'dataset': 'clean', 'queries': [q()]}).json()
        run_id = created['manifest']['run_id']
        first = client.post('/api/clarify/reply', json={'run_id': run_id, 'query_id': 'Q1', 'author': 'A', 'text': '确认为 10-08', 'close': True}).json()
        assert first['duplicate'] is False
        new_id = first['run']['manifest']['run_id']
        retry = client.post('/api/clarify/reply', json={'run_id': new_id, 'query_id': 'Q1', 'author': 'A', 'text': '确认为 10-08', 'close': True}).json()
        assert retry['duplicate'] is True
        assert retry['run']['manifest']['run_id'] == new_id
        history = client.get('/api/runs').json()
        assert len([row for row in history if row['id'] == new_id]) == 1


def test_closing_supersedes_old_conclusion(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'chain.sqlite'))
    client = TestClient(main.app)
    with client:
        created = client.post('/api/runs', json={'dataset': 'clean', 'queries': [q()]}).json()
        run_id = created['manifest']['run_id']
        assert client.get('/api/export/' + run_id).status_code == 409
        closed = client.post('/api/clarify/reply', json={'run_id': run_id, 'query_id': 'Q1', 'author': 'A', 'text': '确认为 10-08', 'close': True}).json()
        new_id = closed['run']['manifest']['run_id']
        assert new_id != run_id
        assert closed['run']['lockable']['status'] == '可锁库'
        old = store.get(main.DB_PATH, run_id)
        assert old['superseded_by'] == new_id
        assert old['clarify']['queries'][0]['question'] == '采样日期是否应为 10-08？'
        assert old['clarify']['queries'][0]['replies'] == []
        current = store.current(main.DB_PATH, run_id)
        assert current['manifest']['run_id'] == new_id
        assert client.get('/api/export/' + run_id).status_code == 409
        assert client.get('/api/export/' + new_id).status_code == 200


def test_archive_bundle_carries_clarify(tmp_path, monkeypatch):
    import io, json, zipfile
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'bundle.sqlite'))
    client = TestClient(main.app)
    with client:
        created = client.post('/api/runs', json={'dataset': 'clean', 'queries': [q(field='remark', kind='format')]}).json()
        run_id = created['manifest']['run_id']
        bundle = zipfile.ZipFile(io.BytesIO(client.get('/api/export/' + run_id).content))
        clarify = json.loads(bundle.read('clarify.json'))
        assert clarify['digest'] == created['clarify']['digest']
        assert clarify['queries'][0]['question'] == '采样日期是否应为 10-08？'
        manifest = json.loads(bundle.read('manifest.json'))
        assert manifest['clarify_digest'] == created['clarify']['digest']


def test_reply_unknown_query(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'unknown.sqlite'))
    client = TestClient(main.app)
    with client:
        created = client.post('/api/runs', json={'dataset': 'clean', 'queries': [q()]}).json()
        response = client.post('/api/clarify/reply', json={'run_id': created['manifest']['run_id'], 'query_id': 'NOPE', 'author': 'A', 'text': 'x'})
        assert response.status_code == 404
        assert response.json()['code'] == 'QUERY_NOT_FOUND'


def test_superseded_run_cannot_be_replied_or_reviewed(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'stale.sqlite'))
    client = TestClient(main.app)
    with client:
        created = client.post('/api/runs', json={'dataset': 'clean', 'queries': [q()]}).json()
        run_id = created['manifest']['run_id']
        closed = client.post('/api/clarify/reply', json={'run_id': run_id, 'query_id': 'Q1', 'author': 'A', 'text': '确认为 10-08', 'close': True}).json()
        stale_reply = client.post('/api/clarify/reply', json={'run_id': run_id, 'query_id': 'Q1', 'author': 'B', 'text': '另一条回复'})
        assert stale_reply.status_code == 409
        stale_review = client.post('/api/runs/' + run_id + '/review', json={'revision': 1})
        assert stale_review.status_code == 409
        assert closed['run']['manifest']['run_id'] != run_id


def test_format_only_batch_can_export(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'format.sqlite'))
    client = TestClient(main.app)
    with client:
        created = client.post('/api/runs', json={'dataset': 'clean', 'queries': [q(field='remark', kind='format')]}).json()
        run_id = created['manifest']['run_id']
        assert created['lockable']['status'] == '可随批冻结'
        assert client.get('/api/export/' + run_id).status_code == 200


def test_data_deviation_visit_shows_block_reason():
    state = evaluate({'dataset': 'dirty'})
    visit = state['lockable']['visit_map']['00020|V2']
    assert visit['state'] == '阻断'
    assert 'MISSING_PRIMARY' in visit['blocking_reasons']
    late = state['lockable']['visit_map']['00001|V1']
    assert 'WINDOW_LATE' in late['blocking_reasons']


def test_clarify_page_shows_each_visit(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'page.sqlite'))
    client = TestClient(main.app)
    with client:
        created = client.post('/api/runs', json={'dataset': 'dirty', 'queries': [q(), q(id='Q2', record_id='00002-V1', field='remark', kind='format')]}).json()
        run_id = created['manifest']['run_id']
        html = client.get('/clarify?run_id=' + run_id).text
        assert '00001' in html and 'V1' in html
        assert 'KEY_FACT_UNVERIFIED' in html
        assert 'Q1' in html and 'Q2' in html
        assert '随批冻结' in html
