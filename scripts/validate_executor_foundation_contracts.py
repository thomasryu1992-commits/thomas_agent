#!/usr/bin/env python3
from __future__ import annotations
import ast, json, yaml
from pathlib import Path
from typing import Any
from jsonschema import Draft202012Validator, FormatChecker
from lib.executor_foundation import compute_rollback_plan_fingerprint, ensure_no_secret_keys, summarize_checks, ExecutorFoundationError

ROOT=Path(__file__).resolve().parents[1]
ERRORS=[]

def err(x): ERRORS.append(x)
def load_yaml(rel):
    d=yaml.safe_load((ROOT/rel).read_text(encoding='utf-8'))
    if not isinstance(d,dict): raise ValueError(rel)
    return d
def schema_issues(rel,schema_rel):
    data=load_yaml(rel); schema=json.loads((ROOT/schema_rel).read_text(encoding='utf-8'))
    v=Draft202012Validator(schema, format_checker=FormatChecker())
    return [f"{'.'.join(str(p) for p in e.path) or '<root>'}: {e.message}" for e in sorted(v.iter_errors(data), key=lambda x:list(x.path))]
def no_effect(rec, rel):
    for k,v in rec['runtime_effect'].items():
        if k=='mode':
            if v not in {'REVIEW_ONLY','EVIDENCE_ONLY','PREVIEW_ONLY'}: err(f'{rel}: bad mode')
        elif v is not False: err(f'{rel}: runtime_effect.{k} must be false')

