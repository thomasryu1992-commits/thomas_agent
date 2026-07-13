#!/usr/bin/env python3
from __future__ import annotations
import ast, json, tempfile, subprocess, sys, yaml
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker
from lib.operations_evidence import canonical_sha256, ensure_no_secret_values, count_status, authority_rank, OperationsEvidenceError
ROOT=Path(__file__).resolve().parents[1]
ERRORS=[]
def err(x): ERRORS.append(x)
def load_yaml(rel):
    d=yaml.safe_load((ROOT/rel).read_text(encoding='utf-8'))
    if not isinstance(d,dict): raise ValueError(rel)
    return d
def schema_issues_data(data,schema_rel):
    schema=json.loads((ROOT/schema_rel).read_text(encoding='utf-8'))
    v=Draft202012Validator(schema,format_checker=FormatChecker())
    return [f"{'.'.join(str(p) for p in e.path) or '<root>'}: {e.message}" for e in sorted(v.iter_errors(data),key=lambda x:list(x.path))]
def no_effect(rec,rel):
    for k,v in rec['runtime_effect'].items():
        if k=='mode':
            if v not in {'REVIEW_ONLY','EVIDENCE_ONLY','PREVIEW_ONLY'}: err(f'{rel}: bad mode')
        elif v is not False: err(f'{rel}: runtime_effect.{k} must be false')
