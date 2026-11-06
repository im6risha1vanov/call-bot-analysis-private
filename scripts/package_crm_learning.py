"""Create an offline CRM deployment package; never modifies production or services."""
from __future__ import annotations
import argparse,hashlib,json,os,re,shutil,subprocess,tarfile
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BASE='2a11936af00465fafdaa07059da179ee8ce7313a'
def git(*args,raw=False):
    return subprocess.check_output(['git',*args],cwd=ROOT,text=not raw)
def package(dest,base=None):
    base=base or BASE
    if not re.fullmatch(r"[a-f0-9]{40}",base):raise ValueError("Use a full verified base commit hash.")
    dest=Path(dest).resolve()
    if dest.exists() or dest.with_suffix('.tar.gz').exists():raise ValueError('Output already exists; do not overwrite a release.')
    if git('status','--porcelain').strip():raise ValueError('Commit the reviewed source first.')
    target=git('rev-parse','HEAD').strip();branch=git('symbolic-ref','HEAD').strip()
    subprocess.run(['git','merge-base','--is-ancestor',base,target],cwd=ROOT,check=True)
    changed=git('diff','--name-only','-z',base,target,raw=True).split(b'\0')
    names=[p.decode() for p in changed if p]
    for name in names:
        path=Path(name)
        if path.is_absolute() or '..' in path.parts or not (name=='README.md' or name=='.gitignore' or name.startswith(('crm/','docs/','scripts/','tests/'))):raise ValueError('Unexpected release scope: '+name)
        if path.name.startswith('.env') or path.name in {'webphone.json','auth.json'} or path.suffix in {'.log','.dump','.sql','.ogg','.mp3','.wav','.db'}:raise ValueError('Sensitive or out-of-scope artifact: '+name)
    dest.mkdir(mode=0o700)
    subprocess.run(['git','bundle','create',str(dest/'update.bundle'),branch,'^'+base],cwd=ROOT,check=True)
    (dest/'rollback.patch').write_bytes(git('diff','--binary','--full-index',target,base,raw=True))
    before=[]
    for name in names:
        check=subprocess.run(['git','cat-file','-e',base+':'+name],cwd=ROOT,capture_output=True)
        if check.returncode==0:before.append(name.encode())
    (dest/'before-files.zlist').write_bytes(b'\0'.join(before)+b'\0')
    assets=['index.html','app.js','help.js','learning/engine.js','learning/learning.js','learning/learning.css','learning/examples/save_outcome_callback.gif']
    assets += [p for p in ['learning/tour-guard.js','learning/guide.js'] if (ROOT/'crm/static'/p).is_file()]
    (dest/'routes.txt').write_text('/api/learning/context\n'+('/api/learning/guide\n' if (ROOT/'crm/tour.py').is_file() else ''))
    (dest/'health.tsv').write_text(''.join(hashlib.sha256((ROOT/'crm/static'/p).read_bytes()).hexdigest()+' '+p+'\n' for p in assets))
    shutil.copyfile(ROOT/'scripts/deploy_crm_learning.sh',dest/'deploy.sh')
    info={'created_utc':datetime.now(timezone.utc).isoformat(),'base':base,'target':target,'source_ref':branch,'app':'/opt/callbot-astra','service':'callbot-crm.service','domain':'averonleads.ru','changed_files':names,'migrations':False,'dependencies_changed':False}
    (dest/'release.json').write_text(json.dumps(info,ensure_ascii=False,indent=2)+'\n')
    (dest/'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in sorted(dest.iterdir())))
    for p in dest.iterdir():p.chmod(0o700 if p.name=='deploy.sh' else 0o600)
    archive=dest.with_suffix('.tar.gz')
    with tarfile.open(archive,'w:gz') as output:output.add(dest,arcname=dest.name)
    archive.chmod(0o600)
    return {'directory':str(dest),'archive':str(archive),'bytes':archive.stat().st_size,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'target':target,'files':len(names)}
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory');parser.add_argument('--base',help='Full verified production commit; default is the original release base.');args=parser.parse_args()
    print(json.dumps(package(args.directory,args.base),ensure_ascii=False))
