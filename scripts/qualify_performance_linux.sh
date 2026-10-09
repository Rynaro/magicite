#!/usr/bin/env bash
# Exclusive fresh GitHub VM qualification only. No publication or deployment.
set -euo pipefail
mode="${1:?preflight or run}"; out="${2:?owned evidence path}"
if [[ "$mode" == preflight ]]; then
  mkdir -p "$out"
  python3 - "$out" <<'PREFLIGHT'
import json,os,platform,subprocess,sys
from pathlib import Path
# Installed allocation is distinct from usable MemTotal after kernel reservation.
# SMBIOS type 17 sizes are powers of two (dmidecode KB/MB/GB labels).
def installed_memory_evidence(result):
 import re
 if result['exit']!=0:return None,[]
 devices=[]
 for block in re.split(r'(?m)^Memory Device\s*$',result['stdout'])[1:]:
  sizes=re.findall(r'(?m)^\s*Size:\s*(.*?)\s*$',block)
  if len(sizes)!=1:return None,devices
  if sizes[0]=='No Module Installed':continue
  match=re.fullmatch(r'(\d+) (kB|KB|MB|GB|TB)',sizes[0])
  if match is None:return None,devices
  units={'kB':1024,'KB':1024,'MB':1024**2,'GB':1024**3,'TB':1024**4}
  value=int(match[1])*units[match[2]]
  if value<=0:return None,devices
  devices.append({'reported_size':sizes[0],'allocated_bytes':value})
 return (sum(row['allocated_bytes'] for row in devices) if devices else None),devices

def allocation_failures(allocated,limit,affinity,quota,usable=None):
 failures=[]
 if affinity<4 or (quota is not None and quota<4):failures.append('four allocated logical CPU units not observed')
 if allocated is None:failures.append('installed guest RAM allocation not substantiated by actual SMBIOS evidence')
 elif allocated<16*1024**3:failures.append('installed guest RAM allocation below original 16 GiB')
 if allocated is not None and usable is not None and usable>allocated:failures.append('usable MemTotal conflicts with installed memory allocation')
 if limit is None:failures.append('actual cgroup memory limit unavailable')
 elif limit!='max' and int(limit)<16*1024**3:failures.append('cgroup memory limit restricts original 16 GiB allocation')
 return failures

def effective_cgroup_limits(rows):
 memory=[]; cpu=[]; errors=[]
 if not rows:errors.append('actual cgroup ancestry unavailable')
 for row in rows:
  for key in ['memory_max','cpu_max']:
   if row.get(key) is None and not row.get('global_root'):
    errors.append('actual ancestor cgroup limit unavailable: '+key)
  try:
   if row.get('memory_max') not in [None,'max']:
    value=int(row['memory_max'])
    if value<=0:raise ValueError('invalid memory limit')
    memory.append(value)
   if row.get('cpu_max') is not None:
    quota,period=row['cpu_max'].split()
    if int(period)<=0:raise ValueError('invalid CPU period')
    if quota!='max':
     if int(quota)<=0:raise ValueError('invalid CPU quota')
     cpu.append(int(quota)/int(period))
  except (ValueError,TypeError):errors.append('malformed ancestor cgroup limit')
 return (str(min(memory)) if memory else 'max'), (min(cpu) if cpu else None), errors
def preflight_outcome(architecture,system,comparison_failures,free):
 if system!='Linux':return 'UNEVALUATED',['native Linux required']
 if architecture in ['aarch64','arm64']:
  return ('ARM_OBSERVATION_READY',[]) if free>=12*1024**3 else ('UNEVALUATED',['preparation disk capacity unavailable'])
 if architecture!='x86_64':return 'UNEVALUATED',['unsupported native qualification architecture']
 return ('UNEVALUATED',comparison_failures) if comparison_failures else ('REFERENCE_PREFLIGHT_PASSED',[])
