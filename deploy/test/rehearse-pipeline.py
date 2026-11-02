"""Real Docker/PostgreSQL rehearsal; touches only its own unpublished sandbox projects."""
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import uuid
import time
sys.path.insert(0,'/opt/odoo20-test')
from release import Controller, atomic, load, require, run

main=Controller('/opt/odoo20-test/release-config.json')
with main.locks():
    main.guards()
    candidates=[load(p) for p in (main.root/'releases').glob('candidate-*/release.json')]
    candidates=[c for c in candidates if c['status']=='prepared']
    require(len(candidates)==1,'Select an unambiguous prepared candidate')
    template=candidates[0]
    proof_id='proof-'+uuid.uuid4().hex[:10]
    root=main.root/'rehearsals'/proof_id;root.mkdir(parents=True,mode=0o700)
    config=dict(main.cfg,root=str(root),guard=None,port=None,sandbox=True)
    config['backup_uri']+='/rehearsals/'+proof_id
    atomic(root/'config.json',config)
    for name in ('smoke.py','init-db.sh'):shutil.copy2(main.root/name,root/name)
    (root/'init-db.sh').chmod(0o444)
    # Reuse the already verified immutable image recovery archives without copying large files.
    (root/'image-archives').mkdir()
    for source in (main.root/'image-archives').iterdir():
        if source.is_file():os.link(source,root/'image-archives'/source.name)
    initial=Path(template['snapshot'])
    snapshot=root/'snapshots'/initial.name
    shutil.copytree(initial,snapshot)
    c=Controller(root/'config.json')
    baseline=c.new_record(template['image'],template['source'],label='proof-baseline')
    records=[]
    def shell(record,code):
        return c.dc(record,'exec','-T','app','python','/opt/odoo/odoo-bin','shell','-c','/tmp/odoo.conf','--no-http',stdin=code.encode())
    token=uuid.uuid4().hex
    first_code="""import base64,json
lead=env['crm.lead'].create({'name':'PIPELINE_PERSISTENCE_TOKEN'})
env['ir.config_parameter'].sudo().set_str('odoo20.pipeline.proof','TOKEN')
attachment=env['ir.attachment'].create({'name':'pipeline-proof.txt','res_model':'crm.lead','res_id':lead.id,'raw':b'PERSISTED_FILE_TOKEN'})
env.cr.commit()
print(json.dumps({'lead':lead.id,'attachment':attachment.id}))
""".replace('TOKEN',token)
    verify_code="""import base64
lead=env['crm.lead'].search([('name','=','PIPELINE_PERSISTENCE_TOKEN')])
assert len(lead)==1
assert env['ir.config_parameter'].sudo().get_str('odoo20.pipeline.proof')=='TOKEN'
attachment=env['ir.attachment'].search([('name','=','pipeline-proof.txt'),('res_id','=',lead.id)])
assert len(attachment)==1 and attachment.raw.content==b'PERSISTED_FILE_TOKEN'
print('CRM record, setting and attachment persisted')
""".replace('TOKEN',token)
    try:
        c.restore_new(baseline,str(snapshot));c.upgrade(baseline,neutralize=True)
        atomic(c.state_path,{'version':1,'current':baseline,'history':[]})
        shell(baseline,first_code)
        baseline_signature=c.data_signature(baseline)
        frozen=c.snapshot(baseline)
        prepared=c.new_record(template['image'],template['source'],upgrade=['crm'],label='candidate')
        c.restore_new(prepared,frozen);c.upgrade(prepared,neutralize=True)
        prepared.update(status='prepared',base_id=baseline['id']);c.save(prepared)
        c.dc(prepared,'stop','app','db')
        try:c.cutover(prepared,fail_before_exposure=True)
        except RuntimeError as error:require('Intentional rehearsal failure' in str(error),'Unexpected rollback test failure')
        else:raise RuntimeError('Failure injection did not fail')
        require(c.current()['id']==baseline['id'] and c.running(baseline),'Old release not recovered')
        shell(baseline,verify_code)
        active=c.cutover(prepared)
        shell(active,verify_code)
        # Kill the exact container init process from the host namespace; this is a real crash,
        # not a Docker manual stop (which suppresses unless-stopped restart behavior).
        crashes={}
        for service in ('app','db'):
            container=c.dc(active,'ps','-q',service)
            inspected=json.loads(run(['docker','inspect',container]))[0]
            require(inspected['Name'].startswith('/'+active['project']+'-'),'Unexpected crash target')
            pid=inspected['State']['Pid'];require(pid>1,'Invalid container init PID')
            before=inspected['RestartCount']
            os.kill(pid,signal.SIGKILL)
            for attempt in range(90):
                after=json.loads(run(['docker','inspect',container]))[0]['RestartCount']
                if after>before:break
                time.sleep(1)
            c.wait(active,service)
            after=json.loads(run(['docker','inspect',container]))[0]['RestartCount']
            require(after>before,'Restart policy did not recover crashed container')
            shell(active,verify_code)
            crashes[service]={'restart_count_before':before,'restart_count_after':after}
        c.dc(active,'up','-d','--no-deps','--force-recreate','app');c.wait(active,'app')
        shell(active,verify_code)
        c.recover();shell(c.current(),verify_code)
        # Prove refusal to silently discard post-promotion data, then explicit snapshot restore.
        shell(active,"env['crm.lead'].create({'name':'PIPELINE_AFTER_PROMOTION_TOKEN'}); env.cr.commit()".replace('TOKEN',token))
        after_signature=c.data_signature(active)
        try:c.rollback('wrong-release-id')
        except RuntimeError:pass
        else:raise RuntimeError('Rollback accepted stale/missing data-loss acknowledgement')
        require(c.data_signature(active)==after_signature,'Rejected rollback changed data')
        rolled=c.rollback(active['id'])
        shell(rolled,verify_code)
        shell(rolled,"assert not env['crm.lead'].search([('name','=','PIPELINE_AFTER_PROMOTION_TOKEN')]); print('Expected earlier snapshot restored')".replace('TOKEN',token))
        archive=load(c.journal_path)['backup']
        retained=c.new_record(active['image'],active['source'],label='proof-retained-data')
        c.restore_new(retained,archive);c.upgrade(retained)
        shell(retained,"assert env['crm.lead'].search([('name','=','PIPELINE_AFTER_PROMOTION_TOKEN')]); print('Post-promotion data recoverable from pre-rollback archive')".replace('TOKEN',token))
        result={'status':'passed','sandbox':proof_id,'source':template['source'],
            'module_upgrade':'crm','pre_exposure_failure_recovery':'passed','positive_promotion':'passed',
            'container_crashes':crashes,'app_recreation':'passed','targeted_recover':'passed',
            'crm_record_setting_attachment':'preserved','unacknowledged_rollback':'rejected_without_changes',
            'explicit_snapshot_rollback':'passed','post_promotion_data_archive_restore':'passed',
            'real_host_reboot':'not_performed','main_release_unchanged':main.current()['id']}
        atomic(root/'proof-result.json',result);atomic(main.root/'pipeline-proof-result.json',result)
        print(json.dumps(result,indent=2))
    finally:
        for path in (root/'releases').glob('*/release.json'):
            record=load(path)
            try:c.stop_allocated(record)
            except Exception:pass
        # Retain volumes, failed releases and off-host backups. No destructive cleanup.
