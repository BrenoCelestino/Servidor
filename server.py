import socket
import json
import time
from flask import Flask, render_template_string
from flask_socketio import SocketIO

# ==========================================
# CONFIGURAÇÕES DO SERVIDOR
# ==========================================
UDP_IP = "0.0.0.0"
UDP_PORT = 8080
WEB_PORT = 5000

app = Flask(__name__)
# A MUDANÇA PRINCIPAL ESTÁ AQUI: Usando 'threading' em vez de 'eventlet'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

trajeto = []
last_packet_time = 0
current_status = "Aguardando conexão..."

# ==========================================
# TAREFA 1: RECEPTOR UDP
# ==========================================
def udp_listener():
    global last_packet_time
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT))
    print(f"[*] Servidor UDP aguardando telemetria na porta {UDP_PORT}...")

    while True:
        data, addr = sock.recvfrom(1024)
        last_packet_time = time.time()
        
        try:
            payload = data.decode('utf-8')
            telemetria = json.loads(payload)
            
            lat = telemetria.get('lat', 0.0)
            lng = telemetria.get('lng', 0.0)
            
            print(f"[{addr[0]}] GPS: {lat:.5f}, {lng:.5f} | IMU: {telemetria.get('roll')}°, {telemetria.get('pitch')}°")

            if lat != 0.0 and lng != 0.0:
                trajeto.append([lat, lng])

            # Agora o SocketIO consegue emitir sem ser bloqueado!
            socketio.emit('nova_telemetria', telemetria)

        except json.JSONDecodeError:
            pass
        except Exception as e:
            print(f"[!] Erro: {e}")

# ==========================================
# TAREFA 2: MONITOR DE STATUS DE REDE
# ==========================================
def connection_monitor():
    global current_status, last_packet_time
    time_restored = 0
    
    while True:
        # Usando o sleep nativo do SocketIO para não travar a comunicação web
        socketio.sleep(0.5) 
        
        if last_packet_time == 0:
            socketio.emit('status_conexao', {'status': "Aguardando...", 'delta': '--'})
            continue

        delta = time.time() - last_packet_time
        new_status = current_status

        if delta < 1.0:
            if current_status in ["Conexão Perdida", "Perdendo Sinal", "Pouco Sinal", "Aguardando conexão...", "Aguardando..."]:
                new_status = "Conexão restabelecida"
                time_restored = time.time()
            elif current_status == "Conexão restabelecida" and (time.time() - time_restored > 3.0):
                new_status = "Conectado"
            elif current_status != "Conexão restabelecida":
                new_status = "Conectado"
                
        elif 1.0 <= delta < 3.0:
            new_status = "Pouco Sinal"
        elif 3.0 <= delta < 5.0:
            new_status = "Perdendo Sinal"
        elif delta >= 5.0:
            new_status = "Conexão Perdida"

        if new_status != current_status:
            current_status = new_status
            print(f"\n====================================")
            print(f"[STATUS DISPOSITIVO] {current_status}")
            print(f"====================================\n")
            
        socketio.emit('status_conexao', {'status': current_status, 'delta': round(delta, 1)})

