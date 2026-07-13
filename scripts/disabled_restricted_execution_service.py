#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path
import yaml


def main() -> int:
    parser=argparse.ArgumentParser(description='Evidence-only disabled Restricted Execution Service')
    parser.add_argument('--execution-request', required=True)
    parser.add_argument('--output', required=True)
    args=parser.parse_args()
    request_path=Path(args.execution_request)
    request=yaml.safe_load(request_path.read_text(encoding='utf-8'))
    record={
        'schema_version':'disabled_executor_evidence.v0.1',
        'evidence_id':'disexevidence_'+request['execution_request_id'],
        'execution_request_id':request['execution_request_id'],
        'execution_request_ref':request_path.as_posix(),
        'execution_request_fingerprint':request['request_fingerprint'],
        'service':{'service_id':'restricted.execution.service.disabled','service_version':'0.1.0','status':'DISABLED','implementation_mode':'EVIDENCE_ONLY','network_clients_present':False,'external_adapters_present':False,'secret_reader_present':False,'subprocess_runner_present':False},
        'decision':{'result':'BLOCKED_DISABLED_SERVICE','reason_codes':['disabled_service_has_no_execution_adapter','executor_handoff_disabled'],'message':'Execution request was refused by the disabled evidence-only service.'},
        'effects':{'execution_performed':False,'executor_called':False,'tool_execution_performed':False,'program_execution_performed':False,'approval_consumed':False,'external_side_effect_performed':False,'financial_side_effect_performed':False,'runtime_mutation_performed':False,'files_mutated':[],'network_calls':0},
        'runtime_effect':{'mode':'EVIDENCE_ONLY','executor_registration_allowed':False,'executor_activation_allowed':False,'executor_handoff_allowed':False,'executor_call_allowed':False,'tool_execution_allowed':False,'program_execution_allowed':False,'approval_consumption_allowed':False,'external_execution_allowed':False,'financial_execution_allowed':False,'runtime_mutation_allowed':False,'side_effects_allowed':False,'permission_expansion_allowed':False,'authority_expansion_allowed':False},
        'created_at':'2026-07-13T04:00:10Z','audit_refs':['audit:disabled_executor:'+request['execution_request_id']],
    }
    output=Path(args.output); output.parent.mkdir(parents=True, exist_ok=True); output.write_text(yaml.safe_dump(record, sort_keys=False), encoding='utf-8'); print(output); return 0
if __name__=='__main__': raise SystemExit(main())
