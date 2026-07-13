#!/usr/bin/env python3
from __future__ import annotations
import argparse, shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
FILES=[
 "monitoring_snapshot_offline_review_v0.1.yaml","alert_event_not_sent_review_only_v0.1.yaml",
 "health_snapshot_review_only_v0.1.yaml","clock_sync_evidence_observation_only_v0.1.yaml",
 "kill_switch_state_review_only_unbound_v0.1.yaml","kill_switch_command_review_not_dispatched_v0.1.yaml",
]
def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('--output-dir',required=True); a=ap.parse_args()
    out=Path(a.output_dir).resolve(); out.mkdir(parents=True,exist_ok=True)
    src=ROOT/'examples/operations_evidence'
    for name in FILES: shutil.copy2(src/name,out/name)
    print(f'PASS: wrote {len(FILES)} I0.4.6 operations evidence previews to {out}')
    return 0
if __name__=='__main__': raise SystemExit(main())
