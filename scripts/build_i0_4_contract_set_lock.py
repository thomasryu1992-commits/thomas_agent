#!/usr/bin/env python3
"""One-shot generator (STATUS: dormant by design, no automated caller).

Regenerates ``generated/legacy/i0_4_consolidation/I0_4_CONTRACT_SET_LOCK.yaml`` from the
frozen I0.4 contract-set index. The committed lock is the artifact of record; this script
exists only to rebuild it if the frozen set were ever deliberately changed — a legacy
governance decision, not a routine operation. Kept because the lock's ``generated_by``
names it; nothing in CI, the gates, or the runtime invokes it.
"""
from __future__ import annotations
import argparse, hashlib, yaml
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def sha(p): return 'sha256:'+hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--output',default='generated/legacy/i0_4_consolidation/I0_4_CONTRACT_SET_LOCK.yaml'); a=ap.parse_args()
 idx=yaml.safe_load((ROOT/'05_REGISTRIES/I0_4_RUNTIME_CONTRACT_SET_INDEX.yaml').read_text(encoding='utf-8'))
 paths=[]
 paths += [x['path'] for x in idx['baseline_dependencies']]
 paths += [x['contract_path'] for x in idx['record_contracts']]
 paths += [x['schema_path'] for x in idx['record_contracts']]
 paths += [x['path'] for x in idx['non_schema_documents']]
 paths += [x['path'] for x in idx['focused_validators']]
 paths += ['05_REGISTRIES/I0_4_RUNTIME_CONTRACT_SET_INDEX.yaml','schemas/i0_4_runtime_contract_set_index.v0.1.schema.json','historical/runtime-contracts/i0_4/I0_4_RUNTIME_CONTRACT_SET_INDEX_V0.1.md','historical/runtime-contracts/i0_4/I0_4_CONSOLIDATION_CHECKPOINT_V0.1.md','historical/runtime-contracts/i0_4/I0_4_CONSOLIDATION_REVIEW_ONLY_BOUNDARY_V0.1.md']
 uniq=sorted(set(paths)); entries=[]
 for rel in uniq:
  p=ROOT/rel
  if not p.exists(): raise SystemExit('missing indexed lock input: '+rel)
  entries.append({'path':rel,'sha256':sha(p),'size_bytes':p.stat().st_size})
 h=hashlib.sha256()
 for e in entries: h.update((e['path']+'\0'+e['sha256']+'\0'+str(e['size_bytes'])+'\n').encode())
 out={'schema_version':'i0_4_contract_set_lock.v0.1','status':'REVIEW_ONLY_EVIDENCE','generated_at_utc':datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z'),'generated_by':'scripts/build_i0_4_contract_set_lock.py','contract_set_sha256':'sha256:'+h.hexdigest(),'source_file_count':len(entries),'files':entries,'scope':{'grants_runtime_permission':False,'grants_runtime_activation':False,'grants_executor_activation':False,'authorizes_i0_5_implementation':False}}
 op=ROOT/a.output; op.parent.mkdir(parents=True,exist_ok=True); op.write_text(yaml.safe_dump(out,sort_keys=False,allow_unicode=True),encoding='utf-8',newline='\n')
 print('PASS: wrote I0.4 contract-set lock evidence'); print(op.relative_to(ROOT).as_posix()); print(out['contract_set_sha256']); return 0
if __name__=='__main__': raise SystemExit(main())
