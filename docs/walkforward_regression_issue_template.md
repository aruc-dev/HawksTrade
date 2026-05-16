# Walk-Forward Regression Issue Template

Use this body when a regenerated master walk-forward report falls below the
configured stressed pass-rate threshold.

```text
Profile: master
Cost level: stressed
Pass rate: <passed>/<total> (<percent>)
Required: <configured threshold>
Report: reports/walkforward_master.md

Failing windows:
- <window label>: <failure reasons>

Per-strategy attribution:
- <strategy>: <notes>

Stop-the-line action:
- Pause capital scaling, strategy enablement, and new-entry expansion until the
  regression is explained or reverted.
```