def provider_identity_errors(repository,run,jobs,tag,context,label,architecture,document_sha):
 errors=[]
 expected='ubuntu-24.04' if architecture=='x86_64' else 'ubuntu-24.04-arm' if architecture in ['aarch64','arm64'] else None
 if repository.get('full_name')!='Rynaro/magicite' or repository.get('private') is not False:errors.append('actual public repository unsubstantiated')
 if label!=expected or context.get('GITHUB_REPOSITORY')!='Rynaro/magicite':errors.append('standard native runner class mismatch')
 if document_sha!='4f959a553da1bbfc86d4f24167981fb76177bb14c39b01296bc0ab6b6c779c38':errors.append('reviewed provider document bytes changed or missing')
 if run.get('id')!=int(context.get('GITHUB_RUN_ID','0')) or run.get('head_sha')!=context.get('checked_out_commit') or run.get('head_sha')!=context.get('GITHUB_SHA') or run.get('event')!='push' or run.get('path')!='.github/workflows/performance-qualification.yml' or run.get('run_attempt')!=int(context.get('GITHUB_RUN_ATTEMPT','0')) or run.get('actor',{}).get('login')!=context.get('GITHUB_ACTOR'):errors.append('actual run/context identity mismatch')
 ref='refs/tags/qualification/e6-rc4-20261009-02'
 if context.get('GITHUB_REF')!=ref or context.get('GITHUB_EVENT_NAME')!='push' or context.get('GITHUB_WORKFLOW_REF')!='Rynaro/magicite/.github/workflows/performance-qualification.yml@'+ref or tag.get('ref')!=ref or tag.get('object',{}).get('type')!='commit' or tag.get('object',{}).get('sha')!=run.get('head_sha'):errors.append('actual exact qualification tag/source mismatch')
 matches=[row for row in jobs.get('jobs',[]) if row.get('name')=='E6 / '+str(label)]
 if len(matches)!=1:errors.append('actual standard runner job unresolved')
 else:
  job=matches[0]
  if job.get('run_id')!=run.get('id') or job.get('head_sha')!=run.get('head_sha') or job.get('labels')!=[label] or not job.get('runner_id') or job.get('runner_name')!=context.get('RUNNER_NAME') or job.get('runner_group_id')!=0 or job.get('runner_group_name')!='GitHub Actions':errors.append('actual hosted runner/job identity mismatch')
 if context.get('RUNNER_OS')!='Linux' or context.get('RUNNER_ARCH')!=('X64' if architecture=='x86_64' else 'ARM64'):errors.append('native OS/architecture context mismatch')
 return errors

def guest_local_mapping(filesystems,blockdevices):
 import re
 if len(filesystems)!=1:return None
 fs=filesystems[0]
 if not re.fullmatch(r'\d+:\d+',str(fs.get('maj:min',''))):return None
 if fs.get('fstype') not in ['ext4','xfs','btrfs'] or fs.get('target')!='/' or not str(fs.get('source','')).startswith('/dev/'):return None
 matches=[]
 def walk(rows,parent=None):
  for row in rows:
   disk=row if row.get('type')=='disk' else parent
   if row.get('maj:min')==fs.get('maj:min') and disk is not None and re.fullmatch(r'\d+:\d+',str(disk.get('maj:min',''))):matches.append({'mounted_device':row,'backing_disk':disk.get('name'),'backing_major_minor':disk.get('maj:min'),'rotational':disk.get('rota'),'filesystem':fs})
   walk(row.get('children',[]),disk)
 walk(blockdevices)
 return matches[0] if len(matches)==1 else None

def guest_storage_errors(evidence,anchor,prospective,sysfs):
 errors=[]
 if evidence is None or anchor is None or prospective is None:return ['actual evidence/created workload/prospective local block mapping unresolved']
 if evidence['filesystem']!=anchor['filesystem'] or prospective['filesystem']!=anchor['filesystem'] or evidence['backing_major_minor']!=anchor['backing_major_minor'] or prospective['backing_major_minor']!=anchor['backing_major_minor']:errors.append('evidence/prospective/created workload guest block mounts differ')
 if not sysfs.get('source_is_block_device') or sysfs.get('block_major_minor')!=evidence['filesystem']['maj:min'] or sysfs.get('sysfs_device_major_minor')!=evidence['filesystem']['maj:min'] or sysfs.get('queue_rotational') not in ['0','1'] or bool(int(sysfs['queue_rotational']))!=evidence['rotational']:errors.append('actual sysfs/block/virtual rotational observations unresolved or conflicting')
 return errors
# End installed allocation semantics.
out=Path(sys.argv[1])
def raw(command):
 try:result=subprocess.run(command,capture_output=True,text=True,timeout=30)
 except (OSError,subprocess.TimeoutExpired) as error:return {'command':command,'exit':1,'stdout':'','stderr':type(error).__name__}
 return {'command':command,'exit':result.returncode,'stdout':result.stdout,'stderr':result.stderr}
def read(path):
 return Path(path).read_text().strip() if Path(path).exists() else None
