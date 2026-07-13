#!/usr/bin/env python3
from __future__ import annotations
import argparse, shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
FILES=[
 ('examples/control_supervision/control_channel_identity_binding_review_only_unbound_v0.1.yaml','control_channel_identity_binding.yaml'),
 ('examples/control_supervision/control_channel_command_envelope_review_not_dispatched_v0.1.yaml','control_channel_command_envelope_review.yaml'),
 ('examples/control_supervision/process_supervisor_snapshot_disabled_review_only_v0.1.yaml','process_supervisor_snapshot.yaml'),
 ('examples/control_supervision/scheduler_plan_review_not_installed_v0.1.yaml','scheduler_plan_review.yaml'),
 ('examples/threshold_policy/monitoring_alert_threshold_policy_review_draft_v0.1.yaml','monitoring_alert_threshold_policy.yaml'),
 ('examples/threshold_policy/monitoring_alert_threshold_evaluation_warn_review_only_v0.1.yaml','monitoring_alert_threshold_evaluation.yaml'),
 ('examples/sandbox_candidate/local_reversible_sandbox_candidate_test_plan_review_only_v0.1.yaml','sandbox_candidate_test_plan.yaml'),
 ('examples/sandbox_candidate/local_reversible_sandbox_candidate_test_review_not_run_v0.1.yaml','sandbox_candidate_test_review.yaml'),
]
def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('--output-dir',required=True); a=ap.parse_args(); out=Path(a.output_dir).resolve(); out.mkdir(parents=True,exist_ok=True)
    for src,name in FILES: shutil.copy2(ROOT/src,out/name)
    print(f'PASS: wrote {len(FILES)} I0.4.7 Review-only previews to {out}'); return 0
if __name__=='__main__': raise SystemExit(main())
