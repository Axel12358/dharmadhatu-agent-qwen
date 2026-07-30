#!/bin/bash

# ================================================================
# CONSULTAR A QWEN PARA PLAN DE OPTIMIZACIÓN INTEGRAL
# ================================================================

echo "🧠 CONSULTANDO A QWEN PARA PLAN DE OPTIMIZACIÓN"
echo "=================================================="

# Crear carpeta para la consulta
CONSULTA_DIR="consulta_qwen_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$CONSULTA_DIR"
cd "$CONSULTA_DIR" || exit

# 1. RECOPILAR INFORMACIÓN ACTUAL
echo "📂 Recopilando contexto del proyecto..."

echo "--- CONFIGURACIÓN ACTUAL (config_temp.json) ---" > contexto.txt
cat ../config_temp.json 2>/dev/null >> contexto.txt || echo "No existe config_temp.json" >> contexto.txt

echo -e "\n\n--- ESTRUCTURA DE SCRAPERS ---" >> contexto.txt
ls -la ../scrapers/*.py 2>/dev/null >> contexto.txt

echo -e "\n\n--- FUNCIONES PRINCIPALES DE SCRAPERS ---" >> contexto.txt
for scraper in ../scrapers/*.py; do
    echo "👉 $(basename $scraper):" >> contexto.txt
    grep -E "^(async )?def " "$scraper" 2>/dev/null | head -3 >> contexto.txt
    echo "" >> contexto.txt
done

echo -e "\n\n--- ÚLTIMOS RESULTADOS DEL BOT ---" >> contexto.txt
if [ -f ../loop_completo.log ]; then
    tail -30 ../loop_completo.log | grep -E "Score|eventos|Tiempo" >> contexto.txt
else
    echo "No hay loop_completo.log" >> contexto.txt
fi

echo -e "\n\n--- PRINCIPIOS DEL PROYECTO ---" >> contexto.txt
echo "- Siempre sumar, nunca restar (no eliminar funcionalidades)" >> contexto.txt
echo "- Dejar lo que sirve (mantener código que funciona)" >> contexto.txt
echo "- Siempre es una implementación de mejora" >> contexto.txt
echo "- Todo local, gratuito y compatible con Python 3.9" >> contexto.txt

echo "✅ Contexto recopilado en contexto.txt"

# 2. CONSTRUIR PROMPT PARA QWEN
echo "🧠 Construyendo prompt para Qwen..."

cat > prompt_qwen.txt << 'PROMPT'
Eres un experto en scraping, optimización de sistemas con Python asyncio, Playwright y Ollama.

## CONTEXTO DEL PROYECTO
El proyecto Dharmadhatu Bot v5 es un sistema de scraping de eventos psytrance que utiliza:
- Playwright (async) para navegación
- Ollama + Qwen para enriquecimiento y decisiones
- asyncio para paralelismo
- Configuración dinámica (países, subgéneros, tipos, ciudades)

## PRINCIPIOS FUNDAMENTALES (NO ROMPER NUNCA)
1. **Siempre sumar, nunca restar**: No eliminar funcionalidades existentes.
2. **Dejar lo que sirve**: Mantener scrapers que dan resultados.
3. **Siempre es una implementación de mejora**: Cada cambio debe optimizar.
4. **Todo local y gratuito**: Sin dependencias de pago.

## PARÁMETROS ACTUALES (basados en config_temp.json)
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

### 1. ANÁLISIS DE PARÁMETROS ACTUALES
- ¿Qué países, subgéneros, tipos y ciudades deberían priorizarse para maximizar eventos reales?
- ¿Cuáles son las combinaciones más efectivas (ej: "psytrance festival Madrid")?

### 2. ESTRATEGIA DE LOOP DE OPTIMIZACIÓN
- ¿Cuántas iteraciones recomiendas y con qué combinaciones?
- ¿Cómo deberían variar los parámetros en cada iteración?
- ¿Qué métrica de éxito (score) deberíamos usar?

### 3. MEJORAS EN SCRAPERS
- ¿Qué cambios concretos sugieres para que Facebook devuelva más eventos reales?
- ¿Cómo manejar excepciones y rate limiting sin romper lo que funciona?
- ¿Es recomendable usar proxies rotativos? ¿Cómo implementarlos localmente?

### 4. INTEGRACIÓN DE SUBAGENTES
- ¿Qué otros scrapers (Resident Advisor, Eventbrite, Instagram) deberíamos integrar y en qué orden?
- ¿Cómo evitar que un scraper que falle detenga todo el proceso?

### 5. PLAN DE IMPLEMENTACIÓN CONCRETO
- Pasos detallados para aplicar las mejoras.
- Código específico si es necesario (marcado con ### ARCHIVO: <nombre>).
- Orden de prioridad de los cambios.

## FORMATO DE RESPUESTA
Genera un informe estructurado con:
### RESUMEN EJECUTIVO
### ANÁLISIS DE PARÁMETROS (valores recomendados)
### ESTRATEGIA DE LOOP (iteraciones, combinaciones)
### MEJORAS EN SCRAPERS (código si aplica)
### PLAN DE IMPLEMENTACIÓN (pasos concretos)
### PREGUNTAS ABIERTAS (si necesitas más info)

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
echo "   - contexto.txt       : Contexto del proyecto"
echo "   - prompt_qwen.txt    : Prompt enviado a Qwen"
echo "   - respuesta_qwen.txt : Respuesta completa de Qwen"
echo ""
echo "👉 Para ver la respuesta completa:"
echo "   cat $(pwd)/respuesta_qwen.txt"
echo ""
echo "🔚 FIN DE LA CONSULTA"
cd ..