# ==========================================
# FRONTEND INTERATIVO (HTML + CSS + JS)
# ==========================================
HTML_PAGE = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <title>Dashboard - Rastreador GPS + IMU</title>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <style>
        body { margin: 0; padding: 0; font-family: Arial, sans-serif; display: flex; height: 100vh; }
        #map { flex: 1; height: 100%; }
        #panel { width: 340px; background: #2c3e50; color: white; padding: 20px; box-sizing: border-box; display: flex; flex-direction: column; }
        h2 { text-align: center; font-size: 1.2rem; margin-top: 0; border-bottom: 1px solid #34495e; padding-bottom: 10px; }
        
        #conn-status-box { 
            text-align: center; padding: 15px 10px; border-radius: 6px; margin-bottom: 15px; 
            background: #7f8c8d; color: white; transition: 0.3s;
        }
        .status-header { display: flex; align-items: center; justify-content: center; font-weight: bold; text-transform: uppercase; font-size: 1em; letter-spacing: 1px; }
        
        .dot { height: 12px; width: 12px; border-radius: 50%; display: inline-block; margin-right: 8px; background-color: #fff; }
        .pulsing { animation: pulse 1s infinite alternate; }
        @keyframes pulse { from { opacity: 1; transform: scale(1); } to { opacity: 0.4; transform: scale(0.8); } }

        #delta-time { font-size: 0.85em; margin-top: 8px; font-weight: normal; opacity: 0.9; }

        .status-conectado { background: #27ae60 !important; box-shadow: 0 0 10px rgba(39, 174, 96, 0.5); }
        .status-pouco { background: #f39c12 !important; }
        .status-perdendo { background: #e67e22 !important; }
        .status-perdida { background: #c0392b !important; }
        .status-restabelecida { background: #2980b9 !important; }

        .data-box { background: #34495e; padding: 15px; margin-bottom: 10px; border-radius: 8px; }
        .data-box span { font-weight: bold; color: #1abc9c; font-size: 1.2em; display: block; margin-top: 5px; }
        .alert { background: #e74c3c; padding: 10px; border-radius: 5px; text-align: center; margin-bottom: 15px; display: none; }
    </style>
</head>
<body>
    <div id="panel">
        <h2>Rastreador Espacial</h2>
        
        <div id="conn-status-box">
            <div class="status-header">
                <span id="status-dot" class="dot"></span>
                <span id="status-text">Aguardando...</span>
            </div>
            <div id="delta-time">Sinal: -- s</div>
        </div>

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

    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <script>
        var map = L.map('map').setView([-15.793889, -47.882778], 4);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '© OpenStreetMap' }).addTo(map);

        var marker = null;
        var polyline = L.polyline([], {color: '#e74c3c', weight: 4}).addTo(map);
        var primeiraLeituraGps = true;
        var socket = io();

        socket.on('nova_telemetria', function(data) {
            document.getElementById('roll').innerText = data.roll + '°';
            document.getElementById('pitch').innerText = data.pitch + '°';
            document.getElementById('sats').innerText = data.sats;

            if (data.lat !== 0.0 && data.lng !== 0.0) {
                document.getElementById('gps-alert').style.display = 'none';
                document.getElementById('latlng').innerText = data.lat.toFixed(5) + ', ' + data.lng.toFixed(5);
                document.getElementById('alt_speed').innerText = data.alt + ' m | ' + data.speed + ' km/h';

                var latlng = [data.lat, data.lng];
                polyline.addLatLng(latlng);

                if (primeiraLeituraGps) {
                    map.setView(latlng, 18); 
                    marker = L.marker(latlng).addTo(map);
                    primeiraLeituraGps = false;
                } else {
                    marker.setLatLng(latlng); 
                }
            } else {
                document.getElementById('gps-alert').style.display = 'block';
            }
        });

        socket.on('status_conexao', function(data) {
            var statusBox = document.getElementById('conn-status-box');
            var statusText = document.getElementById('status-text');
            var statusDot = document.getElementById('status-dot');
            var deltaTime = document.getElementById('delta-time');
            
            statusText.innerText = data.status;
            
            if(data.delta === '--') {
                deltaTime.innerText = "Aguardando primeiro pacote";
            } else {
                deltaTime.innerText = "Último pacote: " + data.delta + "s atrás";
            }
            
            statusBox.classList.remove('status-conectado', 'status-pouco', 'status-perdendo', 'status-perdida', 'status-restabelecida');
            statusDot.classList.remove('pulsing');
            statusDot.style.backgroundColor = "#fff";

            if(data.status === "Conectado") {
                statusBox.classList.add('status-conectado');
                statusDot.classList.add('pulsing');
            } else if(data.status === "Pouco Sinal") {
                statusBox.classList.add('status-pouco');
            } else if(data.status === "Perdendo Sinal") {
                statusBox.classList.add('status-perdendo');
            } else if(data.status === "Conexão Perdida") {
                statusBox.classList.add('status-perdida');
                statusDot.style.backgroundColor = "#333"; 
            } else if(data.status === "Conexão restabelecida") {
                statusBox.classList.add('status-restabelecida');
                statusDot.classList.add('pulsing');
            }
        });

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
    socketio.emit('carregar_trajeto', trajeto)

if __name__ == '__main__':
    # Usando o gerenciador de background nativo do SocketIO
    socketio.start_background_task(target=udp_listener)
    socketio.start_background_task(target=connection_monitor)
    
    print("\n[*] Servidor Iniciado!")
    print("[*] Acesse: http://localhost:5000\n")
    
    socketio.run(app, host='0.0.0.0', port=WEB_PORT, debug=False)