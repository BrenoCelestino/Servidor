import socket
import json
import threading
from flask import Flask, render_template_string
from flask_socketio import SocketIO

# ==========================================
# CONFIGURAÇÕES DO SERVIDOR
# ==========================================
UDP_IP = "0.0.0.0"  # Escuta em todas as interfaces de rede do PC
UDP_PORT = 8080     # Mesma porta definida no código do NodeMCU
WEB_PORT = 5000     # Porta para acessar o mapa no navegador

# Inicializa o Flask e o SocketIO
app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# Variável global para armazenar o trajeto em memória
trajeto = []

# ==========================================
# THREAD DO RECEPTOR UDP
# ==========================================
def udp_listener():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT))
    print(f"[*] Servidor UDP aguardando telemetria na porta {UDP_PORT}...")

    while True:
        data, addr = sock.recvfrom(1024) # Buffer de 1024 bytes
        try:
            payload = data.decode('utf-8')
            telemetria = json.loads(payload)
            
            # 1. Imprime no Console em Tempo Real
            lat = telemetria.get('lat', 0.0)
            lng = telemetria.get('lng', 0.0)
            print(f"[{addr[0]}] GPS: {lat:.6f}, {lng:.6f} | "
                  f"IMU(Fusão): Roll {telemetria.get('roll')}°, Pitch {telemetria.get('pitch')}° | "
                  f"Sats: {telemetria.get('sats')}")

            # 2. Salva o trajeto se o GPS for válido
            if lat != 0.0 and lng != 0.0:
                trajeto.append([lat, lng])

            # 3. Envia os dados para a interface Web via WebSocket
            socketio.emit('nova_telemetria', telemetria)

        except json.JSONDecodeError:
            print("[!] Pacote recebido não é um JSON válido.")
        except Exception as e:
            print(f"[!] Erro ao processar: {e}")

# ==========================================
# FRONTEND INTERATIVO (HTML + OpenStreetMap)
# ==========================================
HTML_PAGE = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <title>Dashboard - Rastreador GPS + IMU</title>
    <!-- CSS do Leaflet (OpenStreetMap) -->
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <style>
        body { margin: 0; padding: 0; font-family: Arial, sans-serif; display: flex; height: 100vh; }
        #map { flex: 1; height: 100%; }
        #panel { width: 320px; background: #2c3e50; color: white; padding: 20px; box-sizing: border-box; display: flex; flex-direction: column; }
        h2 { text-align: center; font-size: 1.2rem; margin-top: 0; border-bottom: 1px solid #34495e; padding-bottom: 10px; }
        .data-box { background: #34495e; padding: 15px; margin-bottom: 10px; border-radius: 8px; }
        .data-box span { font-weight: bold; color: #1abc9c; font-size: 1.2em; display: block; margin-top: 5px; }
        .alert { background: #e74c3c; padding: 10px; border-radius: 5px; text-align: center; margin-bottom: 15px; display: none; }
    </style>
</head>
<body>
    <div id="panel">
        <h2>Rastreador Espacial</h2>
        <div id="gps-alert" class="alert">Buscando sinal GPS...</div>
        
        <div class="data-box">📍 Posição GPS (NEO-6M)
            <span id="latlng">Aguardando...</span>
        </div>
        <div class="data-box">📡 Satélites Fixados
            <span id="sats">0</span>
        </div>
        <div class="data-box">⛰️ Altitude / Vel.
            <span id="alt_speed">0.0 m | 0.0 km/h</span>
        </div>
        <div class="data-box">📐 Inclinação IMU (Fusão)
            <span>Roll: <b id="roll" style="color: #3498db">0.00°</b></span>
            <span>Pitch: <b id="pitch" style="color: #e67e22">0.00°</b></span>
        </div>
    </div>

    <div id="map"></div>

    <!-- Bibliotecas JS -->
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <script>
        // Inicializa o Mapa centrado no Brasil (nível de zoom 4)
        var map = L.map('map').setView([-15.793889, -47.882778], 4);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '© OpenStreetMap contributors'
        }).addTo(map);

        var marker = null;
        var polyline = L.polyline([], {color: '#e74c3c', weight: 4}).addTo(map);
        var primeiraLeituraGps = true;

        // Conecta ao WebSocket local
        var socket = io();

        // Escuta o evento 'nova_telemetria' enviado pelo Python
        socket.on('nova_telemetria', function(data) {
            // Atualiza painel IMU
            document.getElementById('roll').innerText = data.roll + '°';
            document.getElementById('pitch').innerText = data.pitch + '°';
            document.getElementById('sats').innerText = data.sats;

            if (data.lat !== 0.0 && data.lng !== 0.0) {
                // Remove alerta visual
                document.getElementById('gps-alert').style.display = 'none';
                
                // Atualiza Textos
                document.getElementById('latlng').innerText = data.lat.toFixed(5) + ', ' + data.lng.toFixed(5);
                document.getElementById('alt_speed').innerText = data.alt + ' m | ' + data.speed + ' km/h';

                var latlng = [data.lat, data.lng];
                
                // Adiciona ponto ao trajeto vermelho
                polyline.addLatLng(latlng);

                // Gerencia o Marcador no mapa
                if (primeiraLeituraGps) {
                    map.setView(latlng, 18); // Dá zoom máximo na primeira vez que achar sinal
                    marker = L.marker(latlng).addTo(map);
                    primeiraLeituraGps = false;
                } else {
                    marker.setLatLng(latlng); // Move o carrinho/marcador
                    // Descomente a linha abaixo se quiser que o mapa persiga o marcador o tempo todo:
                    // map.panTo(latlng); 
                }
            } else {
                // Alerta que o GPS não encontrou satélites ainda
                document.getElementById('gps-alert').style.display = 'block';
            }
        });

        // Ao conectar na página, carrega o trajeto antigo salvo na memória do servidor
        socket.on('carregar_trajeto', function(trajetoSalvo) {
            if(trajetoSalvo.length > 0) {
                polyline.setLatLngs(trajetoSalvo);
                map.fitBounds(polyline.getBounds());
                primeiraLeituraGps = false;
                marker = L.marker(trajetoSalvo[trajetoSalvo.length - 1]).addTo(map);
            }
        });
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_PAGE)

@socketio.on('connect')
def handle_connect():
    # Quando o usuário abre o navegador, envia a rota já percorrida
    socketio.emit('carregar_trajeto', trajeto)

if __name__ == '__main__':
    # Inicia a thread UDP para não bloquear o servidor Web
    udp_thread = threading.Thread(target=udp_listener, daemon=True)
    udp_thread.start()
    
    print("\n[*] Servidor Web e Dashboard Iniciados!")
    print("[*] Acesse no seu navegador: http://localhost:5000\n")
    
    # Inicia o servidor Web com suporte a WebSockets
    socketio.run(app, host='0.0.0.0', port=WEB_PORT, debug=False)