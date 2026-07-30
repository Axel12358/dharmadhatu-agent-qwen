#!/bin/bash

# ================================================================
# SCRIPT PARA DIAGNOSTICAR Y SOLUCIONAR EL BOT CON QWEN CODE
# ================================================================

echo "🔍 RECOPILANDO DIAGNÓSTICO DEL PROYECTO..."
echo ""

# 1. Crear archivo de diagnóstico
DIAG_FILE="diagnostico_completo.txt"
echo "=== DIAGNÓSTICO DE DHARMADHATU BOT v5 ===" > $DIAG_FILE
echo "" >> $DIAG_FILE

# Estado de Git
echo "--- GIT STATUS ---" >> $DIAG_FILE
git status >> $DIAG_FILE 2>&1
echo "" >> $DIAG_FILE

# Errores del último loop
echo "--- ÚLTIMO ERROR DEL LOOP ---" >> $DIAG_FILE
if [ -f "loop_completo.log" ]; then
    tail -100 loop_completo.log >> $DIAG_FILE
else
    echo "No hay archivo de log" >> $DIAG_FILE
fi
echo "" >> $DIAG_FILE

# Configuración actual
echo "--- CONFIGURACIÓN ACTUAL (config_temp.json) ---" >> $DIAG_FILE
cat config_temp.json 2>/dev/null >> $DIAG_FILE || echo "No existe config_temp.json" >> $DIAG_FILE
echo "" >> $DIAG_FILE

