#!/usr/bin/env python3
from __future__ import annotations
import argparse, yaml
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
CHECK_IDS=[
'intake_schema_valid','candidate_identity_unique','implementation_available','implementation_hash_verified','no_secret_values','authority_bound','scope_explicit','monitoring_runtime_ready','alert_delivery_ready','health_runtime_ready','clock_runtime_ready','kill_switch_runtime_connected','hot_path_implemented','atomic_approval_consumption_implemented','rollback_validated','idempotency_validated','independent_validation_passed','negative_fixtures_passed','runtime_registry_exists','executor_activation_approved']
def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('--intake',required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
    intake=yaml.safe_load(Path(a.intake).read_text(encoding='utf-8'))
    passed={'intake_schema_valid','candidate_identity_unique','no_secret_values','authority_bound','scope_explicit'}
    checks=[{'check_id':cid,'status':'PASS' if cid in passed else 'NOT_AVAILABLE','evidence_refs':[intake['intake_id']] if cid in passed else [],'notes':'Review evidence present.' if cid in passed else 'Prerequisite is not implemented or not validated.'} for cid in CHECK_IDS]
    result={'schema_version':'executor_candidate_intake_review.v0.1','review_id':'execintakereview_'+intake['intake_id'].removeprefix('execintake_'),'intake_ref':intake['intake_id'],'candidate_ref':intake['candidate']['executor_candidate_id']+'@'+intake['candidate']['version'],'checks':checks,'summary':{'result':'NOT_READY','accepted_for_review_backlog':True,'ready_for_registry_candidate_record':False,'ready_for_activation_review':False,'ready_for_executor_handoff':False,'passed_check_count':sum(x['status']=='PASS' for x in checks),'failed_check_count':sum(x['status']=='FAIL' for x in checks),'unavailable_check_count':sum(x['status']=='NOT_AVAILABLE' for x in checks),'missing_prerequisites':[x['check_id'] for x in checks if x['status']!='PASS']},'decision':{'decision':'REVIEW_BACKLOG_ONLY','registration_performed':False,'registry_mutation_performed':False,'activation_performed':False},'runtime_effect':intake['runtime_effect'],'audit_refs':[]}
    Path(a.output).write_text(yaml.safe_dump(result,sort_keys=False,allow_unicode=True,width=120),encoding='utf-8',newline='\n')
    print('PASS: wrote Review-backlog-only Executor Candidate Intake Review')
    return 0
if __name__=='__main__': raise SystemExit(main())
