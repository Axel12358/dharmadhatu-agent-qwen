import subprocess
import json
import time
import pandas as pd
import ollama
import re
import sys
import os

# ============================================================
# CONFIGURACIÓN
# ============================================================
BOT_DIR = os.path.expanduser("~/dharmadhatu_bot")
MODELO_GENERAL = "dharmadhatu-fast"
MODELO_CODER = "qwen2.5-coder:7b"

# Agregar el directorio del bot al path para importar utils y enriquecimiento
sys.path.insert(0, BOT_DIR)

try:
    from utils import consultar_ollama
except:
    def consultar_ollama(prompt, tarea="general"):
        """Fallback si no se puede importar utils"""
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

# Importar enriquecimiento de eventos
try:
    from scrapers.enriquecer_eventos import enriquecer_eventos_con_qwen
    ENRIQUECIMIENTO_DISPONIBLE = True
except ImportError:
    ENRIQUECIMIENTO_DISPONIBLE = False
    print("⚠️ scrapers.enriquecer_eventos no encontrado. Crea el archivo.")

class AgenteQwen:
    def __init__(self):
        self.historial = []
        self.mejor_score = 0
        self.mejor_config = None
        self.bot_dir = BOT_DIR
        self.ultimo_csv = None
    
    def analizar_resultados(self):
        """Lee el CSV y devuelve métricas (con conversión a int para JSON)"""
        try:
            csv_path = os.path.join(self.bot_dir, 'data/events_consolidated_v5.csv')
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
    
    def generar_mejora(self, metricas):
        """Usa Qwen para generar una mejora específica basada en datos"""
        
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
        
        Si ves que hay pocos organizadores (<20) pero muchos eventos (>50), sugiere enriquecer eventos.
        Si ves que hay pocas fuentes (<5), sugiere agregar más fuentes.
        Si ves que el score es bajo, sugiere aumentar max_eventos o busquedas_facebook.
        
        Responde SOLO en formato JSON:
        {{
            "accion": "ENRIQUECER_EVENTOS | AUMENTAR_BUSQUEDAS | AUMENTAR_MAX_EVENTOS | AUMENTAR_TIMEOUT | AGREGAR_PAISES",
            "parametro": "nombre_del_parametro",
            "valor": "nuevo_valor",
            "razon": "explicación basada en los datos"
        }}
        
        Ejemplo:
        {{
            "accion": "ENRIQUECER_EVENTOS",
            "parametro": "organizadores",
            "valor": "true",
            "razon": "Hay 80 eventos pero solo 0 organizadores, es necesario enriquecer"
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
            # Fallback: sugerencia por defecto
            return {
                "accion": "ENRIQUECER_EVENTOS" if metricas['organizadores'] < 20 and metricas['eventos'] > 50 else "AUMENTAR_BUSQUEDAS",
                "parametro": "organizadores" if metricas['organizadores'] < 20 else "busquedas_facebook",
                "valor": "true" if metricas['organizadores'] < 20 else "80",
                "razon": "Basado en análisis de datos"
            }
    
    def aplicar_mejora(self, mejora, config_actual):
        """Aplica la mejora generada por Qwen"""
        if not mejora:
            print("⚠️ No hay mejora, aumentando búsquedas por defecto")
            config_actual["busquedas_facebook"] = min(config_actual.get("busquedas_facebook", 50) + 30, 300)
            return config_actual
        
        accion = mejora.get('accion', '')
        parametro = mejora.get('parametro', '')
        valor = mejora.get('valor', '')
        razon = mejora.get('razon', 'Sin razón')
        
        print(f"\n🤖 Qwen propone: {razon}")
        print(f"   Acción: {accion} | {parametro} -> {valor}")
        
        # ============================================================
        # ACCIÓN: ENRIQUECER EVENTOS (NUEVO)
        # ============================================================
        if accion == "ENRIQUECER_EVENTOS" and ENRIQUECIMIENTO_DISPONIBLE:
            print("🔍 Enriqueciendo eventos con Qwen...")
            try:
                csv_path = os.path.join(self.bot_dir, 'data/events_consolidated_v5.csv')
                df = pd.read_csv(csv_path)
                eventos = df.to_dict('records')
                
                eventos_enriquecidos = enriquecer_eventos_con_qwen(eventos)
                
                df_nuevo = pd.DataFrame(eventos_enriquecidos)
                df_nuevo.to_csv(csv_path, index=False)
                print("✅ Eventos enriquecidos guardados")
            except Exception as e:
                print(f"⚠️ Error en enriquecimiento: {e}")
            return config_actual
        
        # ============================================================
        # ACCIONES EXISTENTES
        # ============================================================
        if accion == "AUMENTAR_BUSQUEDAS" and parametro == "busquedas_facebook":
            try:
                config_actual["busquedas_facebook"] = int(valor)
                print(f"✅ busquedas_facebook: {config_actual['busquedas_facebook']}")
            except:
                config_actual["busquedas_facebook"] = min(config_actual.get("busquedas_facebook", 50) + 30, 300)
        
        elif accion == "AUMENTAR_MAX_EVENTOS" and parametro == "max_eventos":
            try:
                config_actual["max_eventos"] = int(valor)
                print(f"✅ max_eventos: {config_actual['max_eventos']}")
            except:
                config_actual["max_eventos"] = min(config_actual.get("max_eventos", 100) + 50, 300)
        
        elif accion == "AUMENTAR_TIMEOUT" and parametro == "timeout":
            try:
                config_actual["timeout"] = int(valor)
                print(f"✅ timeout: {config_actual['timeout']}")
            except:
                config_actual["timeout"] = min(config_actual.get("timeout", 20) + 5, 60)
        
        elif accion == "AGREGAR_PAISES":
            if isinstance(valor, list):
                for pais in valor:
                    if pais not in config_actual.get("priorizar_paises", []):
                        if "priorizar_paises" not in config_actual:
                            config_actual["priorizar_paises"] = []
                        config_actual["priorizar_paises"].append(pais)
                print(f"✅ Países agregados: {config_actual['priorizar_paises']}")
            else:
                config_actual["priorizar_paises"] = ["España", "Portugal", "Alemania", "Francia", "Italia", "Holanda"]
                print(f"✅ Países por defecto: {config_actual['priorizar_paises']}")
        
        else:
            print(f"⚠️ Acción desconocida: {accion}")
            config_actual["busquedas_facebook"] = min(config_actual.get("busquedas_facebook", 50) + 30, 300)
        
        return config_actual
    
    def loop(self, max_iteraciones=15):
        """Loop de mejora con Qwen"""
        
        print("="*60)
        print("🌀 AGENTE QWEN - MEJORA DEL BOT")
        print("="*60)
        print(f"📌 BOT DIR: {self.bot_dir}")
        print(f"📌 MODELO CODER: {MODELO_CODER}")
        print(f"📌 MODELO GENERAL: {MODELO_GENERAL}")
        print(f"📌 ENRIQUECIMIENTO: {'✅' if ENRIQUECIMIENTO_DISPONIBLE else '❌'}")
        print("="*60)
        
        # Configuración inicial
        config_actual = {
            "max_eventos": 100,
            "busquedas_facebook": 50,
            "timeout": 20,
            "priorizar_paises": [],
            "extraer_contactos": False,
            "agregar_fuentes": [],
            "profundidad_busqueda": 3,
            "buscar_en_grupos": False
        }
        
        with open(os.path.join(self.bot_dir, 'config_temp.json'), 'w') as f:
            json.dump(config_actual, f)
        
        for i in range(max_iteraciones):
            print(f"\n📌 ITERACIÓN {i+1}/{max_iteraciones}")
            print("-"*40)
            
            # 1. Ejecutar bot
            print("🚀 Ejecutando bot...")
            try:
                resultado = subprocess.run(
                    ['python3', 'main_v5.py'],
                    cwd=self.bot_dir,
                    capture_output=True,
                    text=True,
                    timeout=180
                )
                if resultado.returncode == 0:
                    print("✅ Bot ejecutado")
                else:
                    print(f"⚠️ Bot terminó con código {resultado.returncode}")
            except subprocess.TimeoutExpired:
                print("⚠️ Bot tardó mucho")
            except Exception as e:
                print(f"❌ Error: {e}")
            
            # 2. Analizar resultados
            metricas = self.analizar_resultados()
            if not metricas:
                print("❌ No se pudo analizar el CSV")
                break
            
            print(f"\n📊 RESULTADOS:")
            print(f"   Eventos: {metricas['eventos']}")
            print(f"   Organizadores: {metricas['organizadores']}")
            print(f"   Emails: {metricas['emails']}")
            print(f"   Fuentes: {metricas['fuentes']}")
            print(f"   Score: {metricas['score']}")
            
            # ============================================================
            # ENRIQUECIMIENTO AUTOMÁTICO (si hay pocos organizadores)
            # ============================================================
            if metricas['organizadores'] < 20 and metricas['eventos'] > 50 and ENRIQUECIMIENTO_DISPONIBLE:
                print("\n🔍 Pocos organizadores detectados. Enriqueciendo eventos con Qwen...")
                try:
                    csv_path = os.path.join(self.bot_dir, 'data/events_consolidated_v5.csv')
                    df = pd.read_csv(csv_path)
                    eventos = df.to_dict('records')
                    
                    eventos_enriquecidos = enriquecer_eventos_con_qwen(eventos)
                    
                    df_nuevo = pd.DataFrame(eventos_enriquecidos)
                    df_nuevo.to_csv(csv_path, index=False)
                    print("✅ Eventos enriquecidos guardados")
                    
                    # Volver a analizar para ver si mejoró
                    metricas = self.analizar_resultados()
                    print(f"\n📊 RESULTADOS DESPUÉS DE ENRIQUECER:")
                    print(f"   Eventos: {metricas['eventos']}")
                    print(f"   Organizadores: {metricas['organizadores']}")
                    print(f"   Emails: {metricas['emails']}")
                    print(f"   Score: {metricas['score']}")
                except Exception as e:
                    print(f"⚠️ Error en enriquecimiento: {e}")
            
            # 3. Guardar historial
            self.historial.append({
                'iteracion': i+1,
                'config': config_actual.copy(),
                'metricas': metricas
            })
            
            # 4. Verificar mejor score
            if metricas['score'] > self.mejor_score:
                self.mejor_score = metricas['score']
                self.mejor_config = config_actual.copy()
                print(f"⭐ NUEVO MEJOR SCORE: {self.mejor_score}")
            
            # 5. Verificar objetivo
            if metricas['eventos'] >= 200 and metricas['organizadores'] >= 40:
                print(f"\n🎉 ¡OBJETIVO ALCANZADO!")
                break
            
            # 6. Qwen genera mejora
            print("\n🤖 Qwen analizando...")
            mejora = self.generar_mejora(metricas)
            
            if mejora:
                config_actual = self.aplicar_mejora(mejora, config_actual)
            else:
                print("⚠️ Qwen no propuso mejora, aumentando búsquedas por defecto")
                config_actual["busquedas_facebook"] = min(config_actual.get("busquedas_facebook", 50) + 30, 300)
            
            # 7. Guardar configuración
            with open(os.path.join(self.bot_dir, 'config_temp.json'), 'w') as f:
                json.dump(config_actual, f)
            
            print(f"\n⏳ Esperando 5s...")
            time.sleep(5)
        
        # Resultado final
        print("\n" + "="*60)
        print("🏆 LOOP FINALIZADO")
        print("="*60)
        print(f"Mejor score: {self.mejor_score}")
        print(f"Mejor config: {self.mejor_config}")
        
        with open('historial_qwen.json', 'w') as f:
            json.dump(self.historial, f, indent=2, default=str)
        print("📁 Historial guardado en historial_qwen.json")
        
        return self.mejor_config

if __name__ == '__main__':
    agente = AgenteQwen()
    mejor_config = agente.loop(max_iteraciones=15)
