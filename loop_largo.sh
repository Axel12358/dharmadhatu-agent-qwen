#!/bin/bash

echo "🚀 INICIANDO LOOP LARGO DE DHARMADHATU BOT"
echo "==========================================="

while true; do
    echo ""
    echo "[$(date)] ------------------------------"
    echo "[$(date)] Ejecutando bot..."
    python3 main.py >> loop_largo.log 2>&1
    echo "[$(date)] Bot finalizado. Esperando 1 hora..."
    sleep 3600
done
