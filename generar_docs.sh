#!/bin/bash
# Script para generar documentación profesional con Qwen

echo "📌 Generando documentación con Qwen..."

# Asegurar que existe la carpeta docs
mkdir -p docs

# 1. README.md
echo "⏳ Generando README.md..."
ollama run qwen2.5-coder:7b "Eres un experto en documentación de software. Genera un README.md profesional para un proyecto de scraping llamado 'Dharmadhatu Bot v5'.

Contexto:
- Scraping de eventos psytrance desde Goabase, Facebook, Instagram, Songkick, Eventbrite.
- Loop de mejora con IA (Qwen) que ajusta configuraciones automáticamente.
- Score máximo alcanzado: 229.
- Tecnologías: Python 3.9, Playwright async, Ollama + Qwen, Pandas, asyncio.
- Principios: Siempre sumar, nunca restar; dejar lo que sirve; siempre es una mejora.

El README debe incluir:
1. Título con badges (Python, Playwright, Ollama).
2. Descripción del proyecto.
3. Tecnologías usadas.
4. Instalación paso a paso.
5. Uso (comandos principales).
6. Resultados clave (score 229).
7. Arquitectura del sistema (diagrama de flujo).
8. Estructura del proyecto.
9. Principios del proyecto.
10. Lecciones aprendidas.
11. Roadmap.

Devuelve SOLO el contenido del README.md, sin explicaciones adicionales." > README.md

# 2. docs/arquitectura.md
echo "⏳ Generando arquitectura.md..."
ollama run qwen2.5-coder:7b "Genera un archivo docs/arquitectura.md profesional que explique la arquitectura del sistema Dharmadhatu Bot v5. Incluye flujo principal, diagrama ASCII, componentes clave y decisiones técnicas. Devuelve SOLO el contenido del archivo." > docs/arquitectura.md

# 3. docs/loop_agente.md
echo "⏳ Generando loop_agente.md..."
ollama run qwen2.5-coder:7b "Genera un archivo docs/loop_agente.md profesional que explique el loop de mejora con el agente Qwen para el proyecto Dharmadhatu Bot v5. Incluye funcionamiento, ejemplo de prompt, historial y criterio de éxito. Devuelve SOLO el contenido del archivo." > docs/loop_agente.md

# 4. docs/scrapers_asincronos.md
echo "⏳ Generando scrapers_asincronos.md..."
ollama run qwen2.5-coder:7b "Genera un archivo docs/scrapers_asincronos.md profesional que explique los scrapers asíncronos del proyecto Dharmadhatu Bot v5. Incluye Goabase, Facebook, Instagram, Songkick, Eventbrite, paralelismo y manejo de errores. Devuelve SOLO el contenido del archivo." > docs/scrapers_asincronos.md

# 5. docs/enriquecimiento_qwen.md
echo "⏳ Generando enriquecimiento_qwen.md..."
ollama run qwen2.5-coder:7b "Genera un archivo docs/enriquecimiento_qwen.md profesional que explique el enriquecimiento de eventos con Qwen para el proyecto Dharmadhatu Bot v5. Incluye proceso, prompts utilizados, optimización y resultados. Devuelve SOLO el contenido del archivo." > docs/enriquecimiento_qwen.md

echo "✅ Documentación generada correctamente."
echo "📂 Revisa los archivos en el directorio actual y en docs/"
