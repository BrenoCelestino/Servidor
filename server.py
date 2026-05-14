import socket
import json
import threading
import time
from flask import Flask, render_template_string
from flask_socketio import SocketIO

# ==========================================
# CONFIGURAÇÕES DO SERVIDOR
# ==========================================
HOST_IP = "0.0.0.0"
UDP_PORT = 8080    # Porta de Telemetria (Tempo Real)
TCP_PORT = 8081    # Porta de Download do Cartão SD (Histórico)
WEB_PORT = 5000    # Porta do Dashboard Web

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# ==========================================
# BANCO DE DADOS EM MEMÓRIA
# ==========================================
trajeto_online = []   # Rota vermelha (Feita enquanto conectado)
trajeto_offline = []  # Rota azul (Feita enquanto desconectado, lida do SD)
last_packet_time = 0
current_status = "Aguardando conexão..."

# ==========================================
# THREAD 1: RECEPTOR UDP (TEMPO REAL)
# ==========================================
def udp_listener():
    global last_packet_time
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((HOST_IP, UDP_PORT))
    print(f"[*] [UDP] Servidor aguardando telemetria na porta {UDP_PORT}...")

    while True:
        data, addr = sock.recvfrom(1024)
        last_packet_time = time.time()
        
        try:
            payload = data.decode('utf-8')
            telemetria = json.loads(payload)
            
            lat = telemetria.get('lat', 0.0)
            lng = telemetria.get('lng', 0.0)
            
            if lat != 0.0 and lng != 0.0:
                trajeto_online.append([lat, lng])

            # Envia imediatamente para a interface Web
            socketio.emit('nova_telemetria', telemetria)

        except json.JSONDecodeError:
            pass

# ==========================================
# THREAD 2: RECEPTOR TCP (HISTÓRICO DO SD)
# ==========================================
def tcp_listener():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((HOST_IP, TCP_PORT))
    sock.listen(5)
    print(f"[*] [TCP] Servidor aguardando download do Cartão SD na porta {TCP_PORT}...")

    while True:
        conn, addr = sock.accept()
        print(f"\n[+] Conexão TCP de {addr[0]}! Iniciando download do histórico...")
        
        buffer = ""
        try:
            while True:
                data = conn.recv(4096)
                if not data:
                    break
                buffer += data.decode('utf-8')
        except Exception as e:
            print(f"[!] Erro no download do SD: {e}")
        finally:
            conn.close()

        # Processa o arquivo CSV recebido (Data, Lat, Lng, Roll, Pitch)
        if buffer:
            linhas = buffer.strip().split('\n')
            novos_pontos = []
            
            for linha in linhas:
                partes = linha.strip().split(',')
                if len(partes) >= 3: # Garante que a linha tem no mínimo Hora, Lat e Lng
                    try:
                        lat = float(partes[1])
                        lng = float(partes[2])
                        if lat != 0.0 and lng != 0.0:
                            novos_pontos.append([lat, lng])
                            trajeto_offline.append([lat, lng])
                    except ValueError:
                        continue
            
            if novos_pontos:
                # CORREÇÃO AQUI: Uso de aspas simples para 'apagão'
                print(f"[*] SUCESSO: {len(novos_pontos)} coordenadas recuperadas do 'apagão'!")
                socketio.emit('historico_recebido', novos_pontos)

# ==========================================
# THREAD 3: MONITOR DE CONEXÃO (HEARTBEAT)
# ==========================================
def connection_monitor():
    global current_status, last_packet_time
    time_restored = 0
    
    while True:
        socketio.sleep(0.5) 
        
        if last_packet_time == 0:
            socketio.emit('status_conexao', {'status': "Aguardando...", 'delta': '--'})
            continue

        delta = time.time() - last_packet_time
        new_status = current_status

        if delta < 1.0:
            if current_status in ["Conexão Perdida", "Perdendo Sinal", "Pouco Sinal", "Aguardando..."]:
                new_status = "Conexão restabelecida"
                time_restored = time.time()
            elif current_status == "Conexão restabelecida" and (time.time() - time_restored > 3.0):
                new_status = "Conectado"
            elif current_status != "Conexão restabelecida":
                new_status = "Conectado"
                
        elif 1.0 <= delta < 3.0: new_status = "Pouco Sinal"
        elif 3.0 <= delta < 5.0: new_status = "Perdendo Sinal"
        elif delta >= 5.0:       new_status = "Conexão Perdida"

        if new_status != current_status:
            current_status = new_status
            print(f"\n>> [STATUS]: {current_status} <<\n")
            
        socketio.emit('status_conexao', {'status': current_status, 'delta': round(delta, 1)})

