#!/bin/bash

echo "🧠 CONSULTANDO A QWEN CODER PARA IMPLEMENTAR MEJORAS OPEN SOURCE"
echo "================================================================="

# 1. ACTIVAR OLLAMA (si no está activo)
echo "🔍 Verificando Ollama..."
if ! pgrep -x "ollama" > /dev/null; then
    echo "⚠️ Ollama no está corriendo. Iniciando..."
    ollama serve &
    sleep 5
else
    echo "✅ Ollama ya está activo."
fi

# 2. CREAR CARPETA PARA LA CONSULTA
FECHA=$(date +%Y%m%d_%H%M%S)
CONSULTA_DIR=~/dharmadhatu_agent_qwen/consulta_implementacion_${FECHA}
mkdir -p "$CONSULTA_DIR"
cd "$CONSULTA_DIR" || exit

# 3. RECOPILAR CONTEXTO DEL PROYECTO
echo "📂 Recopilando contexto del proyecto..."

{
    echo "=== ESTRUCTURA ACTUAL DEL PROYECTO ==="
    echo "Orquestador: main_v5.py"
    echo "Scrapers: facebook_public.py, instagram_public.py, goabase.py, songkick.py"
    echo "Enriquecedor: enriquecer_eventos.py"
    echo "Loop: loop_optimizer.py"
    echo ""
    echo "=== PROBLEMAS ACTUALES ==="
    echo "1. Facebook: organizador siempre 'No disponible' (selectores fallan)"
    echo "2. Instagram: no extrae perfil del publicador"
    echo "3. Búsquedas: solo usa query fija 'psytrance festival' (no combinaciones)"
    echo "4. Score estancado en 45"
    echo ""
    echo "=== HERRAMIENTAS OPEN SOURCE DISPONIBLES (SIN LOGIN) ==="
    echo "- baberibrar/facebook-events-scraper (Playwright, 100+ eventos sin auth)"
    echo "- mrkkr/fb-events-scraper (Playwright + Flask para visualización)"
    echo "- Async Instagram scraper (Playwright, extrae perfiles sin login)"
    echo "- Crawlee para Python (orquestación robusta)"
    echo "- komutan234/Proxy-List-Free (proxies gratuitos)"
    echo ""
    echo "=== PRINCIPIOS DEL PROYECTO ==="
    echo "- Siempre sumar, nunca restar (no eliminar funcionalidades)"
    echo "- Dejar lo que sirve (mantener scrapers que dan resultados)"
    echo "- Todo local y gratuito (sin APIs de pago)"
    echo "- SIN AUTENTICACIÓN NI LOGUEO DE PERFILES PERSONALES"
} > contexto.txt

# 4. CONSTRUIR PROMPT PARA QWEN CODER
cat > prompt_qwen.txt << 'PROMPT'
Eres un experto en scraping con Python 3.9, Playwright asíncrono y asyncio.

## CONTEXTO DEL PROYECTO
El proyecto **Dharmadhatu Bot v5** es un sistema de scraping de eventos psytrance que actualmente:
- Scrapea Facebook (7 eventos, organizador "No disponible").
- Scrapea Instagram (0 eventos, no extrae perfiles).
- Scrapea Goabase (23 eventos) y Songkick (11 eventos).
- Tiene un loop de optimización que ajusta parámetros.
- Score actual: 45 (bajo).

## HERRAMIENTAS OPEN SOURCE DISPONIBLES (SIN LOGIN)
1. **Facebook**: `baberibrar/facebook-events-scraper` (Playwright, extrae 100+ eventos sin autenticación).
2. **Instagram**: Async Instagram scraper con Playwright (extrae perfiles sin login).
3. **Orquestación**: `Crawlee` para Python (maneja reintentos, rate limiting, almacenamiento).
4. **Proxies**: `komutan234/Proxy-List-Free` (lista de proxies gratuitos actualizada cada 2 minutos).

## OBJETIVO
Necesito un **plan de implementación concreto** para integrar estas herramientas en el proyecto, respetando:

1. **NO usar autenticación ni loguear perfiles personales** (todo scraping público).
2. **NO eliminar lo que ya funciona** (Goabase, Songkick, el scraper actual de Facebook como fallback).
3. **Todo local y gratuito** (sin APIs externas de pago).
4. **Compatible con Python 3.9**.
5. **Principios**: "siempre sumar, nunca restar", "dejar lo que sirve".

## EVIDENCIA (contexto del proyecto)
PROMPT

cat contexto.txt >> prompt_qwen.txt

cat >> prompt_qwen.txt << 'INSTRUCCIONES'

## PREGUNTAS ESPECÍFICAS PARA QWEN
1. **¿Cómo integrar `baberibrar/facebook-events-scraper` sin romper el scraper actual?** (sumarlo como alternativa/fallback)
2. **¿Cómo adaptar el scraper de Instagram para extraer perfiles sin login?** (usando Playwright y selectores públicos)
3. **¿Cómo envolver los scrapers actuales con Crawlee para mejorar la orquestación?** (manteniendo la estructura actual)
4. **¿Cómo integrar proxies rotativos de `komutan234/Proxy-List-Free` en el wrapper?** (sin depender de servicios externos)
5. **¿Qué cambios concretos hacer en `loop_optimizer.py` para que aproveche las mejoras?**

## FORMATO DE RESPUESTA
### ANÁLISIS DE LA SITUACIÓN ACTUAL
### PLAN DE IMPLEMENTACIÓN (paso a paso, con código si es necesario)
### CÓDIGO SUGERIDO (bloques con ### ARCHIVO: <nombre>)
### ORDEN DE PRIORIDAD (qué implementar primero)

Devuelve SOLO el informe estructurado, sin explicaciones adicionales.
INSTRUCCIONES

echo "✅ Prompt generado en prompt_qwen.txt"

# 5. CONSULTAR A QWEN CODER
echo "🚀 Enviando consulta a qwen2.5-coder:7b..."
echo "⏳ Esto puede tomar 3-5 minutos. Espera..."

ollama run qwen2.5-coder:7b "$(cat prompt_qwen.txt)" > respuesta_qwen.txt

echo "✅ Respuesta guardada en respuesta_qwen.txt"

# 6. MOSTRAR RESUMEN
echo ""
echo "📌 RECOMENDACIONES PRINCIPALES (extraído de la respuesta):"
grep -E "^###|^##" respuesta_qwen.txt | head -20

echo ""
echo "📂 Archivos generados en: $CONSULTA_DIR"
echo "   - contexto.txt         : Contexto del proyecto"
echo "   - prompt_qwen.txt      : Prompt enviado a Qwen Coder"
echo "   - respuesta_qwen.txt   : Solución completa de Qwen Coder"
echo ""
echo "👉 Para ver la respuesta completa:"
echo "   cat $CONSULTA_DIR/respuesta_qwen.txt"
echo ""
echo "👉 Para extraer solo el código de los archivos:"
echo "   grep -A 500 '### ARCHIVO:' $CONSULTA_DIR/respuesta_qwen.txt > $CONSULTA_DIR/codigo_extraido.txt"
echo ""
echo "🔚 FIN DE LA CONSULTA"
cd ..
