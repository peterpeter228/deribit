#!/bin/bash
# Manual MCP (HTTP+SSE) connection test script (spec-compliant)
set -euo pipefail

BASE_URL=${MCP_BASE_URL:-http://localhost:8005}
SSE_URL="$BASE_URL/sse"

echo "Testing MCP SSE connection (spec-compliant)..."
echo

TMP_FILE=$(mktemp)

curl -s -N -H "Accept: text/event-stream" "$SSE_URL" >"$TMP_FILE" &
SSE_PID=$!

cleanup() {
  kill "$SSE_PID" 2>/dev/null || true
  rm -f "$TMP_FILE" 2>/dev/null || true
}
trap cleanup EXIT

# Wait for endpoint event
for _ in $(seq 1 50); do
  if grep -q '^event: endpoint' "$TMP_FILE"; then
    break
  fi
  sleep 0.1
done

MESSAGE_URL=$(awk '
  BEGIN{found=0}
  /^event: endpoint/{found=1; next}
  found==1 && /^data: /{sub(/^data: /,"",$0); print; exit}
' "$TMP_FILE")

if [ -z "${MESSAGE_URL:-}" ]; then
  echo "ERROR: Did not receive endpoint event from $SSE_URL"
  echo "--- SSE dump (first 40 lines) ---"
  head -n 40 "$TMP_FILE" || true
  exit 1
fi

echo "Message endpoint: $MESSAGE_URL"
echo

echo "Sending initialize request..."
INIT_BODY='{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2024-11-05",
    "capabilities": {},
    "clientInfo": {"name": "manual-test", "version": "1.0.0"}
  }
}'

INIT_HTTP=$(curl -s -X POST "$MESSAGE_URL" -H "Content-Type: application/json" -d "$INIT_BODY")

echo "Initialize HTTP response:"
echo "$INIT_HTTP" | python3 -m json.tool 2>/dev/null || echo "$INIT_HTTP"

echo
sleep 0.3

echo "Latest SSE message events:"
grep '^data: ' "$TMP_FILE" | tail -n 10 | sed 's/^data: //' || true

echo
echo "Done."
