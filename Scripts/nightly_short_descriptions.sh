#!/usr/bin/env bash
# Nightly "About this product" short-description burn-down (separate from the 23:00
# packaging audit). Self-resuming: the queue is simply active products whose
# short_description is still empty, ordered in-stock-first then by 24m sales. Each
# run stages the next batch, a headless Claude session runs the drafting workflow
# (agents write run/sd_*.json), a deterministic compile writes them into the encoder
# DB (audit-logged), then republishes. No-ops once the backlog is exhausted.
set -u
REPO=/home/axgtan/CatalogAutomation
SDDIR=/home/axgtan/.claude/projects/-home-axgtan-CatalogAutomation/short_descriptions
WF=/home/axgtan/.claude/projects/-home-axgtan-CatalogAutomation/workflows/scripts/nightly-shortdesc.js
CLAUDE=/home/axgtan/.local/bin/claude
PY=$REPO/venv/bin/python
LOG=$REPO/logs/nightly_short_descriptions.log
mkdir -p "$REPO/logs"
exec >>"$LOG" 2>&1
echo "==================== $(date -Iseconds) nightly short descriptions ===================="

# 1) stage the next batch (deterministic). NIGHTLY_TARGET overridable via env.
STAGED=$("$PY" "$SDDIR/stage_shortdesc.py" 2>&1); echo "$STAGED"
CNT=$(echo "$STAGED" | grep -oE 'STAGED [0-9]+' | awk '{print $2}')
[ "${CNT:-0}" -eq 0 ] && { echo "backlog empty — nothing to do."; exit 0; }

# 2) headless Claude: run the drafting workflow (agents write run/sd_*.json)
read -r -d '' PROMPT <<PROMPT_EOF
Run ONE short-description drafting round, non-interactively. Do EXACTLY these steps and nothing else:
1. Invoke the Workflow tool with { scriptPath: "$WF" } and WAIT for it to complete (the agents draft blurbs and each writes $SDDIR/run/sd_<i>.json).
2. Reply with ONE line: "drafted round" and nothing else.
PROMPT_EOF

echo "--- claude round start $(date -Iseconds) ---"
# 45-min background-task ceiling (not infinite, so a hung workflow can't wedge the cron).
env -u ANTHROPIC_API_KEY CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=2700000 "$CLAUDE" -p "$PROMPT" --dangerously-skip-permissions --output-format text < /dev/null 2>&1
echo "--- claude round end $(date -Iseconds) ---"

# 3) compile agent outputs into the encoder DB (deterministic, audit-logged)
WROTE=$("$PY" "$SDDIR/write_shortdesc.py" 2>&1); echo "$WROTE"

# 4) republish so the new blurbs reach the storefront
( cd "$REPO/Storefront" && "$PY" publish_storefront_catalog.py 2>&1 | grep -E "Published|ERROR" )
echo "==================== $(date -Iseconds) done ===================="
