#!/bin/bash

echo "🔍 DIAGNÓSTICO DE FACEBOOK - ¿Qué mierda pasó?"
echo "=================================================="

# Crear carpeta para el diagnóstico
DIAG_DIR="diagnostico_fb_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$DIAG_DIR"
cd "$DIAG_DIR" || exit

# 1. Recopilar el código actual de facebook_public.py
echo "📄 CÓDIGO ACTUAL DE FACEBOOK" > diagnostico.txt
echo "-----------------------------" >> diagnostico.txt
cat ../scrapers/facebook_public.py 2>/dev/null >> diagnostico.txt || echo "No existe facebook_public.py" >> diagnostico.txt

# 2. Recopilar el main_v5.py
echo -e "\n\n📄 CÓDIGO DE main_v5.py" >> diagnostico.txt
echo "-------------------------" >> diagnostico.txt
cat ../main_v5.py 2>/dev/null >> diagnostico.txt || echo "No existe main_v5.py" >> diagnostico.txt

# 3. Recopilar configuración actual
echo -e "\n\n📄 CONFIGURACIÓN (config_temp.json)" >> diagnostico.txt
echo "-----------------------------------" >> diagnostico.txt
cat ../config_temp.json 2>/dev/null >> diagnostico.txt || echo "No existe config_temp.json" >> diagnostico.txt

# 4. Versión de Playwright
echo -e "\n\n📄 VERSIÓN DE PLAYWRIGHT" >> diagnostico.txt
echo "-------------------------" >> diagnostico.txt
pip show playwright 2>/dev/null >> diagnostico.txt || echo "Playwright no instalado" >> diagnostico.txt

# 5. Últimos errores del bot (del loop_completo.log)
echo -e "\n\n📄 ÚLTIMOS ERRORES" >> diagnostico.txt
echo "-------------------" >> diagnostico.txt
if [ -f ../loop_completo.log ]; then
    tail -50 ../loop_completo.log | grep -i "facebook\|error\|exception" >> diagnostico.txt
else
    echo "No hay loop_completo.log" >> diagnostico.txt
fi

# 6. Comparar con versión anterior (si existe backup)
echo -e "\n\n📄 HISTORIAL DE CAMBIOS" >> diagnostico.txt
echo "-----------------------" >> diagnostico.txt
if [ -f ../scrapers/facebook_public.py.bak ]; then
    echo "✅ Existe backup de facebook_public.py" >> diagnostico.txt
    echo "🔍 Diferencias con el backup:" >> diagnostico.txt
    diff ../scrapers/facebook_public.py.bak ../scrapers/facebook_public.py 2>/dev/null >> diagnostico.txt
else
    echo "❌ No hay backup de facebook_public.py" >> diagnostico.txt
fi

# 7. Errores de sintaxis del código
echo -e "\n\n📄 ERRORES DE SINTAXIS" >> diagnostico.txt
echo "----------------------" >> diagnostico.txt
python3 -m py_compile ../scrapers/facebook_public.py 2>&1 >> diagnostico.txt
python3 -m py_compile ../main_v5.py 2>&1 >> diagnostico.txt

echo ""
echo "✅ Diagnóstico guardado en $DIAG_DIR/diagnostico.txt"

# ================================================================
# CONSULTAR A QWEN
# ================================================================

echo ""
echo "🧠 CONSULTANDO A QWEN PARA QUE NOS AYUDE..."
echo ""

# Verificar que Ollama está corriendo
if ! pgrep -x "ollama" > /dev/null; then
    echo "⚠️ Ollama no está corriendo. Iniciando..."
    ollama serve &
    sleep 3
fi

# Construir prompt
cat > prompt_qwen.txt << 'PROMPT'
Eres un experto en scraping con Python, Playwright y asyncio.

## CONTEXTO
El proyecto Dharmadhatu Bot v5 tiene un scraper de Facebook que funcionaba perfectamente hace unos días y ahora devuelve 0 eventos.

## PROBLEMAS ACTUALES
- Facebook devuelve 0 eventos (antes encontraba 13-20 eventos reales).
- El código fue modificado recientemente para "mejorarlo" y ahora está roto.
- No sabemos exactamente qué cambio lo rompió.

## EVIDENCIA
PROMPT

# Añadir el diagnóstico
echo "" >> prompt_qwen.txt
echo "## DIAGNÓSTICO COMPLETO" >> prompt_qwen.txt
echo '```' >> prompt_qwen.txt
cat diagnostico.txt >> prompt_qwen.txt
echo '```' >> prompt_qwen.txt

# Añadir instrucciones finales
cat >> prompt_qwen.txt << 'INSTRUCCIONES'

## PREGUNTAS ESPECÍFICAS
1. ¿Qué cambios en el scraper de Facebook pueden haber causado que devuelva 0 eventos?
2. ¿Qué selectores o lógica de extracción están fallando actualmente?
3. ¿Cómo debería ser el código CORRECTO para que Facebook vuelva a funcionar?
4. ¿Qué partes del código actual debo revertir para que funcione como antes?

## FORMATO DE RESPUESTA
Genera un informe con:
### ANÁLISIS DEL PROBLEMA
### CÓDIGO CORREGIDO (código COMPLETO de facebook_public.py que funciona)
### PASOS PARA APLICAR LA SOLUCIÓN

Devuelve SOLO el informe, sin explicaciones adicionales.
INSTRUCCIONES

echo "🚀 Enviando consulta a Qwen (qwen2.5-coder:7b)..."
ollama run qwen2.5-coder:7b "$(cat prompt_qwen.txt)" > respuesta_qwen.txt

echo "✅ Respuesta guardada en $DIAG_DIR/respuesta_qwen.txt"
echo ""
echo "📌 RESUMEN DE LA SOLUCIÓN:"
grep -E "^###|^##" respuesta_qwen.txt | head -20

echo ""
echo "📂 Archivos generados:"
echo "   - diagnostico.txt       : Diagnóstico completo"
echo "   - prompt_qwen.txt       : Prompt enviado a Qwen"
echo "   - respuesta_qwen.txt    : Respuesta de Qwen"
echo ""
echo "👉 Para ver la respuesta completa:"
echo "   cat $DIAG_DIR/respuesta_qwen.txt"
echo ""
echo "👉 Para aplicar la solución (cuando estés seguro):"
echo "   cp $DIAG_DIR/codigo_corregido.py ../scrapers/facebook_public.py"
echo ""
echo "🔚 FIN"
cd ..
