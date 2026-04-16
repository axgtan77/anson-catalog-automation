#!/bin/bash
# 9PM Sync - equivalent to run_9pm_sync.bat
set -u

cd /home/axgtan/CatalogAutomation

STATUS_FILE="logs/last_run_status.json"
mkdir -p logs

RUN_STARTED_AT="$(date '+%Y-%m-%d %H:%M:%S')"

daily_sync_status="PENDING"
inventory_sync_status="PENDING"
posted_inventory_status="PENDING"
sales_refresh_status="PENDING"
storefront_publish_status="PENDING"
overall_status="RUNNING"
error_summary=""

write_status() {
  python3 - "$STATUS_FILE" <<'PY'
import json, sys
from pathlib import Path
path = Path(sys.argv[1])
payload = json.load(sys.stdin)
path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY
}

emit_status() {
  local finished_at="${1:-}"
  python3 - <<PY | write_status
import json
payload = {
  "started_at": ${RUN_STARTED_AT@Q},
  "finished_at": ${finished_at@Q},
  "daily_sync_status": ${daily_sync_status@Q},
  "inventory_sync_status": ${inventory_sync_status@Q},
  "posted_inventory_status": ${posted_inventory_status@Q},
  "sales_refresh_status": ${sales_refresh_status@Q},
  "storefront_publish_status": ${storefront_publish_status@Q},
  "overall_status": ${overall_status@Q},
  "error_summary": ${error_summary@Q},
}
print(json.dumps(payload))
PY
}

echo "$(date) === Starting 9PM Sync ==="
emit_status ""

# Step 1: Daily sync (MP_MER, prices, Google Sheets)
# Non-fatal: inventory + storefront publish must still run even if MP_MER sync
# or Sheets upload fails, otherwise the storefront freezes on a stale snapshot.
venv/bin/python Scripts/daily_sync.py
if [ $? -eq 0 ]; then
  daily_sync_status="SUCCESS"
else
  daily_sync_status="FAILED"
  error_summary="daily_sync failed"
  echo "daily_sync FAILED - continuing with inventory + storefront steps"
fi
emit_status ""

# Step 2: Inventory syncs
venv/bin/python Scripts/sync_inventory_from_wi_lgr.py
if [ $? -eq 0 ]; then
  inventory_sync_status="SUCCESS"
else
  inventory_sync_status="FAILED"
  error_summary="${error_summary}; inventory_sync failed"
fi
emit_status ""

venv/bin/python Scripts/sync_posted_inventory_from_wi_esc.py
if [ $? -eq 0 ]; then
  posted_inventory_status="SUCCESS"
else
  posted_inventory_status="FAILED"
  error_summary="${error_summary}; posted_inventory_sync failed"
fi
emit_status ""

# Step 3: Sales metrics from backups
venv/bin/python Scripts/refresh_recent_sales_metrics_from_backups.py
if [ $? -eq 0 ]; then
  sales_refresh_status="SUCCESS"
else
  sales_refresh_status="FAILED"
  error_summary="${error_summary}; sales_refresh failed"
fi
emit_status ""

# Step 4: Publish storefront catalog
cd Storefront
../venv/bin/python publish_storefront_catalog.py
if [ $? -eq 0 ]; then
  storefront_publish_status="SUCCESS"
else
  storefront_publish_status="FAILED"
  error_summary="${error_summary}; storefront_publish failed"
fi
cd ..

if [ "$daily_sync_status" = "SUCCESS" ] && [ "$inventory_sync_status" = "SUCCESS" ] && [ "$posted_inventory_status" = "SUCCESS" ] && [ "$sales_refresh_status" = "SUCCESS" ] && [ "$storefront_publish_status" = "SUCCESS" ]; then
  overall_status="SUCCESS"
elif [ "$storefront_publish_status" = "SUCCESS" ] || [ "$inventory_sync_status" = "SUCCESS" ] || [ "$posted_inventory_status" = "SUCCESS" ] || [ "$sales_refresh_status" = "SUCCESS" ]; then
  overall_status="PARTIAL_SUCCESS"
else
  overall_status="FAILED"
fi

RUN_FINISHED_AT="$(date '+%Y-%m-%d %H:%M:%S')"
emit_status "$RUN_FINISHED_AT"

echo "STEP daily_sync: $daily_sync_status"
echo "STEP inventory_sync: $inventory_sync_status"
echo "STEP posted_inventory_sync: $posted_inventory_status"
echo "STEP sales_refresh: $sales_refresh_status"
echo "STEP storefront_publish: $storefront_publish_status"
echo "OVERALL_STATUS: $overall_status"
echo "$(date) === 9PM Sync Complete ==="
