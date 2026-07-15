import pandas as pd
from datetime import datetime
import config

class Evaluator:
    def __init__(self):
        self.historial = []
    
    def analizar(self, csv_path):
        """Analiza el CSV y devuelve métricas en tipos nativos de Python"""
        try:
            df = pd.read_csv(csv_path)
            
            # Convertir todo a tipos nativos de Python (int, float, str)
            metricas = {
                'total_eventos': int(len(df)),
                'fuentes': int(df['fuente'].nunique()) if 'fuente' in df.columns else 0,
                'organizadores': int(df['organizador'].notna().sum()) if 'organizador' in df.columns else 0,
                'emails': int(df['email'].notna().sum()) if 'email' in df.columns else 0,
                'eventos_con_org': int(df[df['organizador'].notna()].shape[0]) if 'organizador' in df.columns else 0,
                'eventos_con_email': int(df[df['email'].notna()].shape[0]) if 'email' in df.columns else 0,
                'eventos_por_pais': int(len(df['pais'].unique())) if 'pais' in df.columns else 0,
                'timestamp': datetime.now().isoformat()
            }
            
            # Países (convertir valores a int)
            paises = {}
            if 'pais' in df.columns:
                for k, v in df['pais'].value_counts().to_dict().items():
                    paises[str(k)] = int(v)
            metricas['paises'] = paises
            
            # Fuentes detalle
            fuentes_detalle = {}
            if 'fuente' in df.columns:
                for k, v in df['fuente'].value_counts().to_dict().items():
                    fuentes_detalle[str(k)] = int(v)
            metricas['fuentes_detalle'] = fuentes_detalle
            
            self.historial.append(metricas)
            return metricas
            
        except FileNotFoundError:
            print(f"❌ No se encontró el archivo: {csv_path}")
            return None
        except Exception as e:
            print(f"❌ Error analizando CSV: {e}")
            return None
    
    def calcular_score(self, metricas):
        """Calcula el score ponderado"""
        if not metricas:
            return 0
        
        score = 0
        score += metricas['total_eventos'] * config.PESO_EVENTOS
        score += metricas['organizadores'] * config.PESO_ORGANIZADORES
        score += metricas['emails'] * config.PESO_EMAILS
        score += metricas['fuentes'] * config.PESO_FUENTES
        score += metricas['eventos_con_org'] * 1.5
        score += metricas['eventos_con_email'] * 2.5
        score += metricas['eventos_por_pais'] * 5.0
        
        return round(score, 2)
    
    def evaluar_objetivo(self, metricas):
        """Verifica si se alcanzó el objetivo"""
        if not metricas:
            return False
        return (metricas['total_eventos'] >= config.OBJETIVO_EVENTOS and 
                metricas['organizadores'] >= config.OBJETIVO_ORGANIZADORES)
    
    def generar_reporte(self, metricas, iteracion):
        """Genera un reporte en texto"""
        reporte = f"""
📊 REPORTE ITERACIÓN {iteracion}
{'='*50}
📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

🎯 OBJETIVOS:
• Eventos: {metricas['total_eventos']}/{config.OBJETIVO_EVENTOS}
• Organizadores: {metricas['organizadores']}/{config.OBJETIVO_ORGANIZADORES}

📈 MÉTRICAS:
• Emails encontrados: {metricas['emails']}
• Fuentes activas: {metricas['fuentes']}
• Países cubiertos: {metricas['eventos_por_pais']}

📊 DISTRIBUCIÓN POR PAÍS:
"""
        for pais, cantidad in list(metricas.get('paises', {}).items())[:5]:
            reporte += f"    • {pais}: {cantidad} eventos\n"
        
        if len(metricas.get('paises', {})) > 5:
            reporte += f"    • ... y {len(metricas['paises']) - 5} países más\n"
        
        return reporte
