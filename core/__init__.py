"""
Núcleo del Loop Central de Optimización (Dharmadhatu Bot v5).

Módulos:
- deduplicador: deduplicación global con hash persistente en JSON.
- recursos: rotación de User-Agents y proxies desde archivos compartidos.
- orquestador: ejecuta todos los scrapers en paralelo con timeouts, dedup
  global y actualización aditiva de eventos_encontrados.csv.
"""
