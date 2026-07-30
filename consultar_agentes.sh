#!/bin/bash

echo "🧠 CONSULTANDO A LLAMA3.2:3b CON ESTRUCTURA DE AGENTES"
echo "======================================================="

# 1. Construir prompt con agentes y subagentes
cat > prompt_agentes.txt << 'PROMPT'
Eres un experto en scraping con Python 3.9, Playwright asíncrono y asyncio.

## CONTEXTO DEL PROYECTO
Dharmadhatu Bot v5 es un sistema de scraping de eventos psytrance que utiliza:
- **Orquestador principal** (`main_v5.py`): ejecuta scrapers en paralelo y consolida resultados.
- **Scrapers asíncronos** (`facebook_public.py`, `instagram_public.py`, `goabase.py`, `songkick.py`, etc.).
- **Enriquecedor** (`enriquecer_eventos.py`): usa Ollama para generar descripciones y extraer organizadores/emails.
- **Loop de optimización** (`loop_optimizer.py`): prueba combinaciones de parámetros para maximizar el score.
- **Subagentes** (nuevos scrapers que se suman sin romper lo existente).

## PROBLEMAS CRÍTICOS ACTUALES

### 1. Facebook - Organizador NO se extrae
- Selectores fallan: `span[data-testid="event-card-host"]` no se captura.
- **Solución**: usar selector correcto y regex `Hosted by\s*([^\n]+)` como fallback.

### 2. Facebook - Búsquedas combinadas NO se usan
- Actualmente solo usa query fija `"psytrance festival"`.
- **Solución**: generar combinaciones de países, subgéneros, tipos y ciudades desde `config_temp.json`.

### 3. Instagram - Perfil NO se extrae
- No se captura el usuario que publica el evento.
- **Solución**: extraer `header a[href*="/"]` o `span[class*="username"]`.

### 4. Loop de optimización - No mejora porque los parámetros no afectan a los scrapers
- El loop cambia `config_temp.json`, pero los scrapers ignoran las combinaciones.
- **Solución**: modificar scrapers para usar `priorizar_paises`, `subgeneros`, `tipos_evento`, `ciudades`.

## EVIDENCIA (código actual de facebook_public.py)
PROMPT

# Incluir el código actual de facebook_public.py (solo la parte relevante)
echo "" >> prompt_agentes.txt
echo "### CÓDIGO ACTUAL DE facebook_public.py (sección de organización)" >> prompt_agentes.txt
grep -A 50 "organizador" ~/dharmadhatu_agent_qwen/scrapers/facebook_public.py 2>/dev/null >> prompt_agentes.txt

echo "" >> prompt_agentes.txt
echo "### CÓDIGO ACTUAL DE instagram_public.py (sección de perfil)" >> prompt_agentes.txt
grep -A 30 "perfil\|profile\|username" ~/dharmadhatu_agent_qwen/scrapers/instagram_public.py 2>/dev/null >> prompt_agentes.txt

echo "" >> prompt_agentes.txt
echo "### CONFIGURACIÓN ACTUAL" >> prompt_agentes.txt
cat ~/dharmadhatu_agent_qwen/config_temp.json 2>/dev/null >> prompt_agentes.txt

cat >> prompt_agentes.txt << 'INSTRUCCIONES'

## OBJETIVO PRINCIPAL
Generar **código CORREGIDO** para:
1. `scrapers/facebook_public.py`: que use combinaciones de países, subgéneros, tipos y ciudades, y extraiga organizador con `span[data-testid="event-card-host"]` y regex.
2. `scrapers/instagram_public.py`: que extraiga perfil/usuario desde el header del post.

## REQUISITOS ESTRICTOS
- **NO romper** lo que ya funciona (Goabase, Songkick, etc.).
- **NO modificar** el loop, agentes ni subagentes (solo los scrapers).
- **Todo local y gratuito** (sin APIs externas).
- **Python 3.9** compatible.
- **Mantener principios**: "siempre sumar, nunca restar", "dejar lo que sirve".

## FORMATO DE RESPUESTA
### ANÁLISIS DEL PROBLEMA
### CÓDIGO CORREGIDO
- ### ARCHIVO: scrapers/facebook_public.py
- ### ARCHIVO: scrapers/instagram_public.py
### PLAN DE IMPLEMENTACIÓN (pasos concretos para aplicar los cambios sin afectar el loop)

Devuelve SOLO el informe estructurado.
INSTRUCCIONES

echo "✅ Prompt generado en prompt_agentes.txt"

# 2. Consultar a llama3.2:3b
echo "🚀 Enviando consulta a llama3.2:3b..."
echo "⏳ Espera 3-5 minutos..."
ollama run llama3.2:3b "$(cat prompt_agentes.txt)" > respuesta_qwen.txt

echo "✅ Respuesta guardada en respuesta_qwen.txt"

# 3. Mostrar resumen
echo ""
echo "📌 RECOMENDACIONES PRINCIPALES:"
grep -E "^###|^##" respuesta_qwen.txt | head -20

echo ""
echo "📂 Archivos generados en: $(pwd)"
echo "   - prompt_agentes.txt   : Prompt enviado a llama3.2:3b"
echo "   - respuesta_qwen.txt   : Solución completa de llama3.2:3b"
echo ""
echo "👉 Para ver respuesta completa:"
echo "   cat respuesta_qwen.txt"
echo ""
echo "👉 Para extraer solo el código de los archivos:"
echo "   grep -A 500 '### ARCHIVO:' respuesta_qwen.txt > codigo_extraido.txt"
echo ""
echo "🔚 FIN"
