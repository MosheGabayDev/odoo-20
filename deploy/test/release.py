#!/usr/bin/env python3
"""Explicit TEST releases. Persistent journals; isolated databases; no volume deletion."""
import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import uuid

SHA = re.compile(r'[0-9a-f]{40}')
MODULE = re.compile(r'[a-z][a-z0-9_]*')
SECRETS = ('postgres_password','odoo_db_password','odoo_master_password','odoo_admin_password')

def require(condition, message):
    if not condition:
        raise RuntimeError(message)

def modules(value):
    result = sorted(set(filter(None, value.split(','))))
    require(all(MODULE.fullmatch(m) and m not in ('all','base') for m in result),
            'Use explicit module names; base/all and paths are prohibited')
    return result

def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()

def atomic(path, value):
    path = Path(path)
    temporary = path.with_name(path.name+'.tmp-'+uuid.uuid4().hex)
    with temporary.open('x') as f:
        os.chmod(temporary,0o600)
        json.dump(value,f,indent=2,sort_keys=True)
        f.flush(); os.fsync(f.fileno())
    os.replace(temporary,path)
    if os.name == 'posix':
        descriptor = os.open(path.parent,os.O_RDONLY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)

def load(path): return json.loads(Path(path).read_text())

def run(args, *, cwd=None, stdin=None, stdout=None, timeout=1800, merge_error=False):
    result = subprocess.run([str(a) for a in args],cwd=cwd,input=stdin,
        stdout=stdout or subprocess.PIPE,stderr=subprocess.STDOUT if merge_error else subprocess.PIPE,timeout=timeout)
    if result.returncode:
        # Runtime logs can contain business data. Keep output in root-only receipts, not exception text.
        if cwd:
            Path(cwd,'last-command-error.log').write_bytes(result.stderr or b'See the private operation log for details.\n')
        raise RuntimeError('Command failed: '+str(args[0])+'; inspect private last-command-error.log')
    return result.stdout.decode().strip() if stdout is None else ''

def validate_archive(archive, target, *, names=None):
    """Extract only ordinary owned files/directories; reject links and traversal."""
    target = Path(target).resolve()
    with tarfile.open(archive,'r:gz') as tar:
        for member in tar.getmembers():
            relative = Path(member.name)
            require(not relative.is_absolute() and '..' not in relative.parts,'Unsafe archive member')
            require(member.isfile() or member.isdir(),'Archive contains links or special files')
            require((target/relative).resolve().is_relative_to(target),'Archive escapes destination')
            if names is not None:
                require(str(relative) in names,'Unexpected secret archive member')

def unpack(archive, target, *, names=None):
    validate_archive(archive,target,names=names)
    target=Path(target)
    with tarfile.open(archive,'r:gz') as tar:
        # Debian 12's Python 3.11.2 predates extractall(filter=...). Only ordinary
        # validated files/directories are copied; archive ownership/modes are ignored.
        for member in tar.getmembers():
            path=target/member.name
            if member.isdir():path.mkdir(parents=True,exist_ok=True,mode=0o700)
            else:
                path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                with tar.extractfile(member) as source,path.open('xb') as destination:
                    shutil.copyfileobj(source,destination)
                path.chmod(0o600)

