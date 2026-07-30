#!/bin/bash

# ================================================================
# CONSULTAR A QWEN PARA MEJORAR EL ENRIQUECIMIENTO DE EVENTOS
# ================================================================

echo "🧠 CONSULTANDO A QWEN PARA MEJORAR EL ENRIQUECIMIENTO"
echo "========================================================"

# Crear carpeta para la consulta
CONSULTA_DIR="consulta_enriquecimiento_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$CONSULTA_DIR"
cd "$CONSULTA_DIR" || exit

# 1. RECOPILAR INFORMACIÓN DEL ENRIQUECIMIENTO
echo "📂 Recopilando contexto del enriquecimiento..."

echo "--- CÓDIGO ACTUAL DE enriquecer_eventos.py ---" > contexto.txt
cat ../scrapers/enriquecer_eventos.py 2>/dev/null >> contexto.txt || echo "No existe enriquecer_eventos.py" >> contexto.txt

echo -e "\n\n--- EJEMPLO DE RESULTADOS (CSV) ---" >> contexto.txt
cat ../eventos_enriquecidos.csv 2>/dev/null | head -20 >> contexto.txt || echo "No existe eventos_enriquecidos.csv" >> contexto.txt

echo -e "\n\n--- FUNCIONES DE EXTRACCIÓN DE CONTACTOS ---" >> contexto.txt
grep -A 10 "extraer_organizador_email" ../scrapers/enriquecer_eventos.py 2>/dev/null >> contexto.txt

echo -e "\n\n--- FILTRO DE EVENTOS REALES EN main_v5.py ---" >> contexto.txt
grep -A 10 "eventos_reales" ../main_v5.py 2>/dev/null >> contexto.txt

echo "✅ Contexto recopilado en contexto.txt"

# 2. CONSTRUIR PROMPT PARA QWEN
echo "🧠 Construyendo prompt para Qwen..."

cat > prompt_qwen.txt << 'PROMPT'
Eres un experto en scraping, enriquecimiento de datos y extracción de información con Python y Ollama.

## CONTEXTO DEL PROYECTO
El Dharmadhatu Bot v5 enriquece eventos con Qwen para extraer:
- Descripciones (funciona bien).
- Organizador (falla: extrae el nombre del evento en lugar del organizador).
- Email (falla: siempre "No disponible").

Además, el filtro de "eventos reales" es demasiado restrictivo:
- Solo pasan 8 de 41 eventos.
- Songkick da 11 eventos pero no pasan el filtro.

## EVIDENCIA
PROMPT

# Añadir el contexto
echo "" >> prompt_qwen.txt
echo "## DIAGNÓSTICO COMPLETO" >> prompt_qwen.txt
echo '```' >> prompt_qwen.txt
cat contexto.txt >> prompt_qwen.txt
echo '```' >> prompt_qwen.txt

# Instrucciones finales
cat >> prompt_qwen.txt << 'INSTRUCCIONES'

## PREGUNTAS ESPECÍFICAS

### 1. EXTRACCIÓN DE ORGANIZADOR
- ¿Cómo puedo modificar `extraer_organizador_email()` para que extraiga el **organizador real** (no el nombre del evento)?
- ¿Qué patrones de texto debería buscar para identificar organizadores (ej: "organizado por", "by", "presenta")?
- ¿Cómo puedo usar el contexto del evento (nombre, lugar, descripción) para inferir el organizador?

### 2. EXTRACCIÓN DE EMAIL
- ¿Cómo puedo extraer emails de contacto de manera más efectiva?
- ¿Qué patrones de regex debería usar además del email estándar?
- ¿Cómo puedo buscar emails en la descripción generada o en el texto del evento?

### 3. MEJORA DEL FILTRO DE EVENTOS REALES
- ¿Qué criterios debería usar para filtrar eventos reales sin ser demasiado restrictivo?
- ¿Cómo puedo hacer que los eventos de Songkick pasen el filtro (tienen nombre y fecha)?
- ¿Cómo manejar eventos con "fecha no disponible" pero con nombre válido?

### 4. MEJORA DEL ENRIQUECIMIENTO
- ¿Cómo puedo hacer que el enriquecimiento sea más rápido (ya tiene semáforo de 5)?
- ¿Cómo puedo paralelizar la extracción de organizador y email con la generación de descripción?

## FORMATO DE RESPUESTA
Genera un informe estructurado con:
### ANÁLISIS DEL PROBLEMA
### CÓDIGO CORREGIDO (para `enriquecer_eventos.py` y `main_v5.py`)
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
echo "   - contexto.txt            : Contexto del enriquecimiento"
echo "   - prompt_qwen.txt         : Prompt enviado a Qwen"
echo "   - respuesta_qwen.txt      : Respuesta completa de Qwen"
echo ""
echo "👉 Para ver la respuesta completa:"
echo "   cat $(pwd)/respuesta_qwen.txt"
echo ""
echo "🔚 FIN DE LA CONSULTA"
cd ..
