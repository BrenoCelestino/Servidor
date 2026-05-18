import socket
import json
import threading
import time
import os
import math
from datetime import datetime
from flask import Flask, render_template_string, send_from_directory
from flask_socketio import SocketIO

# ==========================================
# CONFIGURAÇÕES DO SERVIDOR
# ==========================================
HOST_IP = "0.0.0.0"
UDP_PORT = 8080    
TCP_PORT = 8081    
WEB_PORT = 5000    

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# ==========================================
# MATEMÁTICA: FILTRO GPS (ANTI-ZIGUEZAGUE VISUAL)
# ==========================================
# Usado APENAS para desenhar bonito na tela. O TXT salvará o dado CRU.
def haversine(lat1, lon1, lat2, lon2):
    R = 6371000 
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlon/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

class GPSFilter:
    def __init__(self, window_size=5, min_dist=1.5):
        self.window = []
        self.window_size = window_size
        self.min_dist = min_dist
        self.last_path_point = None

    def process(self, lat, lng):
        self.window.append((lat, lng))
        if len(self.window) > self.window_size:
            self.window.pop(0)
        
        avg_lat = sum(p[0] for p in self.window) / len(self.window)
        avg_lng = sum(p[1] for p in self.window) / len(self.window)
        
        is_new_point = False
        if self.last_path_point is None:
            self.last_path_point = (avg_lat, avg_lng)
            is_new_point = True
        else:
            dist = haversine(self.last_path_point[0], self.last_path_point[1], avg_lat, avg_lng)
            if dist >= self.min_dist:
                self.last_path_point = (avg_lat, avg_lng)
                is_new_point = True
                
        return avg_lat, avg_lng, is_new_point

live_filter = GPSFilter(window_size=5, min_dist=1.5)
offline_filter = GPSFilter(window_size=3, min_dist=2.0)

# ==========================================
# BANCO DE DADOS (RAW DATA - CRU)
# ==========================================
HISTORICO_DIR = "Historico"
if not os.path.exists(HISTORICO_DIR):
    os.makedirs(HISTORICO_DIR)

# Atualizado para salvar TODAS as 11 variáveis do ESP32
def salvar_linha_historico(timestamp_str, lat, lng, spd, crs, hdop, sats, accX, accY, accZ, roll, pitch):
    try:
        data_parte = timestamp_str.split('T')[0]
        ano, mes, dia = data_parte.split('-')
        filename = f"{int(dia):02d}-{int(mes):02d}-{ano}.txt"
    except:
        filename = datetime.now().strftime('%d-%m-%Y.txt')

    filepath = os.path.join(HISTORICO_DIR, filename)
    # Estrutura CSV Crua: Time, Lat, Lng, Spd, Crs, HDOP, Sats, AccX, AccY, AccZ, Roll, Pitch
    linha = f"{timestamp_str},{lat:.6f},{lng:.6f},{spd:.2f},{crs:.2f},{hdop:.1f},{sats},{accX:.3f},{accY:.3f},{accZ:.3f},{roll:.2f},{pitch:.2f}"
    
    with open(filepath, "a", encoding="utf-8") as f:
        f.write(linha + "\n")

# ==========================================
# VARIÁVEIS EM MEMÓRIA
# ==========================================
trajeto_online = []   
trajeto_offline = []  
last_packet_time = 0
current_status = "Aguardando conexão..."

total_uptime_seconds = 0
session_start_time = None

