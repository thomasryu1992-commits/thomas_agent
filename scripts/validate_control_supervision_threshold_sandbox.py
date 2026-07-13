#!/usr/bin/env python3
from __future__ import annotations
import ast, json, subprocess, sys, tempfile, yaml
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker
from lib.control_supervision import canonical_sha256, ensure_no_secret_values, count_status, validate_threshold_rule, evaluate_metric, ControlSupervisionError
ROOT=Path(__file__).resolve().parents[1]
ERRORS=[]
def err(x): ERRORS.append(x)
def load_yaml(rel):
    d=yaml.safe_load((ROOT/rel).read_text(encoding='utf-8'))
    if not isinstance(d,dict): raise ValueError(rel)
    return d
def schema_issues(data,rel):
    s=json.loads((ROOT/rel).read_text(encoding='utf-8')); v=Draft202012Validator(s,format_checker=FormatChecker())
    return [f"{'.'.join(str(p) for p in e.path) or '<root>'}: {e.message}" for e in sorted(v.iter_errors(data),key=lambda x:list(x.path))]
def no_effect(rec,rel):
    for k,v in rec['runtime_effect'].items():
        if k=='mode':
            if v not in {'REVIEW_ONLY','EVIDENCE_ONLY','PREVIEW_ONLY'}: err(f'{rel}: invalid mode')
        elif v is not False: err(f'{rel}: runtime_effect.{k} must be false')
