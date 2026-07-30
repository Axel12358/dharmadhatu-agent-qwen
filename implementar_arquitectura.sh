#!/bin/bash

# ================================================================
# SCRIPT PARA IMPLEMENTAR LA ARQUITECTURA RESILIENTE
# ================================================================
# - Activa Ollama en la terminal nueva
# - Pregunta a Qwen para generar el código de la nueva arquitectura
# - Extrae los bloques de código y los guarda en los archivos correspondientes
# - No rompe nada existente (siempre sumar, nunca restar)
# ================================================================

echo "🧠 IMPLEMENTANDO ARQUITECTURA RESILIENTE PARA FACEBOOK"
echo "========================================================"

# 1. ACTIVAR OLLAMA
echo "🔥 Activando Ollama..."
if ! pgrep -x "ollama" > /dev/null; then
    ollama serve > /dev/null 2>&1 &
    sleep 5
    echo "✅ Ollama activado"
else
    echo "✅ Ollama ya estaba activo"
fi

# 2. CREAR CARPETA PARA EL CÓDIGO GENERADO
mkdir -p ~/dharmadhatu_agent_qwen/codigo_generado
cd ~/dharmadhatu_agent_qwen/codigo_generado || exit

# 3. CONSTRUIR EL PROMPT PARA QWEN
cat > prompt.txt << 'PROMPT'
Eres un experto en Python 3.9, Playwright asíncrono, asyncio y Ollama (Qwen).
Necesito que generes el código completo para implementar la siguiente arquitectura en el proyecto Dharmadhatu Bot v5.

## CONTEXTO DEL PROYECTO
El proyecto Dharmadhatu Bot v5 es un sistema de scraping de eventos psytrance que actualmente:
- Goabase: ✅ Funciona (23 eventos).
- Songkick: ✅ Funciona (11 eventos).
- Facebook: ❌ No funciona (0 eventos porque los selectores han caducado).
- Enriquecimiento con Qwen: ✅ Funciona.
- Score actual: 34.

## PRINCIPIOS (NO ROMPER)
1. **Siempre sumar, nunca restar**: No eliminar funcionalidades existentes.
2. **Dejar lo que sirve**: Mantener Goabase, Songkick y el enriquecimiento tal como están.
3. **Todo local y gratuito**: Sin APIs externas, sin dependencias de pago.
4. **Compatible con Python 3.9**: Usar librerías compatibles.

## ARQUITECTURA PROPUESTA (basada en el análisis previo)

### 1. ESTRATEGIA MÚLTIPLE (Patrón de Estrategias)
Crear un módulo `strategies.py` con las siguientes estrategias, en orden de prioridad:
- **Estrategia 1**: `baberibrar/facebook-events-scraper` (Playwright, sin login).
- **Estrategia 2**: `mrkkr/fb-events-scraper` (alternativa).
- **Estrategia 3**: Extracción por texto (expresiones regulares sobre el HTML).
- **Estrategia 4**: Extracción con Qwen (interpreta el HTML y extrae eventos como JSON).

### 2. ANTI-DETECCIÓN ROBUSTA
Crear un módulo `anti_detection.py` que incluya:
- `playwright-stealth` para evitar detección.
- Rotación de user-agents (lista de user-agents reales).
- Proxies rotativos (usar lista de proxies gratuitos).
- Simulación de comportamiento humano (movimientos de mouse, scroll aleatorio).

### 3. FALLBACK CON MEMORIA
Crear un módulo `fallback.py` que:
- Guarde configuraciones exitosas en archivos markdown (carpeta `configs_exitosas/`).
- Cargue configuraciones previas al inicio del loop de optimización.

### 4. ORQUESTACIÓN
Modificar `facebook_public.py` para que:
- Use el patrón de estrategias (pruebe cada estrategia en orden).
- Use anti-detección en cada intento.
- Si una estrategia falla, pase a la siguiente.
- Si todas fallan, devuelva 0 eventos.

## FORMATO DE RESPUESTA
Genera código para los siguientes archivos, cada uno marcado con ### ARCHIVO: <nombre>:
1. `strategies.py` (módulo con todas las estrategias)
2. `anti_detection.py` (módulo con anti-detección)
3. `fallback.py` (módulo con memoria de configuraciones)
4. `facebook_public.py` (código completo que usa los módulos anteriores)

**Requisitos**:
- Usar Playwright asíncrono (`async/await`).
- Usar `asyncio.Semaphore` para limitar la concurrencia.
- Incluir manejo de errores con `try/except`.
- Todo debe ser instalable localmente con `pip`.

Devuelve SOLO los bloques de código con ### ARCHIVO:, sin explicaciones adicionales.
PROMPT

echo "✅ Prompt construido"

# 4. CONSULTAR A QWEN
echo "🚀 Enviando consulta a qwen2.5-coder:7b..."
echo "⏳ Esto puede tomar 3-5 minutos. Espera..."
ollama run qwen2.5-coder:7b "$(cat prompt.txt)" > respuesta.txt

echo "✅ Respuesta guardada en respuesta.txt"

# 5. EXTRAER BLOQUES DE CÓDIGO
echo "📂 Extrayendo bloques de código..."

# Función para extraer bloques entre ### ARCHIVO: y ### FIN
extract_code() {
    awk '/### ARCHIVO:/{flag=1; next} /### FIN/{flag=0} flag' respuesta.txt | \
    awk 'BEGIN {file=""; content=""} 
         /### ARCHIVO:/ {if (file && content) {print content > file; content=""}; file=$NF; next} 
         {content = content $0 "\n"} 
         END {if (file && content) {print content > file}}'
}

# Extraer archivos
extract_code

# Verificar archivos generados
echo ""
echo "📂 Archivos generados:"
ls -la 2>/dev/null | grep -E "\.py$" || echo "No se generaron archivos .py"

# 6. MOVER ARCHIVOS A SU LUGAR
echo ""
echo "📦 Mover archivos a la carpeta del proyecto..."

# Mover strategies.py
if [ -f strategies.py ]; then
    mv strategies.py ../scrapers/ 2>/dev/null && echo "✅ strategies.py -> scrapers/"
else
    echo "⚠️ No se encontró strategies.py"
fi

# Mover anti_detection.py
if [ -f anti_detection.py ]; then
    mv anti_detection.py ../scrapers/ 2>/dev/null && echo "✅ anti_detection.py -> scrapers/"
else
    echo "⚠️ No se encontró anti_detection.py"
fi

# Mover fallback.py
if [ -f fallback.py ]; then
    mv fallback.py ../scrapers/ 2>/dev/null && echo "✅ fallback.py -> scrapers/"
else
    echo "⚠️ No se encontró fallback.py"
fi

# Mover facebook_public.py (respaldo y reemplazo)
if [ -f facebook_public.py ]; then
    cp ../scrapers/facebook_public.py ../scrapers/facebook_public.py.bak_$(date +%Y%m%d_%H%M%S)
    mv facebook_public.py ../scrapers/ 2>/dev/null && echo "✅ facebook_public.py -> scrapers/"
else
    echo "⚠️ No se encontró facebook_public.py"
fi

echo ""
echo "🔚 FIN DEL PROCESO"
echo ""
echo "👉 Para revisar la respuesta completa:"
echo "   cat ~/dharmadhatu_agent_qwen/codigo_generado/respuesta.txt"
echo ""
echo "👉 Para ejecutar el bot:"
echo "   cd ~/dharmadhatu_agent_qwen && python3 main_v5.py"
