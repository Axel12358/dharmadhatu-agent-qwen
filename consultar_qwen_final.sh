#!/bin/bash

# ================================================================
# CONSULTAR A QWEN PARA SOLUCIÓN DEFINITIVA DE ENRIQUECIMIENTO
# ================================================================

echo "🧠 CONSULTANDO A QWEN PARA SOLUCIÓN FINAL DE ENRIQUECIMIENTO"
echo "============================================================"

# Crear carpeta para la consulta
CONSULTA_DIR="consulta_final_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$CONSULTA_DIR"
cd "$CONSULTA_DIR" || exit

# 1. RECOPILAR INFORMACIÓN ACTUAL
echo "📂 Recopilando contexto del enriquecimiento..."

echo "--- CSV DE EVENTOS ENRIQUECIDOS (última ejecución) ---" > contexto.txt
if [ -f ../eventos_enriquecidos.csv ]; then
    cat ../eventos_enriquecidos.csv >> contexto.txt
else
    echo "No existe eventos_enriquecidos.csv" >> contexto.txt
fi

echo -e "\n\n--- CÓDIGO ACTUAL DE enriquecer_eventos.py ---" >> contexto.txt
cat ../scrapers/enriquecer_eventos.py 2>/dev/null >> contexto.txt || echo "No existe enriquecer_eventos.py" >> contexto.txt

echo -e "\n\n--- CÓDIGO ACTUAL DE facebook_public.py ---" >> contexto.txt
cat ../scrapers/facebook_public.py 2>/dev/null >> contexto.txt || echo "No existe facebook_public.py" >> contexto.txt

echo -e "\n\n--- CONFIGURACIÓN ACTUAL (config_temp.json) ---" >> contexto.txt
cat ../config_temp.json 2>/dev/null >> contexto.txt || echo "No existe config_temp.json" >> contexto.txt

echo -e "\n\n--- ÚLTIMOS RESULTADOS DEL BOT ---" >> contexto.txt
if [ -f ../loop_completo.log ]; then
    tail -50 ../loop_completo.log | grep -E "Score|eventos|Tiempo|Distribución" >> contexto.txt
else
    echo "No hay loop_completo.log" >> contexto.txt
fi

echo "✅ Contexto recopilado en contexto.txt"

# 2. CONSTRUIR PROMPT PARA QWEN
echo "🧠 Construyendo prompt para Qwen..."

cat > prompt_qwen.txt << 'PROMPT'
Eres un experto en scraping, procesamiento de datos y enriquecimiento con IA (Ollama + Qwen).

## CONTEXTO DEL PROYECTO
El proyecto Dharmadhatu Bot v5 es un sistema de scraping de eventos psytrance que:
- Scrapea eventos de Facebook (7 eventos reales por ejecución) y Goabase (3 eventos reales).
- Enriquece los eventos con Qwen (genera descripciones, extrae organizadores y emails).
- Actualmente el score es 40 (bajo), principalmente porque los organizadores y emails no se extraen correctamente.

## PROBLEMAS IDENTIFICADOS
1. **Extracción de organizador**: El CSV muestra que los organizadores son "No disponible" o nombres genéricos, cuando en Facebook cada evento tiene una sección "Hosted by" o "Organizado por".
2. **Extracción de email**: Solo se buscan emails en la descripción generada, no en el contenido original del evento.
3. **Filtro de eventos reales**: Los eventos de Songkick (11) no pasan el filtro porque no tienen fecha o lugar, aunque tienen nombre y enlace.
4. **Enriquecimiento**: El semáforo de 5 tareas es adecuado, pero la extracción de organizador y email no está optimizada.

## EVIDENCIA (del CSV)
PROMPT

# Añadir el contexto completo
echo "" >> prompt_qwen.txt
echo "## DIAGNÓSTICO COMPLETO" >> prompt_qwen.txt
echo '```' >> prompt_qwen.txt
cat contexto.txt >> prompt_qwen.txt
echo '```' >> prompt_qwen.txt

# Instrucciones finales
cat >> prompt_qwen.txt << 'INSTRUCCIONES'

## PREGUNTAS ESPECÍFICAS

### 1. ANÁLISIS DEL PROBLEMA
- ¿Por qué la extracción de organizador falla en Facebook? ¿Qué selectores o patrones deberíamos usar?
- ¿Por qué los emails no se extraen? ¿Dónde deberíamos buscar (en el evento, en la descripción, en la página)?

### 2. CÓDIGO CORREGIDO
- Proporciona el código COMPLETO y funcional de `enriquecer_eventos.py` que:
  - Extraiga correctamente el organizador del evento (usando el campo "Hosted by" o similares).
  - Busque emails en el contenido del evento antes de generar la descripción.
  - Mantenga la paralelización con semáforo (5 tareas).
- Proporciona el código COMPLETO y funcional de `facebook_public.py` que:
  - Extraiga el organizador directamente de la página de Facebook.
  - Incluya el organizador en el evento antes de enviarlo a enriquecimiento.

### 3. MEJORA DEL FILTRO
- ¿Cómo modificar el filtro en `main_v5.py` para incluir eventos de Songkick (que tienen nombre y enlace)?

### 4. PLAN DE IMPLEMENTACIÓN
- Pasos concretos para aplicar los cambios sin romper lo que funciona.
- Orden de prioridad.

## FORMATO DE RESPUESTA
Genera un informe estructurado con:
### ANÁLISIS DEL PROBLEMA
### CÓDIGO CORREGIDO (bloques completos con ### ARCHIVO: <nombre>)
### PLAN DE IMPLEMENTACIÓN (pasos concretos)

Devuelve SOLO el informe, sin explicaciones adicionales.
INSTRUCCIONES

echo "✅ Prompt generado en prompt_qwen.txt"

# 3. CONSULTAR A QWEN
echo "🚀 Enviando consulta a Qwen (qwen2.5-coder:7b)..."
echo "⏳ Esto puede tomar varios minutos. Espera..."

# Verificar que Ollama está corriendo
if ! pgrep -x "ollama" > /dev/null; then
    echo "⚠️ Ollama no está corriendo. Iniciando..."
    ollama serve &
    sleep 3
fi

ollama run qwen2.5-coder:7b "$(cat prompt_qwen.txt)" > respuesta_qwen.txt

echo "✅ Respuesta guardada en respuesta_qwen.txt"

# 4. MOSTRAR RESUMEN
echo ""
echo "📌 RESUMEN DE RECOMENDACIONES (extraído de la respuesta):"
grep -E "^###|^##" respuesta_qwen.txt | head -20

echo ""
echo "📂 Archivos generados en: $(pwd)"
echo "   - contexto.txt       : Contexto del enriquecimiento"
echo "   - prompt_qwen.txt    : Prompt enviado a Qwen"
echo "   - respuesta_qwen.txt : Respuesta completa de Qwen"
echo ""
echo "👉 Para ver la respuesta completa:"
echo "   cat $(pwd)/respuesta_qwen.txt"
echo ""
echo "🔚 FIN DE LA CONSULTA"
cd ..
