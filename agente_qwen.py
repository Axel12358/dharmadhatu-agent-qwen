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

# Agregar el directorio del bot al path para importar utils
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

class AgenteQwen:
    def __init__(self):
        self.historial = []
        self.mejor_score = 0
        self.mejor_config = None
        self.bot_dir = BOT_DIR
    
    def analizar_resultados(self):
        try:
            csv_path = os.path.join(self.bot_dir, 'data/events_consolidated_v5.csv')
            df = pd.read_csv(csv_path)
            eventos = len(df)
            fuentes = df['fuente'].nunique() if 'fuente' in df.columns else 0
            organizadores = df['organizador'].notna().sum() if 'organizador' in df.columns else 0
            emails = df['email'].notna().sum() if 'email' in df.columns else 0
            
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
        prompt = f"""
        Eres un agente experto en scraping de Facebook y optimización de bots.
        
        Estos son los resultados actuales del bot:
        - Eventos encontrados: {metricas['eventos']}
        - Fuentes activas: {metricas['fuentes']}
        - Organizadores extraídos: {metricas['organizadores']}
        - Emails extraídos: {metricas['emails']}
        - Score actual: {metricas['score']}
        
        Objetivo: llegar a 200 eventos y 40 organizadores.
        
        Responde SOLO en formato JSON con:
        {{
            "accion": "AUMENTAR_BUSQUEDAS | ACTIVAR_CONTACTOS | AGREGAR_PAISES | CAMBIAR_QUERIES",
            "parametro": "nombre_del_parametro",
            "valor": "nuevo_valor",
            "razon": "explicación breve"
        }}
        """
        
        try:
            respuesta = consultar_ollama(prompt, tarea="coder")
            return json.loads(respuesta)
        except Exception as e:
            print(f"❌ Qwen falló: {e}")
            return None
    
    def aplicar_mejora(self, mejora, config_actual):
        if not mejora:
            return config_actual
        
        accion = mejora.get('accion', '')
        parametro = mejora.get('parametro', '')
        valor = mejora.get('valor', '')
        
        print(f"\n🤖 Qwen propone: {mejora.get('razon', 'Sin razón')}")
        print(f"   Acción: {accion}")
        print(f"   Parametro: {parametro} -> {valor}")
        
        if accion == "AUMENTAR_BUSQUEDAS" and parametro == "busquedas_facebook":
            config_actual["busquedas_facebook"] = int(valor) if str(valor).isdigit() else 100
            print(f"✅ Actualizado busquedas_facebook a {config_actual['busquedas_facebook']}")
        
        elif accion == "ACTIVAR_CONTACTOS":
            config_actual["extraer_contactos"] = True
            print("✅ Activada extracción de contactos")
        
        elif accion == "AGREGAR_PAISES":
            if isinstance(valor, list):
                for pais in valor:
                    if pais not in config_actual.get("priorizar_paises", []):
                        if "priorizar_paises" not in config_actual:
                            config_actual["priorizar_paises"] = []
                        config_actual["priorizar_paises"].append(pais)
                print(f"✅ Países agregados: {config_actual['priorizar_paises']}")
            else:
                config_actual["priorizar_paises"] = ["España", "Portugal", "Alemania", "Francia", "Italia", "Holanda", "Bélgica", "Suiza"]
                print("✅ Países agregados (lista por defecto)")
        
        elif accion == "CAMBIAR_QUERIES":
            print(f"🔄 Qwen sugiere cambiar queries: {valor}")
        
        return config_actual
    
    def loop(self, max_iteraciones=10):
        print("="*60)
        print("🌀 AGENTE QWEN - MEJORA DEL BOT")
        print("="*60)
        print(f"📌 BOT DIR: {self.bot_dir}")
        print(f"📌 MODELO CODER: {MODELO_CODER}")
        print(f"📌 MODELO GENERAL: {MODELO_GENERAL}")
        print("="*60)
        
        config_actual = {
            "max_eventos": 100,
            "busquedas_facebook": 50,
            "timeout": 20,
            "priorizar_paises": [],
            "extraer_contactos": False,
            "agregar_fuentes": [],
            "profundidad_busqueda": 3
        }
        
        with open(os.path.join(self.bot_dir, 'config_temp.json'), 'w') as f:
            json.dump(config_actual, f)
        
        for i in range(max_iteraciones):
            print(f"\n📌 ITERACIÓN {i+1}/{max_iteraciones}")
            print("-"*40)
            
            print("🚀 Ejecutando bot...")
            try:
                resultado = subprocess.run(
                    ['python3', 'main_v5.py'],
                    cwd=self.bot_dir,
                    capture_output=True,
                    text=True,
                    timeout=120
                )
                print(f"✅ Bot ejecutado (código: {resultado.returncode})")
            except subprocess.TimeoutExpired:
                print("⚠️ Bot tardó mucho, continuando...")
            except Exception as e:
                print(f"❌ Error ejecutando bot: {e}")
            
            metricas = self.analizar_resultados()
            if not metricas:
                print("❌ No se pudieron analizar los resultados")
                break
            
            print(f"\n📊 RESULTADOS:")
            print(f"   Eventos: {metricas['eventos']}")
            print(f"   Organizadores: {metricas['organizadores']}")
            print(f"   Emails: {metricas['emails']}")
            print(f"   Fuentes: {metricas['fuentes']}")
            print(f"   Score: {metricas['score']}")
            
            self.historial.append({
                'iteracion': i+1,
                'config': config_actual.copy(),
                'metricas': metricas
            })
            
            if metricas['score'] > self.mejor_score:
                self.mejor_score = metricas['score']
                self.mejor_config = config_actual.copy()
                print(f"⭐ NUEVO MEJOR SCORE: {self.mejor_score}")
            
            if metricas['eventos'] >= 200 and metricas['organizadores'] >= 40:
                print(f"\n🎉 ¡OBJETIVO ALCANZADO!")
                break
            
            print("\n🤖 Qwen pensando...")
            mejora = self.generar_mejora(metricas)
            
            if mejora:
                config_actual = self.aplicar_mejora(mejora, config_actual)
            else:
                print("⚠️ Qwen no propuso mejora, aumentando búsquedas por defecto")
                config_actual["busquedas_facebook"] = min(config_actual.get("busquedas_facebook", 50) + 30, 300)
            
            with open(os.path.join(self.bot_dir, 'config_temp.json'), 'w') as f:
                json.dump(config_actual, f)
            
            print(f"\n⏳ Esperando 5s...")
            time.sleep(5)
        
        print("\n" + "="*60)
        print("🏆 LOOP FINALIZADO")
        print("="*60)
        print(f"Mejor score: {self.mejor_score}")
        print(f"Mejor config: {self.mejor_config}")
        
        with open('historial_qwen.json', 'w') as f:
            json.dump(self.historial, f, indent=2)
        print("📁 Historial guardado en historial_qwen.json")
        
        return self.mejor_config

if __name__ == '__main__':
    agente = AgenteQwen()
    mejor_config = agente.loop(max_iteraciones=10)
