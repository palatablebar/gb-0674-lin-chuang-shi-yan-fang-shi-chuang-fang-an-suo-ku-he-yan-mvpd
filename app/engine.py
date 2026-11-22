from importlib import import_module
from copy import deepcopy
import json, time, base64
from app.config import ROOT, MODULES, MAX_BYTES
from app.errors import BusinessError
from app.manifest import digest, make_manifest
from app import clarify

def _query_inputs(payload, context):
    queries = payload.get('queries', context.get('queries', []))
    corrections = payload.get('corrections', context.get('corrections', {}))
    if not isinstance(queries, list): raise BusinessError('BAD_QUERIES','澄清问题须为列表')
    if not isinstance(corrections, dict): raise BusinessError('BAD_QUERIES','澄清更正须为对象')
    return deepcopy(queries), deepcopy(corrections)

def evaluate(payload=None, until=8, snapshot=None):
    payload=payload or {}
    dataset=payload.get('dataset','dirty')
    if dataset not in {'dirty','clean','legacy'}: raise BusinessError('BAD_DATASET','样例名称无效')
    if not isinstance(payload.get('overrides',{}),dict): raise BusinessError('BAD_CONTEXT','参数须为对象')
    raw=(base64.b64decode(snapshot['bytes_b64'],validate=True) if snapshot is not None else (str(payload['csv_text']).encode() if 'csv_text' in payload else (ROOT/'sample_data'/f'{dataset}.csv').read_bytes()))
    if len(raw)>MAX_BYTES: raise BusinessError('TOO_LARGE','文件超过2MB',413)
    context=deepcopy(snapshot['context']) if snapshot is not None else json.loads((ROOT/'sample_data/context.json').read_text())
    context.update(deepcopy(payload.get('overrides',{})))
    queries,corrections=_query_inputs(payload,context)
    context['queries']=queries;context['corrections']=corrections
    start=time.perf_counter()
    state={'raw_bytes':raw,'context':context,'records':[],'quarantine':[],'counts':{},'views':{},'source':dataset,
           'query_input':deepcopy(queries),'corrections':deepcopy(corrections)}
    for index,key in enumerate(MODULES,1):
        if index>until: break
        import_module(f'app.modules.{key}.rules').apply(state)
        if key=='intake':
            clarify.adjudicate(state)
            clarify.apply_corrections(state)
        elif key=='protocol':
            _verify_corrections(state)
        clarify.refresh(state)
    state['progress']=_progress(state)
    state['input_snapshot']={'bytes_b64':base64.b64encode(raw).decode(),'context':deepcopy(context)}
    state.pop('raw_bytes',None)
    base_context={k:v for k,v in context.items() if k not in {'queries','corrections'}}
    state['manifest']=make_manifest(digest(raw,context),dataset,time.perf_counter()-start,lineage=digest(raw,base_context))
    if state.get('clarify'):
        state['manifest']['clarify_digest']=state['clarify']['digest']
        state['manifest']['clarify_status']=state['clarify']['status']
    return state

def _progress(state):
    """占位进度：尚未运行锁库模块时也给出可见状态，不冒充锁库结论。"""
    balance=dict(state.get('balance') or {})
    if not balance:
        balance={'expected':len((state.get('context') or {}).get('subjects') or {})*len((state.get('context') or {}).get('required_visits') or []),
                 'completed':len(state.get('completed') or []),'deviations':len(state.get('deviations') or []),
                 'status':'计算中' if 'locking' not in (state.get('views') or {}) else '待处理'}
    balance['clarify_status']=(state.get('clarify') or {}).get('status','')
    balance['clarify_blocked']=len((state.get('clarify') or {}).get('blocking') or [])
    return balance

def _verify_corrections(state):
    applied=state.get('corrections_applied') or []
    if not applied: return
    accepted={r.get('id') for r in state.get('records') or []}
    missing=sorted({item['record_id'] for item in applied if item['record_id'] not in accepted})
    if missing:
        raise BusinessError('CORRECTION_REJECTED','澄清更正后的记录未通过方案校验：'+', '.join(missing))
