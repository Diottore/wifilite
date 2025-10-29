```markdown
# Termux Network Tester (iperf3 + ping + RSSI) — GUI web control

Descripción
-----------
Este proyecto proporciona una pequeña aplicación que puedes ejecutar desde Termux en Android (sin root) para:
- Medir RSSI (termux-api).
- Medir latencia (ping) y calcular jitter a partir de pings.
- Medir download y upload usando iperf3 (iperf3 CLI con salida JSON).
- Ejecutar pruebas en 8 ubicaciones (p1..p8), con 3–5 iteraciones por ubicación.
- Pausar después de completar las iteraciones en cada ubicación para que te muevas a la siguiente.
- Guardar logs detallados y exportar a JSON y CSV.
- Interfaz web simple (Flask) accesible desde el navegador.

Requisitos (Termux)
-------------------
1. Instala paquetes en Termux:
   pkg update && pkg upgrade
   pkg install python git
   pkg install iperf3
   pkg install termux-api

2. Instala dependencias Python:
   pip install -r requirements.txt

Cómo ejecutar
-------------
1. Copia `run_tests.py` en tu Termux (por ejemplo `~/termux-network-tester/run_tests.py`).
2. Ejecuta:
   python run_tests.py --host 0.0.0.0 --port 5000
3. Abre en tu navegador (en el mismo teléfono): http://localhost:5000
   O desde otra máquina en la misma red: http://<ip-de-tu-telefono>:5000

Uso
---
- Introduce la IP/host del servidor iperf3 (asegúrate que el servidor esté escuchando).
- Elige iteraciones por ubicación (3-5).
- Duración (en segundos) por prueba iperf3 (por defecto 60s).
- Inicia. La app correrá por ubicación p1..p8.
- Tras completar las iteraciones de cada ubicación, la ejecución se pausará (por defecto) y deberás pulsar "Reanudar" para continuar con la siguiente ubicación.
- Puedes descargar los resultados en JSON o CSV.

Notas, limitaciones y recomendaciones
------------------------------------
- RSSI se obtiene con `termux-wifi-connectioninfo` (termux-api). Si no tienes termux-api instalado o no proporciona el campo esperado, RSSI podrá aparecer como `null`.
- iperf3 TCP es usado para download/upload. Jitter se calcula a partir de la variación de RTT de ping (estimación), ya que medir jitter "real" requiere UDP iperf3 tests y configuración de ancho de banda. Si prefieres jitter de iperf3 directamente, se puede añadir un test UDP adicional (pero puede provocar pérdida si el ancho de banda es mayor que el enlace).
- Cada iteración ejecuta iperf3 upload (cliente->servidor) y un iperf3 download (-R) de `duration` segundos cada uno. Por ende cada iteración tarda aproximadamente 2 * duration s (más overhead). Ajusta `duration` si quieres reducir tiempo de ejecución.
- Asegúrate que el servidor iperf3 esté accesible desde el teléfono.

Salida
-----
- Logs detallados por iteración (incluyen muestras de ping, estadísticas, iperf3 JSON crudo).
- Resumen por ubicación con media, mediana, percentil 95 (p95) para latencia y tasas.
- Export JSON y CSV desde la GUI.

Ejemplo de mejora
-----------------
- Añadir pruebas UDP con iperf3 para obtener jitter probado por iperf3 (recomendable solo si controlas el servidor y conoces ancho de banda).
- Añadir persistencia de resultados (guardar automáticamente con nombre por fecha).
- Mejorar interfaz gráfica (gráficas en tiempo real).
```