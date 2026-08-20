#!/bin/bash
# Ejecución semanal del bot dharmadhatu_agent_qwen.
# Captura incremental: solo suma eventos nuevos (Deduplicador por nombre+fecha+lugar).
# Requiere Tor activo en 127.0.0.1:9050 para Facebook/Telegram/Goabase.
set -u

cd /Users/angelgarcia/dharmadhatu_agent_qwen || exit 1
source venv/bin/activate

LOG="/Users/angelgarcia/dharmadhatu_agent_qwen/logs/semanal_$(date +%Y%m%d_%H%M%S).log"
mkdir -p "$(dirname "$LOG")"

echo "=== Corrida semanal $(date) ===" | tee -a "$LOG"
python3 -u main.py 2>&1 | tee -a "$LOG"
echo "=== Fin $(date) ===" | tee -a "$LOG"
