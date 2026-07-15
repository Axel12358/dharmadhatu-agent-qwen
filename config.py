# ============================================================
# CONFIGURACIÓN DEL BOT
# ============================================================

# Directorio de datos
DATA_DIR = "data"

# ============================================================
# PALABRAS CLAVE PARA BÚSQUEDAS
# ============================================================

# Palabras clave de estilos de psytrance
KEYWORDS_TU_ESTILO = [
    "darkpsy", "forest", "forest psy", "ritual", 
    "twilight", "hitech", "zenonesque", "suomi", "night psy"
]

# Palabras clave generales de psytrance
KEYWORDS_PSY = [
    "psytrance", "goa", "psychedelic", "trance", 
    "rave", "open air", "festival", "party", "gathering"
]

# ============================================================
# PAÍSES Y FECHAS
# ============================================================

PAISES_EUROPA = [
    "Germany", "France", "Spain", "Italy", "Portugal", 
    "Netherlands", "Belgium", "Switzerland", "Austria", 
    "Hungary", "Poland", "Czech Republic", "Greece"
]

YEAR_START = 2024
YEAR_END = 2026

# ============================================================
# OLLAMA
# ============================================================

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "dharmadhatu-fast"  # Modelo principal

# ============================================================
# MODELOS ESPECIALIZADOS (AGREGADOS)
# ============================================================

OLLAMA_MODEL_GENERAL = "dharmadhatu-fast"   # Para tareas rápidas
OLLAMA_MODEL_CODER = "qwen2.5-coder:7b"     # Para extracción y razonamiento

# ============================================================
# HEADERS PARA SCRAPING
# ============================================================

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# ============================================================
# PARÁMETROS DEL BOT
# ============================================================

MAX_EVENTOS = 150
BUSQUEDAS_FACEBOOK = 100
TIMEOUT = 30
EXTRAER_CONTACTOS = True
PRIORIZAR_PAISES = PAISES_EUROPA

# ============================================================
# PARÁMETROS DEL LOOP
# ============================================================

OBJETIVO_EVENTOS = 200
OBJETIVO_ORGANIZADORES = 40
MAX_ITERACIONES = 20

PESO_EVENTOS = 1.0
PESO_ORGANIZADORES = 2.0
PESO_EMAILS = 3.0
PESO_FUENTES = 10.0

TIMEOUT_ENTRE_ITERACIONES = 5

# ============================================================
# DIRECTORIOS
# ============================================================

import os
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_DIR = BASE_DIR
LOGS_DIR = os.path.join(BASE_DIR, 'logs')
