#!/bin/bash

echo "🧠 CONSULTANDO A QWEN PARA EXTRAER ORGANIZADORES/HOSTS"
echo "========================================================"

CONSULTA_DIR="consulta_organizadores_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$CONSULTA_DIR"
cd "$CONSULTA_DIR" || exit

# 1. Recopilar código actual
echo "--- CÓDIGO ACTUAL DE facebook_public.py (extracción de organizador) ---" > contexto.txt
grep -A 30 -B 5 "organizador" ../scrapers/facebook_public.py 2>/dev/null >> contexto.txt

echo -e "\n\n--- CÓDIGO ACTUAL DE instagram_public.py (extracción de perfil) ---" >> contexto.txt
grep -A 20 -B 5 "perfil\|profile\|username" ../scrapers/instagram_public.py 2>/dev/null >> contexto.txt

echo -e "\n\n--- ESTRUCTURA HTML DE UN EVENTO DE FACEBOOK (ejemplo) ---" >> contexto.txt
cat << 'HTML' >> contexto.txt
<div role="article">
  <span dir="auto">Nombre del evento</span>
  <span data-testid="event-card-date">Fecha</span>
  <span data-testid="event-card-location">Lugar</span>
  <span data-testid="event-card-host">Hosted by: Nombre del Organizador</span>
  <a href="/events/123456">Enlace</a>
</div>
HTML

echo -e "\n\n--- ESTRUCTURA HTML DE UN POST DE INSTAGRAM (ejemplo) ---" >> contexto.txt
cat << 'HTML' >> contexto.txt
<article>
  <header>
    <a href="/username/">@username</a>
    <span>Nombre del perfil</span>
  </header>
  <div>Contenido del post</div>
  <time>Fecha</time>
</article>
HTML

echo -e "\n\n--- ÚLTIMOS LOGS DE ERRORES (organizadores) ---" >> contexto.txt
grep -i "organizador\|host\|email\|perfil" ../loop_rapido.log 2>/dev/null | tail -20 >> contexto.txt

echo -e "\n\n--- CONFIGURACIÓN ACTUAL ---" >> contexto.txt
cat ../config_temp.json 2>/dev/null >> contexto.txt

# 2. Construir prompt para Qwen
cat > prompt_qwen.txt << 'PROMPT'
Eres un experto en scraping con Python, Playwright y asyncio, especializado en extracción de datos de redes sociales.

## CONTEXTO
El proyecto Dharmadhatu Bot v5 scrapea eventos de Facebook e Instagram, pero **no está extrayendo correctamente los organizadores/hosts**:
- En Facebook: cada evento tiene un "Hosted by" o "Organizado por" que no se captura.
- En Instagram: cada post tiene un perfil de usuario que publica el evento, pero no se extrae.

## PROBLEMAS ACTUALES
1. **Facebook**: Los selectores actuales no encuentran el organizador (salen "No disponible").
2. **Instagram**: No se extrae el nombre de usuario o perfil de quien publica.
3. **Enriquecimiento**: Se intenta extraer organizador del texto, pero es poco fiable.

## EVIDENCIA
- Código actual de extracción de organizador.
- Ejemplos de estructura HTML real de Facebook e Instagram.
- Logs donde se ven "Organizador: No disponible".

## OBJETIVO
Proporcionar **selectores precisos** y **lógica de extracción mejorada** para:
1. **Facebook**: Extraer el organizador desde `data-testid="event-card-host"` o mediante patrones de texto "Hosted by".
2. **Instagram**: Extraer el nombre de usuario y perfil desde el `header` del post o desde `a[href*="/"]`.
3. **Mantener la compatibilidad** con Python 3.9 y Playwright.
4. **No romper** lo que ya funciona (Goabase, Songkick, etc.).

## FORMATO DE RESPUESTA
Genera un informe con:
### ANÁLISIS DEL PROBLEMA
### CÓDIGO CORREGIDO
- Bloque con ### ARCHIVO: scrapers/facebook_public.py (código completo con nuevos selectores)
- Bloque con ### ARCHIVO: scrapers/instagram_public.py (código completo con extracción de perfil)
### PLAN DE IMPLEMENTACIÓN (pasos para aplicar los cambios sin afectar el loop ni los agentes)

Devuelve SOLO el informe, sin explicaciones adicionales.
PROMPT

# Añadir el contexto
echo "" >> prompt_qwen.txt
echo "## CONTEXTO COMPLETO" >> prompt_qwen.txt
echo '```' >> prompt_qwen.txt
cat contexto.txt >> prompt_qwen.txt
echo '```' >> prompt_qwen.txt

echo "✅ Prompt generado en prompt_qwen.txt"

# 3. Consultar a Qwen
echo "🚀 Enviando consulta a Qwen (qwen2.5-coder:7b)..."
echo "⏳ Esto puede tomar varios minutos. Espera..."

if ! pgrep -x "ollama" > /dev/null; then
    echo "⚠️ Ollama no está corriendo. Iniciando..."
    ollama serve &
    sleep 3
fi

ollama run qwen2.5-coder:7b "$(cat prompt_qwen.txt)" > respuesta_qwen.txt

echo "✅ Respuesta guardada en respuesta_qwen.txt"

# 4. Mostrar resumen
echo ""
echo "📌 RESUMEN DE RECOMENDACIONES (extraído de la respuesta):"
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
