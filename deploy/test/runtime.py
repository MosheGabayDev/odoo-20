"""Fail-closed one-time initialization and normal Odoo startup; no secrets in argv."""
import os
import pathlib
import subprocess
import sys

import psycopg2

def secret(name):
    value = pathlib.Path('/run/secrets', name).read_text().strip()
    if len(value) < 32 or any(c in value for c in '\r\n%'):
        raise ValueError('Invalid secret file: ' + name)
    return value

password = secret('odoo_db_password')
config = pathlib.Path('/tmp/odoo.conf')
os.umask(0o077)
config.write_text('[options]\n'
    'db_host = db\ndb_port = 5432\ndb_user = odoo\n'
    f'db_password = {password}\n'
    'db_name = odoo20\ndbfilter = ^odoo20$\nlist_db = False\n'
    f'admin_passwd = {secret("odoo_master_password")}\n'
    'data_dir = /var/lib/odoo\naddons_path = /opt/odoo/addons\n'
    'http_interface = 0.0.0.0\nhttp_port = 8069\nproxy_mode = False\n'
    'workers = 0\nmax_cron_threads = 1\n'
    'limit_time_cpu = 120\nlimit_time_real = 240\n')
base = [sys.executable, '/opt/odoo/odoo-bin']
mode = sys.argv[1] if len(sys.argv) > 1 else 'serve'
if mode == 'bootstrap':
    secret('odoo_admin_password')
    with psycopg2.connect(host='db', dbname='odoo20', user='odoo', password=password) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
            if cursor.fetchone()[0]:
                sys.exit('Refusing initialization: database already contains tables.')
    subprocess.run(base + ['-c', str(config), '-i', 'base,crm', '--without-demo',
                          '--stop-after-init', '--no-http'], check=True)
    script = """from pathlib import Path
admin = env.ref('base.user_admin')
admin.write({'login': 'admin', 'password': Path('/run/secrets/odoo_admin_password').read_text().strip()})
env.cr.commit()
assert env['ir.module.module'].search([('name', '=', 'crm')]).state == 'installed'
print('Dedicated admin password configured; CRM installed.')
"""
    subprocess.run(base + ['shell', '-c', str(config), '--no-http'], input=script,
                   text=True, check=True)
    pathlib.Path('/var/lib/odoo/.initialized').write_text('0cdbc41e6e1588ba5755df944ba2407bd4ed888f\n')
elif mode == 'serve':
    if not pathlib.Path('/var/lib/odoo/.initialized').is_file():
        sys.exit('Run isolated bootstrap successfully before starting the HTTP service.')
    os.execv(sys.executable, base + ['-c', str(config)])
else:
    sys.exit('Supported modes: bootstrap, serve')
