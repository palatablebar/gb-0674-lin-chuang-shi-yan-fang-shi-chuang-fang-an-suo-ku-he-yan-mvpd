import hashlib,json

def _basis(state):
    """冻结凭证只收选定结论、窗口、偏离、复检与澄清状态，任何变动都会换封。"""
    return {
        'selected':state['selected_results'],
        'windows':state['windows'],
        'deviations':state['deviations'],
        'retest_issues':state.get('retest_issues',[]),
        'clarify':state.get('clarify',{}).get('queries',[]),
        'clarify_digest':state.get('clarify',{}).get('digest',''),
        'balance':state.get('balance',{}),
    }

def freeze(state):
    balance=state['balance']
    clarify=state.get('clarify') or {}
    if balance['status']=='待处理':status='待处理'
    elif balance['status']=='可随批冻结':status='可随批冻结'
    else:status='可冻结'
    return {'status':status,'seal':hashlib.sha256(json.dumps(_basis(state),sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest(),
            'subjects':len(state['context']['subjects']),'results':len(state['selected_results']),
            'clarify_digest':clarify.get('digest',''),'clarify_status':clarify.get('status',''),
            'clarify_blocked':len(clarify.get('blocking') or []),'with_batch':len(clarify.get('with_batch') or [])}

def apply(state):
    state['archive']=freeze(state)
    clarify=state.get('clarify') or {}
    rows=list(state['selected_results'])+[{'scope':'澄清','id':q['id'],'subject':q.get('subject',''),'visit':q.get('visit',''),'state':q['freeze'],'code':q['code'],'reason':q['reason'],'clarify_digest':clarify.get('digest','')} for q in clarify.get('queries') or []]
    state['views']['archive']={'title':'冻结清单','rows':rows,'metrics':{'受试者':state['archive']['subjects'],'结果':state['archive']['results'],'状态':state['archive']['status'],'澄清阻断':state['archive']['clarify_blocked'],'随批冻结':state['archive']['with_batch']}}
