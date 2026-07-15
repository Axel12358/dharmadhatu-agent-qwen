#!/bin/bash
# Script para ejecutar el loop extenso del agente Qwen

echo "🌀 INICIANDO LOOP EXTENSO DEL AGENTE QWEN"
echo "=========================================="

cd ~/dharmadhatu_agent_qwen
source venv/bin/activate

# Crear nombre de archivo con timestamp
LOG_FILE="loop_extenso_$(date +%Y%m%d_%H%M%S).log"

echo "📁 Log guardado en: $LOG_FILE"
echo "🚀 Iniciando agente Qwen (15 iteraciones)..."
echo "⏳ Puedes cerrar esta terminal, el proceso seguirá corriendo."

# Ejecutar en segundo plano
nohup python agente_qwen.py > "$LOG_FILE" 2>&1 &

PID=$!
echo "✅ Proceso iniciado con PID: $PID"
echo ""
echo "📊 Para ver el progreso en tiempo real:"
echo "   tail -f $LOG_FILE"
echo ""
echo "🛑 Para detener el loop:"
echo "   kill $PID"
echo "   o pkill -f agente_qwen"
echo ""
echo "⏳ Esperando 5 segundos para mostrar el inicio del log..."
sleep 5
tail -20 "$LOG_FILE"
