"""Closed field contract and three-way reconciliation, without network or ORM."""
import hashlib
import json
import re

FIELDS=('first_name','last_name','email','phone','mobile_phone','company')
LIMITS=dict(first_name=100,last_name=100,email=255,phone=50,mobile_phone=50,company=200)


def values(data):
    if not isinstance(data,dict) or set(data)!=set(FIELDS):raise ValueError('invalid_fields')
    if any(not isinstance(v,str) or len(v)>LIMITS[k] or any(ord(c)<32 for c in v) for k,v in data.items()):
        raise ValueError('invalid_fields')
    if not data['first_name'].strip():raise ValueError('name_required')
    return dict(data)


def digest(data):
    return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def reconcile(base,left,right):
    base,left,right=values(base),values(left),values(right)
    to_platform,to_odoo,conflicts,agreed={},{},{},dict(base)
    for key in FIELDS:
        if left[key]==right[key]:agreed[key]=left[key]
        elif left[key]==base[key]:to_platform[key]=right[key]
        elif right[key]==base[key]:to_odoo[key]=left[key]
        else:conflicts[key]=dict(base=base[key],platform=left[key],odoo=right[key])
    return dict(to_platform=to_platform,to_odoo=to_odoo,conflicts=conflicts,agreed=agreed)


def merged_baseline(plan,*,platform_confirmed=False,odoo_confirmed=False):
    result=dict(plan['agreed'])
    if platform_confirmed:result.update(plan['to_platform'])
    if odoo_confirmed:result.update(plan['to_odoo'])
    return result