# ==========================================
# FRONTEND HTML + MAPA (DUAS CORES)
# ==========================================
HTML_PAGE = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <title>Painel - Data Logger Espacial</title>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <style>
        body { margin: 0; padding: 0; font-family: Arial, sans-serif; display: flex; height: 100vh; overflow: hidden; }
        #map { flex: 1; height: 100%; }
        #panel { width: 340px; background: #2c3e50; color: white; padding: 20px; box-sizing: border-box; display: flex; flex-direction: column; z-index: 10;}
        h2 { text-align: center; font-size: 1.2rem; margin-top: 0; border-bottom: 1px solid #34495e; padding-bottom: 10px; }
        
        #conn-status-box { text-align: center; padding: 15px 10px; border-radius: 6px; margin-bottom: 15px; background: #7f8c8d; color: white; transition: 0.3s;}
        .status-header { display: flex; align-items: center; justify-content: center; font-weight: bold; text-transform: uppercase; letter-spacing: 1px; }
        .dot { height: 12px; width: 12px; border-radius: 50%; display: inline-block; margin-right: 8px; background-color: #fff; }
        .pulsing { animation: pulse 1s infinite alternate; }
        @keyframes pulse { from { opacity: 1; transform: scale(1); } to { opacity: 0.4; transform: scale(0.8); } }
        #delta-time { font-size: 0.85em; margin-top: 8px; opacity: 0.9; }

        .status-conectado { background: #27ae60 !important; box-shadow: 0 0 10px rgba(39, 174, 96, 0.5); }
        .status-pouco { background: #f39c12 !important; }
        .status-perdendo { background: #e67e22 !important; }
        .status-perdida { background: #c0392b !important; }
        .status-restabelecida { background: #2980b9 !important; }

        .data-box { background: #34495e; padding: 15px; margin-bottom: 10px; border-radius: 8px; }
        .data-box span { font-weight: bold; color: #1abc9c; font-size: 1.2em; display: block; margin-top: 5px; }
        .alert { background: #e74c3c; padding: 10px; border-radius: 5px; text-align: center; margin-bottom: 15px; display: none; }
        
        /* Notificação de Recuperação de Dados */
        #recovery-toast { position: absolute; bottom: 20px; left: 50%; transform: translateX(-50%); background: #2980b9; color: white; padding: 10px 20px; border-radius: 30px; font-weight: bold; box-shadow: 0 4px 6px rgba(0,0,0,0.3); display: none; z-index: 1000;}
    </style>
</head>
<body>
    <div id="panel">
        <h2>Data Logger</h2>
        <div id="conn-status-box">
            <div class="status-header"><span id="status-dot" class="dot"></span><span id="status-text">Aguardando...</span></div>
            <div id="delta-time">Sinal: -- s</div>
        </div>
        <div id="gps-alert" class="alert">Buscando satélites...</div>
        <div class="data-box">📍 Posição GPS<span id="latlng">0.00, 0.00</span></div>
        <div class="data-box">📡 Satélites (Fix)<span id="sats">0</span></div>
        <div class="data-box">📐 Inércia (Fusão)
            <span>Roll: <b id="roll" style="color:#3498db">0.00°</b></span>
            <span>Pitch: <b id="pitch" style="color:#e67e22">0.00°</b></span>
        </div>
        
        <div style="margin-top:20px; font-size: 0.8em; color: #bdc3c7;">
            <b>Legenda do Mapa:</b><br>
            <span style="color:#e74c3c; font-size:1.5em;">■</span> Trajeto Online (Ao vivo)<br>
            <span style="color:#3498db; font-size:1.5em;">■</span> Trajeto Offline (Recuperado)
        </div>
    </div>

    <div id="map"></div>
    <div id="recovery-toast">📦 Dados offline recuperados do Cartão SD!</div>

    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <script>
        var map = L.map('map').setView([-15.7938, -47.8827], 4);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png').addTo(map);

        var marker = null;
        var polyline_online = L.polyline([], {color: '#e74c3c', weight: 4}).addTo(map);
        var polyline_offline = L.polyline([], {color: '#3498db', weight: 4, dashArray: '5, 10'}).addTo(map);
        var primeiraLeituraGps = true;
        var socket = io();

        // RECEBE DADOS EM TEMPO REAL (UDP)
        socket.on('nova_telemetria', function(data) {
            document.getElementById('roll').innerText = data.roll + '°';
            document.getElementById('pitch').innerText = data.pitch + '°';
            document.getElementById('sats').innerText = data.sats;

            if (data.lat !== 0.0 && data.lng !== 0.0) {
                document.getElementById('gps-alert').style.display = 'none';
                document.getElementById('latlng').innerText = data.lat.toFixed(5) + ', ' + data.lng.toFixed(5);
                var latlng = [data.lat, data.lng];
                polyline_online.addLatLng(latlng);

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

        // RECEBE DADOS RECUPERADOS DO CARTÃO SD (TCP)
        socket.on('historico_recebido', function(pontos) {
            // Adiciona todos os pontos na linha Azul (Offline)
            for(var i=0; i<pontos.length; i++){
                polyline_offline.addLatLng(pontos[i]);
            }
            // Enquadra o mapa para mostrar a nova rota inteira
            map.fitBounds(polyline_offline.getBounds());
            
            // Mostra aviso na tela por 4 segundos
            var toast = document.getElementById('recovery-toast');
            toast.style.display = 'block';
            setTimeout(() => { toast.style.display = 'none'; }, 4000);
        });

        // ATUALIZA INTERFACE DO HEARTBEAT
        socket.on('status_conexao', function(data) {
            var box = document.getElementById('conn-status-box');
            document.getElementById('status-text').innerText = data.status;
            document.getElementById('delta-time').innerText = data.delta === '--' ? "Aguardando primeiro pacote" : "Último pacote: " + data.delta + "s atrás";
            
            box.className = ''; document.getElementById('status-dot').className = 'dot';
            if(data.status === "Conectado") { box.classList.add('status-conectado'); document.getElementById('status-dot').classList.add('pulsing'); }
            else if(data.status === "Pouco Sinal") box.classList.add('status-pouco');
            else if(data.status === "Perdendo Sinal") box.classList.add('status-perdendo');
            else if(data.status === "Conexão Perdida") box.classList.add('status-perdida');
            else if(data.status === "Conexão restabelecida") { box.classList.add('status-restabelecida'); document.getElementById('status-dot').classList.add('pulsing'); }
        });

        // AO ABRIR O NAVEGADOR, CARREGA TUDO QUE JÁ ESTAVA NA MEMÓRIA
        socket.on('carregar_trajetos_salvos', function(dados) {
            polyline_online.setLatLngs(dados.online);
            polyline_offline.setLatLngs(dados.offline);
            if(dados.online.length > 0) {
                primeiraLeituraGps = false;
                marker = L.marker(dados.online[dados.online.length - 1]).addTo(map);
                map.fitBounds(polyline_online.getBounds());
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
    socketio.emit('carregar_trajetos_salvos', {'online': trajeto_online, 'offline': trajeto_offline})

if __name__ == '__main__':
    socketio.start_background_task(target=udp_listener)
    socketio.start_background_task(target=tcp_listener)
    socketio.start_background_task(target=connection_monitor)
    
    print("\n================================================")
    print("🚀 SERVIDOR DE TELEMETRIA ESPACIAL INICIADO!")
    print("   Acesse o painel: http://localhost:5000")
    print("================================================\n")
    
    socketio.run(app, host=HOST_IP, port=WEB_PORT, debug=False)