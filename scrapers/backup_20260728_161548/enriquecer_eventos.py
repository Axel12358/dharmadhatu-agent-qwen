import asyncio
import re
import logging
import ollama

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def generar_descripcion_evento(nombre_evento):
    prompt = f"Genera una descripción atractiva para el evento '{nombre_evento}' de música psytrance. Incluye estilo musical, ambiente y recomendaciones. Máximo 150 palabras. En español."
    try:
        response = ollama.chat(model='qwen2.5-coder:7b', messages=[{'role': 'user', 'content': prompt}])
        return response['message']['content'].strip()
    except Exception as e:
        logger.error(f"Error en descripción: {e}")
        return "Evento de música psytrance"

def extraer_organizador_email(texto):
    organizador = "No disponible"
    email = "No disponible"

    # Buscar organizador con patrones
    patterns = [
        r'\bHosted by\s*([^\n,]+)',
        r'\bOrganizado por\s*([^\n,]+)',
        r'\bby\s*([^\n,]+)',
        r'\bpresenta\s*([^\n,]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, texto, re.IGNORECASE)
        if match:
            organizador = match.group(1).strip()
            break

    # Buscar email
    email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    emails = re.findall(email_pattern, texto)
    if emails:
        email = emails[0]

    return organizador, email

async def enriquecer_un_evento(evento):
    nombre = evento.get('nombre', 'Sin nombre')
    lugar = evento.get('lugar', '')
    loop = asyncio.get_running_loop()

    # Si ya tiene organizador y email, no sobrescribir (priorizar los extraídos de Facebook)
    if evento.get('organizador') and evento['organizador'] != 'No disponible':
        organizador = evento['organizador']
        email = evento.get('email', 'No disponible')
    else:
        # Generar descripción
        descripcion = await loop.run_in_executor(None, generar_descripcion_evento, nombre)
        # Combinar texto para buscar organizador/email
        texto_completo = f"{nombre} {lugar} {descripcion}"
        organizador, email = await loop.run_in_executor(None, extraer_organizador_email, texto_completo)
        evento['descripcion'] = descripcion

    evento['organizador'] = organizador
    evento['email'] = email

    return evento

async def enriquecer_eventos_con_qwen(eventos):
    if not eventos:
        return eventos

    semaphore = asyncio.Semaphore(5)
    logger.info(f"🤖 Enriqueciendo {len(eventos)} eventos con Qwen...")

    async def trabajar_con_sem(evento, idx):
        async with semaphore:
            try:
                logger.info(f"   {idx}/{len(eventos)}: {evento.get('nombre', 'Sin nombre')[:30]}...")
                return await enriquecer_un_evento(evento)
            except Exception as e:
                logger.error(f"❌ Error en evento {idx}: {e}")
                evento['descripcion'] = "Descripción no disponible"
                if not evento.get('organizador'):
                    evento['organizador'] = "No disponible"
                if not evento.get('email'):
                    evento['email'] = "No disponible"
                return evento

    tareas = [trabajar_con_sem(evento, i+1) for i, evento in enumerate(eventos)]
    resultados = await asyncio.gather(*tareas)
    logger.info(f"✅ {len(resultados)} eventos enriquecidos")
    return resultados
