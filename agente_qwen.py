import subprocess
import json
import time
import pandas as pd
import ollama
import re
import sys
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
import shutil
import glob

# ============================================================
# CONFIGURACIÓN OPTIMIZADA
# ============================================================
BOT_DIR = os.path.expanduser("~/dharmadhatu_bot")
MODELO_GENERAL = "dharmadhatu-fast"
MODELO_CODER = "qwen2.5-coder:7b"
MAX_WORKERS = 2  # Reducido a 2 para menos overhead

# Lista reducida de países para probar en paralelo (evita saturar)
PAISES_VARIANTES = [
    ["España", "Portugal"],
    ["Alemania", "Francia"],
    ["Italia", "Holanda"]
]

sys.path.insert(0, BOT_DIR)

try:
    from utils import consultar_ollama
except:
    def consultar_ollama(prompt, tarea="general"):
        model = MODELO_CODER if tarea == "coder" else MODELO_GENERAL
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
            print(f"❌ Error: {e}")
            return "{}"

try:
    from scrapers.enriquecer_eventos import enriquecer_eventos_con_qwen
    ENRIQUECIMIENTO_DISPONIBLE = True
except ImportError:
    ENRIQUECIMIENTO_DISPONIBLE = False
    print("⚠️ scrapers.enriquecer_eventos no encontrado.")

# ============================================================
# FUNCIÓN PARA EJECUTAR EL BOT CON UNA CONFIGURACIÓN DADA
# ============================================================
def ejecutar_bot_worker(config, worker_id):
    """Ejecuta main_v5.py con una configuración específica y devuelve el CSV generado."""
    temp_dir = os.path.join(BOT_DIR, f"temp_worker_{worker_id}")
    os.makedirs(temp_dir, exist_ok=True)

    config_path = os.path.join(temp_dir, 'config_temp.json')
    with open(config_path, 'w') as f:
        json.dump(config, f)

    try:
        result = subprocess.run(
            ['python3', 'main_v5.py'],
            cwd=BOT_DIR,
            capture_output=True,
            text=True,
            timeout=300
        )
        csv_files = glob.glob(os.path.join(BOT_DIR, 'data', 'events_consolidated_v5.csv'))
        if csv_files:
            dest_csv = os.path.join(temp_dir, f'events_worker_{worker_id}.csv')
            shutil.copy(csv_files[0], dest_csv)
            return dest_csv, result.returncode == 0
        else:
            return None, False
    except Exception as e:
        print(f"❌ Worker {worker_id} falló: {e}")
        return None, False

