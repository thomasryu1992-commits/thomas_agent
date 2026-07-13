#!/usr/bin/env python3
from __future__ import annotations
import argparse, yaml
from datetime import datetime, timezone
from pathlib import Path
from lib.control_supervision import canonical_sha256, evaluate_metric
ROOT=Path(__file__).resolve().parents[1]
def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('--policy',required=True); ap.add_argument('--metric-id',required=True); ap.add_argument('--value',type=float); ap.add_argument('--unit',required=True); ap.add_argument('--age-seconds',type=int,default=0); ap.add_argument('--data-status',choices=['AVAILABLE','MISSING','INVALID'],default='AVAILABLE'); ap.add_argument('--output',required=True); a=ap.parse_args()
    policy_path=Path(a.policy); policy_path=policy_path if policy_path.is_absolute() else ROOT/policy_path
    policy=yaml.safe_load(policy_path.read_text(encoding='utf-8'))
    rules=[r for r in policy['rules'] if r['metric_id']==a.metric_id]
    if len(rules)!=1: raise ValueError('metric-id must match exactly one policy rule')
    rule=rules[0]
    if a.unit!=rule['unit']: raise ValueError('metric unit does not match policy rule')
    metric={'metric_id':a.metric_id,'observed_value':a.value if a.data_status=='AVAILABLE' else None,'unit':a.unit,'data_status':a.data_status,'age_seconds':a.age_seconds}
    severity,reasons=evaluate_metric(rule,metric)
    now=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
    eid=f"threval_preview_{a.metric_id}"
    payload={'evaluation_id':eid,'policy_ref':str(policy_path.relative_to(ROOT)).replace('\\','/'),'policy_fingerprint':policy['policy_fingerprint'],'metric':metric}
    false_effects={'alert_event_created':False,'notification_sent':False,'remediation_performed':False,'kill_switch_triggered':False,'runtime_state_changed':False}
    runtime_keys=['control_channel_connected', 'identity_binding_activated', 'challenge_issued', 'challenge_verified', 'command_dispatched', 'supervisor_connected', 'process_control_enabled', 'process_started', 'process_stopped', 'process_restarted', 'process_killed', 'scheduler_connected', 'scheduler_job_installed', 'scheduler_job_enabled', 'scheduler_dispatch_performed', 'task_created_by_scheduler', 'monitoring_policy_activated', 'alert_delivery_performed', 'automatic_remediation_performed', 'kill_switch_triggered', 'sandbox_created', 'sandbox_test_executed', 'filesystem_write_performed', 'network_call_performed', 'subprocess_performed', 'secret_access_performed', 'executor_candidate_registered', 'executor_registry_mutation_allowed', 'executor_activation_allowed', 'executor_handoff_allowed', 'runtime_mutation_allowed', 'external_execution_allowed', 'financial_execution_allowed', 'side_effects_allowed', 'permission_expansion_allowed', 'authority_expansion_allowed']
    runtime={'mode':'PREVIEW_ONLY',**{k:False for k in runtime_keys}}
    record={'schema_version':'monitoring_alert_threshold_evaluation.v0.1','evaluation_id':eid,'policy_ref':payload['policy_ref'],'policy_fingerprint':policy['policy_fingerprint'],'metric':metric,'result':{'severity':severity,'matched_rule_id':rule['rule_id'],'reasons':reasons},'decision':{'alert_event_candidate_recommended':severity in {'WARN','CRITICAL','STALE','NOT_AVAILABLE'},'alert_delivery_allowed':False,'automatic_remediation_allowed':False,'kill_switch_trigger_allowed':False},'evaluation_fingerprint_payload':payload,'evaluation_fingerprint':canonical_sha256(payload),'effects':false_effects,'runtime_effect':runtime,'created_at':now,'audit_refs':[]}
    out=Path(a.output); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(yaml.safe_dump(record,sort_keys=False,allow_unicode=True,width=120),encoding='utf-8',newline='\n')
    print('PASS: wrote Review-only threshold evaluation to '+str(out)); return 0
if __name__=='__main__': raise SystemExit(main())
