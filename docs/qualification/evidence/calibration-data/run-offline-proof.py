"""Actual offline fixture controls; independent of legacy quality/ranking outcomes."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path('/Users/henrique/.codex/worktrees/v1-r3-qualification/magicite')
PYTHON = '/private/tmp/magicite-r3-qualification/venv/bin/python'
RUNNER = ROOT / 'scripts/qualify_calibration_data.py'
OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=False)
HEAD = subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
if subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip():
    raise RuntimeError('actual evidence needs clean committed candidate')
env = {**os.environ, 'PYTHONPATH':str(ROOT/'src'), 'PYTHONDONTWRITEBYTECODE':'1'}
records=[]
def h(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def load(p): return json.loads(p.read_bytes())
def save(p,v): p.write_text(json.dumps(v,sort_keys=True,indent=2)+'\n')
def run(name,args,expected=0):
    command=[PYTHON,str(RUNNER),*map(str,args)]
    result=subprocess.run(command,cwd=ROOT,env=env,capture_output=True)
    stdout=OUT/(name+'.stdout.json'); stderr=OUT/(name+'.stderr.txt')
    stdout.write_bytes(result.stdout); stderr.write_bytes(result.stderr)
    records.append({'name':name,'argv':command,'exit_code':result.returncode,'expected_exit_code':expected,
                    'stdout':{'path':stdout.name,'sha256':h(stdout)},'stderr':{'path':stderr.name,'sha256':h(stderr)}})
    if result.returncode!=expected: raise RuntimeError(name+' unexpected exit '+result.stderr.decode())
    return json.loads(result.stdout) if expected==0 else result.stderr.decode()
fixture=OUT/'inputs'; shutil.copytree(ROOT/'tests/fixtures/calibration-data',fixture)
packet=fixture/'packet.json'; a=OUT/'freeze-a.json'; b=OUT/'freeze-b.json'
report=run('validate',['validate',packet])
first=run('freeze-a',['freeze',packet,'--output',a,'--source-commit',HEAD])
second=run('freeze-b',['freeze',packet,'--output',b,'--source-commit',HEAD])
assert first['identity']==second['identity']
run('verify',['verify',a])
opened=run('open',['open',a,'--purpose','synthetic-control'])
receipt=a.with_name(a.name+'.final-access.json'); prior=receipt.read_bytes()
run('repeat-fresh-denied',['open',a,'--purpose','synthetic-control'],1)
replayed=run('replay',['open',a,'--purpose','synthetic-control','--replay'])
assert receipt.read_bytes()==prior and opened==replayed
assert 'conflicting' in run('wrong-purpose-denied',['open',a,'--purpose','changed','--replay'],1)
assert opened['qualifying'] is False and opened['authentic_readiness']=='UNEVALUATED'
# Drift on a second freeze must deny before creating its receipt.
pool=fixture/'candidate-one.txt'; original=pool.read_bytes();pool.write_bytes(original+b'drift')
assert 'changed' in run('byte-drift-denied',['open',b,'--purpose','synthetic-control'],1)
assert not b.with_name(b.name+'.final-access.json').exists();pool.write_bytes(original)
# Known upstream identity + renamed experiment is still exposed.
exposed=OUT/'exposed';shutil.copytree(fixture,exposed)
p=load(exposed/'packet.json');p['dataset']['revision']='6583d7d2ed07644d0fb8938ed8178f3a7dc42a12'
prereg=exposed/'preregistration.json';v=load(prereg);v['experiment_id']='renamed-official-experiment';save(prereg,v)
p['files']['preregistration']['sha256']=h(prereg);save(exposed/'packet.json',p)
e=OUT/'exposed-freeze.json'
run('exposed-freeze',['freeze',exposed/'packet.json','--output',e,'--source-commit',HEAD])
assert 'known exposed' in run('exposed-open-denied',['open',e,'--purpose','renamed'],1)
assert not e.with_name(e.name+'.final-access.json').exists()
# Supported-runtime focus on exact measured C; includes only scoped tests.
testcommand=[PYTHON,'-m','pytest','-p','no:cacheprovider','-q','tests/unit/eval/test_data_readiness.py',
             'tests/unit/eval/test_manifests.py','tests/unit/eval/test_production_empirical.py',
             'tests/unit/test_qualify_calibration_data.py']
for k in [1]:
    r=subprocess.run(testcommand,cwd=ROOT,env=env,capture_output=True)
    (OUT/f'focused-{k}.log').write_bytes(r.stdout+r.stderr)
    records.append({'name':f'focused-{k}','argv':testcommand,'exit_code':r.returncode,'expected_exit_code':0,
                    'log':{'path':f'focused-{k}.log','sha256':h(OUT/f'focused-{k}.log')}})
    if r.returncode:raise RuntimeError('source-bound scoped test failure')
if subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip():
    raise RuntimeError('source became dirty during proof')
summary={'schema':'magicite/calibration-data-control-evidence/1','measured_source_commit':HEAD,
         'assertion_grade':'self-attested','execution':'actual offline fixture packet CLI, no model/rank/fit/activation',
         'python_runtime':subprocess.check_output([PYTHON,'--version'],text=True).strip(),
         'runner_sha256':h(RUNNER),'source_inputs':first['binding']['source_inputs'],
         'fixture_inputs':first['binding']['inputs'],'freeze_identity':first['identity'],
         'deterministic_two_freezes':True,'exact_replay_retains_receipt':True,'commands':records,
         'report':report['report'],'empirical_readiness':'UNEVALUATED',
         'limits':['Synthetic invented declarations only; hashes do not authenticate independence or event truth',
                   'Local receipts cannot prevent out-of-band reads, copied ledgers or rollback',
                   'No prior official-quality result is attributed to this measured source',
                   'Current-head CI and independent review are separate evidence']}
save(OUT/'control-summary.json',summary)
print(json.dumps({'source':HEAD,'summary':str(OUT/'control-summary.json'),'freeze_identity':first['identity']}))
