# ARLO patch rationale — Wave 1 shim refactor

**Branch:** `shim-refactor-wave1` (built atop `bridge-ninja-patches-v2026.4.23` HEAD `69a22db36`).
**Pattern:** Wrap-and-delegate. Upstream `gateway/platforms/slack.py` retains tiny shim entries; the BN logic bodies live in `arlo/*.py`.
**Goal:** weekly Hermes upgrades complete in <10 min by reducing patch surface in upstream files.

## Wave 1 clusters

| # | SHA(s) on bn fork | Subject | Cluster | Status | Removal criterion |
|---|---|---|---|---|---|
| 3 | 477d2683f | clear assistant `Generating response...` after each send | status-clear | extracted-as-shim → arlo/slack_send.py | When Hermes ships post-send hook |
| 4 | af988f203 | block_actions handler for arlo write approvals | block-actions | extracted-as-shim → arlo/slack_write_approval.py | When Hermes ships pluggable block_actions registry |
| 11 | 063b0cd0c | arlo-write handler acks stale clicks instead of silent no-op | block-actions (folded with #4) | extracted-as-shim → arlo/slack_write_approval.py | (same as #4) |
| 1 | ddbcf40e6 | add arlo halt-switch (stop/halt/exit → flag, resume → clear) | halt | not-extracted-yet | When Hermes ships adapter-level lifecycle hooks |
| 2 | 90afced3a | halt also denies in-flight sandbox approvals | halt (folded with #1) | not-extracted-yet | (same as #1) |
| 7 | 044354f1d | HERMES_CACHE_TTL env var | config-knob | intentional minimal-surface — do not extract | Permanent — config knob |

## Wave 2 (deferred)

Home-tab cluster — patches #5, #6, #8, #9, #10, #12, #13. Re-evaluate at Milestone H decision after Wave 1 soaks ≥1 week.

## Field meanings

- **Status:** `not-extracted-yet | extracted-as-shim | intentional minimal-surface | superseded`
- **Removal criterion:** the concrete event that allows this patch to be deleted from BN's tree (e.g. upstream PR merged, abstraction obsolete)

## Maintenance rule

Update this file in the same commit that adds, extracts, or removes a BN patch.
