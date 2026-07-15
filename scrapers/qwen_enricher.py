"""
Enriquecimiento de eventos con Qwen Coder (vía Ollama)
"""

import json
import re
import time

def enriquecer_eventos(eventos, delay=0.5):
    """Enriquece una lista de eventos con Qwen"""
    
    print(f"🤖 Enriqueciendo {len(eventos)} eventos con Qwen...")
    
    eventos_enriquecidos = []
    for i, evento in enumerate(eventos):
        print(f"   {i+1}/{len(eventos)}: {evento['nombre'][:30]}...")
        evento_enriquecido = enriquecer_evento_qwen(evento)
        eventos_enriquecidos.append(evento_enriquecido)
        time.sleep(delay)
    
    return eventos_enriquecidos

def enriquecer_evento_qwen(evento):
    """Usa Qwen para extraer organizador y email del texto del evento"""
    
    texto = evento.get('raw_text', '') + ' ' + evento.get('nombre', '')
    if len(texto) < 20:
        return evento
    
    try:
        from utils import consultar_ollama
    except ImportError:
        return evento
    
    prompt = f"""
    Analiza este texto de un evento de psytrance:
    
    {texto[:500]}
    
    Extrae:
    1. Organizador (nombre de la persona o colectivo)
    2. Email de contacto (si aparece)
    
    Responde SOLO en formato JSON:
    {{"organizador": "nombre", "email": "correo@ejemplo.com"}}
    Si no encuentras información, usa valores vacíos.
    """
    
    try:
        respuesta = consultar_ollama(prompt, tarea="coder")
        json_match = re.search(r'\{.*\}', respuesta, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group(0))
            evento['organizador'] = data.get('organizador', evento.get('organizador', 'N/A'))
            evento['email'] = data.get('email', evento.get('email', 'N/A'))
    except Exception as e:
        print(f"⚠️ Error en Qwen: {e}")
    
    return evento
