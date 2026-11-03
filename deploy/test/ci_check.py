"""Disposable Linux Docker integration check. No AWS access or deployment secrets."""
import argparse
import json
import pathlib
import secrets
import subprocess
import tempfile
import uuid
import shutil
import tarfile
from release import compose_document, Controller, atomic, digest, SECRETS

def run(*args):return subprocess.check_output(args,text=True).strip()

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--commit',required=True);args=parser.parse_args()
    root=pathlib.Path(__file__).resolve().parent
    project='odoo20-ci-'+uuid.uuid4().hex[:10]
    with tempfile.TemporaryDirectory(prefix='odoo20-ci-') as temp:
        work=pathlib.Path(temp);secret_dir=work/'secrets';secret_dir.mkdir(mode=0o700)
        for name in ('postgres_password','odoo_db_password','odoo_master_password','odoo_admin_password'):
            file=secret_dir/name;file.write_text(secrets.token_hex(32));file.chmod(0o444)
        run('docker','pull','postgres:16')
        pg=run('docker','image','inspect','postgres:16','--format','{{index .RepoDigests 0}}')
        tag=project+':test'
        subprocess.run(['docker','build','-t',tag,'--build-arg','ODOO_COMMIT='+args.commit,str(root)],check=True)
        image=run('docker','inspect',tag,'--format','{{.Id}}')
        config=work/'compose.json'
        config.write_text(json.dumps(compose_document(project,image,pg,secret_dir,root/'init-db.sh')))
        dc=['docker','compose','-p',project,'-f',str(config)]
        restored=None
        try:
            subprocess.run(dc+['up','-d','--wait','db'],check=True)
            subprocess.run(dc+['run','--rm','--no-deps','-T','app','bootstrap'],check=True)
            subprocess.run(dc+['run','--rm','--no-deps','-T','app','upgrade','crm'],check=True)
            subprocess.run(dc+['run','--rm','--no-deps','-T','app','install','platform_bridge'],check=True)
            subprocess.run(dc+['run','--rm','--no-deps','-T','app','test','platform_bridge'],check=True)
            subprocess.run(dc+['up','-d','--wait','app'],check=True)
            subprocess.run(dc+['exec','-T','app','python','/usr/local/bin/odoo-smoke.py','crm'],check=True)
            fixture=b"""import base64
lead=env['crm.lead'].create({'name':'CI_PERSISTENCE_PROOF'})
env['ir.config_parameter'].sudo().set_str('odoo20.ci.setting','preserved')
env['ir.attachment'].create({'name':'ci-proof.txt','res_model':'crm.lead','res_id':lead.id,'raw':b'preserved attachment'})
env.cr.commit()
"""
            subprocess.run(dc+['exec','-T','app','python','/opt/odoo/odoo-bin','shell','-c','/tmp/odoo.conf','--no-http'],input=fixture,check=True)
            # Recreate only disposable CI containers; named volumes and secrets must survive.
            subprocess.run(dc+['up','-d','--force-recreate','--wait','app'],check=True)
            subprocess.run(dc+['exec','-T','app','python','/usr/local/bin/odoo-smoke.py','crm'],check=True)
            # Exercise the actual restore path without AWS using only disposable CI data.
            controller=object.__new__(Controller)
            controller.root=work;controller.cfg={'pg_image':pg,'secrets':str(secret_dir)}
            shutil.copy2(root/'smoke.py',work/'smoke.py');shutil.copy2(root/'init-db.sh',work/'init-db.sh')
            source={'directory':str(work),'compose':'compose.json','project':project,'image':image,'source':args.commit,'legacy':False}
            subprocess.run(dc+['stop','app'],check=True)
            snapshot=work/'snapshots'/'ci';snapshot.mkdir(parents=True)
            for name,command in [('database.dump',['exec','-T','db','pg_dump','-U','postgres','-Fc','odoo20']),
                                 ('filestore.tar.gz',['run','--rm','--no-deps','-T','--entrypoint','tar','app','-czf','-','-C','/var/lib/odoo','.'])]:
                with (snapshot/name).open('wb') as output:subprocess.run(dc+command,stdout=output,check=True)
            with tarfile.open(snapshot/'secrets.tar.gz','w:gz') as tar:
                for name in SECRETS:tar.add(secret_dir/name,arcname=name)
            atomic(snapshot/'manifest.json',{'record':source,'data_signature':controller.data_signature(source),
                'files':{n:digest(snapshot/n) for n in ('database.dump','filestore.tar.gz','secrets.tar.gz')}})
            atomic(snapshot/'complete.json',{'manifest_sha256':digest(snapshot/'manifest.json')})
            restored=controller.new_record(image,args.commit,upgrade=['crm'],label='ci-restore')
            controller.restore_new(restored,str(snapshot));controller.upgrade(restored,neutralize=True)
            controller.dc(restored,'exec','-T','app','python','/opt/odoo/odoo-bin','shell','-c','/tmp/odoo.conf','--no-http',
                stdin=b"assert env['ir.config_parameter'].sudo().get_str('odoo20.ci.setting')=='preserved'; assert env['crm.lead'].search_count([('name','=','CI_PERSISTENCE_PROOF')])==1; print('Settings and CRM restore verified')")
            # Exercise the real prefork/gevent configuration without publishing a host port.
            controller.cfg['public_url']='https://odoo.test.ai-data-platform.com'
            controller.dc(restored,'stop','app')
            controller.write_compose(restored,preview=False)
            controller.smoke(restored)
            controller.dc(restored,'exec','-T','app','python','-c',
                "import urllib.request; assert urllib.request.urlopen('http://127.0.0.1:8072/websocket/health',timeout=10).status==200; print('Public-mode gevent readiness passed')")
        finally:
            if restored:
                assert pathlib.Path(restored['directory']).is_relative_to(work/'releases')
                controller.dc(restored,'down','--volumes')
            assert project.startswith('odoo20-ci-') and work.name.startswith('odoo20-ci-')
            subprocess.run(dc+['down','--volumes'],check=True)

if __name__=='__main__':main()
