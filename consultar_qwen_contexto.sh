#!/bin/bash

echo "🧠 CONSULTANDO A QWEN CON CONTEXTO COMPLETO"
echo "============================================"

# 1. Recopilar contexto completo
cd ~/dharmadhatu_agent_qwen

{
    echo "=== CONFIGURACIÓN ACTUAL (config_temp.json) ==="
    cat config_temp.json 2>/dev/null || echo "No existe config_temp.json"
    
    echo -e "\n=== CÓDIGO ACTUAL DE facebook_public.py (función generar_queries) ==="
    grep -A 30 "def generar_queries_facebook" scrapers/facebook_public.py 2>/dev/null || echo "No se encontró la función"
    
    echo -e "\n=== CÓDIGO ACTUAL DE main_v5.py (sección de priorizar_paises) ==="
    grep -B 5 -A 5 "priorizar_paises" main_v5.py 2>/dev/null || echo "No se encontró priorizar_paises"
    
    echo -e "\n=== LOG DE LA ÚLTIMA EJECUCIÓN (ERRORES) ==="
    tail -50 loop_rapido.log 2>/dev/null | grep -i "error\|facebook\|priorizar" || echo "No hay log"
    
    echo -e "\n=== ESTRUCTURA DEL REPOSITORIO (primer commit) ==="
    git log --oneline --all | head -5 2>/dev/null || echo "No es un repositorio git"
    
    echo -e "\n=== LISTA DE SCRAPERS DISPONIBLES ==="
    ls -la scrapers/*.py 2>/dev/null || echo "No hay scrapers"
    
} > contexto_completo.txt

echo "✅ Contexto recopilado en contexto_completo.txt"

# 2. Construir prompt para Qwen
cat > prompt_qwen.txt << 'PROMPT'
Eres un experto en Python, Playwright asíncrono, scraping y optimización de sistemas.

## CONTEXTO DEL PROYECTO
El proyecto **Dharmadhatu Bot v5** es un sistema de scraping de eventos psytrance que ya funcionaba correctamente en una versión anterior del repositorio. Actualmente está fallando porque:

1. **Prioriza países fijos** en lugar de usar combinaciones aleatorias de países, subgéneros, tipos de evento y ciudades.
2. **No está usando la lógica de combinaciones** que funcionaba antes (cuando el bot encontraba eventos reales en grupos de Facebook).
3. **El score está estancado en 34**, mientras que antes superaba 50.

## PROBLEMAS ESPECÍFICOS A RESOLVER

1. **Generación de queries**: El bot debe generar combinaciones ALEATORIAS de países, subgéneros, tipos de evento y ciudades. NO debe priorizar un país fijo.
2. **Búsqueda en grupos**: Debe buscar en grupos de Facebook usando esas combinaciones, no solo en el buscador general.
3. **Extracción de organizador**: Debe extraer correctamente el organizador usando `data-testid="event-card-host"` y regex como fallback.
4. **Paralelismo**: Debe ejecutar las queries en paralelo (usando asyncio y semáforo) para ser rápido, pero sin sobrecargar el sistema.

## EVIDENCIA (contexto completo)
PROMPT

cat contexto_completo.txt >> prompt_qwen.txt

cat >> prompt_qwen.txt << 'INSTRUCCIONES'

## PREGUNTAS ESPECÍFICAS

1. **¿Cómo debería ser la función `generar_queries_facebook` para que genere combinaciones aleatorias y NO priorice países fijos?**
2. **¿Cómo debería estructurarse la búsqueda en grupos de Facebook para que use esas combinaciones?**
3. **¿Qué cambios concretos hay que hacer en `main_v5.py` para que no fuerce países fijos?**
4. **Proporciona el código COMPLETO y FUNCIONAL de `facebook_public.py` que resuelva estos problemas.**

## FORMATO DE RESPUESTA
### ANÁLISIS DEL PROBLEMA
### CÓDIGO CORREGIDO
- ### ARCHIVO: scrapers/facebook_public.py (código completo)
### PLAN DE IMPLEMENTACIÓN (pasos concretos)

Devuelve SOLO el informe estructurado.
INSTRUCCIONES

echo "✅ Prompt generado en prompt_qwen.txt"

# 3. Consultar a Qwen
echo "🚀 Enviando consulta a Qwen (qwen2.5-coder:7b)..."
echo "⏳ Espera 3-5 minutos..."
ollama run qwen2.5-coder:7b "$(cat prompt_qwen.txt)" > respuesta_qwen.txt

echo "✅ Respuesta guardada en respuesta_qwen.txt"

# 4. Mostrar resumen
echo ""
echo "📌 RECOMENDACIONES PRINCIPALES:"
grep -E "^###|^##" respuesta_qwen.txt | head -20

echo ""
echo "📂 Archivos generados:"
echo "   - contexto_completo.txt   : Contexto completo del proyecto"
echo "   - prompt_qwen.txt         : Prompt enviado a Qwen"
echo "   - respuesta_qwen.txt      : Solución de Qwen"
echo ""
echo "👉 Para ver la respuesta completa:"
echo "   cat respuesta_qwen.txt"
echo ""
echo "👉 Para extraer el código:"
echo "   grep -A 500 '### ARCHIVO:' respuesta_qwen.txt > codigo_extraido.txt"
echo ""
echo "🔚 FIN"