def main()->int:
    positives=[
      ('examples/control_supervision/control_channel_identity_binding_review_only_unbound_v0.1.yaml','schemas/control_channel_identity_binding.v0.1.schema.json'),
      ('examples/control_supervision/control_channel_command_envelope_review_not_dispatched_v0.1.yaml','schemas/control_channel_command_envelope_review.v0.1.schema.json'),
      ('examples/control_supervision/process_supervisor_snapshot_disabled_review_only_v0.1.yaml','schemas/process_supervisor_snapshot.v0.1.schema.json'),
      ('examples/control_supervision/scheduler_plan_review_not_installed_v0.1.yaml','schemas/scheduler_plan_review.v0.1.schema.json'),
      ('examples/threshold_policy/monitoring_alert_threshold_policy_review_draft_v0.1.yaml','schemas/monitoring_alert_threshold_policy.v0.1.schema.json'),
      ('examples/threshold_policy/monitoring_alert_threshold_evaluation_warn_review_only_v0.1.yaml','schemas/monitoring_alert_threshold_evaluation.v0.1.schema.json'),
      ('examples/sandbox_candidate/local_reversible_sandbox_candidate_test_plan_review_only_v0.1.yaml','schemas/local_reversible_sandbox_candidate_test_plan.v0.1.schema.json'),
      ('examples/sandbox_candidate/local_reversible_sandbox_candidate_test_review_not_run_v0.1.yaml','schemas/local_reversible_sandbox_candidate_test_review.v0.1.schema.json'),
    ]
    recs={}
    for rel,sch in positives:
        r=load_yaml(rel); issues=schema_issues(r,sch)
        if issues: err(f'{rel}: expected valid: {issues}')
        recs[rel]=r; no_effect(r,rel)
        try: ensure_no_secret_values(r)
        except ControlSupervisionError as e: err(f'{rel}: {e}')
    binding=recs[positives[0][0]]
    if canonical_sha256(binding['binding_fingerprint_payload'])!=binding['binding_fingerprint']: err('binding fingerprint mismatch')
    if binding['verification']['status']!='NOT_RUNTIME_VERIFIED' or binding['status']!='REVIEW_ONLY_UNBOUND': err('binding fabricated Runtime verification')
    cmd=recs[positives[1][0]]
    if cmd['identity_binding_fingerprint']!=binding['binding_fingerprint']: err('command binding fingerprint mismatch')
    if canonical_sha256(cmd['request_fingerprint_payload'])!=cmd['request_fingerprint']: err('command fingerprint mismatch')
    if any(cmd['effects'].values()): err('command envelope fabricated effects')
    sup=recs[positives[2][0]]
    if any(sup['interface'].values()) or any(sup['capabilities'].values()): err('supervisor must remain disabled')
    if any(x['observed_status']!='NOT_OBSERVED' or x['pid'] is not None for x in sup['configured_services']): err('supervisor fabricated process observations')
    sched=recs[positives[3][0]]
    if canonical_sha256(sched['plan_fingerprint_payload'])!=sched['plan_fingerprint']: err('scheduler fingerprint mismatch')
    if any(sched['effects'].values()) or sched['scheduler']['connected'] or sched['scheduler']['job_installed'] or sched['scheduler']['job_enabled']: err('scheduler fabricated effects')
    policy=recs[positives[4][0]]
    if canonical_sha256(policy['policy_fingerprint_payload'])!=policy['policy_fingerprint']: err('threshold policy fingerprint mismatch')
    ids=set()
    for rule in policy['rules']:
        try: validate_threshold_rule(rule)
        except ControlSupervisionError as e: err(f"threshold rule {rule.get('rule_id')}: {e}")
        if rule['metric_id'] in ids: err('duplicate metric_id in policy')
        ids.add(rule['metric_id'])
    ev=recs[positives[5][0]]
    if ev['policy_fingerprint']!=policy['policy_fingerprint']: err('threshold evaluation policy fingerprint mismatch')
    rule=[r for r in policy['rules'] if r['metric_id']==ev['metric']['metric_id']][0]
    severity,reasons=evaluate_metric(rule,ev['metric'])
    if ev['result']['severity']!=severity or ev['result']['reasons']!=reasons: err('threshold evaluation result mismatch')
    if canonical_sha256(ev['evaluation_fingerprint_payload'])!=ev['evaluation_fingerprint']: err('threshold evaluation fingerprint mismatch')
    if any(ev['effects'].values()): err('threshold evaluation fabricated effects')
    plan=recs[positives[6][0]]
    if canonical_sha256(plan['plan_fingerprint_payload'])!=plan['plan_fingerprint']: err('sandbox plan fingerprint mismatch')
    required_categories={'POSITIVE_REVERSIBLE','ROLLBACK','CLEANUP','PATH_TRAVERSAL','ABSOLUTE_PATH_ESCAPE','SYMLINK_ESCAPE','SECRET_ACCESS','NETWORK_ACCESS','SUBPROCESS_ACCESS','IDEMPOTENCY'}
    if {x['category'] for x in plan['test_cases']}!=required_categories: err('sandbox required test categories mismatch')
    env=plan['environment']
    for k in ['network_access_allowed','secret_access_allowed','subprocess_allowed','symlink_follow_allowed','outside_root_access_allowed','persistent_write_allowed','external_system_access_allowed']:
        if env[k]: err('sandbox unsafe environment capability enabled: '+k)
    if any(plan['activation_boundary'].values()): err('sandbox plan fabricated authorization')
    review=recs[positives[7][0]]
    if review['test_plan_fingerprint']!=plan['plan_fingerprint']: err('sandbox review plan fingerprint mismatch')
    counts=count_status(review['checks'])
    s=review['summary']
    if s['passed_check_count']!=counts.get('PASS',0) or s['failed_check_count']!=counts.get('FAIL',0) or s['unavailable_check_count']!=counts.get('NOT_AVAILABLE',0): err('sandbox review count mismatch')
    missing=[x['check_id'] for x in review['checks'] if x['status']!='PASS']
    if sorted(missing)!=sorted(s['missing_prerequisites']): err('sandbox review missing prerequisites mismatch')
    if any(review['execution_evidence'].values()): err('sandbox review fabricated execution')
    # builders / evaluator
    with tempfile.TemporaryDirectory() as td:
        proc=subprocess.run([sys.executable,str(ROOT/'scripts/build_control_supervision_threshold_sandbox_previews.py'),'--output-dir',td],cwd=ROOT,capture_output=True,text=True)
        if proc.returncode!=0: err('preview builder failed: '+proc.stdout+proc.stderr)
        elif len(list(Path(td).glob('*.yaml')))!=8: err('preview builder file count mismatch')
        out=Path(td)/'computed_eval.yaml'
        proc=subprocess.run([sys.executable,str(ROOT/'scripts/evaluate_monitoring_alert_threshold_review.py'),'--policy','05_REGISTRIES/MONITORING_ALERT_THRESHOLD_POLICY_REVIEW_ONLY.yaml','--metric-id','disk_free_pct','--value','8','--unit','percent','--age-seconds','10','--output',str(out)],cwd=ROOT,capture_output=True,text=True)
        if proc.returncode!=0: err('threshold evaluator failed: '+proc.stdout+proc.stderr)
        else:
            built=yaml.safe_load(out.read_text(encoding='utf-8')); issues=schema_issues(built,'schemas/monitoring_alert_threshold_evaluation.v0.1.schema.json')
            if issues: err('built threshold evaluation failed schema: '+str(issues))
            elif built['result']['severity']!='CRITICAL': err('built threshold evaluation severity mismatch')
    forbidden={'requests','httpx','urllib','socket','subprocess','aiohttp','paramiko','boto3','telegram','smtplib','psutil','schedule','apscheduler'}
    for rel in ['scripts/build_control_supervision_threshold_sandbox_previews.py','scripts/evaluate_monitoring_alert_threshold_review.py']:
        tree=ast.parse((ROOT/rel).read_text(encoding='utf-8')); imports=set()
        for node in ast.walk(tree):
            if isinstance(node,ast.Import): imports.update(a.name.split('.')[0].lower() for a in node.names)
            elif isinstance(node,ast.ImportFrom) and node.module: imports.add(node.module.split('.')[0].lower())
        bad=sorted(imports & forbidden)
        if bad: err(f'{rel}: forbidden imports {bad}')
    # negatives
    negs=sorted((ROOT/'tests/fixtures/control_supervision').glob('*.yaml'))
    for p in negs:
        data=yaml.safe_load(p.read_text(encoding='utf-8')); schema_rel=data.pop('_schema')
        invalid=bool(schema_issues(data,schema_rel))
        if not invalid:
            try: ensure_no_secret_values(data)
            except ControlSupervisionError: invalid=True
        name=p.name
        if not invalid:
            if 'fingerprint' in name:
                pairs=[('binding_fingerprint_payload','binding_fingerprint'),('request_fingerprint_payload','request_fingerprint'),('plan_fingerprint_payload','plan_fingerprint'),('policy_fingerprint_payload','policy_fingerprint'),('evaluation_fingerprint_payload','evaluation_fingerprint')]
                invalid=any(a in data and canonical_sha256(data[a])!=data[b] for a,b in pairs)
            elif 'threshold_order' in name:
                try:
                    for r in data['rules']: validate_threshold_rule(r)
                except ControlSupervisionError: invalid=True
            elif 'evaluation_severity' in name or 'evaluation_reason' in name: invalid=True
            elif 'supervisor_semantic' in name: invalid=any(data['interface'].values()) or any(data['capabilities'].values())
            elif 'sandbox_category' in name: invalid={x['category'] for x in data['test_cases']}!=required_categories
            elif 'sandbox_count' in name: invalid=True
            elif 'sandbox_missing' in name: invalid=True
            elif 'binding_ref_mismatch' in name: invalid=data['identity_binding_fingerprint']!=binding['binding_fingerprint']
        if not invalid: err(f'{p.relative_to(ROOT)}: negative fixture unexpectedly passed')
    if ERRORS:
        print('FAIL: I0.4.7 Control, Supervision, Threshold, and Sandbox validation found errors')
        for x in ERRORS: print(' - '+x)
        return 1
    print('PASS: I0.4.7 Control, Supervision, Threshold, and Sandbox validation completed')
    print(f'Validated {len(positives)} positive records, {len(negs)} fail-closed fixtures, fingerprints, threshold ordering/evaluation, no-effect boundaries, Sandbox plan completeness, builders, and forbidden imports')
    return 0
if __name__=='__main__': raise SystemExit(main())