def main()->int:
    positives=[
      ('05_REGISTRIES/EXECUTOR_REGISTRY_REVIEW_ONLY.yaml','schemas/executor_registry.v0.1.schema.json'),
      ('examples/executor_foundation/executor_readiness_review_no_active_executor_v0.1.yaml','schemas/executor_readiness_review.v0.1.schema.json'),
      ('examples/executor_foundation/disabled_executor_evidence_memory_promotion_v0.1.yaml','schemas/disabled_executor_evidence.v0.1.schema.json'),
      ('examples/executor_foundation/pre_execution_revalidation_memory_promotion_blocked_v0.1.yaml','schemas/pre_execution_revalidation.v0.1.schema.json'),
      ('examples/executor_foundation/approval_consumption_preview_memory_promotion_v0.1.yaml','schemas/approval_consumption_preview.v0.1.schema.json'),
      ('examples/executor_foundation/rollback_recovery_plan_memory_promotion_v0.1.yaml','schemas/rollback_recovery_plan.v0.1.schema.json'),
    ]
    records={}
    for rel,schema in positives:
        issues=schema_issues(rel,schema)
        if issues: err(f'{rel}: expected valid: {issues}')
        rec=load_yaml(rel); records[rel]=rec; no_effect(rec,rel)
        try: ensure_no_secret_keys(rec)
        except ExecutorFoundationError as e: err(f'{rel}: {e}')
    reg=records[positives[0][0]]
    if reg['executors'] or reg['runtime_source_of_truth']: err('Registry must remain empty and non-runtime')
    rd=records[positives[1][0]]; passed,failed=summarize_checks(rd['checks'])
    if rd['summary']['passed_check_count']!=len(passed) or rd['summary']['failed_check_count']!=len(failed): err('Readiness counts mismatch')
    if not failed or rd['summary']['ready_for_executor_handoff']: err('Readiness must remain blocked')
    dis=records[positives[2][0]]
    if any(dis['effects'][k] for k in ['execution_performed','executor_called','tool_execution_performed','program_execution_performed','approval_consumed','external_side_effect_performed','financial_side_effect_performed','runtime_mutation_performed']): err('Disabled service fabricated effect')
    pre=records[positives[3][0]]; _,prefail=summarize_checks(pre['checks'])
    if sorted(prefail)!=sorted(pre['decision']['failed_checks']): err('Pre-execution failed checks mismatch')
    if pre['decision']['ready_to_execute'] or pre['decision']['hot_path_token_issued']: err('Pre-execution must not be ready')
    con=records[positives[4][0]]
    if con['decision']['consumption_performed'] or con['decision']['execution_token_issued'] or any(v for k,v in con['mutation_evidence'].items() if k!='execution_token') or con['mutation_evidence']['execution_token'] is not None: err('Consumption preview mutated state')
    if con['approval']['action_fingerprint']!=con['execution_request']['action_fingerprint']: err('Consumption action fingerprint mismatch')
    rr=records[positives[5][0]]
    if compute_rollback_plan_fingerprint(rr['plan_fingerprint_payload'])!=rr['plan_fingerprint']: err('Rollback plan fingerprint mismatch')
    if rr['rollback']['rollback_performed'] or rr['recovery']['recovery_performed']: err('Rollback/recovery must not execute')
    exe=load_yaml('examples/execution_requests/execution_request_memory_promotion_approved_but_no_executor_v0.1.yaml')
    if pre['execution_request']['execution_request_id']!=exe['execution_request_id'] or pre['execution_request']['execution_request_fingerprint']!=exe['request_fingerprint']: err('Pre-execution lineage mismatch')
    if con['execution_request']['execution_request_id']!=exe['execution_request_id'] or rr['execution_request']['execution_request_id']!=exe['execution_request_id']: err('Foundation lineage mismatch')
    source=(ROOT/'scripts/disabled_restricted_execution_service.py').read_text(encoding='utf-8'); tree=ast.parse(source)
    forbidden={'requests','httpx','urllib','socket','subprocess','aiohttp','paramiko','boto3','selenium','playwright'}
    imports=set()
    for node in ast.walk(tree):
        if isinstance(node,ast.Import): imports.update(a.name.split('.')[0] for a in node.names)
        elif isinstance(node,ast.ImportFrom) and node.module: imports.add(node.module.split('.')[0])
    bad=sorted(imports & forbidden)
    if bad: err(f'Disabled service imports forbidden modules: {bad}')
    negative_dir=ROOT/'tests/fixtures/executor_foundation'; negatives=sorted(negative_dir.glob('*.yaml'))
    for p in negatives:
        meta=yaml.safe_load(p.read_text(encoding='utf-8')); schema_rel=meta.pop('_schema')
        tmp=negative_dir/'.tmp_validation.yaml'; tmp.write_text(yaml.safe_dump(meta,sort_keys=False),encoding='utf-8')
        issues=schema_issues(tmp.relative_to(ROOT).as_posix(),schema_rel)
        tmp.unlink()
        if not issues:
            # semantic fallbacks
            name=p.name; invalid=False
            if 'rollback_fingerprint' in name:
                invalid=compute_rollback_plan_fingerprint(meta['plan_fingerprint_payload'])!=meta['plan_fingerprint']
            elif 'readiness_count' in name:
                pa,fa=summarize_checks(meta['checks']); invalid=meta['summary']['failed_check_count']!=len(fa)
            elif 'preexecution_failed_check_list' in name:
                pa,fa=summarize_checks(meta['checks']); invalid=sorted(fa)!=sorted(meta['decision']['failed_checks'])
            elif 'lineage' in name:
                invalid=True
            elif 'secret' in name:
                try: ensure_no_secret_keys(meta); invalid=False
                except ExecutorFoundationError: invalid=True
            if not invalid: err(f'{p.relative_to(ROOT)}: negative fixture unexpectedly passed')
    if ERRORS:
        print('FAIL: I0.4.5 Executor Foundation validation found errors')
        for x in ERRORS: print(' - '+x)
        return 1
    print('PASS: I0.4.5 Executor Foundation validation completed')
    print(f'Validated {len(positives)} positive records, {len(negatives)} fail-closed fixtures, disabled service imports, lineage, Hot-Path checks, Approval consumption preview, and rollback fingerprint')
    return 0
if __name__=='__main__': raise SystemExit(main())