# ==========================================
# THREAD 1: RECEPTOR UDP (TEMPO REAL)
# ==========================================
def udp_listener():
    global last_packet_time
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((HOST_IP, UDP_PORT))
    print(f"[*] [UDP] Servidor aguardando telemetria na porta {UDP_PORT}...")

    last_terminal_print = 0

    while True:
        data, addr = sock.recvfrom(1024)
        last_packet_time = time.time()
        
        try:
            payload = data.decode('utf-8')
            telemetria = json.loads(payload)
            
            # Extrai os Raw Data do pacote
            lat = telemetria.get('lat', 0.0)
            lng = telemetria.get('lng', 0.0)
            spd = telemetria.get('spd', 0.0)
            crs = telemetria.get('crs', 0.0)
            hdop = telemetria.get('hdop', 99.9)
            sats = telemetria.get('sats', 0)
            accX = telemetria.get('accX', 0.0)
            accY = telemetria.get('accY', 0.0)
            accZ = telemetria.get('accZ', 0.0)
            roll = telemetria.get('roll', 0.0)
            pitch = telemetria.get('pitch', 0.0)
            
            # Print no terminal do PC (Apenas 1 vez por segundo para não poluir, embora receba a 5Hz)
            if time.time() - last_terminal_print >= 1.0:
                last_terminal_print = time.time()
                print(f"[{addr[0]}] LIVE | Lat:{lat:.5f} Lng:{lng:.5f} | HDOP:{hdop:.1f} Sats:{sats} | AccX:{accX:.2f}g")

            # 1. SALVAR DADO CRU (Sem filtros Visuais) no TXT
            timestamp = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
            salvar_linha_historico(timestamp, lat, lng, spd, crs, hdop, sats, accX, accY, accZ, roll, pitch)

            # 2. FILTRO VISUAL PARA A TELA (Ignora Ziguezague no UI)
            if lat != 0.0 and lng != 0.0:
                s_lat, s_lng, is_new = live_filter.process(lat, lng)
                telemetria['lat'] = s_lat
                telemetria['lng'] = s_lng
                telemetria['is_new_point'] = is_new
                
                if is_new:
                    trajeto_online.append([s_lat, s_lng])
            else:
                telemetria['is_new_point'] = False

            # Envia pacote para o Navegador Web
            socketio.emit('nova_telemetria', telemetria)
            
        except json.JSONDecodeError:
            pass

# ==========================================
# THREAD 2: RECEPTOR TCP (HISTÓRICO DA RAM)
# ==========================================
def tcp_listener():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((HOST_IP, TCP_PORT))
    sock.listen(5)
    print(f"[*] [TCP] Servidor aguardando download da Memória RAM na porta {TCP_PORT}...")

    while True:
        conn, addr = sock.accept()
        print(f"\n[+] Conexão TCP de {addr[0]}! Descarregando RAW DATA atrasados...")
        buffer = ""
        try:
            while True:
                data = conn.recv(4096)
                if not data:
                    break
                buffer += data.decode('utf-8')
        except Exception as e:
            pass
        finally:
            conn.close()

        if buffer:
            linhas = buffer.strip().split('\n')
            novos_pontos = []
            offline_filter.window.clear()
            offline_filter.last_path_point = None
            
            for linha in linhas:
                partes = linha.strip().split(',')
                # O novo formato tem 12 variáveis
                if len(partes) >= 12: 
                    try:
                        timestamp = partes[0]
                        lat = float(partes[1])
                        lng = float(partes[2])
                        spd = float(partes[3])
                        crs = float(partes[4])
                        hdop = float(partes[5])
                        sats = int(partes[6])
                        accX = float(partes[7])
                        accY = float(partes[8])
                        accZ = float(partes[9])
                        roll = float(partes[10])
                        pitch = float(partes[11])
                        
                        # Salva o Dado Cru recuperado no Banco de Dados
                        salvar_linha_historico(timestamp, lat, lng, spd, crs, hdop, sats, accX, accY, accZ, roll, pitch)
                        
                        # Filtro Visual apenas para gerar a Linha Azul no Mapa
                        if lat != 0.0 and lng != 0.0:
                            s_lat, s_lng, is_new = offline_filter.process(lat, lng)
                            if is_new:
                                novos_pontos.append([s_lat, s_lng])
                                trajeto_offline.append([s_lat, s_lng])
                                
                    except ValueError:
                        continue
            
            if novos_pontos:
                print(f"[*] SUCESSO: {len(novos_pontos)} coordenadas recuperadas da 'Caixa Preta' (RAM)!")
                socketio.emit('historico_recebido', novos_pontos)

# ==========================================
# THREAD 3: MONITOR DE CONEXÃO E UPTIME
# ==========================================
def connection_monitor():
    global current_status, last_packet_time, total_uptime_seconds, session_start_time
    time_restored = 0
    
    while True:
        socketio.sleep(0.5) 
        if last_packet_time == 0:
            socketio.emit('status_conexao', {'status': "Aguardando...", 'delta': '--', 'uptime': 0})
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

        is_online = (new_status in ["Conectado", "Conexão restabelecida"])
        
        if is_online and session_start_time is None:
            session_start_time = time.time() 
        elif not is_online and session_start_time is not None:
            total_uptime_seconds += (time.time() - session_start_time) 
            session_start_time = None

        current_uptime = total_uptime_seconds
        if is_online and session_start_time is not None:
            current_uptime += (time.time() - session_start_time)

        if new_status != current_status:
            current_status = new_status
            print(f"\n>> [STATUS DA FROTA]: {current_status} <<\n")
            socketio.emit('atualizar_lista', obter_lista_historicos())
            
        socketio.emit('status_conexao', {
            'status': current_status, 
            'delta': round(delta, 1),
            'uptime': current_uptime
        })

