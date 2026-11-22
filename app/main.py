from contextlib import asynccontextmanager
from importlib import import_module
import json
from fastapi import FastAPI, Request, Body
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from app.config import ROOT, META, MODULES, DB_PATH
from app.errors import BusinessError
from app.engine import evaluate
from app import store, clarify
from app.archive import serialize

@asynccontextmanager
async def lifespan(app):
    store.save(DB_PATH,evaluate())
    yield
app=FastAPI(title=META['title'],lifespan=lifespan)
app.mount('/static',StaticFiles(directory=str(ROOT/'app/static')),name='static')
@app.exception_handler(BusinessError)
async def business_error(request:Request,error:BusinessError):
    return JSONResponse({'code':error.code,'detail':error.message},status_code=error.status)
@app.get('/')
def home():
    return RedirectResponse('/'+MODULES[0],status_code=303)
@app.get('/healthz')
def health():
    return {'status':'ok','project':META['short']}
@app.post('/api/runs')
def create(payload:dict=Body(default={})):
    return store.save(DB_PATH,evaluate(payload))
@app.post('/api/runs/{run_id}/rerun')
def rerun(run_id:str):
    previous=store.get(DB_PATH,run_id)
    if previous.get('superseded_by'):
        raise BusinessError('SUPERSEDED','该版本已被新结论取代，请重跑 '+previous['superseded_by'],409)
    if 'input_snapshot' not in previous:
        raise BusinessError('INPUT_SNAPSHOT_MISSING','旧批缺少源快照，请重新导入')
    # 重试沿同一输入快照计算；失败不写库，旧批仍是当前版本。
    return store.save(DB_PATH,evaluate({'dataset':previous['source']},snapshot=previous['input_snapshot']))
@app.get('/api/runs')
def runs():
    return store.history(DB_PATH)
@app.get('/api/runs/{run_id}')
def run(run_id:str):
    return store.get(DB_PATH,run_id)
@app.post('/api/runs/{run_id}/review')
def review(run_id:str,payload:dict=Body(...)):
    revision=payload.get('revision')
    if not isinstance(revision,int) or revision<1: raise BusinessError('BAD_REVISION','版本号须为正整数')
    existing=store.get(DB_PATH,run_id)
    if existing.get('superseded_by'):
        raise BusinessError('SUPERSEDED','该版本已被新结论取代，请复核 '+existing['superseded_by'],409)
    return store.review(DB_PATH,run_id,revision)
@app.get('/api/export/{run_id}')
def export(run_id:str):
    result=store.get(DB_PATH,run_id)
    if result.get('superseded_by'):
        raise BusinessError('SUPERSEDED','该版本已被新结论取代，请下载 '+result['superseded_by'],409)
    if (result.get('lockable') or {}).get('status')=='待处理':
        raise BusinessError('BLOCKED','仍有阻断项，不能生成冻结凭证',409)
    return Response(serialize(result),media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="{run_id}.zip"'})
@app.get('/api/clarify/sample')
def clarify_sample():
    """示例澄清清单：未关闭的关键质疑与格式待办，用于演示裁决口径。"""
    return JSONResponse(json.loads((ROOT/'sample_data/clarify_queries.json').read_text()))
@app.post('/api/clarify/reply')
def reply(payload:dict=Body(...)):
    """追加澄清回复并从同一输入快照重算；同回复重试幂等，失败旧版保留。"""
    run_id=str(payload.get('run_id','') or '')
    if not run_id: raise BusinessError('BAD_RUN','缺少批次编号')
    base=store.get(DB_PATH,run_id)
    if base.get('superseded_by'):
        raise BusinessError('SUPERSEDED','该版本已被新结论取代，请在新批次上提交：'+base['superseded_by'],409)
    if 'input_snapshot' not in base:
        raise BusinessError('INPUT_SNAPSHOT_MISSING','旧批缺少源快照，请重新导入')
    snapshot=base['input_snapshot']
    queries=snapshot['context'].get('queries',[])
    reply_body=payload.get('reply') or {'text':payload.get('text',''),'author':payload.get('author','')}
    updated,duplicate=clarify.append_reply(queries,payload.get('query_id'),reply_body,close=bool(payload.get('close')))
    if duplicate and not payload.get('close'):
        return {'duplicate':True,'run':store.get(DB_PATH,run_id)}
    corrections={**snapshot['context'].get('corrections',{}),**(payload.get('corrections') or {})}
    snapshot=dict(snapshot);snapshot['context']={**snapshot['context'],'queries':updated,'corrections':corrections}
    result=evaluate({'dataset':base['source']},snapshot=snapshot)
    saved=store.save(DB_PATH,result,previous_id=run_id)
    return {'duplicate':duplicate,'run':saved}
for key in MODULES:
    app.include_router(import_module(f'app.modules.{key}.handler').router)