cpu=raw(['lscpu','--json']); disk=raw(['lsblk','--json','-o','NAME,TYPE,ROTA,SIZE,MOUNTPOINTS,MAJ:MIN'])
mount=raw(['findmnt','--json','-o','SOURCE,FSTYPE,MAJ:MIN,TARGET','-T',str(out)])
mem=int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemTotal:')))*1024
# Read the actual process cgroup and every visible ancestor, not just mount root.
cgroup_rows=[]; cgroup_errors=[]; mount_records=[]
for line in Path('/proc/self/mountinfo').read_text().splitlines():
 fields=line.split(); divider=fields.index('-')
 if fields[divider+1]=='cgroup2':mount_records.append({'root':fields[3],'mountpoint':fields[4]})
process_cgroups=Path('/proc/self/cgroup').read_text().splitlines()
unified=[line.split(':',2)[2] for line in process_cgroups if line.startswith('0::')]
if len(mount_records)!=1 or len(unified)!=1:
 cgroup_errors.append('actual unified cgroup hierarchy unresolved')
else:
 mount_record=mount_records[0]; mountpoint=Path(mount_record['mountpoint'])
 if mount_record['root']!='/':cgroup_errors.append('cgroup mount hides external ancestor restrictions')
 try:
  relative=Path(unified[0]).relative_to(Path(mount_record['root']))
  directory=mountpoint/relative
  if not directory.is_dir() or not directory.resolve().is_relative_to(mountpoint.resolve()):raise ValueError('cgroup path unresolved')
  while True:
   cgroup_rows.append({'path':str(directory),'global_root':directory==mountpoint and mount_record['root']=='/','memory_max':read(directory/'memory.max'),'cpu_max':read(directory/'cpu.max')})
   if directory==mountpoint:break
   directory=directory.parent
 except ValueError:cgroup_errors.append('actual cgroup path unresolved')
limit,quota,limit_errors=effective_cgroup_limits(cgroup_rows)
cgroup_errors.extend(limit_errors)
cpu_max=read('/sys/fs/cgroup/cpu.max')
memory_devices=raw(['sudo','-n','dmidecode','--type','17'])
allocated,allocation_devices=installed_memory_evidence(memory_devices)
capacity=min(allocated,int(limit)) if allocated is not None and limit and limit!='max' else allocated
affinity=len(os.sched_getaffinity(0))
# Distinguish logical allocation from visible physical topology; no rounding RAM.
cores={(read(f'/sys/devices/system/cpu/cpu{i}/topology/physical_package_id'),read(f'/sys/devices/system/cpu/cpu{i}/topology/core_id')) for i in os.sched_getaffinity(0)}
accelerators=[str(p) for p in Path('/dev').glob('nvidia*')]+[str(p) for p in Path('/dev/dri').glob('render*')]
devices=json.loads(disk['stdout']) if disk['exit']==0 else {}
mounts=json.loads(mount['stdout']).get('filesystems',[]) if mount['exit']==0 else []
local_mapping=guest_local_mapping(mounts,devices.get('blockdevices',[]))
backing=[local_mapping] if local_mapping else []
import hashlib,urllib.request
context=json.loads((out/'workflow-context.json').read_bytes())
label=os.environ.get('E6_STANDARD_RUNNER','')
provider_errors=[]; api_receipts=[]; provider={}
def acquire_public(url,name):
 request=urllib.request.Request(url,headers={'User-Agent':'magicite-e6-qualification','Accept':'application/vnd.github+json' if url.startswith('https://api.github.com/') else 'text/html'})
 with urllib.request.urlopen(request,timeout=30) as response:
  payload=response.read(2*1024*1024)
  if response.read(1):raise ValueError('public metadata response exceeds bounded size')
  receipt={'url':url,'final_url':response.url,'status':response.status,'sha256':hashlib.sha256(payload).hexdigest(),'bytes':len(payload)}
 (out/name).write_bytes(payload);api_receipts.append(receipt)
 return payload
try:
 repository=json.loads(acquire_public('https://api.github.com/repos/Rynaro/magicite','provider-repository.json'))
 run_id=context['GITHUB_RUN_ID']
 run=json.loads(acquire_public('https://api.github.com/repos/Rynaro/magicite/actions/runs/'+run_id,'provider-run.json'))
 jobs=json.loads(acquire_public('https://api.github.com/repos/Rynaro/magicite/actions/runs/'+run_id+'/jobs?per_page=100','provider-jobs.json'))
 tag=json.loads(acquire_public('https://api.github.com/repos/Rynaro/magicite/git/ref/tags/qualification/e6-rc4-20261009-02','provider-tag.json'))
 document=acquire_public('https://docs.github.com/en/actions/reference/runners/github-hosted-runners','provider-runner-document.html')
 provider_errors=provider_identity_errors(repository,run,jobs,tag,context,label,platform.machine(),hashlib.sha256(document).hexdigest())