# ============================================================
# CLASE AGENTE QWEN (CON PARALELISMO OPTIMIZADO)
# ============================================================
class AgenteQwen:
    def __init__(self):
        self.historial = []
        self.mejor_score = 0
        self.mejor_config = None
        self.bot_dir = BOT_DIR

    def analizar_resultados(self, csv_path=None):
        try:
            if csv_path is None:
                csv_path = os.path.join(self.bot_dir, 'data', 'events_consolidated_v5.csv')
            df = pd.read_csv(csv_path)
            eventos = int(len(df))
            fuentes = int(df['fuente'].nunique()) if 'fuente' in df.columns else 0
            organizadores = int(df['organizador'].notna().sum()) if 'organizador' in df.columns else 0
            emails = int(df['email'].notna().sum()) if 'email' in df.columns else 0
            return {
                'eventos': eventos,
                'fuentes': fuentes,
                'organizadores': organizadores,
                'emails': emails,
                'score': eventos + (organizadores * 2) + (emails * 3) + (fuentes * 10)
            }
        except Exception as e:
            print(f"❌ Error analizando: {e}")
            return None

    def consolidar_csvs(self, csv_list):
        """Consolida múltiples CSVs en uno solo."""
        if not csv_list:
            return None
        dfs = []
        for csv_path in csv_list:
            try:
                df = pd.read_csv(csv_path)
                dfs.append(df)
            except:
                continue
        if not dfs:
            return None
        df_consolidado = pd.concat(dfs, ignore_index=True)
        df_consolidado = df_consolidado.drop_duplicates(subset=['nombre', 'fecha', 'lugar'], keep='first')
        output_path = os.path.join(self.bot_dir, 'data', 'events_consolidated_v5.csv')
        df_consolidado.to_csv(output_path, index=False)
        return output_path

    def loop(self, max_iteraciones=10):  # Reducido a 10 iteraciones
        print("="*60)
        print("🌀 AGENTE QWEN OPTIMIZADO (PARALELO + BUSQUEDAS AJUSTADAS)")
        print("="*60)
        print(f"📌 BOT DIR: {self.bot_dir}")
        print(f"📌 MODELO CODER: {MODELO_CODER}")
        print(f"📌 MODELO GENERAL: {MODELO_GENERAL}")
        print(f"📌 ENRIQUECIMIENTO: {'✅' if ENRIQUECIMIENTO_DISPONIBLE else '❌'}")
        print(f"📌 MAX WORKERS: {MAX_WORKERS}")
        print("="*60)

        config_actual = {
            "max_eventos": 120,          # Ajustado
            "busquedas_facebook": 100,   # Ajustado para evitar bloqueos
            "timeout": 30,
            "priorizar_paises": [],
            "extraer_contactos": True,
            "agregar_fuentes": [],
            "profundidad_busqueda": 5,
            "buscar_en_grupos": True
        }

        for i in range(max_iteraciones):
            print(f"\n📌 ITERACIÓN {i+1}/{max_iteraciones}")
            print("-"*40)

            # ---- Preparar configuraciones para workers ----
            configs = []
            for idx, paises in enumerate(PAISES_VARIANTES[:MAX_WORKERS]):
                config_variante = config_actual.copy()
                config_variante["priorizar_paises"] = paises
                configs.append((config_variante, idx))

            print(f"🚀 Lanzando {len(configs)} workers en paralelo...")
            csvs_generados = []
            with ProcessPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = {executor.submit(ejecutar_bot_worker, config, idx): idx for config, idx in configs}
                for future in as_completed(futures):
                    idx = futures[future]
                    try:
                        csv_path, exito = future.result()
                        if exito and csv_path:
                            csvs_generados.append(csv_path)
                            print(f"✅ Worker {idx} completado. CSV: {csv_path}")
                        else:
                            print(f"⚠️ Worker {idx} falló")
                    except Exception as e:
                        print(f"❌ Worker {idx} error: {e}")

            if not csvs_generados:
                print("❌ Ningún worker generó CSV. Usando configuración por defecto.")
                # Ejecutar el bot una vez secuencial
                try:
                    with open(os.path.join(self.bot_dir, 'config_temp.json'), 'w') as f:
                        json.dump(config_actual, f)
                    result = subprocess.run(
                        ['python3', 'main_v5.py'],
                        cwd=self.bot_dir,
                        capture_output=True,
                        text=True,
                        timeout=300
                    )
                    if result.returncode != 0:
                        print("❌ Error ejecutando bot secuencial")
                        break
                    csv_path = os.path.join(self.bot_dir, 'data', 'events_consolidated_v5.csv')
                    if not os.path.exists(csv_path):
                        break
                    metricas = self.analizar_resultados(csv_path)
                except Exception as e:
                    print(f"❌ Error en ejecución secuencial: {e}")
                    break
            else:
                # Consolidar CSVs
                csv_consolidado = self.consolidar_csvs(csvs_generados)
                if csv_consolidado is None:
                    print("❌ No se pudo consolidar CSVs")
                    break
                metricas = self.analizar_resultados(csv_consolidado)

            if not metricas:
                print("❌ No se pudo analizar el CSV")
                break

            print(f"\n📊 RESULTADOS CONSOLIDADOS:")
            print(f" Eventos: {metricas['eventos']}")
            print(f" Organizadores: {metricas['organizadores']}")
            print(f" Emails: {metricas['emails']}")
            print(f" Fuentes: {metricas['fuentes']}")
            print(f" Score: {metricas['score']}")

            # ---- Enriquecimiento en paralelo (opcional) ----
            if metricas['organizadores'] < 40 and metricas['eventos'] > 50 and ENRIQUECIMIENTO_DISPONIBLE:
                print("\n🔍 Pocos organizadores. Lanzando subagente de enriquecimiento...")
                csv_path = os.path.join(self.bot_dir, 'data', 'events_consolidated_v5.csv')
                try:
                    df = pd.read_csv(csv_path)
                    eventos = df.to_dict('records')
                    eventos_enriquecidos = enriquecer_eventos_con_qwen(eventos)
                    df_nuevo = pd.DataFrame(eventos_enriquecidos)
                    df_nuevo.to_csv(csv_path, index=False)
                    print("✅ Enriquecimiento completado")
                    metricas = self.analizar_resultados(csv_path)
                    print(f"📊 RESULTADOS DESPUÉS DE ENRIQUECER:")
                    print(f" Eventos: {metricas['eventos']}")
                    print(f" Organizadores: {metricas['organizadores']}")
                    print(f" Emails: {metricas['emails']}")
                    print(f" Score: {metricas['score']}")
                except Exception as e:
                    print(f"⚠️ Error en enriquecimiento: {e}")

            # ---- Guardar historial ----
            self.historial.append({
                'iteracion': i+1,
                'config': config_actual.copy(),
                'metricas': metricas
            })

            # ---- Actualizar mejor score ----
            if metricas['score'] > self.mejor_score:
                self.mejor_score = metricas['score']
                self.mejor_config = config_actual.copy()
                print(f"⭐ NUEVO MEJOR SCORE: {self.mejor_score}")

            # ---- Verificar objetivo ----
            if metricas['eventos'] >= 250 and metricas['organizadores'] >= 60:
                print(f"\n🎉 ¡OBJETIVO ALCANZADO!")
                break

            # ---- Qwen propone mejora ----
            print("\n🤖 Qwen analizando...")
            mejora = self.generar_mejora(metricas)
            if mejora:
                config_actual = self.aplicar_mejora(mejora, config_actual)
            else:
                print("⚠️ Qwen no propuso mejora, aumentando búsquedas por defecto")
                config_actual["busquedas_facebook"] = min(config_actual.get("busquedas_facebook", 100) + 30, 200)

            # ---- Guardar configuración ----
            with open(os.path.join(self.bot_dir, 'config_temp.json'), 'w') as f:
                json.dump(config_actual, f)

            print(f"\n⏳ Esperando 5s...")
            time.sleep(5)

        # ---- Resultado final ----
        print("\n" + "="*60)
        print("🏆 LOOP FINALIZADO")
        print("="*60)
        print(f"Mejor score: {self.mejor_score}")
        print(f"Mejor config: {self.mejor_config}")
        with open('historial_qwen.json', 'w') as f:
            json.dump(self.historial, f, indent=2, default=str)
        print("📁 Historial guardado en historial_qwen.json")
        return self.mejor_config

    def generar_mejora(self, metricas):
        prompt = f"""
Eres un agente experto en scraping y optimización de bots.

Estos son los resultados ACTUALES del bot:
- Eventos totales: {metricas['eventos']}
- Fuentes activas: {metricas['fuentes']}
- Organizadores extraídos: {metricas['organizadores']}
- Emails extraídos: {metricas['emails']}
- Score: {metricas['score']}

Analiza estos datos y propón UNA mejora CONCRETA.
Tu objetivo es SUBIR el score.
Si ves que hay pocos organizadores (<40) pero muchos eventos (>50), sugiere enriquecer eventos.
Si ves que hay pocas fuentes (<5), sugiere agregar más fuentes.
Si ves que el score es bajo, sugiere aumentar max_eventos o busquedas_facebook.
Responde SOLO en formato JSON:
{{
  "accion": "ENRIQUECER_EVENTOS | AUMENTAR_BUSQUEDAS | AUMENTAR_MAX_EVENTOS | AUMENTAR_TIMEOUT | AGREGAR_PAISES",
  "parametro": "nombre_del_parametro",
  "valor": "nuevo_valor",
  "razon": "explicación basada en los datos"
}}
"""
        try:
            respuesta = consultar_ollama(prompt, tarea="coder")
            json_match = re.search(r'\{.*\}', respuesta, re.DOTALL)
            if json_match:
                return json.loads(json_match.group(0))
            return json.loads(respuesta)
        except Exception as e:
            print(f"❌ Qwen falló: {e}")
            return {
                "accion": "ENRIQUECER_EVENTOS" if metricas['organizadores'] < 40 and metricas['eventos'] > 50 else "AUMENTAR_BUSQUEDAS",
                "parametro": "organizadores" if metricas['organizadores'] < 40 else "busquedas_facebook",
                "valor": "true" if metricas['organizadores'] < 40 else "120",
                "razon": "Basado en análisis de datos"
            }

    def aplicar_mejora(self, mejora, config_actual):
        if not mejora:
            print("⚠️ No hay mejora, aumentando búsquedas por defecto")
            config_actual["busquedas_facebook"] = min(config_actual.get("busquedas_facebook", 100) + 30, 200)
            return config_actual

        accion = mejora.get('accion', '')
        parametro = mejora.get('parametro', '')
        valor = mejora.get('valor', '')
        razon = mejora.get('razon', 'Sin razón')
        print(f"\n🤖 Qwen propone: {razon}")
        print(f" Acción: {accion} | {parametro} -> {valor}")

        if accion == "ENRIQUECER_EVENTOS" and ENRIQUECIMIENTO_DISPONIBLE:
            print("🔍 Enriqueciendo eventos con Qwen...")
            try:
                csv_path = os.path.join(self.bot_dir, 'data', 'events_consolidated_v5.csv')
                df = pd.read_csv(csv_path)
                eventos = df.to_dict('records')
                eventos_enriquecidos = enriquecer_eventos_con_qwen(eventos)
                df_nuevo = pd.DataFrame(eventos_enriquecidos)
                df_nuevo.to_csv(csv_path, index=False)
                print("✅ Eventos enriquecidos guardados")
            except Exception as e:
                print(f"⚠️ Error en enriquecimiento: {e}")
            return config_actual

        if accion == "AUMENTAR_BUSQUEDAS" and parametro == "busquedas_facebook":
            try:
                config_actual["busquedas_facebook"] = int(valor)
                print(f"✅ busquedas_facebook: {config_actual['busquedas_facebook']}")
            except:
                config_actual["busquedas_facebook"] = min(config_actual.get("busquedas_facebook", 100) + 30, 200)

        elif accion == "AUMENTAR_MAX_EVENTOS" and parametro == "max_eventos":
            try:
                config_actual["max_eventos"] = int(valor)
                print(f"✅ max_eventos: {config_actual['max_eventos']}")
            except:
                config_actual["max_eventos"] = min(config_actual.get("max_eventos", 120) + 30, 200)

        elif accion == "AUMENTAR_TIMEOUT" and parametro == "timeout":
            try:
                config_actual["timeout"] = int(valor)
                print(f"✅ timeout: {config_actual['timeout']}")
            except:
                config_actual["timeout"] = min(config_actual.get("timeout", 30) + 10, 90)

        elif accion == "AGREGAR_PAISES":
            if isinstance(valor, list):
                for pais in valor:
                    if pais not in config_actual.get("priorizar_paises", []):
                        if "priorizar_paises" not in config_actual:
                            config_actual["priorizar_paises"] = []
                        config_actual["priorizar_paises"].append(pais)
                print(f"✅ Países agregados: {config_actual['priorizar_paises']}")
            else:
                config_actual["priorizar_paises"] = ["España", "Portugal", "Alemania", "Francia", "Italia", "Holanda", "Reino Unido"]
                print(f"✅ Países por defecto: {config_actual['priorizar_paises']}")

        else:
            print(f"⚠️ Acción desconocida: {accion}")
            config_actual["busquedas_facebook"] = min(config_actual.get("busquedas_facebook", 100) + 30, 200)

        return config_actual

if __name__ == "__main__":
    agente = AgenteQwen()
    mejor_config = agente.loop(max_iteraciones=10)  # 10 iteraciones para prueba rápida
