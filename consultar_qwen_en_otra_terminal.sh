#!/bin/bash

# ================================================================
# CONSULTAR A QWEN EN OTRA TERMINAL - ORGANIZADORES Y PERFILES
# ================================================================
# Ejecuta este script en UNA NUEVA VENTANA DE TERMINAL
# ================================================================

echo "🧠 CONSULTANDO A QWEN (en otra terminal)"
echo "============================================"
echo "📂 Directorio: $(pwd)"

CONSULTA_DIR="consulta_organizadores_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$CONSULTA_DIR"
cd "$CONSULTA_DIR" || exit

# 1. RECOPILAR CONTEXTO
echo "📂 Recopilando contexto..."

echo "--- CÓDIGO ACTUAL DE facebook_public.py ---" > contexto.txt
cat ../scrapers/facebook_public.py 2>/dev/null | grep -A 30 -B 5 "organizador\|host\|Hosted" >> contexto.txt

echo -e "\n\n--- CÓDIGO ACTUAL DE instagram_public.py ---" >> contexto.txt
cat ../scrapers/instagram_public.py 2>/dev/null | grep -A 20 -B 5 "perfil\|username\|profile" >> contexto.txt

echo -e "\n\n--- CONFIGURACIÓN ACTUAL ---" >> contexto.txt
cat ../config_temp.json 2>/dev/null >> contexto.txt

echo -e "\n\n--- LOGS RECIENTES (organizadores) ---" >> contexto.txt
grep -i "organizador\|host\|email\|perfil" ../loop_rapido.log 2>/dev/null | tail -30 >> contexto.txt

# 2. CONSTRUIR PROMPT
cat > prompt_qwen.txt << 'PROMPT'
Eres un experto en scraping con Python, Playwright y asyncio.

## CONTEXTO
El proyecto Dharmadhatu Bot v5 scrapea eventos de Facebook e Instagram, pero NO extrae correctamente organizadores/hosts/perfiles.

## PROBLEMAS
1. Facebook: selectores actuales no capturan "Hosted by" o "Organizado por" → salen "No disponible".
2. Instagram: no se extrae el nombre de usuario o perfil de quien publica.
3. El enriquecimiento con Qwen es poco fiable para extraer organizadores del texto.

## OBJETIVO
Proporcionar código CORREGIDO y FUNCIONAL para:
1. Facebook: extraer organizador con selectores precisos.
2. Instagram: extraer nombre de usuario y perfil.

## EVIDENCIA
PROMPT

cat contexto.txt >> prompt_qwen.txt

cat >> prompt_qwen.txt << 'INSTRUCCIONES'

## FORMATO DE RESPUESTA
Genera un informe con:
### ANÁLISIS DEL PROBLEMA
### CÓDIGO CORREGIDO
- Bloque con ### ARCHIVO: scrapers/facebook_public.py (código completo)
- Bloque con ### ARCHIVO: scrapers/instagram_public.py (código completo)
### PLAN DE IMPLEMENTACIÓN

Devuelve SOLO el informe, sin explicaciones adicionales.
INSTRUCCIONES

echo "✅ Prompt generado en prompt_qwen.txt"

# 3. CONSULTAR A QWEN
echo "🚀 Enviando consulta a Qwen (qwen2.5-coder:7b)..."
echo "⏳ Esto puede tomar varios minutos. Espera..."

if ! pgrep -x "ollama" > /dev/null; then
    echo "⚠️ Ollama no está corriendo. Iniciando..."
    ollama serve &
    sleep 3
fi

ollama run qwen2.5-coder:7b "$(cat prompt_qwen.txt)" > respuesta_qwen.txt

echo "✅ Respuesta guardada en respuesta_qwen.txt"

# 4. MOSTRAR RESUMEN
echo ""
echo "📌 RESUMEN DE RECOMENDACIONES:"
grep -E "^###|^##" respuesta_qwen.txt | head -20

echo ""
echo "📂 Archivos generados en: $(pwd)"
echo "   - contexto.txt       : Contexto del problema"
echo "   - prompt_qwen.txt    : Prompt enviado a Qwen"
echo "   - respuesta_qwen.txt : Respuesta completa de Qwen"
echo ""
echo "👉 Para ver la respuesta completa:"
echo "   cat $(pwd)/respuesta_qwen.txt"
echo ""
echo "🔚 FIN DE LA CONSULTA"
cd ..
