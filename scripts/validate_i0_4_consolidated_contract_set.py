#!/usr/bin/env python3
from __future__ import annotations
import json, sys
from pathlib import Path
import yaml
from jsonschema import Draft202012Validator
ROOT=Path(__file__).resolve().parents[1]
ERRORS=[]
def err(x): ERRORS.append(x)
def load_yaml(p): return yaml.safe_load((ROOT/p).read_text(encoding='utf-8'))
def load_json(p): return json.loads((ROOT/p).read_text(encoding='utf-8'))

def main()->int:
    idx_path='05_REGISTRIES/I0_4_RUNTIME_CONTRACT_SET_INDEX.yaml'
    sch_path='schemas/i0_4_runtime_contract_set_index.v0.1.schema.json'
    try:
        idx=load_yaml(idx_path); sch=load_json(sch_path)
    except Exception as e:
        print('FAIL: unable to load consolidation index/schema:',e); return 1
    for issue in Draft202012Validator(sch).iter_errors(idx):
        err('index schema: '+('.'.join(map(str,issue.path)) or '<root>')+': '+issue.message)
    if idx.get('runtime_source_of_truth') is not False: err('index must not be Runtime source of truth')
    c=idx.get('contract_set',{})
    for k in ['new_i0_4_contracts_allowed','grants_runtime_permission','grants_runtime_activation','grants_executor_activation','grants_external_execution','grants_financial_execution','grants_permission_expansion','grants_authority_expansion']:
        if c.get(k) is not False: err('contract_set.'+k+' must be false')
    # Baseline dependency existence in an applied Repository.
    for d in idx.get('baseline_dependencies',[]):
        if d.get('required_in_repository') and not (ROOT/d['path']).exists(): err('missing baseline dependency: '+d['path'])
    records=idx.get('record_contracts',[])
    cps=[x['contract_path'] for x in records]; sps=[x['schema_path'] for x in records]; ids=[x['record_schema'] for x in records]
    for label,vals in [('contract_path',cps),('schema_path',sps),('record_schema',ids)]:
        if len(vals)!=len(set(vals)): err('duplicate '+label+' in index')
    for x in records:
        for key in ['contract_path','schema_path']:
            if not (ROOT/x[key]).exists(): err('missing indexed '+key+': '+x[key])
        if (ROOT/x['schema_path']).exists():
            sd=load_json(x['schema_path']); const=sd.get('properties',{}).get('schema_version',{}).get('const')
            if const!=x['record_schema']: err(f"{x['schema_path']}: schema_version const {const!r} != {x['record_schema']!r}")
        if x.get('runtime_effect')!='NONE_REVIEW_ONLY': err(x['subject']+': runtime_effect must remain NONE_REVIEW_ONLY')
    for x in idx.get('non_schema_documents',[]):
        if not (ROOT/x['path']).exists(): err('missing indexed non-schema document: '+x['path'])
        if x.get('runtime_effect')!='NONE': err(x['subject']+': non-schema document runtime_effect must be NONE')
    # All I0.4-owned schemas and docs must be indexed, excluding this consolidation layer itself.
    indexed_schema=set(sps)
    actual_schema={p.relative_to(ROOT).as_posix() for p in (ROOT/'schemas').glob('*.json') if p.name!='i0_4_runtime_contract_set_index.v0.1.schema.json'}
    expected_owned={p for p in actual_schema if p.split('/')[-1] in {Path(v).name for v in sps}}
    if indexed_schema!=expected_owned:
        err('I0.4 record schema index mismatch: missing='+str(sorted(expected_owned-indexed_schema))+' extra='+str(sorted(indexed_schema-expected_owned)))
    indexed_docs=set(cps)|{x['path'] for x in idx.get('non_schema_documents',[])}
    actual_docs={p.relative_to(ROOT).as_posix() for p in (ROOT/'docs/runtime-contracts').iterdir() if p.is_file() and p.name not in {'I0_4_RUNTIME_CONTRACT_SET_INDEX_V0.1.md','I0_4_CONSOLIDATION_CHECKPOINT_V0.1.md','I0_4_CONSOLIDATION_REVIEW_ONLY_BOUNDARY_V0.1.md'}}
    # Only compare the known I0.4-owned docs; baseline repo docs are intentionally outside this index.
    known_names={Path(p).name for p in indexed_docs}
    actual_owned={p for p in actual_docs if Path(p).name in known_names}
    if indexed_docs!=actual_owned:
        err('I0.4 document index mismatch: missing='+str(sorted(actual_owned-indexed_docs))+' extra='+str(sorted(indexed_docs-actual_owned)))
    # Validator presence and fixture floors.
    fixture_map={
        'I0.4.2':['tests/fixtures/permission','tests/fixtures/approval'],
        'I0.4.3':['tests/fixtures/tool_requests','tests/fixtures/program_requests'],
        'I0.4.4':['tests/fixtures/execution_requests','tests/fixtures/execution_results','tests/fixtures/validation_results','tests/fixtures/audit'],
        'I0.4.5':['tests/fixtures/executor_foundation'],
        'I0.4.6':['tests/fixtures/operations_evidence','tests/fixtures/executor_candidate_intake'],
        'I0.4.7':['tests/fixtures/control_supervision'],
    }
    for v in idx.get('focused_validators',[]):
        if not (ROOT/v['path']).exists(): err('missing focused validator: '+v['path'])
        phase=v['phase']; count=0
        for d in fixture_map.get(phase,[]):
            path=ROOT/d
            if path.exists(): count += len(list(path.glob('*.yaml')))
        if count < v['minimum_negative_fixture_count']: err(f"{phase}: negative fixture count {count} below {v['minimum_negative_fixture_count']}")
    # Critical registry guards.
    ex=load_yaml('05_REGISTRIES/EXECUTOR_REGISTRY_REVIEW_ONLY.yaml')
    if ex.get('runtime_source_of_truth') is not False or ex.get('executors') not in ([],None): err('Executor Registry must remain non-runtime and empty')
    th=load_yaml('05_REGISTRIES/MONITORING_ALERT_THRESHOLD_POLICY_REVIEW_ONLY.yaml')
    if th.get('status')!='REVIEW_DRAFT_NOT_RUNTIME_ACTIVE': err('threshold policy must remain REVIEW_DRAFT_NOT_RUNTIME_ACTIVE')
    for rp,collection in [('05_REGISTRIES/TOOL_REGISTRY.yaml','tools'),('05_REGISTRIES/PROGRAM_REGISTRY.yaml','programs')]:
        reg=load_yaml(rp)
        for item in reg.get(collection,[]):
            if item.get('status')=='active' or item.get('enabled') is not False or item.get('runtime_implementation_available') is not False:
                err(rp+': resources must remain non-active, disabled, and without Runtime implementation at I0.4 checkpoint')
    if idx.get('next_stage',{}).get('phase')!='I0.5': err('next stage must be I0.5')
    if ERRORS:
        print('FAIL: I0.4 consolidated contract-set validation found errors')
        for e in ERRORS: print(' - '+e)
        return 1
    print('PASS: I0.4 consolidated Runtime contract-set validation completed')
    print(f"Indexed {len(records)} record contracts, {len(idx.get('non_schema_documents',[]))} policy/boundary documents, and {len(idx.get('focused_validators',[]))} focused validators")
    print('The set is frozen for I0.5 Read-only Runtime Kernel design and grants no Runtime permission or activation')
    return 0
if __name__=='__main__': raise SystemExit(main())