# Lista de scrapers disponibles
echo "--- SCRAPERS DISPONIBLES ---" >> $DIAG_FILE
ls -la scrapers/*.py 2>/dev/null >> $DIAG_FILE
echo "" >> $DIAG_FILE

# Funciones de los scrapers
echo "--- FUNCIONES EN SCRAPERS ---" >> $DIAG_FILE
for scraper in scrapers/*.py; do
    echo "👉 $scraper:" >> $DIAG_FILE
    grep -E "^(async )?def " "$scraper" 2>/dev/null | head -5 >> $DIAG_FILE
    echo "" >> $DIAG_FILE
done

# Problemas conocidos del último error
echo "--- ERRORES RECURRENTES ---" >> $DIAG_FILE
grep -i "error\|exception\|traceback" loop_completo.log 2>/dev/null | tail -20 >> $DIAG_FILE
echo "" >> $DIAG_FILE

echo "✅ Diagnóstico guardado en $DIAG_FILE"
echo ""

# ================================================================
# ARMAR PROMPT PARA QWEN
# ================================================================

echo "🧠 ARMANDO PROMPT PARA QWEN CODE..."
echo ""

PROMPT_FILE="prompt_para_qwen.txt"

cat > $PROMPT_FILE << 'PROMPT'
Eres un experto en Python asyncio, Playwright y scraping. Necesito que resuelvas los problemas del proyecto Dharmadhatu Bot v5.

## PROBLEMAS ACTUALES
1. El loop de optimización falla con "unhashable type: 'list'" porque estamos pasando una lista de tareas a asyncio.gather en lugar de argumentos individuales.
2. Algunos scrapers son síncronos y no se pueden ejecutar con asyncio.gather.
3. No estamos generando queries combinadas de Facebook correctamente (solo usa "psytrance festival").
4. El enriquecimiento es secuencial y lento (35 eventos en 32 minutos).

## EVIDENCIA
PROMPT

# Añadir el diagnóstico completo al prompt
echo "" >> $PROMPT_FILE
echo "## DIAGNÓSTICO COMPLETO" >> $PROMPT_FILE
echo '```' >> $PROMPT_FILE
cat $DIAG_FILE >> $PROMPT_FILE
echo '```' >> $PROMPT_FILE

# Añadir instrucciones finales
cat >> $PROMPT_FILE << 'INSTRUCCIONES'

## SOLUCIÓN ESPERADA
Genera 4 archivos completos y funcionales (código Python puro, listo para copiar y pegar):
1. `scrapers/facebook_public.py` (optimizado, con queries combinadas y semáforo)
2. `scrapers/enriquecer_eventos.py` (optimizado, con semáforo y concurrencia)
3. `main_v5.py` (orquestador asíncrono con asyncio.gather)
4. `loop_optimizer.py` (loop de mejora robusto, con manejo de errores)

**Instrucciones específicas:**
- TODOS los scrapers deben ser asíncronos (def async).
- Usa `asyncio.gather(*lista_de_tareas)` (con asterisco).
- Cada scraper debe recibir el argumento correcto (int o dict según su firma).
- Incluye manejo de errores con `return_exceptions=True`.
- El enriquecimiento debe usar `asyncio.Semaphore(5)`.
- El loop debe probar combinaciones de parámetros y guardar la mejor configuración.

**Devuelve SOLO los 4 bloques de código, sin explicaciones adicionales.**
Cada bloque debe comenzar con `### ARCHIVO: ...` y terminar con `### FIN`.

INSTRUCCIONES

echo "✅ Prompt guardado en $PROMPT_FILE"
echo ""

# ================================================================
# EJECUTAR QWEN
# ================================================================

echo "🚀 ENVIANDO PROMPT A QWEN CODE..."
echo "Esto puede tomar unos minutos. Espera..."
echo ""

# Verificar que Ollama está corriendo
if ! pgrep -x "ollama" > /dev/null; then
    echo "⚠️ Ollama no está corriendo. Iniciando..."
    ollama serve &
    sleep 3
fi

# Ejecutar Qwen con el prompt
echo "📤 Enviando a qwen2.5-coder:7b..."
ollama run qwen2.5-coder:7b "$(cat $PROMPT_FILE)" > solucion_qwen.txt

echo ""
echo "✅ Respuesta de Qwen guardada en solucion_qwen.txt"
echo ""

# ================================================================
# EXTRAER ARCHIVOS DE LA RESPUESTA
# ================================================================

echo "📂 EXTRAYENDO ARCHIVOS DE LA SOLUCIÓN..."

# Buscar bloques de código en la respuesta
if grep -q "### ARCHIVO:" solucion_qwen.txt; then
    echo "✅ Se encontraron bloques de código en la respuesta."
    echo "📝 Puedes copiar los archivos manualmente desde solucion_qwen.txt"
    echo ""
    echo "Los bloques están marcados con ### ARCHIVO: <nombre>"
    echo ""
    
    # Extraer cada bloque a un archivo temporal
    echo "📦 Archivos extraídos:"
    csplit -q -f "temp_bloque_" solucion_qwen.txt '/### ARCHIVO:/' '{*}' 2>/dev/null
    
    for file in temp_bloque_*; do
        if [ -f "$file" ]; then
            # Extraer nombre del archivo
            NOMBRE=$(head -1 "$file" | sed -n 's/.*### ARCHIVO: \(.*\)/\1/p')
            if [ ! -z "$NOMBRE" ]; then
                # Crear directorio si es necesario
                DIR=$(dirname "$NOMBRE")
                mkdir -p "$DIR"
                # Guardar contenido (eliminando la primera línea)
                tail -n +2 "$file" > "$NOMBRE"
                echo "   ✅ $NOMBRE"
            fi
            rm "$file"
        fi
    done
    
    echo ""
    echo "✅ Archivos generados en el directorio actual."
    echo "📂 Revisa los cambios antes de ejecutar."
else
    echo "⚠️ No se encontraron bloques de código en la respuesta."
    echo "📄 Revisa solucion_qwen.txt para ver la respuesta completa."
fi

echo ""
echo "🔚 FIN DEL DIAGNÓSTICO"
echo ""
echo "👉 Si quieres aplicar automáticamente los cambios, ejecuta:"
echo "   ./aplicar_solucion.sh"
echo ""
echo "👉 Para revisar la solución antes de aplicarla:"
echo "   cat solucion_qwen.txt"
echo ""

