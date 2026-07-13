# I0.4.7 Control, Supervision, Threshold, and Sandbox Review-Only Boundary

I0.4.7 creates contracts, schemas, examples, validators, and preview builders only.

```text
Control Channel Binding Review != Runtime identity verification
Command Envelope Review != command dispatch
Supervisor Snapshot != process observation or control
Scheduler Plan != installed or enabled job
Threshold Evaluation != alert delivery or remediation
Sandbox Test Plan != Sandbox creation or test execution
Sandbox Test Review != Executor registration or activation
```

The following remain false: control-channel connection, identity activation, challenge verification, command dispatch, process observation/control, process start/stop/restart/kill, scheduler connection/installation/enablement/dispatch, Task creation by scheduler, Monitoring policy activation, alert delivery, remediation, Kill Switch trigger, Sandbox creation, Sandbox test execution, filesystem writes, network calls, subprocesses, secret access, Executor registration, Registry mutation, activation, handoff, Runtime mutation, external execution, financial execution, Permission expansion, and Authority expansion.
