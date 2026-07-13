#!/usr/bin/env python3
from __future__ import annotations
import argparse, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def run(label,cmd):
 print('\n=== '+label+' ==='); p=subprocess.run(cmd,cwd=ROOT,text=True)
 if p.returncode: raise SystemExit(f'{label} failed with exit code {p.returncode}')
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--full-repository-gate',action='store_true'); a=ap.parse_args(); py=sys.executable
 checks=[
 ('Permission and Approval',[py,'scripts/validate_permission_approval_contracts.py']),
 ('Tool and Program Request',[py,'scripts/validate_tool_program_request_contracts.py']),
 ('Execution Validation Audit',[py,'scripts/validate_execution_validation_audit_contracts.py']),
 ('Executor Foundation',[py,'scripts/validate_executor_foundation_contracts.py']),
 ('Operations Evidence and Executor Intake',[py,'scripts/validate_operations_evidence_executor_intake.py']),
 ('Control Supervision Threshold Sandbox',[py,'scripts/validate_control_supervision_threshold_sandbox.py']),
 ('I0.4 Consolidated Contract Set',[py,'scripts/validate_i0_4_consolidated_contract_set.py']),
 ]
 for label,cmd in checks: run(label,cmd)
 for label,path in [('Contract Schema Parity','scripts/validate_contract_schema_parity.py'),('Static Integrity','scripts/validate_static_integrity.py')]:
  if (ROOT/path).exists(): run(label,[py,path])
 run('I0.4 Contract Set Lock',[py,'scripts/build_i0_4_contract_set_lock.py'])
 if (ROOT/'.git').exists(): run('Git Diff Check',['git','diff','--check'])
 if a.full_repository_gate: run('Full Repository Release Gate',[py,'scripts/run_repository_release_gate.py','--full'])
 print('\nPASS: I0.4 Consolidation Gate completed')
 print('No Runtime permission, activation, Executor handoff, external execution, or financial execution was granted.')
 return 0
if __name__=='__main__': raise SystemExit(main())
