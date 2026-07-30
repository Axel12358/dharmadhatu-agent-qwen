import json

# Cargar clasificación de Goabase
try:
    with open('clasificacion_goabase.json', 'r') as f:
        goabase = json.load(f)
except FileNotFoundError:
    print("❌ No se encontró clasificacion_goabase.json. Ejecuta primero el scraper.")
    exit(1)

# Cargar configuración actual
try:
    with open('config_temp.json', 'r') as f:
        config = json.load(f)
except FileNotFoundError:
    config = {}

# Fusionar: mantener lo que ya existe y añadir lo nuevo
config['subgeneros'] = list(set(config.get('subgeneros', []) + goabase.get('subgeneros', [])))
config['tipos_evento'] = list(set(config.get('tipos_evento', []) + goabase.get('tipos_evento', [])))
config['priorizar_paises'] = list(set(config.get('priorizar_paises', []) + goabase.get('paises', [])))
config['ciudades'] = list(set(config.get('ciudades', []) + goabase.get('locaciones', [])))

# Guardar configuración fusionada
with open('config_temp.json', 'w') as f:
    json.dump(config, f, indent=2)

print("✅ Configuración fusionada y guardada en 'config_temp.json'")
print(f"📊 Subgéneros: {len(config['subgeneros'])}")
print(f"📊 Tipos de evento: {len(config['tipos_evento'])}")
print(f"📊 Países: {len(config['priorizar_paises'])}")
print(f"📊 Ciudades: {len(config['ciudades'])}")
