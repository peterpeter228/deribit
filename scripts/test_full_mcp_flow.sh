#!/bin/bash
set -euo pipefail

BASE_URL=${MCP_BASE_URL:-http://localhost:8005}
SSE_URL="$BASE_URL/sse"

echo "Testing MCP (HTTP+SSE) flow at: $BASE_URL"
echo

# 1) Health
echo "1. Testing health endpoint..."
curl -s "$BASE_URL/health" | python3 -m json.tool || true
echo

# 2) Connect SSE and capture the endpoint event (per MCP spec)
echo "2. Connecting to SSE endpoint and waiting for 'endpoint' event..."
TMP_FILE=$(mktemp)

# Start SSE stream in background
curl -s -N -H "Accept: text/event-stream" "$SSE_URL" >"$TMP_FILE" &
SSE_PID=$!

cleanup() {
  kill "$SSE_PID" 2>/dev/null || true
  rm -f "$TMP_FILE" 2>/dev/null || true
}
trap cleanup EXIT

# Wait (up to 5s) for endpoint event
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

# 3) Initialize
echo "3. Sending initialize request..."
INIT_BODY='{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2024-11-05",
    "capabilities": {},
    "clientInfo": {"name": "test-client", "version": "1.0.0"}
  }
}'

INIT_HTTP=$(curl -s -X POST "$MESSAGE_URL" \
  -H "Content-Type: application/json" \
  -d "$INIT_BODY")

echo "Initialize HTTP response:"
echo "$INIT_HTTP" | python3 -m json.tool 2>/dev/null || echo "$INIT_HTTP"
echo

# Give SSE a moment to flush
sleep 0.3

echo "Initialize SSE response (latest message events):"
# Print last few SSE message data lines
grep '^data: ' "$TMP_FILE" | tail -n 5 | sed 's/^data: //' | python3 -m json.tool 2>/dev/null || true

echo

# 4) tools/list
echo "4. Testing tools/list..."
TOOLS_LIST_BODY='{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/list",
  "params": {}
}'

TOOLS_HTTP=$(curl -s -X POST "$MESSAGE_URL" \
  -H "Content-Type: application/json" \
  -d "$TOOLS_LIST_BODY")

echo "Tools/list HTTP response:"
echo "$TOOLS_HTTP" | python3 -m json.tool 2>/dev/null || echo "$TOOLS_HTTP"

echo
sleep 0.3

echo "Tools/list SSE response (latest message events):"
grep '^data: ' "$TMP_FILE" | tail -n 5 | sed 's/^data: //' | python3 -m json.tool 2>/dev/null || true

echo

echo "✓ All tests completed"
