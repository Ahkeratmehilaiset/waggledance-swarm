#!/bin/bash
# WaggleDance Overnight Monitor
# Checks system status every 5 minutes and logs to file

LOG="/tmp/waggledance_monitor.log"
echo "=== Monitoring started: $(date) ===" >> "$LOG"

while true; do
  echo "" >> "$LOG"
  echo "--- Check: $(date) ---" >> "$LOG"

  # API Status
  STATUS=$(curl -s http://localhost:8000/api/status 2>&1)
  if [ $? -eq 0 ]; then
    echo "$STATUS" | python -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(f\"✅ API: {d['mode']} - {d['status']} - Uptime: {d['uptime']}\")
    print(f\"   Agents: {d['agents']['total']} total, {d['agents']['active']} active\")
    if 'memory' in d:
        mem = d.get('memory', {})
        print(f\"   Memory: {mem.get('count', 0)} facts\")
    if 'heartbeat' in d:
        hb = d.get('heartbeat', {})
        print(f\"   Heartbeat: {hb.get('count', 0)} beats\")
except Exception as e:
    print(f\"⚠️  API parse error: {e}\")
" >> "$LOG" 2>&1
  else
    echo "❌ API not responding" >> "$LOG"
  fi

  # Dashboard check
  curl -s http://localhost:5173 >/dev/null 2>&1
  if [ $? -eq 0 ]; then
    echo "✅ Dashboard responding" >> "$LOG"
  else
    echo "❌ Dashboard not responding" >> "$LOG"
  fi

  # Process check
  PYTHON_COUNT=$(ps aux 2>/dev/null | grep "python.*main.py" | grep -v grep | wc -l)
  echo "   Python processes: $PYTHON_COUNT" >> "$LOG"

  # Memory check from DB
  FACTS=$(python -c "
import sys
sys.path.insert(0, '/u/project2')
try:
    from consciousness import Consciousness
    c = Consciousness(db_path='data/chroma_db')
    print(c.memory.count)
except Exception as e:
    print('0')
" 2>/dev/null)
  echo "   ChromaDB facts: $FACTS" >> "$LOG"

  sleep 300  # 5 minutes
done
