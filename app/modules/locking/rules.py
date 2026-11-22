def _clarify(state):
    return state.get('clarify') or {}

def decide(state):
    c=state['counts'];explained=len(state['records'])+len(state['quarantine'])+c['blank']+c['duplicate']+c['repeated_header']
    clarify=_clarify(state)
    query_blocked=len(clarify.get('blocking') or [])
    blocked=len(state['deviations'])+len(state['retest_issues'])+len(state['quarantine'])+query_blocked
    if blocked or c['input']!=explained:
        status='待处理'
    elif clarify.get('with_batch'):
        status='可随批冻结'
    else:
        status='可锁库'
    return {'input':c['input'],'accepted':len(state['records']),'quarantined':len(state['quarantine']),'row_balanced':c['input']==explained,'completed':len(state['completed']),'expected':len(state['context']['subjects'])*len(state['context']['required_visits']),'deviations':len(state['deviations']),'retest_issues':len(state['retest_issues']),'clarify_blocked':query_blocked,'with_batch':len(clarify.get('with_batch') or []),'blocked':blocked,'status':status}

def apply(state):
    state['balance']=decide(state)
    clarify=state.get('clarify') or {}
    state['balance']['clarify_digest']=clarify.get('digest','')
    state['balance']['clarify_status']=clarify.get('status','')
    state['views']['locking']={'title':'锁库闸门','rows':[state['balance']],
        'queries':clarify.get('queries') or [],'visits':clarify.get('visits') or [],
        'metrics':{'应访视':state['balance']['expected'],'已完成':state['balance']['completed'],'阻断项':state['balance']['blocked'],'澄清阻断':state['balance']['clarify_blocked'],'随批冻结':state['balance']['with_batch']}}
