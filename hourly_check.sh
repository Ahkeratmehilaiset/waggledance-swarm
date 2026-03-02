#!/bin/bash
# Hourly status check for overnight monitoring

LOG="/tmp/hourly_status.log"

while true; do
  echo "" >> "$LOG"
  echo "==================== $(date) ====================" >> "$LOG"

  # API comprehensive check
  curl -s http://localhost:8000/api/status | python -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(f\"Mode: {d['mode']}\")
    print(f\"Status: {d['status']}\")
    print(f\"Uptime: {d['uptime']}\")
    print(f\"Agents: {d['agents']['total']} total, {d['agents']['active']} active\")

    mem = d.get('memory', {})
    print(f\"Memory: {mem.get('count', 0)} facts\")

    if 'heartbeat' in d and d['heartbeat']:
        hb = d['heartbeat']
        print(f\"Heartbeat: {hb.get('count', 0)} beats\")
        if hb.get('last_insight'):
            insight = hb['last_insight'][:80]
            print(f\"Last insight: {insight}...\")

    if 'learning' in d and d['learning']:
        learn = d['learning']
        print(f\"Learning: {learn.get('total_learned', 0)} facts learned\")

except Exception as e:
    print(f\"ERROR: {e}\")
" >> "$LOG" 2>&1

  # ChromaDB direct check
  python -c "
import sys
sys.path.insert(0, 'U:/project2')
from consciousness import Consciousness
c = Consciousness(db_path='data/chroma_db')
print(f'ChromaDB direct: {c.memory.count} facts')
" >> "$LOG" 2>&1

  # Process health
  PYTHON_PROCS=$(ps aux 2>/dev/null | grep -c "python.*main.py" || echo "0")
  NODE_PROCS=$(ps aux 2>/dev/null | grep -c "node.*vite" || echo "0")
  echo "Processes: Python=$PYTHON_PROCS, Node=$NODE_PROCS" >> "$LOG"

  # Resource usage
  if command -v free >/dev/null 2>&1; then
    free -h | grep Mem >> "$LOG"
  fi

  sleep 3600  # 1 hour
done
