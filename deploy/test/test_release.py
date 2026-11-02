import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import gzip
import sys
import unittest
from unittest.mock import Mock, patch

spec=importlib.util.spec_from_file_location('release',Path(__file__).with_name('release.py'))
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)

class SafetyTests(unittest.TestCase):
    def test_backup_binary_stream_cannot_be_corrupted_by_compose_status_stderr(self):
        with tempfile.TemporaryDirectory() as folder:
            archive=Path(folder)/'archive.gz'
            with archive.open('wb') as output:
                r.run([sys.executable,'-c',"import sys,gzip; sys.stderr.write('Container creating\\n'); sys.stdout.buffer.write(gzip.compress(b'important-data'))"],stdout=output)
            self.assertEqual(gzip.decompress(archive.read_bytes()),b'important-data')

    def test_rejects_shell_and_broad_module_names(self):
        for name in ('../crm','crm;id','all','base','crm,$(id)','CRM','--force'):
            with self.subTest(name=name),self.assertRaises(RuntimeError):r.modules(name)
        self.assertEqual(r.modules('crm,sale,crm'),['crm','sale'])

    def test_preview_has_no_public_port_or_egress_and_dedicated_data(self):
        doc=r.compose_document('odoo20-unit','sha256:'+'a'*64,'postgres@sha256:'+'b'*64,'/private','/init')
        self.assertNotIn('ports',doc['services']['app'])
        self.assertTrue(all(net['internal'] for net in doc['networks'].values()))
        self.assertNotIn('ports',doc['services']['db'])
        self.assertEqual(doc['services']['app']['environment']['ODOO_PREVIEW'],'1')
        self.assertEqual(doc['services']['db']['restart'],'unless-stopped')
        self.assertIn('filestore:/var/lib/odoo',doc['services']['app']['volumes'])
        self.assertNotIn('POSTGRES_PASSWORD',doc['services']['db']['environment'])

    def test_live_exposes_loopback_only(self):
        doc=r.compose_document('odoo20-unit','sha256:'+'a'*64,'postgres@sha256:'+'b'*64,'/private','/init',False,18069)
        self.assertEqual(doc['services']['app']['ports'],['127.0.0.1:18069:8069'])

    def test_rejects_mutable_image_and_other_project(self):
        for project,image,pg in [('platform','sha256:'+'a'*64,'postgres@sha256:abc'),
                                 ('odoo20-unit','latest','postgres@sha256:abc'),
                                 ('odoo20-unit','sha256:'+'a'*64,'postgres:16')]:
            with self.assertRaises(RuntimeError):r.compose_document(project,image,pg,'/s','/i')

    def test_tar_rejects_path_traversal_links_and_unknown_secrets(self):
        with tempfile.TemporaryDirectory() as folder:
            for name,link in [('../../escape',False),('/etc/passwd',False),('safe',True)]:
                archive=Path(folder)/'bad.tar.gz'
                with tarfile.open(archive,'w:gz') as tar:
                    item=tarfile.TarInfo(name)
                    if link:item.type=tarfile.SYMTYPE;item.linkname='/etc'
                    tar.addfile(item,io.BytesIO())
                with self.assertRaises(RuntimeError):r.unpack(archive,Path(folder)/'target')

    def test_atomic_state_and_manifest_tamper(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'state.json';r.atomic(path,{'current':'old'});r.atomic(path,{'current':'new'})
            self.assertEqual(r.load(path),{'current':'new'})
            controller=object.__new__(r.Controller);controller.root=Path(folder)
            snap=Path(folder)/'snapshots'/'one';snap.mkdir(parents=True)
            (snap/'dump').write_text('good')
            r.atomic(snap/'manifest.json',{'files':{'dump':r.digest(snap/'dump')}})
            r.atomic(snap/'complete.json',{'manifest_sha256':r.digest(snap/'manifest.json')})
            controller.snapshot_data(snap)
            (snap/'dump').write_text('tampered')
            with self.assertRaises(RuntimeError):controller.snapshot_data(snap)

    def test_rollback_rejects_missing_or_stale_data_ack_before_any_effect(self):
        controller=object.__new__(r.Controller)
        controller.current=Mock(return_value={'id':'current','rollback_backup':'backup'})
        controller.cutover=Mock()
        for value in ('','yes','previous'):
            with self.assertRaises(RuntimeError):controller.rollback(value)
        controller.cutover.assert_not_called()

    def test_incomplete_transaction_blocks_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            controller=object.__new__(r.Controller);controller.journal_path=Path(folder)/'journal.json'
            for phase in ('freezing','private_validation','exposure_started'):
                r.atomic(controller.journal_path,{'phase':phase})
                with self.assertRaises(RuntimeError):controller.clean_journal()
            r.atomic(controller.journal_path,{'phase':'recovered'});controller.clean_journal()

    def test_recovery_after_exposure_preserves_new_current_data(self):
        with tempfile.TemporaryDirectory() as folder:
            controller=object.__new__(r.Controller);controller.journal_path=Path(folder)/'journal.json'
            next_record={'id':'new'};old={'id':'old'}
            r.atomic(controller.journal_path,{'phase':'exposure_started','next':next_record,'previous':old})
            controller.current=Mock(return_value=next_record)
            controller.dc=Mock();controller.wait=Mock();controller.smoke=Mock()
            controller.recover()
            self.assertTrue(all(call.args[0]==next_record for call in controller.dc.call_args_list))
            self.assertEqual(r.load(controller.journal_path)['phase'],'recovered')

    def test_recovery_before_exposure_returns_unchanged_previous(self):
        with tempfile.TemporaryDirectory() as folder:
            controller=object.__new__(r.Controller);controller.journal_path=Path(folder)/'journal.json'
            next_record={'id':'new'};old={'id':'old'}
            r.atomic(controller.journal_path,{'phase':'private_validation','next':next_record,'previous':old})
            controller.dc=Mock();controller.wait=Mock()
            controller.recover()
            controller.dc.assert_any_call(next_record,'stop','app','db')
            controller.dc.assert_any_call(old,'start','app')

if __name__=='__main__':unittest.main()
