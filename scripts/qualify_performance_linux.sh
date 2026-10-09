#!/usr/bin/env bash
# Exclusive fresh GitHub VM qualification only. No publication or deployment.
set -euo pipefail
mode="${1:?preflight or run}"; out="${2:?owned evidence path}"
if [[ "$mode" == preflight ]]; then
  mkdir -p "$out"
  python3 - "$out" <<'PREFLIGHT'
import json,os,platform,subprocess,sys
from pathlib import Path
out=Path(sys.argv[1])
def raw(command):
 result=subprocess.run(command,capture_output=True,text=True)
 return {'command':command,'exit':result.returncode,'stdout':result.stdout,'stderr':result.stderr}
def read(path):
 return Path(path).read_text().strip() if Path(path).exists() else None
cpu=raw(['lscpu','--json']); disk=raw(['lsblk','--json','-o','NAME,TYPE,ROTA,SIZE,MOUNTPOINTS,MAJ:MIN'])
mount=raw(['findmnt','--json','-o','SOURCE,FSTYPE,MAJ:MIN,TARGET','-T',str(out)])
mem=int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemTotal:')))*1024
limit=read('/sys/fs/cgroup/memory.max'); cpu_max=read('/sys/fs/cgroup/cpu.max')
quota=None if cpu_max is None or cpu_max.split()[0]=='max' else int(cpu_max.split()[0])/int(cpu_max.split()[1])
capacity=min(mem,int(limit)) if limit and limit!='max' else mem
affinity=len(os.sched_getaffinity(0))
# Distinguish logical allocation from visible physical topology; no rounding RAM.
cores={(read(f'/sys/devices/system/cpu/cpu{i}/topology/physical_package_id'),read(f'/sys/devices/system/cpu/cpu{i}/topology/core_id')) for i in os.sched_getaffinity(0)}
accelerators=[str(p) for p in Path('/dev').glob('nvidia*')]+[str(p) for p in Path('/dev/dri').glob('render*')]
devices=json.loads(disk['stdout']) if disk['exit']==0 else {}
mounts=json.loads(mount['stdout']).get('filesystems',[]) if mount['exit']==0 else []
backing=[]
def walks(rows,parent=None):
 for row in rows:
  disk=row if row.get('type')=='disk' else parent
  if mounts and row.get('maj:min')==mounts[0].get('maj:min') and disk is not None:
   backing.append({'mounted_device':row,'backing_disk':disk.get('name'),'rotational':disk.get('rota')})
  walks(row.get('children',[]),disk)
