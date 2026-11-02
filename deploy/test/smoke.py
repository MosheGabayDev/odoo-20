"""Business/readiness checks executed inside the selected app container."""
import html
import json
import pathlib
import re
import sys
import requests
import psycopg2

required = sys.argv[1:] or ['crm']
with psycopg2.connect(host='db', user='odoo', dbname='odoo20',
                     password=pathlib.Path('/run/secrets/odoo_db_password').read_text().strip()) as conn:
    conn.set_session(readonly=True)
    with conn.cursor() as c:
        c.execute("SELECT name FROM ir_module_module WHERE state IN ('to install','to upgrade','to remove')")
        assert not c.fetchall(), 'Pending module changes'
        c.execute("SELECT name FROM ir_module_module WHERE state='installed' AND name=ANY(%s)", (required,))
        assert set(r[0] for r in c.fetchall()) == set(required), 'Required modules missing'
        c.execute("SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user")
        assert c.fetchone() == (False, False, False), 'Application role is overprivileged'
        c.execute('SELECT count(*) FROM crm_lead')
        leads = c.fetchone()[0]
s = requests.Session()
base = 'http://127.0.0.1:8069'
page = s.get(base+'/web/login', timeout=30)
assert page.status_code == 200
csrf = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.text)
assert csrf, 'Missing login token'
response = s.post(base+'/web/login', data={'login':'admin', 'type':'password',
    'password':pathlib.Path('/run/secrets/odoo_admin_password').read_text().strip(),
    'csrf_token':html.unescape(csrf.group(1))}, allow_redirects=False, timeout=30)
assert response.status_code in (302,303) and '/web/login' not in response.headers.get('Location',''), 'Login failed'
info = s.post(base+'/web/session/get_session_info', json={'jsonrpc':'2.0','method':'call','params':{},'id':1}, timeout=30).json()
assert info.get('result',{}).get('uid') and info['result']['db']=='odoo20', 'No authenticated session'
backend = s.get(base+'/odoo', timeout=30)
assert backend.status_code == 200
assets = re.findall(r'(?:src|href)="(/web/assets/[^"?]+)', backend.text)
assert assets
for path in assets:
    assert s.get(base+html.unescape(path), timeout=120).status_code == 200, 'Asset failed'
assert 'database manager has been disabled' in s.get(base+'/web/database/manager',timeout=30).text
s.post(base+'/web/session/destroy',json={'jsonrpc':'2.0','method':'call','params':{},'id':2},timeout=15)
print(json.dumps({'login':'passed','modules':required,'assets':len(assets),'crm_leads':leads}))
