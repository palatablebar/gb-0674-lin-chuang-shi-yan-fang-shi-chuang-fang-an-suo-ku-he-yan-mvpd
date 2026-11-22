import io, csv, json, zipfile, hashlib
from app.errors import BusinessError

def serialize(result):
    if not result.get('manifest') or not result.get('views'):
        raise BusinessError('INCOMPLETE_RUN','批次尚未完成')
    clarify=result.get('clarify') or {}
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json',json.dumps({**result['manifest'],'revision':result.get('revision',1),'status':result.get('status','待复核'),'clarify_digest':clarify.get('digest',''),'clarify_status':clarify.get('status','')},ensure_ascii=False,indent=2))
        archive.writestr('evidence.json',json.dumps({'counts':result['counts'],'views':result['views'],'quarantine':result['quarantine'],'lockable':result.get('lockable',{})},ensure_ascii=False,indent=2))
        archive.writestr('clarify.json',json.dumps({'digest':clarify.get('digest',''),'status':clarify.get('status',''),'queries':clarify.get('queries',[]),'visits':clarify.get('visits',[]),'corrections':clarify.get('corrections',{})},ensure_ascii=False,indent=2))
    return buffer.getvalue()
