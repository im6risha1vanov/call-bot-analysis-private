"""Real temporary Git repos/bundles; systemd and HTTP are explicit fake executables."""
from __future__ import annotations
import hashlib,importlib.util,json,os,shutil,subprocess,sys,tarfile,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('crm_release_packager',ROOT/'scripts/package_crm_learning.py')
packager=importlib.util.module_from_spec(spec);spec.loader.exec_module(packager)
FAKE=r'''#!/usr/bin/env python3
import json,os,subprocess,sys
from pathlib import Path
cfg=json.loads(Path(os.environ['CRM_DEPLOY_TEST_STATE']).read_text());app=Path(cfg['app']);args=sys.argv[1:];tool=Path(sys.argv[0]).name
log=Path(cfg['log']);rows=log.read_text().splitlines() if log.exists() else []
if tool=='hostname':print(cfg.get('hostname','9015421-nt422325.test'));sys.exit()
if tool=='sleep':sys.exit()
if tool=='systemctl':
 if args[0]=='is-active':sys.exit(1 if cfg.get('deny_systemd') else 0)
 if args[0]=='show':
  if 'WorkingDirectory' in args:print(app)
  else:print('MainPID=123\nActiveState=active')
  sys.exit()
 if args[0]=='restart':
  rows.append(json.dumps(args));log.write_text('\n'.join(rows)+'\n')
  sys.exit(1 if cfg.get('fail_restart') and len(rows)==1 else 0)
 raise SystemExit(2)
if tool=='curl':
 url=next(a for a in args if a.startswith('http'))
 if url.endswith('/api/learning/context'):
  print('404' if cfg.get('bad_route') else '401',end='');sys.exit()
 if '/static/' in url:
  data=(app/'crm/static'/url.split('/static/',1)[1]).read_bytes()
  if cfg.get('bad_asset'):data+=b'wrong version'
  sys.stdout.buffer.write(data);sys.exit()
 if '-o' not in args:print('fake CRM')
 sys.exit()
raise SystemExit(2)
'''
class DeploymentTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(prefix='averon-deploy-test-');self.addCleanup(self.temp.cleanup);self.base_dir=Path(self.temp.name)
  self.source=self.base_dir/'source';self.source.mkdir();self.app=self.base_dir/'app';self.bin=self.base_dir/'bin';self.bin.mkdir()
  self.git('init','-b','main',cwd=self.source)
  (self.source/'scripts').mkdir();shutil.copyfile(ROOT/'scripts/deploy_crm_learning.sh',self.source/'scripts/deploy_crm_learning.sh')
  (self.source/'.gitignore').write_text('.env\n.venv/\n.deployments/\n')
  self.assets=['index.html','app.js','help.js','learning/engine.js','learning/learning.js','learning/learning.css','learning/examples/save_outcome_callback.gif']
  for name in self.assets:
   path=self.source/'crm/static'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('old '+name+'\n')
  self.commit(self.source,'base');self.base=self.git('rev-parse','HEAD',cwd=self.source).strip()
  for name in self.assets:(self.source/'crm/static'/name).write_text('new '+name+'\n')
  (self.source/'crm/learning.py').write_text('# added module\n');self.commit(self.source,'learning');self.target=self.git('rev-parse','HEAD',cwd=self.source).strip()
  self.git('clone',str(self.source),str(self.app),cwd=self.base_dir);self.git('checkout',self.base,cwd=self.app)
  py=self.app/'.venv/bin/python';py.parent.mkdir(parents=True);py.symlink_to(sys.executable)
  (self.app/'.env').write_text('SYNTHETIC_SECRET=do-not-touch\n');(self.app/'webphone.json').write_text('synthetic-private-state')
  for name in ['hostname','systemctl','curl','sleep']:
   path=self.bin/name;path.write_text(FAKE);path.chmod(0o700)
  self.cfg=self.base_dir/'fake.json';self.log=self.base_dir/'calls.jsonl';self.configure()
  oldroot,oldbase=packager.ROOT,packager.BASE;packager.ROOT=self.source;packager.BASE=self.base
  try:self.release=self.base_dir/'release';self.report=packager.package(self.release)
  finally:packager.ROOT=oldroot;packager.BASE=oldbase
  script=self.release/'deploy.sh';script.write_text(script.read_text().replace('APP=/opt/callbot-astra','APP='+str(self.app),1));self.checksums()
 def git(self,*args,cwd=None):return subprocess.check_output(['git',*args],cwd=cwd or self.app,stderr=subprocess.DEVNULL,text=True)
 def commit(self,path,message):
  self.git('add','.',cwd=path);self.git('-c','user.name=Fixture','-c','user.email=fixture@invalid','commit','-m',message,cwd=path)
 def configure(self,**kwargs):self.cfg.write_text(json.dumps({'app':str(self.app),'log':str(self.log),**kwargs}))
 def checksums(self):
  (self.release/'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in sorted(self.release.iterdir()) if p.name!='SHA256SUMS'))
 def run_deploy(self,mode='apply'):
  env={**os.environ,'PATH':str(self.bin)+os.pathsep+os.environ.get('PATH','/usr/bin:/bin'),'CRM_DEPLOY_TEST_STATE':str(self.cfg)}
  return subprocess.run(['bash',str(self.release/'deploy.sh'),mode],cwd=self.app,env=env,capture_output=True,text=True,timeout=20)
 def restarts(self):return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []
 def assert_private_state(self):
  self.assertEqual((self.app/'.env').read_text(),'SYNTHETIC_SECRET=do-not-touch\n');self.assertEqual((self.app/'webphone.json').read_text(),'synthetic-private-state')
 def test_package_verifies_and_unpacks(self):
  with tarfile.open(self.report['archive']) as archive:
   self.assertIn('release/update.bundle',archive.getnames());self.assertFalse(any('/.git/' in p or p.endswith('/.env') or p.endswith('/webphone.json') for p in archive.getnames()))
  self.assertEqual(Path(self.report['archive']).stat().st_mode&0o777,0o600)
  self.assertEqual(self.run_deploy('check').returncode,0);self.assertEqual(self.git('rev-parse','HEAD').strip(),self.base);self.assertFalse((self.app/'.deployments').exists());self.assertEqual(self.restarts(),[])
 def test_success_restarts_only_crm_and_backs_up_source(self):
  result=self.run_deploy();self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(self.git('rev-parse','HEAD').strip(),self.target);self.assertEqual(self.restarts(),[['restart','callbot-crm.service']]);self.assert_private_state()
  backup=next((self.app/'.deployments').glob('crm-learning-*/source-before.tar.gz'))
  with tarfile.open(backup) as archive:
   self.assertNotIn('.env',archive.getnames());self.assertNotIn('webphone.json',archive.getnames());self.assertEqual(archive.extractfile('crm/static/app.js').read(),b'old app.js\n')
  self.assertEqual((self.app/'crm/learning.py').stat().st_mode&0o777,0o644)
 def test_denied_systemd_never_changes_code(self):
  self.configure(deny_systemd=True);result=self.run_deploy();self.assertNotEqual(result.returncode,0);self.assertIn('No code changed',result.stderr);self.assertEqual(self.git('rev-parse','HEAD').strip(),self.base);self.assertFalse((self.app/'.deployments').exists());self.assert_private_state()
 def test_wrong_host_and_newer_head_stop(self):
  self.configure(hostname='different-server');self.assertNotEqual(self.run_deploy().returncode,0);self.configure();(self.app/'README.md').write_text('another change');self.commit(self.app,'another deployment');head=self.git('rev-parse','HEAD');self.assertNotEqual(self.run_deploy().returncode,0);self.assertEqual(self.git('rev-parse','HEAD'),head);self.assertEqual(self.restarts(),[])
 def test_local_edits_and_untracked_collision_are_not_overwritten(self):
  path=self.app/'crm/static/app.js';path.write_text('someone else changed it');self.assertNotEqual(self.run_deploy().returncode,0);self.assertEqual(path.read_text(),'someone else changed it');self.git('restore','crm/static/app.js')
  collision=self.app/'crm/learning.py';collision.write_text('untracked content');self.assertNotEqual(self.run_deploy().returncode,0);self.assertEqual(collision.read_text(),'untracked content');self.assertEqual(self.git('rev-parse','HEAD').strip(),self.base);self.assertEqual(self.restarts(),[])
 def test_bad_asset_rolls_back_without_reset(self):
  self.configure(bad_asset=True);result=self.run_deploy();self.assertNotEqual(result.returncode,0);self.assertEqual(self.git('diff',self.base,'HEAD'),'');self.assertNotEqual(self.git('rev-parse','HEAD').strip(),self.base);self.assertTrue(self.git('log','-1','--format=%s').startswith('Rollback CRM learning'));self.assertEqual(self.restarts(),[['restart','callbot-crm.service']]*2);self.assert_private_state()
 def test_failed_restart_rolls_back(self):
  self.configure(fail_restart=True);result=self.run_deploy();self.assertNotEqual(result.returncode,0);self.assertEqual(self.git('diff',self.base,'HEAD'),'');self.assertEqual(len(self.restarts()),2);self.assert_private_state()
 def test_manual_rollback_and_dirty_rollback_guard(self):
  self.assertEqual(self.run_deploy().returncode,0);path=self.app/'crm/static/app.js';path.write_text('new user edit');self.assertNotEqual(self.run_deploy('rollback').returncode,0);self.assertEqual(path.read_text(),'new user edit');self.git('restore','crm/static/app.js');result=self.run_deploy('rollback');self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(self.git('diff',self.base,'HEAD'),'');self.assert_private_state()
 def test_tampered_package_is_rejected(self):
  (self.release/'health.tsv').write_text('tampered');self.assertNotEqual(self.run_deploy().returncode,0);self.assertEqual(self.git('rev-parse','HEAD').strip(),self.base);self.assertEqual(self.restarts(),[])
if __name__=='__main__':unittest.main()
