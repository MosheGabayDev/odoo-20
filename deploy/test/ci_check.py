"""Disposable Linux Docker integration check. No AWS access or deployment secrets."""
import argparse
import json
import pathlib
import secrets
import subprocess
import tempfile
import uuid
from release import compose_document

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
        try:
            subprocess.run(dc+['up','-d','--wait','db'],check=True)
            subprocess.run(dc+['run','--rm','--no-deps','-T','app','bootstrap'],check=True)
            subprocess.run(dc+['run','--rm','--no-deps','-T','app','upgrade','crm'],check=True)
            subprocess.run(dc+['up','-d','--wait','app'],check=True)
            subprocess.run(dc+['exec','-T','app','python','/usr/local/bin/odoo-smoke.py','crm'],check=True)
            # Recreate only disposable CI containers; named volumes and secrets must survive.
            subprocess.run(dc+['up','-d','--force-recreate','--wait','app'],check=True)
            subprocess.run(dc+['exec','-T','app','python','/usr/local/bin/odoo-smoke.py','crm'],check=True)
        finally:
            assert project.startswith('odoo20-ci-') and work.name.startswith('odoo20-ci-')
            subprocess.run(dc+['down','--volumes'],check=True)

if __name__=='__main__':main()
