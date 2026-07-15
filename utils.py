import ollama
import json
import re
import config

def consultar_ollama(prompt, tarea="general"):
    """
    tarea: 'general' -> usa dharmadhatu-fast
           'coder'   -> usa qwen2.5-coder:7b
    """
    if tarea == "coder":
        model = config.OLLAMA_MODEL_CODER
    else:
        model = config.OLLAMA_MODEL_GENERAL
    
    try:
        response = ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}]
        )
        contenido = response['message']['content']
        
        json_match = re.search(r'\{.*\}', contenido, re.DOTALL)
        if json_match:
            return json_match.group(0)
        return contenido
            
    except Exception as e:
        print(f"❌ Error con {model}: {e}")
        try:
            response = ollama.chat(
                model=config.OLLAMA_MODEL_GENERAL,
                messages=[{"role": "user", "content": prompt}]
            )
            contenido = response['message']['content']
            json_match = re.search(r'\{.*\}', contenido, re.DOTALL)
            if json_match:
                return json_match.group(0)
            return contenido
        except Exception as e2:
            print(f"❌ Error en fallback: {e2}")
            return "{}"
