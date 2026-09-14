#!/bin/bash
# Watchdog Dharmadhatu — mantiene matriz + searxng vivos y en cola.
# 1) Si searxng no devuelve resultados google (engines suspendidos) → reinicio.
# 2) Si la matriz no está corriendo y quedan seeds → lanza la siguiente tanda.
# 3) Log en /tmp/watchdog.log
cd "/Users/angelgarcia/dharmadhatu_agent_qwen" || exit 1

SEEDS=(13 17 23 29)
ROUNDS_TOTAL=4
STATE=/tmp/watchdog_state.txt

log() { echo "[$(date '+%H:%M:%S')] $*" >> /tmp/watchdog.log; }

# init state
if [ ! -f "$STATE" ]; then
  echo "round=0" > "$STATE"
fi
round=$(grep round "$STATE" | cut -d= -f2)
round=${round:-0}

probe_searxng() {
  # devuelve 0 si searxng está OK (google responde con >=1 result)
  local out
  out=$(curl -s -m 25 "http://localhost:8888/search?q=site%3Afacebook.com%2Fevents+psytrance+Berlin&format=json" 2>/dev/null)
  local n
  n=$(echo "$out" | python3 -c "import sys,json
try:
  d=json.load(sys.stdin); print(sum(1 for x in d.get('results',[]) if 'facebook.com' in x.get('url','')))
except: print(0)")
  [ "$n" -ge 1 ]
}

fail_count=0
while true; do
  # --- searxng health ---
  if probe_searxng; then
    fail_count=0
  else
    fail_count=$((fail_count+1))
    log "searxng sin resultados (fallo $fail_count/3)"
    if [ "$fail_count" -ge 3 ]; then
      log "REINICIANDO searxng..."
      docker restart searxng >/dev/null 2>&1
      fail_count=0
      sleep 20
    fi
  fi

  # --- matriz ---
  if ! pgrep -f "core.matriz_busqueda" >/dev/null 2>&1; then
    if [ "$round" -lt "$ROUNDS_TOTAL" ]; then
      seed=${SEEDS[$round]}
      round=$((round+1))
      echo "round=$round" > "$STATE"
      log "Lanzando matriz round $round (semilla $seed, 2000 dorks)"
      nohup .venv/bin/python -u -m core.matriz_busqueda --presupuesto 2000 --semilla "$seed" --ejecutar > "/tmp/matriz_round${round}.log" 2>&1 &
    else
      # Fase 2: rounds agotados → enriquecer emails (google libre de la matriz)
      if ! pgrep -f "enriquecer_contactos" >/dev/null 2>&1; then
        log "Rounds agotados → lanzando enriquecimiento de emails (batch 40)"
        (cd /Users/angelgarcia/dharmadhatu_agent_qwen && nohup .venv/bin/python -u -c "
import sys; sys.path.insert(0,'.')
from core.enriquecer_contactos_dorks import enriquecer_contactos_dorks
print('STATS:', enriquecer_contactos_dorks(max_n=40, timeout_dork=20))
" >> /tmp/enriquecer_fase2.log 2>&1 &)
      fi
    fi
  fi

  sleep 45
done