walks(devices.get('blockdevices',[]))
failures=[]
if platform.system()!='Linux':failures.append('native Linux required')
if affinity<4 or (quota is not None and quota<4):failures.append('four allocated CPU units not observed')
if len(cores)<4:failures.append('four distinct CPU cores not observed; logical allocation separately recorded')
if capacity<16*1024**3:failures.append('observed guest/cgroup RAM below original 16 GiB')
if accelerators:failures.append('GPU devices observed')
if len(mounts)!=1 or mounts[0].get('fstype') not in ['ext4','xfs','btrfs'] or len(backing)!=1 or backing[0]['rotational'] is not False:failures.append('owned output filesystem not bound to a local observed nonrotational disk')
stat=os.statvfs(out); free=stat.f_bavail*stat.f_frsize
if free<12*1024**3:failures.append('insufficient owned local disk space')
receipt={'status':'UNEVALUATED' if failures else 'REFERENCE_PREFLIGHT_PASSED','failures':failures,'platform':platform.platform(),'architecture':platform.machine(),'affinity_cpu_count':affinity,'visible_distinct_core_count':len(cores),'memtotal_bytes':mem,'cgroup_memory_max':limit,'effective_memory_bytes':capacity,'cgroup_cpu_max':cpu_max,'effective_cpu_quota':quota,'cpu':cpu,'blockdevices':disk,'filesystem':mount,'owned_mount_backing':backing,'free_bytes':free,'gpu_devices':accelerators,'process_snapshot':raw(['ps','-eo','pid,ppid,uid,comm']),'nominal_runner_allocation':'4 vCPU / 16 GB; marketing allocation is not guest GiB proof','job_exclusive':True,'shared_physical_infrastructure':True,'workflow_run':os.environ.get('GITHUB_RUN_ID'),'workflow_attempt':os.environ.get('GITHUB_RUN_ATTEMPT'),'runner':os.environ.get('RUNNER_NAME'),'architecture_role':'amd64 reference' if platform.machine()=='x86_64' else 'separate native ARM observation'}
(out/'hardware.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps({'status':receipt['status'],'failures':failures}))
raise SystemExit(2 if failures else 0)
PREFLIGHT
  exit $?
fi
[[ "$mode" == run && "$(id -u)" == 0 && "$(uname -s)" == Linux ]]
py="${3:?installed RC4 Python}"; harness="${4:?reviewed checkout}"; lockroot="${5:?immutable runtime lock checkout}"
# No changes to preexisting /etc descriptors, services, identities or paths.
[[ ! -e /var/lib/magicite-e6 ]]
install -d -m 0755 /var/lib/magicite-e6
"$py" - "$out" "$harness" "$lockroot" <<'EXECUTE'
import hashlib,json,os,pwd,subprocess,sys,time
from pathlib import Path
from magicite.core.trust import default_policy
out,harness,lockroot=map(Path,sys.argv[1:]); py=sys.executable
hardware=json.loads((out/'hardware.json').read_bytes())
if hardware['status']!='REFERENCE_PREFLIGHT_PASSED':raise ValueError('capacity gate required before execution')
if json.loads(subprocess.check_output([py,'-c',"import importlib.metadata,json;print(json.dumps(importlib.metadata.version('magicite')))" ]))!='1.0.0rc4':raise ValueError('published RC4 subject required')
base=Path('/var/lib/magicite-e6')
for name,uid in [('magicite-e6-custodian',41011),('magicite-e6-client',41012)]:
 try:pwd.getpwnam(name)
 except KeyError:pass
 else:raise ValueError('preexisting task identity')
 subprocess.run(['useradd','--uid',str(uid),'--user-group','--no-create-home','--shell','/usr/sbin/nologin',name],check=True)
custodian=pwd.getpwnam('magicite-e6-custodian'); client=pwd.getpwnam('magicite-e6-client')
# Public prepared corpus/cache are root-owned; model files immutable during measurement.
cache=out/'model-cache'; manifest=out/'model-manifest.json'
for path in cache.rglob('*'):
 if not path.is_symlink():os.chmod(path,0o555 if path.is_dir() else 0o444)
os.chmod(cache,0o555)
policy=base/'policy.json'; policy.write_text(json.dumps(default_policy().to_dict()))
policy_digest=hashlib.sha256(policy.read_bytes()).hexdigest()
registry_root=Path('/etc/magicite/registries'); registry_root.mkdir(parents=True,exist_ok=True,mode=0o755)
services=[]; receipts=[]
env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':'','MAGICITE_EMBEDDING_PROVIDER':'fastembed','HF_HUB_OFFLINE':'1','HF_DATASETS_OFFLINE':'1'}
def command(uid,args,*,capture=True):
 result=subprocess.run([py,'-m','magicite','custody',*args],user=uid,group=uid,extra_groups=[],env=env,text=True,capture_output=capture,check=True)
 return result.stdout
try:
 for stratum in ['small-100','small-1k','synthetic-10k','real-10k']:
  roots=base/stratum; roots.mkdir(mode=0o755)
  for repetition in range(3):
   root=roots/f'repetition-{repetition:03d}';root.mkdir(mode=0o700);os.chown(root,client.pw_uid,client.pw_gid)
   custody=base/f'{stratum}-{repetition}-custody';custody.mkdir(mode=0o755);os.chown(custody,custodian.pw_uid,custodian.pw_gid)
   store=custody/'private';profile=custody/'profile.json';sock=custody/'socket';registry_id=f'e6-{stratum}-{repetition}'
   command(custodian.pw_uid,['init','--directory',str(store)])
   command(custodian.pw_uid,['enroll','--directory',str(store),'--registry-id',registry_id,'--policy',str(policy),'--actor','isolated-performance-operator','--reviewed-sha256',policy_digest])
   descriptor=command(custodian.pw_uid,['profile','--directory',str(store),'--registry-id',registry_id,'--project-root',str(root),'--client-uid',str(client.pw_uid),'--socket-path',str(sock),'--profile-path',str(profile)])
   destination=registry_root/(hashlib.sha256(str(root.resolve()).encode()).hexdigest()+'.json')
   with destination.open('x') as stream:stream.write(descriptor)
   log=(out/f'custody-{stratum}-{repetition}.log').open('w')
   service=subprocess.Popen([py,'-m','magicite','custody','serve','--directory',str(store),'--profile-path',str(profile)],user=custodian.pw_uid,group=custodian.pw_uid,extra_groups=[],env=env,stdout=log,stderr=log)
   services.append((service,log))
   for _ in range(100):
    if sock.exists():break
    if service.poll() is not None:raise RuntimeError('custody service terminated before readiness')
    time.sleep(.05)
   head=json.loads(command(client.pw_uid,['status','--project-root',str(root)]))
   genesis=command(client.pw_uid,['initialize-journal','--project-root',str(root)])
   receipts.append({'root':str(root),'registry_id':registry_id,'custodian_uid':custodian.pw_uid,'client_uid':client.pw_uid,'service_pid':service.pid,'descriptor':json.loads(descriptor),'profile':json.loads(profile.read_bytes()),'reviewed_policy_sha256':policy_digest,'initial_authenticated_head':head,'genesis':json.loads(genesis),'private_store_mode':oct(store.stat().st_mode&0o777),'local_review_scope':'isolated performance evaluation only; no production human admissions'})
 (out/'custody-provisioning.json').write_text(json.dumps(receipts,indent=2)+'\n')
 results=out/'results';results.mkdir(mode=0o755);os.chown(results,client.pw_uid,client.pw_gid)
 support=[]; statuses=[]
 for stratum in ['small-100','small-1k','synthetic-10k','real-10k']:
  profile=stratum if stratum in ['small-100','small-1k'] else 'supported-10k'
  result=results/(stratum+'.json')
  args=[py,str(harness/'scripts/run_benchmark_matrix.py'),'--provider','production','--profile',profile,'--custody','protected','--project-root',str(base/stratum),'--model-cache',str(cache),'--model-manifest',str(manifest),'--project-root-for-lock',str(lockroot),'--environment-label',hardware['architecture_role']+':github-run-'+str(hardware['workflow_run']),'--envelope-mode','budget','--output',str(result)]
  if stratum=='real-10k':
   args+=['--corpus-manifest',str(out/'corpus/corpus-manifest.json')]
   for previous in support:args+=['--support-run',str(previous)]
  with (out/(stratum+'.stdout')).open('w') as stdout,(out/(stratum+'.stderr')).open('w') as stderr:
   run=subprocess.run(args,user=client.pw_uid,group=client.pw_uid,extra_groups=[],env=env,stdout=stdout,stderr=stderr)
  snapshots=[]
  snapshot_program = """
import hashlib,json,os,sys
from pathlib import Path
from magicite.config import Config
from magicite.core import trust
snapshot=trust.authenticated_snapshot(Config.load(Path(sys.argv[1])))
reviews=[d for d in snapshot.decisions if d['decision']=='admit']
scope=sorted((d['engram_id'],d['content_digest'],d.get('resource_digest'),d['actor'],d['reasons']) for d in reviews)
print(json.dumps({'root':sys.argv[1],'uid':os.getuid(),'pid':os.getpid(),'authenticated_head':snapshot.head,'policy_sha256':hashlib.sha256(json.dumps(snapshot.policy,sort_keys=True,separators=(',',':')).encode()).hexdigest(),'authenticated_record_count':len(snapshot.records),'authenticated_records_sha256':hashlib.sha256(json.dumps(snapshot.records,sort_keys=True,separators=(',',':')).encode()).hexdigest(),'decision_count':len(snapshot.decisions),'local_admit_count':len(reviews),'local_admit_digest_scope':scope,'production_human_admissions':False}))
"""
  for repetition in range(3):
   root=base/stratum/f'repetition-{repetition:03d}'
   snapshot=subprocess.run([py,'-c',snapshot_program,str(root)],user=client.pw_uid,group=client.pw_uid,extra_groups=[],env=env,capture_output=True,text=True,check=True)
   snapshots.append(json.loads(snapshot.stdout))
  (out/(stratum+'-authenticated-custody.json')).write_text(json.dumps(snapshots,indent=2)+'\n')
  statuses.append({'stratum':stratum,'exit':run.returncode,'result':str(result)})
  if not result.is_file():raise RuntimeError('missing benchmark result')
  if run.returncode not in [0,3]:raise RuntimeError('benchmark process incomplete; preserve cause before diagnosis')
  support.append(result)
 (out/'execution-completion.json').write_text(json.dumps({'status':'MEASURED','rows':statuses,'ga_approval':False,'architecture_role':hardware['architecture_role']},indent=2)+'\n')
finally:
 for service,log in services:
  service.terminate()
  try:service.wait(timeout=10)
  except subprocess.TimeoutExpired:service.kill();service.wait()
  log.close()
 # Fresh ephemeral VM teardown owns all task identities/store; never upload private store.
EXECUTE