except (OSError,ValueError,KeyError) as error:provider_errors.append('actual public provider proof unavailable: '+type(error).__name__)
provider={'errors':provider_errors,'actual_public_api_receipts':api_receipts,'standard_label':label,'documented_storage_class':'standard public GitHub-hosted VM SSD','physical_media_independently_observed':False,'virtual_rotational_flag_observed':local_mapping.get('rotational') if local_mapping else None}
(out/'provider-class-evidence.json').write_text(json.dumps(provider,indent=2)+'\n')
anchor=Path('/var/lib/magicite-e6')
prospective=raw(['findmnt','--json','-o','SOURCE,FSTYPE,MAJ:MIN,TARGET','-T',str(anchor.parent)])
if anchor.exists() or anchor.is_symlink():raise ValueError('preexisting workload anchor refused')
subprocess.run(['sudo','-n','mkdir','--mode=0755',str(anchor)],check=True)
anchor_info=anchor.lstat()
created_mount=raw(['findmnt','--json','-o','SOURCE,FSTYPE,MAJ:MIN,TARGET','-T',str(anchor)])
anchor_mounts=json.loads(created_mount['stdout']).get('filesystems',[]) if created_mount['exit']==0 else []
anchor_mapping=guest_local_mapping(anchor_mounts,devices.get('blockdevices',[]))
anchor_receipt={'path':str(anchor),'device':anchor_info.st_dev,'inode':anchor_info.st_ino,'owner_uid':anchor_info.st_uid,'mode':oct(anchor_info.st_mode&0o777),'prospective_parent_mount':prospective,'created_mount':created_mount,'mapping':anchor_mapping,'newly_created':True}
(out/'workload-anchor.json').write_text(json.dumps(anchor_receipt,indent=2)+'\n')
prospective_mounts=json.loads(prospective['stdout']).get('filesystems',[]) if prospective['exit']==0 else []
prospective_mapping=guest_local_mapping(prospective_mounts,devices.get('blockdevices',[]))
block_observation={}
if local_mapping:
 source=Path(local_mapping['filesystem']['source']); device=Path('/sys/dev/block')/local_mapping['filesystem']['maj:min']
 block_observation={'source_is_block_device':source.is_block_device(),'sysfs_path':str(device.resolve()),'sysfs_device_major_minor':read(device/'dev'),'queue_rotational':read(Path('/sys/class/block')/local_mapping['backing_disk']/'queue/rotational')}
 if source.is_block_device():
  source_info=source.stat();block_observation['block_major_minor']=str(os.major(source_info.st_rdev))+':'+str(os.minor(source_info.st_rdev))