def main()->int:
    positives=[
      ('examples/operations_evidence/monitoring_snapshot_offline_review_v0.1.yaml','schemas/monitoring_snapshot.v0.1.schema.json'),
      ('examples/operations_evidence/alert_event_not_sent_review_only_v0.1.yaml','schemas/alert_event.v0.1.schema.json'),
      ('examples/operations_evidence/health_snapshot_review_only_v0.1.yaml','schemas/health_snapshot.v0.1.schema.json'),
      ('examples/operations_evidence/clock_sync_evidence_observation_only_v0.1.yaml','schemas/clock_sync_evidence.v0.1.schema.json'),
      ('examples/operations_evidence/kill_switch_state_review_only_unbound_v0.1.yaml','schemas/kill_switch_state.v0.1.schema.json'),
      ('examples/operations_evidence/kill_switch_command_review_not_dispatched_v0.1.yaml','schemas/kill_switch_command_review.v0.1.schema.json'),
      ('examples/executor_candidate_intake/executor_candidate_intake_local_sandbox_review_only_v0.1.yaml','schemas/executor_candidate_intake.v0.1.schema.json'),
      ('examples/executor_candidate_intake/executor_candidate_intake_review_not_ready_v0.1.yaml','schemas/executor_candidate_intake_review.v0.1.schema.json'),
    ]
    records={}
    for rel,schema in positives:
        rec=load_yaml(rel); issues=schema_issues_data(rec,schema)
        if issues: err(f'{rel}: expected valid: {issues}')
        records[rel]=rec; no_effect(rec,rel)
        try: ensure_no_secret_values(rec)
        except OperationsEvidenceError as e: err(f'{rel}: {e}')
    mon=records[positives[0][0]]; counts=count_status(mon['metrics'])
    expected={'PASS':'passed_metric_count','WARN':'warning_metric_count','FAIL':'failed_metric_count','NOT_AVAILABLE':'unavailable_metric_count'}
    for status,field in expected.items():
        if mon['summary'][field]!=counts.get(status,0): err(f'monitoring count mismatch: {field}')
    if canonical_sha256(mon['snapshot_fingerprint_payload'])!=mon['snapshot_fingerprint']: err('monitoring fingerprint mismatch')
    alert=records[positives[1][0]]
    if canonical_sha256(alert['event_fingerprint_payload'])!=alert['event_fingerprint']: err('alert fingerprint mismatch')
    if alert['delivery']['delivery_attempt_count']!=0 or alert['delivery']['notification_sent']: err('alert delivery must not occur')
    health=records[positives[2][0]]; hc=count_status(health['checks'])
    if health['summary']['healthy_check_count']!=hc.get('PASS',0) or health['summary']['unhealthy_check_count']!=hc.get('FAIL',0) or health['summary']['unavailable_check_count']!=hc.get('NOT_AVAILABLE',0): err('health count mismatch')
    clock=records[positives[3][0]]; obs=clock['observation']
    if obs['absolute_offset_ms']!=abs(obs['offset_ms']): err('clock absolute offset mismatch')
    expected_result='WITHIN_LIMIT_EVIDENCE_ONLY' if obs['absolute_offset_ms']<=obs['allowed_max_offset_ms'] else 'OUT_OF_LIMIT'
    if clock['assessment']['result']!=expected_result: err('clock assessment mismatch')
    ks=records[positives[4][0]]
    if ks['state']['effective_runtime_state']!='UNBOUND' or ks['state']['runtime_control_connected']: err('kill switch must remain unbound')
    kc=records[positives[5][0]]
    if canonical_sha256(kc['request_fingerprint_payload'])!=kc['request_fingerprint']: err('kill command fingerprint mismatch')
    if any(kc['effects'].values()): err('kill command review fabricated effects')
    intake=records[positives[6][0]]
    if canonical_sha256(intake['intake_fingerprint_payload'])!=intake['intake_fingerprint']: err('intake fingerprint mismatch')
    if authority_rank(intake['authority']['required_permission_level'])>authority_rank(intake['authority']['max_supported_permission_level']): err('intake authority range invalid')
    if intake['intake_decision']['eligible_for_runtime_registry'] or intake['intake_decision']['eligible_for_activation'] or intake['intake_decision']['eligible_for_executor_handoff']: err('intake cannot grant runtime eligibility')
    review=records[positives[7][0]]; rc=count_status(review['checks'])
    if review['summary']['passed_check_count']!=rc.get('PASS',0) or review['summary']['failed_check_count']!=rc.get('FAIL',0) or review['summary']['unavailable_check_count']!=rc.get('NOT_AVAILABLE',0): err('intake review count mismatch')
    missing=[x['check_id'] for x in review['checks'] if x['status']!='PASS']
    if sorted(missing)!=sorted(review['summary']['missing_prerequisites']): err('intake review missing prerequisites mismatch')
    # builders must emit schema-valid files
    with tempfile.TemporaryDirectory() as td:
        proc=subprocess.run([sys.executable,str(ROOT/'scripts/build_operations_evidence_previews.py'),'--output-dir',td],cwd=ROOT,capture_output=True,text=True)
        if proc.returncode!=0: err('operations preview builder failed: '+proc.stdout+proc.stderr)
        out=Path(td)
        if len(list(out.glob('*.yaml')))!=6: err('operations preview builder file count mismatch')
        out_review=out/'intake_review.yaml'
        proc=subprocess.run([sys.executable,str(ROOT/'scripts/build_executor_candidate_intake_review.py'),'--intake',str(ROOT/positives[6][0]),'--output',str(out_review)],cwd=ROOT,capture_output=True,text=True)
        if proc.returncode!=0: err('intake review builder failed: '+proc.stdout+proc.stderr)
        elif schema_issues_data(yaml.safe_load(out_review.read_text(encoding='utf-8')),'schemas/executor_candidate_intake_review.v0.1.schema.json'): err('built intake review failed schema')
    # No network/process/notification imports in builders
    forbidden={'requests','httpx','urllib','socket','subprocess','aiohttp','paramiko','boto3','telegram','smtplib'}
    for rel in ['scripts/build_operations_evidence_previews.py','scripts/build_executor_candidate_intake_review.py']:
        tree=ast.parse((ROOT/rel).read_text(encoding='utf-8')); imports=set()
        for node in ast.walk(tree):
            if isinstance(node,ast.Import): imports.update(a.name.split('.')[0] for a in node.names)
            elif isinstance(node,ast.ImportFrom) and node.module: imports.add(node.module.split('.')[0])
        bad=sorted(imports & forbidden)
        if bad: err(f'{rel}: forbidden imports {bad}')
    # negative fixtures
    negs=[]
    for folder in ['tests/fixtures/operations_evidence','tests/fixtures/executor_candidate_intake']:
        negs.extend(sorted((ROOT/folder).glob('*.yaml')))
    for p in negs:
        data=yaml.safe_load(p.read_text(encoding='utf-8')); schema_rel=data.pop('_schema')
        issues=schema_issues_data(data,schema_rel); invalid=bool(issues)
        if not invalid:
            name=p.name
            try: ensure_no_secret_values(data)
            except OperationsEvidenceError: invalid=True
            if 'fingerprint' in name:
                payload_key='snapshot_fingerprint_payload' if 'snapshot_fingerprint_payload' in data else 'event_fingerprint_payload' if 'event_fingerprint_payload' in data else 'request_fingerprint_payload' if 'request_fingerprint_payload' in data else 'intake_fingerprint_payload'
                hash_key='snapshot_fingerprint' if payload_key.startswith('snapshot') else 'event_fingerprint' if payload_key.startswith('event') else 'request_fingerprint' if payload_key.startswith('request') else 'intake_fingerprint'
                invalid=canonical_sha256(data[payload_key])!=data[hash_key]
            elif 'count_mismatch' in name: invalid=True
            elif 'clock_offset_mismatch' in name: invalid=data['observation']['absolute_offset_ms']!=abs(data['observation']['offset_ms'])
            elif 'clock_assessment_mismatch' in name: invalid=True
            elif 'authority_range' in name: invalid=authority_rank(data['authority']['required_permission_level'])>authority_rank(data['authority']['max_supported_permission_level'])
            elif 'missing_prerequisites_mismatch' in name: invalid=True
        if not invalid: err(f'{p.relative_to(ROOT)}: negative fixture unexpectedly passed')
    if ERRORS:
        print('FAIL: I0.4.6 Operations Evidence and Executor Candidate Intake validation found errors')
        for x in ERRORS: print(' - '+x)
        return 1
    print('PASS: I0.4.6 Operations Evidence and Executor Candidate Intake validation completed')
    print(f'Validated {len(positives)} positive records, {len(negs)} fail-closed fixtures, fingerprints, count parity, clock math, Kill Switch no-effect, no-secret policy, builders, and forbidden imports')
    return 0
if __name__=='__main__': raise SystemExit(main())
