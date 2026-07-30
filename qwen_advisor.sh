#!/bin/bash

# ================================================================
# QWEN ADVISOR - Diagnóstico y mejora automática con IA
# ================================================================
# Principios: Siempre sumar, nunca restar | Dejar lo que sirve
# Compatible con Python 3.9 y Ollama (qwen2.5-coder:7b)
# ================================================================

echo "🧠 QWEN ADVISOR v1.0 - Diagnóstico y mejora del proyecto"
echo "============================================================="

# ================================================================
# 1. RECOPILACIÓN DE DIAGNÓSTICO
# ================================================================
DIAG_DIR="diagnostico_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$DIAG_DIR"
cd "$DIAG_DIR" || exit

echo "📂 Recopilando diagnóstico en $DIAG_DIR..."

# 1.1 Configuración actual
echo "--- CONFIGURACIÓN (config_temp.json) ---" > config.txt
cat ../config_temp.json 2>/dev/null >> config.txt || echo "No existe config_temp.json" >> config.txt

# 1.2 Últimos logs de error
echo -e "\n--- ÚLTIMOS ERRORES ---" >> config.txt
if [ -f ../loop_completo.log ]; then
    tail -100 ../loop_completo.log >> config.txt
elif [ -f ../main_v5.log ]; then
    tail -100 ../main_v5.log >> config.txt
else
    echo "No se encontraron logs de error." >> config.txt
fi

# 1.3 Estado de Git
echo -e "\n--- ESTADO DE GIT ---" >> config.txt
(cd .. && git status) >> config.txt 2>&1

# 1.4 Lista de archivos importantes
echo -e "\n--- ARCHIVOS PRINCIPALES ---" >> config.txt
ls -la ../*.py ../scrapers/*.py ../*.json 2>/dev/null >> config.txt

# 1.5 Funciones de los scrapers
echo -e "\n--- FUNCIONES EN SCRAPERS ---" >> config.txt
for scraper in ../scrapers/*.py; do
    echo "👉 $(basename $scraper):" >> config.txt
    grep -E "^(async )?def " "$scraper" 2>/dev/null | head -5 >> config.txt
    echo "" >> config.txt
done

# ================================================================
# 2. GENERACIÓN DEL PROMPT PARA QWEN
# ================================================================
echo "🧠 Generando prompt para Qwen..."

PROMPT_FILE="prompt.txt"
cat > "$PROMPT_FILE" << 'PROMPT'
Eres un experto en Python, asyncio, Playwright y scraping de datos. 
Tu tarea es analizar el proyecto Dharmadhatu Bot v5 y proporcionar:

1. **Diagnóstico de errores potenciales**: Basado en el historial de errores y la configuración actual, identifica qué puede fallar en el futuro y por qué.
2. **Mejoras recomendadas**: Sugiere cambios concretos para mejorar la velocidad, estabilidad y cantidad de eventos reales, sin eliminar funcionalidades existentes (principio "siempre sumar, nunca restar").
3. **Nuevas funcionalidades**: Propón al menos 2 funcionalidades que se puedan agregar (ej: caching, proxy rotatorio, búsquedas combinadas) que sean compatibles con Python 3.9 y gratuitas.
4. **Código específico**: Si es necesario, proporciona bloques de código Python para implementar las mejoras (marcados con ```python ... ```).

## EVIDENCIA
PROMPT

# Añadir el diagnóstico completo
echo "" >> "$PROMPT_FILE"
echo "## DIAGNÓSTICO COMPLETO" >> "$PROMPT_FILE"
echo '```' >> "$PROMPT_FILE"
cat config.txt >> "$PROMPT_FILE"
echo '```' >> "$PROMPT_FILE"

# Instrucciones finales
cat >> "$PROMPT_FILE" << 'INSTRUCCIONES'

## FORMATO DE RESPUESTA
Genera un informe estructurado con:

### RESUMEN EJECUTIVO (máx 5 líneas)
### ANÁLISIS DE RIESGOS (posibles fallos)
### PLAN DE MEJORA (pasos concretos, priorizados)
### CÓDIGO SUGERIDO (si aplica, con bloques marcados con ### ARCHIVO: <nombre>)
### PREGUNTAS ABIERTAS (si necesitas más info)

Asegúrate de que las sugerencias sean:
- **Totalmente compatibles** con Python 3.9.
- **Sin dependencias externas de pago**.
- **Respetando los principios**: no eliminar nada que funcione, solo mejorar.

Devuelve SOLO el informe, sin explicaciones adicionales.
INSTRUCCIONES

echo "✅ Prompt generado en $PROMPT_FILE"

# ================================================================
# 3. CONSULTA A QWEN (Ollama)
# ================================================================
echo "🚀 Enviando consulta a Qwen (qwen2.5-coder:7b)..."
echo "⏳ Esto puede tomar unos minutos. Espera..."

# Verificar que Ollama está corriendo
if ! pgrep -x "ollama" > /dev/null; then
    echo "⚠️ Ollama no está corriendo. Iniciando..."
    ollama serve &
    sleep 3
fi

# Ejecutar Qwen y guardar respuesta
ollama run qwen2.5-coder:7b "$(cat "$PROMPT_FILE")" > respuesta_qwen.txt

echo "✅ Respuesta guardada en respuesta_qwen.txt"

# ================================================================
# 4. PROCESAMIENTO DE LA RESPUESTA
# ================================================================
echo "📂 Procesando respuesta de Qwen..."

if grep -q "### ARCHIVO:" respuesta_qwen.txt; then
    echo "🔍 Se encontraron bloques de código para archivos."
    mkdir -p codigo_generado
    csplit -q -f "codigo_generado/bloque_" respuesta_qwen.txt '/### ARCHIVO:/' '{*}' 2>/dev/null
    
    for file in codigo_generado/bloque_*; do
        if [ -f "$file" ]; then
            NOMBRE=$(head -1 "$file" | sed -n 's/.*### ARCHIVO: \(.*\)/\1/p')
            if [ ! -z "$NOMBRE" ]; then
                tail -n +2 "$file" > "codigo_generado/$(basename "$NOMBRE")"
                echo "   ✅ Código extraído: $NOMBRE"
            fi
            rm "$file"
        fi
    done
else
    echo "ℹ️ No se encontraron bloques de código en la respuesta."
fi

echo ""
echo "📌 RESUMEN DE RECOMENDACIONES (extraído de la respuesta):"
grep -E "^###|^##" respuesta_qwen.txt | head -20

echo ""
echo "✅ DIAGNÓSTICO COMPLETADO"
echo "📁 Archivos generados en: $(pwd)"
echo "   - config.txt       : Diagnóstico completo"
echo "   - prompt.txt       : Prompt enviado a Qwen"
echo "   - respuesta_qwen.txt : Respuesta completa de Qwen"
if [ -d "codigo_generado" ]; then
    echo "   - codigo_generado/ : Archivos de código sugeridos por Qwen"
fi

echo ""
echo "👉 Para revisar la respuesta completa:"
echo "   cat $(pwd)/respuesta_qwen.txt"
echo ""
echo "👉 Para aplicar sugerencias de código (con precaución):"
echo "   cp $(pwd)/codigo_generado/* ../  # (si estás seguro)"
echo ""

cd ..

echo "🔚 FIN DEL PROCESO"
echo "============================================================="
