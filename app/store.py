import sqlite3, json
from datetime import datetime, timezone
from pathlib import Path
from app.errors import BusinessError

SCHEMA = {
    'lineage': 'TEXT', 'superseded_by': 'TEXT', 'created_at': 'TEXT',
}

def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con=sqlite3.connect(path,timeout=15)
    con.row_factory=sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL, result TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT \'待复核\')')
    existing={row['name'] for row in con.execute('PRAGMA table_info(runs)')}
    for name,decl in SCHEMA.items():
        if name not in existing: con.execute(f'ALTER TABLE runs ADD COLUMN {name} {decl}')
    return con

def save(path, result, previous_id=None):
    """保存批次；同输入同规则同回复幂等，不新增处理也不重复取代。

    previous_id 只在新结论成功算完后传入，失败时旧批次仍是当前版本。
    """
    run_id=result['manifest']['run_id']
    lineage=result['manifest'].get('lineage') or result['manifest']['sha256']
    created=datetime.now(timezone.utc).isoformat()
    with connect(path) as con:
        con.execute('INSERT OR IGNORE INTO runs(id,digest,result,lineage,created_at) VALUES(?,?,?,?,?)',
                    (run_id,result['manifest']['sha256'],json.dumps(result,ensure_ascii=False),lineage,created))
        if previous_id and previous_id!=run_id:
            exists=con.execute('SELECT 1 FROM runs WHERE id=?',(run_id,)).fetchone()
            if exists: con.execute('UPDATE runs SET superseded_by=? WHERE id=? AND superseded_by IS NULL',(run_id,previous_id))
    return get(path,run_id)

def get(path, run_id):
    with connect(path) as con: row=con.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
    if not row: raise BusinessError('NOT_FOUND','批次不存在',404)
    result=json.loads(row['result']);result.update(revision=row['revision'],status=row['status'])
    if row['superseded_by']: result['superseded_by']=row['superseded_by']
    if row['lineage']: result['lineage']=row['lineage']
    return _normalize(result)

def _normalize(result):
    """旧库批次缺少澄清字段时补可见占位，不冒充新的澄清结论。"""
    clarify=result.setdefault('clarify',{})
    clarify.setdefault('queries',[])
    clarify.setdefault('blocking',[])
    clarify.setdefault('with_batch',[])
    clarify.setdefault('digest',result.get('manifest',{}).get('clarify_digest',''))
    clarify.setdefault('status','')
    lockable=result.setdefault('lockable',{})
    balance=result.get('balance') or {}
    lockable.setdefault('digest',clarify.get('digest',''))
    lockable.setdefault('clarify_status',clarify.get('status',''))
    lockable.setdefault('status',balance.get('status','计算中'))
    lockable.setdefault('visits',[])
    lockable.setdefault('visit_map',{})
    lockable.setdefault('blocking_visits',[])
    lockable.setdefault('blocking_queries',[])
    lockable.setdefault('with_batch_queries',[])
    lockable.setdefault('blocking_items',[])
    lockable.setdefault('corrections_applied',[])
    lockable.setdefault('summary',{'visits':0,'blocked':0,'query_blocked':0,'query_with_batch':0})
    result.setdefault('superseded_by',None)
    return result


def current(path, run_id):
    """沿取代链取当前结论；旧版仍可读取，但不再是当前结论。"""
    seen=set()
    while True:
        if run_id in seen: raise BusinessError('LINEAGE_LOOP','批次取代链异常')
        seen.add(run_id)
        result=get(path,run_id)
        successor=result.get('superseded_by')
        if not successor: return result
        run_id=successor

def history(path):
    with connect(path) as con:
        return [dict(r) for r in con.execute('SELECT id,revision,status,lineage,superseded_by FROM runs ORDER BY rowid DESC LIMIT 100')]

def review(path,run_id,revision):
    with connect(path) as con:
        cur=con.execute('UPDATE runs SET revision=revision+1,status=? WHERE id=? AND revision=?',('已复核',run_id,revision))
        if cur.rowcount!=1:
            if not con.execute('SELECT 1 FROM runs WHERE id=?',(run_id,)).fetchone():
                raise BusinessError('NOT_FOUND','批次不存在',404)
            raise BusinessError('STALE_VERSION','复核版本已更新，请刷新',409)
    return get(path,run_id)
