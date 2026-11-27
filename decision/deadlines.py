"""Immutable display deadlines based on assessment time and one config snapshot (#44)."""

from datetime import datetime,timedelta,timezone


def aware(value):
    stamp = datetime.fromisoformat(value.replace('Z','+00:00')) if isinstance(value,str) else value
    if not isinstance(stamp,datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError('An aware deadline timestamp is required')
    return stamp.astimezone(timezone.utc)


def with_deadlines(recommendation,assessed_at,level,snapshot):
    if recommendation is None:
        return None
    stamp = aware(assessed_at)
    minutes = snapshot['costs']['reassessment_minutes_by_level'][level]
    entries = [{'kind':'next_assessment','label':'다음 판단 시점','action_id':None,'applicable':True,
        'at':(stamp+timedelta(minutes=minutes)).isoformat(),'source_status':'assumed',
        'basis':'relative','source':'config/costs.yaml:reassessment_minutes_by_level','expired':False}]
    for action,kind,label,policy in (
        ('release','release_application','방류 신청 마감',snapshot['release_rules']['application_deadline']),
        ('harvest','harvest','출하 가능 기한',snapshot['costs']['harvest_deadline'])):
        option = next((o for o in recommendation['options'] if o['id']==action),None)
        if option is None:
            continue
        at = (aware(policy['at']) if policy['at'] is not None else
              stamp+timedelta(hours=policy['hours_from_assessment']) if policy['hours_from_assessment'] is not None else None)
        entries.append({'kind':kind,'label':label,'action_id':action,'applicable':option['available'],
            'at':at.isoformat() if at else None,'source_status':policy['source_status'],
            'basis':'absolute' if policy['at'] is not None else 'relative' if at else 'unconfirmed',
            'source':policy['source'],'expired':bool(at and at<=stamp)})
    values = [aware(e['at']) for e in entries if e['applicable'] and e['at'] is not None]
    deadline = min(values).isoformat() if values else None
    return {**recommendation,'deadline':deadline,'deadlines':entries,
            'deadline_expired':bool(deadline and aware(deadline)<=stamp)}