def compose_document(project, image, pg_image, secrets, init_script, preview=True, port=None):
    require(re.fullmatch(r'odoo20-[a-z0-9-]+',project),'Invalid project name')
    require(re.fullmatch(r'sha256:[0-9a-f]{64}',image),'Image must be an immutable local ID')
    require('@sha256:' in pg_image,'PostgreSQL image must use a digest')
    app = {'image':image,'restart':'unless-stopped','read_only':True,
        'environment':{'ODOO_PREVIEW':'1' if preview else '0'},
        'depends_on':{'db':{'condition':'service_healthy'}},
        'networks':['database','application'],'volumes':['filestore:/var/lib/odoo'],
        'secrets':list(SECRETS[1:]),'tmpfs':['/tmp:mode=1777,size=256m'],
        'cap_drop':['ALL'],'security_opt':['no-new-privileges:true'],
        'mem_limit':'2g','cpus':1.0,'pids_limit':256,
        'healthcheck':{'test':['CMD','python','-c',"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8069/web/login',timeout=5)"],
                       'interval':'15s','timeout':'10s','start_period':'45s','retries':8}}
    if port: app['ports']=[f'127.0.0.1:{int(port)}:8069']
    db = {'image':pg_image,'restart':'unless-stopped','environment':{
        'POSTGRES_PASSWORD_FILE':'/run/secrets/postgres_password','POSTGRES_INITDB_ARGS':'--auth-host=scram-sha-256'},
        'secrets':['postgres_password','odoo_db_password'],'networks':['database'],
        'volumes':['pgdata:/var/lib/postgresql/data',f'{init_script}:/docker-entrypoint-initdb.d/10-odoo.sh:ro'],
        'mem_limit':'768m','cpus':0.5,'pids_limit':128,
        'healthcheck':{'test':['CMD-SHELL','pg_isready -U postgres -d odoo20'],'interval':'3s','timeout':'5s','retries':60}}
    for service in (app,db):
        service['logging']={'driver':'json-file','options':{'max-size':'10m','max-file':'3'}}
    return {'name':project,'services':{'app':app,'db':db},
        'networks':{'database':{'internal':True},'application':{'internal':preview}},
        'volumes':{'pgdata':{},'filestore':{}},
        'secrets':{name:{'file':str(Path(secrets)/name)} for name in SECRETS}}

