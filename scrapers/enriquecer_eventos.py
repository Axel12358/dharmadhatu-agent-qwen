import json
import re
import sys
import os
import time
import math

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import consultar_ollama

def es_valido(valor):
    """Verifica si un valor no es NaN, None o string vacío"""
    if valor is None:
        return False
    if isinstance(valor, float) and math.isnan(valor):
        return False
    if isinstance(valor, str) and valor.strip() == '':
        return False
    return True

def generar_descripcion_evento(nombre_evento):
    """Genera una descripción realista para un evento usando Qwen"""
    if not es_valido(nombre_evento):
        return ""
    prompt = f"""
    Genera una descripción corta (2-3 líneas) para este evento de psytrance:
    "{nombre_evento}"
    Incluye el tipo de música, ambiente y un posible organizador.
    """
    try:
        respuesta = consultar_ollama(prompt, tarea="coder")
        desc = respuesta.strip()
        if len(desc) > 500:
            desc = desc[:500]
        return desc
    except Exception as e:
        print(f"⚠️ Error generando descripción: {e}")
        return f"Evento de psytrance: {nombre_evento}"

def extraer_organizador_email(texto):
    """Extrae organizador y email de un texto usando Qwen con énfasis en emails"""
    if not es_valido(texto) or len(str(texto)) < 10:
        return '', ''
    
    prompt = f"""
    Del siguiente texto, extrae el ORGANIZADOR y el EMAIL de contacto.
    
    IMPORTANTE: Busca explícitamente correos electrónicos con formato nombre@dominio.com.
    Si encuentras un email, inclúyelo aunque no tengas organizador.
    
    Texto: {str(texto)[:500]}
    
    Responde SOLO en JSON: {{"organizador": "nombre", "email": "correo@ejemplo.com"}}
    Si no hay información, usa valores vacíos.
    """
    try:
        respuesta = consultar_ollama(prompt, tarea="coder")
        json_match = re.search(r'\{.*\}', respuesta, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group(0))
            return data.get('organizador', ''), data.get('email', '')
        return '', ''
    except Exception as e:
        print(f"⚠️ Error extrayendo: {e}")
        return '', ''

def enriquecer_evento_con_qwen(evento):
    """Enriquece un evento individual con descripción, organizador y email"""
    
    if not isinstance(evento, dict):
        return evento
    
    nombre = evento.get('nombre', '')
    if not es_valido(nombre):
        return evento
    
    # Si ya tiene organizador y email, no tocar
    if es_valido(evento.get('organizador')) and es_valido(evento.get('email')):
        return evento
    
    # 1. Generar descripción si no existe
    raw_text = evento.get('raw_text', '')
    if not es_valido(raw_text) or len(str(raw_text)) < 20:
        print(f"   📝 Generando descripción para: {nombre[:30]}...")
        desc = generar_descripcion_evento(nombre)
        evento['raw_text'] = desc
        time.sleep(0.3)
    
    # 2. Extraer organizador y email del texto
    texto_completo = str(evento.get('raw_text', '')) + ' ' + str(nombre)
    organizador, email = extraer_organizador_email(texto_completo)
    
    if es_valido(organizador):
        evento['organizador'] = str(organizador)
    if es_valido(email):
        evento['email'] = str(email)
    
    return evento

def enriquecer_eventos_con_qwen(eventos):
    """Enriquece una lista de eventos con descripción, organizador y email usando Qwen"""
    print(f"🤖 Enriqueciendo {len(eventos)} eventos con Qwen...")
    
    eventos_enriquecidos = []
    contador = 0
    for i, evento in enumerate(eventos):
        if not isinstance(evento, dict):
            continue
        nombre = evento.get('nombre', '')
        if not es_valido(nombre):
            continue
        
        contador += 1
        print(f"   {contador}/{len(eventos)}: {str(nombre)[:30]}...")
        evento_enriquecido = enriquecer_evento_con_qwen(evento)
        eventos_enriquecidos.append(evento_enriquecido)
        if contador % 5 == 0:
            time.sleep(0.5)
    
    print(f"✅ {len(eventos_enriquecidos)} eventos enriquecidos")
    return eventos_enriquecidos
