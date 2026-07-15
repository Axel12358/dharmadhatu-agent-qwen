import json
import pandas as pd
import ollama
import config
import re

def extraer_contactos():
    print("📧 Extrayendo organizadores y emails con Ollama...")
    
    try:
        df = pd.read_csv('data/events_consolidated_v5.csv')
        print(f"📊 {len(df)} eventos cargados")
        
        organizadores = []
        emails = []
        
        for i, row in df.iterrows():
            nombre = row.get('nombre', '')
            descripcion = row.get('descripcion', '')
            lugar = row.get('lugar', '')
            
            if not nombre or str(nombre) == 'nan':
                organizadores.append('')
                emails.append('')
                continue
            
            prompt = f"""
            Evento: {nombre}
            Lugar: {lugar}
            Descripción: {descripcion[:500] if descripcion and str(descripcion) != 'nan' else ''}
            
            Extrae el ORGANIZADOR y EMAIL de este evento.
            Responde SOLO en formato JSON:
            {{"organizador": "nombre_del_organizador", "email": "correo@ejemplo.com"}}
            Si no hay información, responde con valores vacíos.
            """
            
            try:
                response = ollama.chat(
                    model=config.OLLAMA_MODEL,
                    messages=[{"role": "user", "content": prompt}]
                )
                contenido = response['message']['content']
                json_match = re.search(r'\{.*\}', contenido, re.DOTALL)
                if json_match:
                    data = json.loads(json_match.group(0))
                else:
                    data = json.loads(contenido)
                
                organizadores.append(data.get('organizador', ''))
                emails.append(data.get('email', ''))
                print(f"✅ {i+1}/{len(df)}: {nombre[:30]} → {data.get('organizador', 'N/A')}")
            except Exception as e:
                print(f"⚠️ {i+1}/{len(df)}: Error en {nombre[:30]}: {e}")
                organizadores.append('')
                emails.append('')
        
        df['organizador'] = organizadores
        df['email'] = emails
        df.to_csv('data/events_con_contactos.csv', index=False)
        
        total_org = sum(1 for o in organizadores if o)
        total_email = sum(1 for e in emails if e)
        
        print(f"\n✅ Guardado en data/events_con_contactos.csv")
        print(f"   Organizadores extraídos: {total_org}")
        print(f"   Emails extraídos: {total_email}")
        
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == '__main__':
    extraer_contactos()