class Controller:
    def __init__(self, config):
        self.config_path=Path(config).resolve()
        self.cfg=load(self.config_path)
        self.root=Path(self.cfg['root']).resolve()
        require(self.root.is_relative_to(Path('/opt/odoo20-test')),'Root must be under /opt/odoo20-test')
        require(self.cfg['repo']=='https://github.com/MosheGabayDev/odoo-20.git','Unapproved source repository')
        require(self.root.is_dir(),'Controller root missing')
        require(re.fullmatch(r's3://[a-z0-9.-]+/[a-zA-Z0-9/_-]+',self.cfg['backup_uri']),'Invalid backup URI')
        self.state_path=self.root/'release-state.json'
        self.journal_path=self.root/'release-transaction.json'

    @contextlib.contextmanager
    def locks(self):
        import fcntl
        handles=[]
        try:
            for name in ('platform-live-editor-deploy.lock','odoo20-release.lock','odoo20-backup.lock'):
                handle=open('/run/lock/'+name,'a');handles.append(handle)
                fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            yield
        finally:
            for handle in reversed(handles): handle.close()

    def state(self): return load(self.state_path)
    def current(self): return self.state()['current']
    def path(self, record):
        path=Path(record['directory']).resolve()
        require(path.is_relative_to(self.root),'Record directory escapes controller root')
        require(re.fullmatch(r'odoo20-[a-z0-9-]+',record['project']),'Invalid recorded project')
        return path

    def dc(self, record, *args, **kwargs):
        return run(['docker','compose','-p',record['project'],'-f',str(self.path(record)/record['compose']),*args],
                   cwd=self.path(record),**kwargs)

    def record(self, ident):
        require(re.fullmatch(r'[a-z0-9-]+',ident),'Invalid release ID')
        return load(self.root/'releases'/ident/'release.json')

    def save(self, record): atomic(self.path(record)/'release.json',record)
    def stop_allocated(self, record):
        # A power failure can occur after journaling intent but before creating a Compose file.
        if (self.path(record)/record['compose']).is_file(): self.dc(record,'stop','app','db')
    def clean_journal(self):
        if self.journal_path.exists():
            require(load(self.journal_path).get('phase') in ('complete','recovered'),
                    'Unfinished transaction: run status and recover; never blindly replay')

    def guards(self):
        if self.cfg.get('guard'):
            guard=Path(self.cfg['guard']).resolve()
            require(guard.parent==self.root,'Guard must be a private root-owned controller file')
            require(guard.stat().st_uid==0 and not guard.stat().st_mode & 0o022,'Unsafe guard ownership')
            run(['/bin/bash',str(guard)],cwd=self.root,timeout=90)
        require(shutil.disk_usage(self.root).free>8*1024**3,'Hold: fewer than 8 GiB free')
        for name in SECRETS:
            path=Path(self.cfg['secrets'])/name
            require(path.is_file() and not path.is_symlink(),'Missing/linked secret')

    def capture_platform(self):
        result={}
        for unit in self.cfg.get('protected_units',[]):
            result[unit]=run(['systemctl','show',unit,'-p','MainPID','-p','ActiveEnterTimestampMonotonic'])
        return result

    def check_platform(self, before):
        require(self.capture_platform()==before,'Protected service identity changed; hold for inspection')
        import urllib.request
        for url in self.cfg.get('health_urls',[]):
            require(urllib.request.urlopen(url,timeout=10).status==200,'Protected service unhealthy')

    def adopt(self):
        require(not self.state_path.exists(),'Already adopted')
        image=run(['docker','inspect','odoo20-test-app-1','--format','{{.Image}}'])
        record={'id':'baseline','project':'odoo20-test','directory':str(self.root),'compose':'compose.yaml',
                'image':image,'source':run(['docker','exec','odoo20-test-app-1','cat','/opt/odoo/SOURCE_COMMIT']),
                'status':'active','required':['crm'],'upgrade':[],'install':[],'test':[],'legacy':True}
        atomic(self.root/'baseline-release.json',record)
        atomic(self.state_path,{'version':1,'current':record,'history':[]})
        return record

    def ensure_image_archive(self, image):
        folder=self.root/'image-archives';folder.mkdir(exist_ok=True,mode=0o700)
        name=image.split(':')[1]
        archive=folder/(name+'.tar.gz')
        receipt=folder/(name+'.json')
        if not receipt.exists():
            temp=archive.with_suffix('.partial')
            with temp.open('wb') as f:
                source=subprocess.Popen(['docker','image','save',image],stdout=subprocess.PIPE)
                compressed=subprocess.run(['gzip','-1'],stdin=source.stdout,stdout=f,stderr=subprocess.PIPE)
                source.stdout.close()
                require(source.wait()==0 and compressed.returncode==0,'Image archive failed')
            os.replace(temp,archive)
            sha=digest(archive)
            uri=self.cfg['backup_uri']+'/images/'+archive.name
            run(['aws','s3','cp',str(archive),uri,'--sse','aws:kms','--only-show-errors'],cwd=folder)
            atomic(receipt,{'sha256':sha,'uri':uri,'image':image})
        return load(receipt)

    def running(self, record): return 'app' in self.dc(record,'ps','--status','running','--services').splitlines()

    def data_signature(self, record):
        query="""SELECT json_build_object(
          'crm', (SELECT md5(coalesce(string_agg(row(id,name,type,user_id,team_id,company_id,active)::text, '|' ORDER BY id),'')) FROM crm_lead),
          'users', (SELECT md5(coalesce(string_agg(row(id,login,active,company_id)::text, '|' ORDER BY id),'')) FROM res_users),
          'companies', (SELECT md5(coalesce(string_agg(row(id,name,currency_id)::text, '|' ORDER BY id),'')) FROM res_company),
          'attachments', (SELECT md5(coalesce(string_agg(row(id,res_model,res_id,store_fname,checksum)::text,'|' ORDER BY id),'')) FROM ir_attachment WHERE res_model NOT IN ('ir.ui.view','ir.asset') AND res_model IS NOT NULL),
          'parameters', (SELECT md5(coalesce(string_agg(row(key,value)::text,'|' ORDER BY key),'')) FROM ir_config_parameter)
        )::text"""
        return json.loads(self.dc(record,'exec','-T','db','psql','-U','postgres','-d','odoo20','-Atc',query))

    def snapshot(self, record, *, keep_stopped=False):
        """Freeze app; capture DB + files + secrets; verify off-host uploads before success."""
        image_receipt=self.ensure_image_archive(record['image'])
        ident=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:6]
        directory=self.root/'snapshots'/ident;directory.mkdir(parents=True,mode=0o700)
        was_running=self.running(record)
        atomic(self.root/'backup-transaction.json',{'phase':'freezing','record':record,'was_running':was_running})
        success=False
        try:
            self.dc(record,'stop','app')
            signature=self.data_signature(record)
            with (directory/'database.dump').open('wb') as f:
                self.dc(record,'exec','-T','db','pg_dump','-U','postgres','-Fc','odoo20',stdout=f)
            with (directory/'filestore.tar.gz').open('wb') as f:
                self.dc(record,'run','--rm','--no-deps','-T','--entrypoint','tar','app',
                        '-czf','-','-C','/var/lib/odoo','.',stdout=f)
            with (directory/'database.dump').open('rb') as f:
                # pg_restore --list reads stdin without connecting to any database.
                result=subprocess.run(['docker','compose','-p',record['project'],'-f',str(self.path(record)/record['compose']),
                    'exec','-T','db','pg_restore','--list'],stdin=f,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                require(result.returncode==0,'Invalid database archive')
            with tarfile.open(directory/'filestore.tar.gz','r:gz') as tar:
                require(bool(tar.getmembers()),'Empty filestore archive')
            with tarfile.open(directory/'secrets.tar.gz','w:gz') as tar:
                source=Path(self.cfg['secrets']) if record.get('legacy') else self.path(record)/'secrets'
                for name in SECRETS: tar.add(source/name,arcname=name,recursive=False)
            manifest={'id':ident,'record':record,'data_signature':signature,'image_archive':image_receipt,'files':{n:digest(directory/n) for n in
                ('database.dump','filestore.tar.gz','secrets.tar.gz')},'created_utc':dt.datetime.now(dt.timezone.utc).isoformat()}
            atomic(directory/'manifest.json',manifest)
            uri=self.cfg['backup_uri']+'/snapshots/'+ident
            run(['aws','s3','cp',str(directory)+'/',uri+'/','--recursive','--sse','aws:kms','--only-show-errors'],cwd=directory)
            for name in (*manifest['files'],'manifest.json'):
                # AWS CLI transfer success plus object length/encryption independently checked.
                bucket,key=(uri+'/'+name)[5:].split('/',1)
                head=json.loads(run(['aws','s3api','head-object','--bucket',bucket,'--key',key,
                    '--query','{size:ContentLength,encryption:ServerSideEncryption}','--output','json'],cwd=directory))
                require(head['size']==(directory/name).stat().st_size and head['encryption']=='aws:kms','Off-host backup verification failed')
            receipt={'uri':uri,'manifest_sha256':digest(directory/'manifest.json')}
            atomic(directory/'complete.pending',receipt)
            run(['aws','s3','cp',str(directory/'complete.pending'),uri+'/complete.json','--sse','aws:kms','--only-show-errors'],cwd=directory)
            atomic(directory/'complete.json',receipt)
            success=True
            return str(directory)
        finally:
            if was_running and (not keep_stopped or not success): self.dc(record,'start','app')
            atomic(self.root/'backup-transaction.json',{'phase':'complete','record':record,'was_running':was_running})

    def snapshot_data(self, path):
        path=Path(path).resolve()
        require(path.is_relative_to(self.root/'snapshots'),'Snapshot outside controller root')
        require((path/'complete.json').exists(),'Incomplete snapshot cannot be restored')
        m=load(path/'manifest.json')
        require(digest(path/'manifest.json')==load(path/'complete.json')['manifest_sha256'],'Snapshot manifest changed')
        for name,sha in m['files'].items(): require(digest(path/name)==sha,'Backup integrity mismatch')
        return m

    def new_record(self, image, source, upgrade=(), install=(), tests=(), label='release'):
        require(SHA.fullmatch(source),'Full source SHA required')
        ident=label+'-'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d%H%M%S')+'-'+uuid.uuid4().hex[:6]
        folder=self.root/'releases'/ident;folder.mkdir(parents=True,mode=0o700)
        record={'id':ident,'project':'odoo20-'+uuid.uuid4().hex[:12], 'directory':str(folder),
            'compose':'compose.json','image':image,'source':source,'upgrade':list(upgrade),
            'install':list(install),'test':list(tests),'required':sorted(set(['crm',*upgrade,*install,*tests])),
            'status':'created','legacy':False}
        self.save(record);return record

    def write_compose(self, record, *, preview=True, port=None):
        document=compose_document(record['project'],record['image'],self.cfg['pg_image'],
            self.path(record)/'secrets',self.root/'init-db.sh',preview,port)
        atomic(self.path(record)/record['compose'],document)

    def wait(self, record, service):
        for _ in range(90):
            ident=self.dc(record,'ps','-q',service)
            if ident:
                health=run(['docker','inspect',ident,'--format','{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}'])
                if health=='healthy':return
                require(health not in ('exited','dead'),'Container exited')
            time.sleep(2)
        raise RuntimeError('Readiness timeout for '+service)

    def restore_new(self, record, snapshot):
        manifest=self.snapshot_data(snapshot)
        path=self.path(record)
        require(not (path/'restore-started.json').exists(),'Never restore over an existing release; allocate a fresh release')
        atomic(path/'restore-started.json',{'snapshot':snapshot})
        secret_dir=path/'secrets';secret_dir.mkdir(mode=0o700)
        unpack(Path(snapshot)/'secrets.tar.gz',secret_dir,names=set(SECRETS))
        for name in SECRETS: (secret_dir/name).chmod(0o444)
        self.write_compose(record)
        self.dc(record,'up','-d','db');self.wait(record,'db')
        with (Path(snapshot)/'database.dump').open('rb') as f:
            result=subprocess.run(['docker','compose','-p',record['project'],'-f',str(path/record['compose']),
                'exec','-T','db','pg_restore','-U','postgres','--role=odoo','-d','odoo20','--exit-on-error','--no-owner','--no-acl'],
                stdin=f,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=300)
        if result.returncode:(path/'database-restore-error.log').write_bytes(result.stderr)
        require(result.returncode==0,'Database restore failed; retain private release for inspection')
        require(self.data_signature(record)==manifest['data_signature'],'Restored business/configuration data mismatch')
        volume=record['project']+'_filestore'
        self.dc(record,'create','--no-recreate','app')
        validate_archive(Path(snapshot)/'filestore.tar.gz',Path('/restore'))
        with (Path(snapshot)/'filestore.tar.gz').open('rb') as f:
            result=subprocess.run(['docker','run','--rm','-i','--network','none','--user','10001:10001',
                '--cap-drop','ALL',
                '--mount','type=volume,source='+volume+',target=/restore','--entrypoint','sh',record['image'],
                '-c','tar -xzf - -C /restore --no-same-owner'],
                stdin=f,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=180)
        if result.returncode:(path/'filestore-restore-error.log').write_bytes(result.stderr)
        require(result.returncode==0,'Filestore restore failed')
        record['snapshot']=snapshot;record['status']='restored';self.save(record)

    def maintenance(self, record, mode, names=()):
        log=self.path(record)/(mode+'.log')
        previous_size=log.stat().st_size if log.exists() else 0
        with log.open('ab') as f:
            self.dc(record,'run','--rm','--no-deps','-T','app',mode,*names,stdout=f,timeout=1800,merge_error=True)
        if mode=='test':
            summaries=re.findall(rb'(\d+) failed, (\d+) error\(s\) of (\d+) tests',log.read_bytes()[previous_size:])
            require(summaries and any(int(n)>0 for _,_,n in summaries), 'No Odoo tests actually ran')
            require(all(int(f)==0 and int(e)==0 for f,e,_ in summaries), 'Odoo test failures')

    def smoke(self, record):
        self.dc(record,'up','-d','app');self.wait(record,'app')
        result=json.loads(self.dc(record,'exec','-T','app','python','-',*record['required'],
            stdin=(self.root/'smoke.py').read_bytes(),timeout=300))
        atomic(self.path(record)/'smoke.json',result)
        return result

    def upgrade(self, record, *, neutralize=False, tests=False):
        if neutralize:self.maintenance(record,'neutralize')
        if record['install']:self.maintenance(record,'install',record['install'])
        if record['upgrade']:self.maintenance(record,'upgrade',record['upgrade'])
        if tests and record['test']:self.maintenance(record,'test',record['test'])
        result=self.smoke(record)
        original=self.snapshot_data(record['snapshot'])['data_signature']
        actual=self.data_signature(record)
        for name in ('crm','users','companies','attachments'):
            require(actual[name]==original[name],'Business data changed during upgrade: '+name+'; review migration explicitly')
        return result

    def build(self, source):
        require(SHA.fullmatch(source),'Supply the complete 40-character GitHub commit SHA')
        folder=self.root/'sources'/source
        if not folder.exists():
            folder.mkdir(parents=True,mode=0o700)
            run(['git','init',str(folder)],cwd=self.root)
            run(['git','-C',str(folder),'fetch','--depth','1',self.cfg['repo'],source],cwd=self.root)
            require(run(['git','-C',str(folder),'rev-parse','FETCH_HEAD'],cwd=self.root)==source,'GitHub source mismatch')
            run(['git','-C',str(folder),'checkout','--detach','FETCH_HEAD'],cwd=self.root)
        require(run(['git','-C',str(folder),'rev-parse','HEAD'])==source,'Source checkout mismatch')
        require(not run(['git','-C',str(folder),'status','--porcelain']),'Source checkout is dirty')
        context=folder/'deploy/test'
        require((context/'smoke.py').is_file(),'Source lacks the release runtime')
        tag='odoo20-release:'+source
        with (self.root/'build.log').open('ab') as f:
            run(['docker','build','--tag',tag,'--build-arg','ODOO_COMMIT='+source,
                 '--build-arg','PYTHON_IMAGE='+self.cfg['python_image'],str(context)],cwd=self.root,stdout=f,merge_error=True)
        image=run(['docker','image','inspect',tag,'--format','{{.Id}}'])
        actual=run(['docker','run','--rm','--network','none','--entrypoint','cat',image,'/opt/odoo/SOURCE_COMMIT'])
        require(actual==source,'Built image source mismatch')
        return image

    def prepare(self, source, upgrade, install, tests):
        self.clean_journal();self.guards()
        protected=self.capture_platform()
        image=self.build(source)
        self.ensure_image_archive(self.current()['image'])
        self.ensure_image_archive(image)
        snapshot=self.snapshot(self.current())
        candidate=self.new_record(image,source,upgrade,install,tests,label='candidate')
        try:
            self.restore_new(candidate,snapshot)
            self.upgrade(candidate,neutralize=True,tests=True)
            candidate['status']='prepared';candidate['base_id']=self.current()['id'];self.save(candidate)
            self.check_platform(protected)
            return candidate
        finally:
            self.stop_allocated(candidate)

    def cutover(self, template, *, frozen_snapshot=None, fail_before_exposure=False):
        self.clean_journal();self.guards()
        protected=self.capture_platform();previous=self.current()
        require(template.get('base_id',previous['id'])==previous['id'],'Candidate is stale; prepare again')
        next_record=self.new_record(template['image'],template['source'],template.get('upgrade',[]),template.get('install',[]),template.get('test',[]))
        next_record['required']=template.get('required',next_record['required'])
        self.save(next_record)
        journal={'phase':'freezing','previous':previous,'next':next_record,'state_before':self.state(),'created_utc':dt.datetime.now(dt.timezone.utc).isoformat()}
        atomic(self.journal_path,journal)
        exposed=False
        try:
            self.ensure_image_archive(previous['image']);self.ensure_image_archive(next_record['image'])
            latest=self.snapshot(previous,keep_stopped=True)
            journal.update(phase='private_validation',backup=latest);atomic(self.journal_path,journal)
            self.restore_new(next_record,frozen_snapshot or latest)
            self.upgrade(next_record)
            if fail_before_exposure: raise RuntimeError('Intentional rehearsal failure before exposure')
            self.check_platform(protected)
            self.dc(next_record,'stop','app')
            self.write_compose(next_record,preview=self.cfg.get('sandbox',False),port=self.cfg.get('port'))
            next_record.update(status='active',rollback_backup=latest,previous_id=previous['id'])
            self.save(next_record)
            state=self.state();state['history'].append(previous);state['current']=next_record
            # Persist the desired current before exposure. After this boundary never discard writes automatically.
            journal['phase']='commit_pending';atomic(self.journal_path,journal)
            atomic(self.state_path,state)
            journal['phase']='exposure_started';atomic(self.journal_path,journal);exposed=True
            self.dc(next_record,'up','-d','app');self.wait(next_record,'app');self.smoke(next_record)
            self.check_platform(protected)
            journal['phase']='complete';atomic(self.journal_path,journal)
            return next_record
        except Exception:
            if not exposed:
                self.stop_allocated(next_record)
                atomic(self.state_path,journal['state_before'])
                self.dc(previous,'start','app')
                self.wait(previous,'app')
                journal['phase']='recovered';atomic(self.journal_path,journal)
            # Exposed failures retain the new stack and transaction for explicit recovery.
            raise

    def promote(self, ident, *, fail=False):
        candidate=self.record(ident)
        require(candidate['status']=='prepared','Only a verified prepared candidate can be promoted')
        self.ci_success(candidate['source'])
        return self.cutover(candidate,fail_before_exposure=fail)

    def ci_success(self, source):
        import urllib.request
        require(SHA.fullmatch(source),'Invalid CI source SHA')
        request=urllib.request.Request('https://api.github.com/repos/MosheGabayDev/odoo-20/actions/runs?head_sha='+source+'&per_page=50',
            headers={'Accept':'application/vnd.github+json','User-Agent':'odoo20-test-release'})
        with urllib.request.urlopen(request,timeout=20) as response: payload=json.load(response)
        runs=[r for r in payload['workflow_runs'] if r['head_sha']==source
              and r['name']=='Odoo TEST release checks' and r['path']=='.github/workflows/odoo-test-ci.yml'
              and r['event'] in ('push','workflow_dispatch')]
        require(runs,'No GitHub CI evidence for this exact commit')
        latest=max(runs,key=lambda r:r['id'])
        require(latest['status']=='completed' and latest['conclusion']=='success','GitHub CI must pass for this exact commit before promotion')
        return latest['html_url']

    def rollback(self, acknowledge):
        current=self.current()
        require(acknowledge==current['id'],'Rollback can restore older data. Supply --accept-snapshot-restore CURRENT_RELEASE_ID')
        snapshot=current.get('rollback_backup')
        require(snapshot,'No earlier compatible snapshot recorded')
        manifest=self.snapshot_data(snapshot)
        template=dict(manifest['record']);template['base_id']=current['id']
        template.update(upgrade=[],install=[],test=[])
        return self.cutover(template,frozen_snapshot=snapshot)

    def recover(self):
        journal=load(self.journal_path) if self.journal_path.exists() else None
        if not journal or journal['phase'] in ('complete','recovered'):
            current=self.current()
            self.dc(current,'up','-d','db','app');self.wait(current,'app');self.smoke(current)
            backup=self.root/'backup-transaction.json'
            if backup.exists():
                transaction=load(backup);transaction['phase']='recovered';atomic(backup,transaction)
            return {'status':'current_release_ready'}
        if journal['phase']=='exposure_started':
            current=self.current()
            require(current['id']==journal['next']['id'],'Unexpected current release; inspect manually')
            self.dc(current,'up','-d','app');self.wait(current,'app');self.smoke(current)
        else:
            self.stop_allocated(journal['next'])
            if 'state_before' in journal: atomic(self.state_path,journal['state_before'])
            self.dc(journal['previous'],'start','app');self.wait(journal['previous'],'app')
        journal['phase']='recovered';atomic(self.journal_path,journal)
        return {'status':'recovered_without_discarding_exposed_data'}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default='/opt/odoo20-test/release-config.json')
    subs=parser.add_subparsers(dest='command',required=True)
    for name in ('adopt','status','backup','recover'):subs.add_parser(name)
    prepare=subs.add_parser('prepare');prepare.add_argument('--commit',required=True)
    for name in ('upgrade','install','test'):prepare.add_argument('--'+name,type=modules,default=[])
    promote=subs.add_parser('promote');promote.add_argument('candidate')
    promote.add_argument('--rehearse-failure',action='store_true',help='Stop before exposure and prove automatic recovery')
    rollback=subs.add_parser('rollback');rollback.add_argument('--accept-snapshot-restore',required=True)
    args=parser.parse_args()
    require(os.geteuid()==0,'Run on the TEST host as root')
    os.umask(0o077)
    def interrupted(signum, frame):
        raise RuntimeError('Interrupted; inspect the persistent transaction and run recover')
    signal.signal(signal.SIGTERM,interrupted)
    controller=Controller(args.config)
    with controller.locks():
        if args.command=='status':
            result={'state':controller.state(),'transaction':load(controller.journal_path) if controller.journal_path.exists() else None}
        elif args.command=='adopt':result=controller.adopt()
        elif args.command=='backup':
            controller.clean_journal();controller.guards();result={'snapshot':controller.snapshot(controller.current())}
        elif args.command=='prepare':result=controller.prepare(args.commit,args.upgrade,args.install,args.test)
        elif args.command=='promote':result=controller.promote(args.candidate,fail=args.rehearse_failure)
        elif args.command=='rollback':result=controller.rollback(args.accept_snapshot_restore)
        elif args.command=='recover':result=controller.recover()
        print(json.dumps(result,indent=2))

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('HOLD/FAIL: '+str(error),file=sys.stderr)
        sys.exit(1)