def obter_lista_historicos():
    arquivos = [f for f in os.listdir(HISTORICO_DIR) if f.endswith('.txt')]
    arquivos.sort(reverse=True)
    return arquivos

# ==========================================
# EVENTOS SOCKET.IO E ROTAS WEB
# ==========================================
@socketio.on('solicitar_lista_historicos')
def handle_historicos():
    socketio.emit('atualizar_lista', obter_lista_historicos())

@socketio.on('solicitar_arquivo_historico')
def handle_arquivo(filename):
    filepath = os.path.join(HISTORICO_DIR, filename)
    pontos = []
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            for linha in f:
                partes = linha.strip().split(',')
                # O formato agora tem 12 colunas. Lat=1, Lng=2
                if len(partes) >= 12:
                    try:
                        lat = float(partes[1])
                        lng = float(partes[2])
                        if lat != 0.0 and lng != 0.0:
                            pontos.append([lat, lng])
                    except: pass
    socketio.emit('historico_arquivo_carregado', pontos)

@app.route('/Arquivos/<path:filename>')
def serve_arquivos(filename):
    return send_from_directory('Arquivos', filename)

# ==========================================
# FRONTEND HTML + MAPA
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
        #map { flex: 1; height: 100%; z-index: 1;}
        
        /* PAINEL LATERAL */
        #panel { position: relative; width: 340px; background: #2c3e50; color: white; padding: 20px; box-sizing: border-box; display: flex; flex-direction: column; z-index: 10; overflow-y: hidden; box-shadow: 2px 0 10px rgba(0,0,0,0.5); transition: 0.3s;}
        h2 { text-align: center; font-size: 1.2rem; margin-top: 0; border-bottom: 1px solid #34495e; padding-bottom: 10px; }
        
        #btn-collapse { position: absolute; bottom: 5px; right: 5px; background: rgba(0,0,0,0.15); border: none; color: rgba(255,255,255,0.6); padding: 4px 8px; border-radius: 4px; cursor: pointer; font-size: 0.8rem; transition: 0.3s; z-index: 100; }
        #btn-collapse:hover { background: rgba(0,0,0,0.8); color: white; transform: scale(1.1); }

        #conn-status-box { text-align: center; padding: 15px 10px; border-radius: 6px; margin-bottom: 15px; background: #7f8c8d; color: white; transition: 0.3s;}
        .status-header { display: flex; align-items: center; justify-content: center; font-weight: bold; text-transform: uppercase; letter-spacing: 1px; }
        .dot { height: 12px; width: 12px; border-radius: 50%; display: inline-block; margin-right: 8px; background-color: #fff; }
        .pulsing { animation: pulse 1s infinite alternate; }
        @keyframes pulse { from { opacity: 1; transform: scale(1); } to { opacity: 0.4; transform: scale(0.8); } }
        #delta-time { font-size: 0.85em; margin-top: 8px; opacity: 0.9; }
        #uptime-time { font-size: 0.9em; margin-top: 4px; color: #f1c40f; font-weight: bold; letter-spacing: 1px;}

        .status-conectado { background: #27ae60 !important; box-shadow: 0 0 10px rgba(39, 174, 96, 0.5); }
        .status-pouco { background: #f39c12 !important; }
        .status-perdendo { background: #e67e22 !important; }
        .status-perdida { background: #c0392b !important; }
        .status-restabelecida { background: #2980b9 !important; }

        .data-box { background: #34495e; padding: 15px; margin-bottom: 10px; border-radius: 8px; }
        .data-box span { font-weight: bold; color: #1abc9c; font-size: 1.2em; display: block; margin-top: 5px; }
        .alert { background: #e74c3c; padding: 10px; border-radius: 5px; text-align: center; margin-bottom: 15px; display: none; }
        
        #btn-fab-history { position: absolute; bottom: 30px; left: 370px; background: #9b59b6; color: white; border: none; padding: 15px 25px; border-radius: 30px; font-size: 1rem; font-weight: bold; cursor: pointer; box-shadow: 0 4px 10px rgba(0,0,0,0.4); z-index: 1000; transition: left 0.4s ease, transform 0.3s; }
        #btn-fab-history:hover { background: #8e44ad; transform: scale(1.05); }
        
        #btn-expand { display: none; position: absolute; top: 90px; left: 10px; z-index: 1000; background: #2c3e50; color: white; border: none; padding: 6px 12px; border-radius: 6px; cursor: pointer; box-shadow: 0 4px 6px rgba(0,0,0,0.3); font-size: 0.9rem; transition: 0.3s; }
        #btn-expand:hover { background: #34495e; transform: scale(1.05); }
        #compact-overlay { display: none; position: absolute; top: 15px; left: 60px; z-index: 1000; gap: 15px; pointer-events: none; }
        .compact-box { background: #2c3e50; color: white; padding: 10px 20px; border-radius: 30px; font-weight: bold; font-size: 0.95rem; box-shadow: 0 4px 10px rgba(0,0,0,0.4); display: flex; align-items: center; gap: 8px; pointer-events: auto; }
        
        .c-conectado { background: #27ae60 !important; }
        .c-pouco { background: #f39c12 !important; }
        .c-perdendo { background: #e67e22 !important; }
        .c-perdida { background: #c0392b !important; }
        .c-restabelecida { background: #2980b9 !important; }

        body.compact-mode #panel { display: none; }
        body.compact-mode #btn-expand { display: block; }
        body.compact-mode #compact-overlay { display: flex; }
        body.compact-mode #btn-fab-history { left: 20px; } 

        .modal-overlay { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.7); z-index: 2000; justify-content: center; align-items: center; }
        .modal-content { background: #2c3e50; color: white; padding: 30px; border-radius: 10px; width: 380px; text-align: center; border-top: 6px solid #9b59b6; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }
        .modal-content h3 { margin-top: 0; }
        .btn-history { width: 100%; margin-top: 10px; padding: 12px; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; transition: 0.2s; font-size: 1rem;}
        .btn-load { background: #27ae60; color: white; margin-top: 20px;}
        .btn-load:hover { background: #2ecc71; }
        .btn-clear { background: #e67e22; color: white; }
        .btn-clear:hover { background: #d35400; }
        .btn-close { background: #e74c3c; color: white; margin-top: 20px;}
        .btn-close:hover { background: #c0392b; }
        select { width: 100%; padding: 10px; margin-top: 10px; background: #ecf0f1; border-radius: 4px; border: none; font-size: 1rem;}
        
        #recovery-toast { position: absolute; bottom: 30px; left: 50%; transform: translateX(-50%); background: #2980b9; color: white; padding: 12px 25px; border-radius: 30px; font-weight: bold; box-shadow: 0 4px 6px rgba(0,0,0,0.3); display: none; z-index: 1000;}

        .transparent-icon { background: transparent; border: none; }
        .radar-dot { width: 16px; height: 16px; background-color: #e74c3c; border-radius: 50%; border: 2px solid white; box-shadow: 0 0 10px rgba(231, 76, 60, 0.8); position: relative; }
        .radar-dot::after { content: ''; width: 100%; height: 100%; border-radius: 50%; background-color: rgba(231, 76, 60, 0.6); position: absolute; top: 0; left: 0; animation: radar-wave 1.5s ease-out infinite; z-index: -1; }
        @keyframes radar-wave { 0% { transform: scale(1); opacity: 1; } 100% { transform: scale(3.5); opacity: 0; } }
    </style>
</head>
<body>
    <button id="btn-expand" onclick="toggleCompactMode()" title="Expandir Painel">▶</button>

    <div id="compact-overlay">
        <div class="compact-box">📍 <span id="compact-latlng">0.00000, 0.00000</span></div>
        <div id="compact-status" class="compact-box">
            <span id="compact-status-text">Aguardando...</span> 
            <span id="compact-uptime" style="margin-left:8px; font-weight:normal; opacity:0.8;">(00:00)</span>
        </div>
    </div>

    <div id="panel">
        <h2>Data Logger</h2>
        <div id="conn-status-box">
            <div class="status-header"><span id="status-dot" class="dot"></span><span id="status-text">Aguardando...</span></div>
            <div id="delta-time">Sinal: -- s</div>
            <div id="uptime-time">Sessão: 00:00:00</div>
        </div>
        <div id="gps-alert" class="alert">Buscando satélites...</div>
        
        <div class="data-box">📍 Posição GPS<span id="latlng">0.00, 0.00</span></div>
        <div class="data-box">📡 Satélites (Fix)
            <!-- Adicionado informação de HDOP ao lado de Satélites -->
            <span><span id="sats">0</span> | HDOP: <span id="hdop">--</span></span>
        </div>
        <div class="data-box">📐 Inércia (Fusão)
            <span>Roll: <b id="roll" style="color:#3498db">0.00°</b></span>
            <span>Pitch: <b id="pitch" style="color:#e67e22">0.00°</b></span>
        </div>
        
        <div style="margin-top:20px; font-size: 0.85em; color: #bdc3c7;">
            <b>Legenda do Mapa:</b><br><br>
            <span style="color:#e74c3c; font-size:1.5em;">■</span> Online (Sessão Ativa)<br>
            <span style="color:#3498db; font-size:1.5em;">■</span> Offline (Recuperado da Memória)<br>
            <span style="color:#9b59b6; font-size:1.5em;">■</span> Consulta de Backup (Dias)
        </div>

        <div style="margin-top: auto; width: 100%; display: flex; justify-content: center; align-items: center; padding-top: 20px; margin-bottom: 25px;">
            <img src="/Arquivos/horus.png" alt="Laboratório HORUS - IFPB" style="max-width: 90%; height: auto; opacity: 0.8;">
        </div>

        <button id="btn-collapse" onclick="toggleCompactMode()" title="Modo Compacto">◀</button>
    </div>

    <div id="map"></div>
    <button id="btn-fab-history" onclick="abrirModal()">🗂️ Acessar Backups</button>
    <div id="recovery-toast">📦 Dados offline sincronizados da Memória RAM!</div>

    <div id="historico-modal" class="modal-overlay">
        <div class="modal-content">
            <h3>🗂️ Banco de Dados (Backup)</h3>
            <p style="font-size: 0.9rem; color: #bdc3c7;">Selecione um arquivo gravado no servidor para visualizar a rota percorrida neste dia.</p>
            <select id="select-historico"><option value="">Carregando arquivos...</option></select>
            <button class="btn-history btn-load" onclick="carregarHistorico()">Plotar Rota Salva</button>
            <button class="btn-history btn-clear" onclick="limparHistoricoVisual()">Ocultar Rota</button>
            <button class="btn-history btn-close" onclick="fecharModal()">Voltar ao Mapa</button>
        </div>
    </div>

    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <script>
        var map = L.map('map', { maxZoom: 22 }).setView([-15.7938, -47.8827], 4);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 22,
            maxNativeZoom: 19 
        }).addTo(map);

        var customPulsingIcon = L.divIcon({
            className: 'transparent-icon', html: '<div class="radar-dot"></div>',
            iconSize: [16, 16], iconAnchor: [8, 8] 
        });

        var marker = null;
        var polyline_online = L.polyline([], {color: '#e74c3c', weight: 4}).addTo(map);
        var polyline_offline = L.polyline([], {color: '#3498db', weight: 4, dashArray: '5, 10'}).addTo(map);
        var polyline_backup = L.polyline([], {color: '#9b59b6', weight: 5, opacity: 0.8}).addTo(map);
        var primeiraLeituraGps = true;
        var socket = io();

        function toggleCompactMode() {
            document.body.classList.toggle('compact-mode');
            setTimeout(function() { map.invalidateSize(); }, 300);
        }

        function abrirModal() {
            document.getElementById('historico-modal').style.display = 'flex';
            socket.emit('solicitar_lista_historicos');
        }
        function fecharModal() { document.getElementById('historico-modal').style.display = 'none'; }

        function formatUptime(totalSeconds) {
            let h = Math.floor(totalSeconds / 3600);
            let m = Math.floor((totalSeconds % 3600) / 60);
            let s = Math.floor(totalSeconds % 60);
            let str = "";
            if (h > 0) str += String(h).padStart(2, '0') + ":";
            str += String(m).padStart(2, '0') + ":" + String(s).padStart(2, '0');
            return str;
        }

        socket.on('atualizar_lista', function(arquivos) {
            var select = document.getElementById('select-historico');
            select.innerHTML = '';
            if(arquivos.length === 0){
                select.innerHTML = '<option value="">Nenhum registro encontrado</option>';
                return;
            }
            arquivos.forEach(function(arq) {
                var opt = document.createElement('option');
                opt.value = arq;
                opt.innerHTML = "📅 Registro: " + arq.replace('.txt', '');
                select.appendChild(opt);
            });
        });

        function carregarHistorico() {
            var val = document.getElementById('select-historico').value;
            if(val) { socket.emit('solicitar_arquivo_historico', val); fecharModal(); }
        }
        function limparHistoricoVisual() { polyline_backup.setLatLngs([]); fecharModal(); }

        socket.on('historico_arquivo_carregado', function(pontos) {
            polyline_backup.setLatLngs(pontos);
            if(pontos.length > 0) map.fitBounds(polyline_backup.getBounds());
            else alert('Arquivo selecionado está vazio ou não possui fix de GPS válido.');
        });

        socket.on('nova_telemetria', function(data) {
            document.getElementById('roll').innerText = data.roll + '°';
            document.getElementById('pitch').innerText = data.pitch + '°';
            document.getElementById('sats').innerText = data.sats;
            document.getElementById('hdop').innerText = data.hdop.toFixed(1);

            if (data.lat !== 0.0 && data.lng !== 0.0) {
                document.getElementById('gps-alert').style.display = 'none';
                var coordText = data.lat.toFixed(5) + ', ' + data.lng.toFixed(5);
                document.getElementById('latlng').innerText = coordText;
                document.getElementById('compact-latlng').innerText = coordText;

                var latlng = [data.lat, data.lng];
                
                if (data.is_new_point) {
                    polyline_online.addLatLng(latlng);
                }

                if (primeiraLeituraGps) {
                    map.setView(latlng, 20); 
                    marker = L.marker(latlng, {icon: customPulsingIcon}).addTo(map);
                    primeiraLeituraGps = false;
                } else { 
                    marker.setLatLng(latlng); 
                }
            } else {
                document.getElementById('gps-alert').style.display = 'block';
            }
        });

        socket.on('historico_recebido', function(pontos) {
            for(var i=0; i<pontos.length; i++){ polyline_offline.addLatLng(pontos[i]); }
            map.fitBounds(polyline_offline.getBounds());
            var toast = document.getElementById('recovery-toast');
            toast.style.display = 'block';
            setTimeout(() => { toast.style.display = 'none'; }, 4000);
        });

        socket.on('status_conexao', function(data) {
            var box = document.getElementById('conn-status-box');
            var compactBox = document.getElementById('compact-status');
            
            document.getElementById('status-text').innerText = data.status;
            document.getElementById('delta-time').innerText = data.delta === '--' ? "Aguardando primeiro pacote" : "Último pacote: " + data.delta + "s atrás";
            document.getElementById('compact-status-text').innerText = data.status;

            var uptimeString = formatUptime(data.uptime);
            document.getElementById('uptime-time').innerText = "Sessão: " + uptimeString;
            document.getElementById('compact-uptime').innerText = "(" + uptimeString + ")";

            box.className = ''; document.getElementById('status-dot').className = 'dot';
            compactBox.className = 'compact-box'; 

            if(data.status === "Conectado") { 
                box.classList.add('status-conectado'); document.getElementById('status-dot').classList.add('pulsing');
                compactBox.classList.add('c-conectado');
            }
            else if(data.status === "Pouco Sinal") { box.classList.add('status-pouco'); compactBox.classList.add('c-pouco'); }
            else if(data.status === "Perdendo Sinal") { box.classList.add('status-perdendo'); compactBox.classList.add('c-perdendo'); }
            else if(data.status === "Conexão Perdida") { box.classList.add('status-perdida'); compactBox.classList.add('c-perdida'); }
            else if(data.status === "Conexão restabelecida") { 
                box.classList.add('status-restabelecida'); document.getElementById('status-dot').classList.add('pulsing');
                compactBox.classList.add('c-restabelecida');
            }
        });

        socket.on('carregar_trajetos_salvos', function(dados) {
            polyline_online.setLatLngs(dados.online);
            polyline_offline.setLatLngs(dados.offline);
            if(dados.online.length > 0) {
                primeiraLeituraGps = false;
                marker = L.marker(dados.online[dados.online.length - 1], {icon: customPulsingIcon}).addTo(map);
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
    print("   Acesso: http://localhost:5000")
    print("================================================\n")
    
    socketio.run(app, host=HOST_IP, port=WEB_PORT, debug=False)