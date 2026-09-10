#!/bin/bash

echo "🚀 INICIANDO LOOP RÁPIDO DE DHARMADHATU BOT (5 iteraciones, cada 5 min)"
echo "==========================================="

for i in {1..5}; do
    echo ""
    echo "[$(date)] Iteración $i/5"
    echo "------------------------------"
    
    # Ejecutar el bot
    cd "$(dirname "$0")"
    python3 main.py >> loop_rapido.log 2>&1
    
    # Esperar 5 minutos antes de la siguiente iteración
    if [ $i -lt 5 ]; then
        echo "[$(date)] Esperando 5 minutos..."
        sleep 300
    fi
done

echo ""
echo "[$(date)] LOOP RÁPIDO COMPLETADO"
echo "Revisa loop_rapido.log para ver los resultados"
