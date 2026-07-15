def scrape_grupos_fijos(limit=100):
    eventos = []
    for i in range(min(limit, 15)):
        eventos.append({
            'nombre': f"Evento en grupo {i+1}",
            'fuente': 'Grupos Fijos',
            'fecha': 'N/A',
            'lugar': 'N/A',
            'pais': 'N/A',
            'organizador': 'N/A',
            'email': 'N/A'
        })
    return eventos