anchor_receipt['sysfs_block_observation']=block_observation
anchor_receipt['prospective_mapping']=prospective_mapping
(out/'workload-anchor.json').write_text(json.dumps(anchor_receipt,indent=2)+'\n')
storage_errors=guest_storage_errors(local_mapping,anchor_mapping,prospective_mapping,block_observation)
if anchor_info.st_uid!=0 or anchor_info.st_mode&0o777!=0o755 or anchor.is_symlink():storage_errors.append('workload anchor protection mismatch')
provider['provider_documented_SSD_class_applicable']=not provider_errors and not storage_errors
provider['guest_local_block_mapping_verified']=not storage_errors
(out/'provider-class-evidence.json').write_text(json.dumps(provider,indent=2)+'\n')
failures=[]
if platform.system()!='Linux':failures.append('native Linux required')
failures.extend(allocation_failures(allocated,limit,affinity,quota,mem))
failures.extend(cgroup_errors)
if accelerators:failures.append('GPU devices observed')
failures.extend(provider_errors)
failures.extend(storage_errors)
stat=os.statvfs(out); free=stat.f_bavail*stat.f_frsize
if free<12*1024**3:failures.append('insufficient owned local disk space')
reference_comparison_failures=list(failures)
architecture=platform.machine()
status,failures=preflight_outcome(architecture,platform.system(),reference_comparison_failures,free)
if architecture in ['aarch64','arm64'] and (provider_errors or storage_errors):status,failures='UNEVALUATED',provider_errors+storage_errors
receipt={'status':status,'reference_comparison_failures':reference_comparison_failures,'reference_qualified':status=='REFERENCE_PREFLIGHT_PASSED','failures':failures,'platform':platform.platform(),'architecture':platform.machine(),'affinity_cpu_count':affinity,'visible_distinct_core_count':len(cores),'memtotal_bytes':mem,'memtotal_semantics':'usable guest RAM after kernel/hardware reservations; not installed allocation','installed_memory_bytes':allocated,'installed_memory_evidence_kind':'actual populated SMBIOS type17 guest memory devices; firmware-reported allocation, not cryptographically authenticated cloud allocation; usable MemTotal cross-checked separately','installed_memory_devices':allocation_devices,'installed_memory_observation':memory_devices,'cgroup_memory_max':limit,'cgroup_ancestry':cgroup_rows,'cgroup_mounts':mount_records,'process_cgroup_paths':process_cgroups,'cgroup_observation_errors':cgroup_errors,'effective_memory_bytes':capacity,'cgroup_cpu_max':cpu_max,'effective_cpu_quota':quota,'cpu':cpu,'blockdevices':disk,'provider_class':provider,'workload_anchor':anchor_receipt,'filesystem':mount,'owned_mount_backing':backing,'free_bytes':free,'gpu_devices':accelerators,'process_snapshot':raw(['ps','-eo','pid,ppid,uid,comm']),'nominal_runner_allocation':'4 vCPU / 16 GB; marketing allocation is not guest GiB proof','job_exclusive':True,'shared_physical_infrastructure':True,'workflow_run':os.environ.get('GITHUB_RUN_ID'),'workflow_attempt':os.environ.get('GITHUB_RUN_ATTEMPT'),'runner':os.environ.get('RUNNER_NAME'),'architecture_role':'amd64 reference' if platform.machine()=='x86_64' else 'separate native ARM observation'}
(out/'hardware.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps({'status':receipt['status'],'failures':failures}))
raise SystemExit(2 if failures else 0)
PREFLIGHT
  exit $?
fi
[[ "$mode" == run && "$(id -u)" == 0 && "$(uname -s)" == Linux ]]
py="${3:?installed RC4 Python}"; harness="${4:?reviewed checkout}"; lockroot="${5:?immutable runtime lock checkout}"
# No changes to preexisting /etc descriptors, services, identities or paths.
[[ -d /var/lib/magicite-e6 && ! -L /var/lib/magicite-e6 ]]
"$py" - "$out" "$harness" "$lockroot" <<'EXECUTE'
import hashlib,json,os,pwd,subprocess,sys,time
from pathlib import Path
from magicite.core.trust import default_policy
out,harness,lockroot=map(Path,sys.argv[1:]); py=sys.executable
hardware=json.loads((out/'hardware.json').read_bytes())
if hardware['status'] not in ['REFERENCE_PREFLIGHT_PASSED','ARM_OBSERVATION_READY']:raise ValueError('reference preflight or native observation readiness required before execution')
if json.loads(subprocess.check_output([py,'-c',"import importlib.metadata,json;print(json.dumps(importlib.metadata.version('magicite')))" ]))!='1.0.0rc4':raise ValueError('published RC4 subject required')
base=Path('/var/lib/magicite-e6')
anchor=json.loads((out/'workload-anchor.json').read_bytes());info=base.lstat()
if anchor['path']!=str(base) or info.st_dev!=anchor['device'] or info.st_ino!=anchor['inode'] or info.st_uid!=0 or info.st_mode&0o777!=0o755 or base.is_symlink() or any(base.iterdir()):raise ValueError('preflight-created workload anchor changed or nonempty')
def observed_mount(path):
 rows=json.loads(subprocess.check_output(['findmnt','--json','-o','SOURCE,FSTYPE,MAJ:MIN,TARGET','-T',str(path)],text=True))['filesystems']
 if len(rows)!=1 or rows[0]!=anchor['mapping']['filesystem']:raise ValueError('actual provisioned workload moved to a different guest block mount')
 return rows[0]
observed_mount(base)
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
 mounts=[{'root':row['root'],'filesystem':observed_mount(Path(row['root']))} for row in receipts]
 (out/'workload-mounts-before-timing.json').write_text(json.dumps(mounts,indent=2)+'\n')
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
