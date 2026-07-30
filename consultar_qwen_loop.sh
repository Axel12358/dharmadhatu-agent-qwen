#!/bin/bash

echo "🧠 CONSULTANDO A QWEN PARA LOOP DE OPTIMIZACIÓN"
echo "================================================"

# 1. Activar Ollama si no está activo
if ! pgrep -x "ollama" > /dev/null; then
    echo "⚠️ Ollama no está corriendo. Iniciando..."
    ollama serve > /dev/null 2>&1 &
    sleep 3
fi

# 2. Recopilar contexto del proyecto
cd ~/dharmadhatu_agent_qwen

echo "📂 Recopilando contexto del proyecto..."

{
    echo "=== CONFIGURACIÓN ACTUAL (config_temp.json) ==="
    cat config_temp.json 2>/dev/null || echo "No existe config_temp.json"
    
    echo -e "\n=== CÓDIGO ACTUAL DE facebook_public.py (solo generación de queries) ==="
    grep -A 50 "def generar_queries_facebook" scrapers/facebook_public.py 2>/dev/null || echo "No se encontró la función"
    
    echo -e "\n=== ESTRUCTURA DE GOABASE (clasificación de eventos) ==="
    if [ -f clasificacion_goabase.json ]; then
        cat clasificacion_goabase.json
    else
        echo "No se encontró clasificacion_goabase.json. Ejecuta primero extraer_clasificacion_goabase.py"
    fi
    
    echo -e "\n=== ÚLTIMOS RESULTADOS DEL BOT ==="
    tail -50 loop_rapido.log 2>/dev/null | grep -E "Score|eventos|Facebook" || echo "No hay logs recientes"
    
    echo -e "\n=== PRINCIPIOS DEL PROYECTO ==="
    echo "- Siempre sumar, nunca restar"
    echo "- Dejar lo que sirve"
    echo "- Todo local y gratuito"
    echo "- Python 3.9 compatible"
} > contexto_loop.txt

echo "✅ Contexto recopilado en contexto_loop.txt"

# 3. Construir prompt para Qwen
cat > prompt_loop.txt << 'PROMPT'
Eres un experto en scraping, optimización con Python asyncio y Playwright.

## CONTEXTO DEL PROYECTO
Dharmadhatu Bot v5 es un scraper de eventos psytrance que:
- Usa Goabase (23 eventos), Songkick (11 eventos), Facebook (0 eventos actualmente).
- Tiene un problema crítico: Facebook no encuentra eventos (score estancado en 34).
- La generación de queries no está optimizada y no usa correctamente las combinaciones.

## PROBLEMAS IDENTIFICADOS
1. **Facebook**: no encuentra eventos en grupos, los selectores no funcionan.
2. **Generación de queries**: no usa combinaciones aleatorias de países, subgéneros, tipos y ciudades.
3. **Clasificación de Goabase**: hay datos valiosos (subgéneros, tipos, ubicaciones) que no se están usando.
4. **Loop de optimización**: no existe un loop automático que mejore el score probando diferentes configuraciones.

## OBJETIVO PRINCIPAL
Necesito que generes **código completo para un sistema de optimización automática** (loop) que:
1. **Use la clasificación de Goabase** (subgéneros, tipos, países, ciudades) para generar combinaciones de búsqueda.
2. **Genere combinaciones aleatorias** y no fijas, mezclando países, subgéneros, tipos y ciudades.
3. **Pruebe diferentes configuraciones** en cada iteración (número de queries, combinaciones, etc.).
4. **Evalúe el score** y guarde la mejor configuración.
5. **No rompa** lo que ya funciona (Goabase, Songkick).

## EVIDENCIA
PROMPT

# Añadir el contexto completo
echo "" >> prompt_loop.txt
echo "## DIAGNÓSTICO COMPLETO" >> prompt_loop.txt
echo '```' >> prompt_loop.txt
cat contexto_loop.txt >> prompt_loop.txt
echo '```' >> prompt_loop.txt

# Instrucciones finales
cat >> prompt_loop.txt << 'INSTRUCCIONES'

## FORMATO DE RESPUESTA
Genera un informe con:
### ANÁLISIS DEL PROBLEMA (qué falla y por qué)
### CÓDIGO CORREGIDO (3 bloques)
- `scrapers/facebook_public.py` (código COMPLETO con generación de combinaciones aleatorias y búsqueda en grupos)
- `loop_optimizer.py` (código COMPLETO para el loop de optimización automática)
- `main_v5.py` (código COMPLETO con integración del loop y scrapers)

### PLAN DE IMPLEMENTACIÓN (pasos concretos para aplicar los cambios)

Devuelve SOLO el informe, sin explicaciones adicionales.
INSTRUCCIONES

echo "✅ Prompt generado en prompt_loop.txt"

# 4. Consultar a Qwen
echo "🚀 Enviando consulta a Qwen (qwen2.5-coder:7b)..."
echo "⏳ Esto puede tomar varios minutos. Espera..."
ollama run qwen2.5-coder:7b "$(cat prompt_loop.txt)" > respuesta_loop.txt

echo "✅ Respuesta guardada en respuesta_loop.txt"

# 5. Mostrar resumen
echo ""
echo "📌 RECOMENDACIONES PRINCIPALES:"
grep -E "^###|^##" respuesta_loop.txt | head -20
echo ""
echo "📂 Archivos generados:"
echo "   - contexto_loop.txt   : Contexto del proyecto"
echo "   - prompt_loop.txt     : Prompt enviado a Qwen"
echo "   - respuesta_loop.txt  : Solución completa de Qwen"
echo ""
echo "👉 Para ver la respuesta completa:"
echo "   cat ~/dharmadhatu_agent_qwen/respuesta_loop.txt"
echo ""
echo "👉 Para extraer código de los archivos:"
echo "   grep -A 500 '### ARCHIVO:' ~/dharmadhatu_agent_qwen/respuesta_loop.txt > ~/dharmadhatu_agent_qwen/codigo_loop.txt"
echo ""
echo "🔚 FIN"
