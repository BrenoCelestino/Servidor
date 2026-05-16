import socket
import json
import threading
import time
import os
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
# GERENCIADOR DE DIRETÓRIO HISTÓRICO E ARQUIVOS
# ==========================================
HISTORICO_DIR = "Historico"
if not os.path.exists(HISTORICO_DIR):
    os.makedirs(HISTORICO_DIR)
    print(f"[*] Diretório de backup '{HISTORICO_DIR}' criado com sucesso.")

def salvar_linha_historico(timestamp_str, lat, lng, roll, pitch):
    try:
        data_parte = timestamp_str.split('T')[0]
        ano, mes, dia = data_parte.split('-')
        filename = f"{int(dia):02d}-{int(mes):02d}-{ano}.txt"
    except:
        filename = datetime.now().strftime('%d-%m-%Y.txt')

    filepath = os.path.join(HISTORICO_DIR, filename)
    linha = f"{timestamp_str},{lat:.6f},{lng:.6f},{roll:.2f},{pitch:.2f}"
    
    with open(filepath, "a", encoding="utf-8") as f:
        f.write(linha + "\n")

# ==========================================
# BANCO DE DADOS EM MEMÓRIA
# ==========================================
trajeto_online = []   
trajeto_offline = []  
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
            roll = telemetria.get('roll', 0.0)
            pitch = telemetria.get('pitch', 0.0)
            
            if lat != 0.0 and lng != 0.0:
                trajeto_online.append([lat, lng])

            timestamp = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
            salvar_linha_historico(timestamp, lat, lng, roll, pitch)

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

        if buffer:
            linhas = buffer.strip().split('\n')
            novos_pontos = []
            
            for linha in linhas:
                partes = linha.strip().split(',')
                if len(partes) >= 5: 
                    try:
                        timestamp = partes[0]
                        lat = float(partes[1])
                        lng = float(partes[2])
                        roll = float(partes[3])
                        pitch = float(partes[4])
                        
                        if lat != 0.0 and lng != 0.0:
                            novos_pontos.append([lat, lng])
                            trajeto_offline.append([lat, lng])
                            
                        salvar_linha_historico(timestamp, lat, lng, roll, pitch)
                    except ValueError:
                        continue
            
            if novos_pontos:
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
            socketio.emit('atualizar_lista', obter_lista_historicos())
            
        socketio.emit('status_conexao', {'status': current_status, 'delta': round(delta, 1)})

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
                if len(partes) >= 3:
                    try:
                        lat = float(partes[1])
                        lng = float(partes[2])
                        if lat != 0.0 and lng != 0.0:
                            pontos.append([lat, lng])
                    except: pass
    socketio.emit('historico_arquivo_carregado', pontos)

# Rota para liberar o acesso da página aos arquivos de imagem na pasta "Arquivos"
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
        #panel { width: 340px; background: #2c3e50; color: white; padding: 20px; box-sizing: border-box; display: flex; flex-direction: column; z-index: 10; overflow-y: auto; box-shadow: 2px 0 10px rgba(0,0,0,0.5);}
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
        
        /* BOTÃO FLUTUANTE (FAB) HISTÓRICO */
        #btn-fab-history {
            position: absolute; bottom: 30px; left: 370px; background: #9b59b6; color: white; border: none;
            padding: 15px 25px; border-radius: 30px; font-size: 1rem; font-weight: bold; cursor: pointer;
            box-shadow: 0 4px 10px rgba(0,0,0,0.4); z-index: 1000; transition: 0.3s;
        }
        #btn-fab-history:hover { background: #8e44ad; transform: scale(1.05); }
        
        /* POP-UP (MODAL) HISTÓRICO */
        .modal-overlay {
            display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(0,0,0,0.7); z-index: 2000; justify-content: center; align-items: center;
        }
        .modal-content {
            background: #2c3e50; color: white; padding: 30px; border-radius: 10px; width: 380px; 
            text-align: center; border-top: 6px solid #9b59b6; box-shadow: 0 10px 25px rgba(0,0,0,0.5);
        }
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
    </style>
</head>
<body>
    <!-- PAINEL LATERAL -->
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
        
        <div style="margin-top:20px; font-size: 0.85em; color: #bdc3c7;">
            <b>Legenda do Mapa:</b><br><br>
            <span style="color:#e74c3c; font-size:1.5em;">■</span> Online (Sessão Ativa)<br>
            <span style="color:#3498db; font-size:1.5em;">■</span> Offline (Recuperado do SD)<br>
            <span style="color:#9b59b6; font-size:1.5em;">■</span> Consulta de Backup (Dias)
        </div>

        <!-- LOGO DA INSTITUIÇÃO -->
        <div style="margin-top: 40px; margin-bottom: 20px; text-align: center;">
            <img src="/Arquivos/horus.png" alt="Laboratório HORUS - IFPB" style="max-width: 90%; height: auto; opacity: 0.8;">
        </div>
    </div>

    <!-- MAPA E ELEMENTOS FLUTUANTES -->
    <div id="map"></div>
    <button id="btn-fab-history" onclick="abrirModal()">🗂️ Acessar Backups</button>
    <div id="recovery-toast">📦 Dados offline recuperados do Cartão SD!</div>

    <!-- MODAL (POP-UP) DE BACKUP -->
    <div id="historico-modal" class="modal-overlay">
        <div class="modal-content">
            <h3>🗂️ Banco de Dados (Backup)</h3>
            <p style="font-size: 0.9rem; color: #bdc3c7;">Selecione um arquivo gravado no servidor para visualizar a rota percorrida neste dia.</p>
            
            <select id="select-historico">
                <option value="">Carregando arquivos...</option>
            </select>
            
            <button class="btn-history btn-load" onclick="carregarHistorico()">Plotar Rota Salva</button>
            <button class="btn-history btn-clear" onclick="limparHistoricoVisual()">Ocultar Rota</button>
            <button class="btn-history btn-close" onclick="fecharModal()">Voltar ao Mapa</button>
        </div>
    </div>

    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <script>
        var map = L.map('map').setView([-15.7938, -47.8827], 4);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png').addTo(map);

        var marker = null;
        var polyline_online = L.polyline([], {color: '#e74c3c', weight: 4}).addTo(map);
        var polyline_offline = L.polyline([], {color: '#3498db', weight: 4, dashArray: '5, 10'}).addTo(map);
        var polyline_backup = L.polyline([], {color: '#9b59b6', weight: 5, opacity: 0.8}).addTo(map);
        var primeiraLeituraGps = true;
        var socket = io();

        // CONTROLE DO MODAL
        function abrirModal() {
            document.getElementById('historico-modal').style.display = 'flex';
            socket.emit('solicitar_lista_historicos');
        }
        function fecharModal() {
            document.getElementById('historico-modal').style.display = 'none';
        }

        // ATUALIZA LISTA DO DROPDOWN
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

        // AÇÕES DOS BOTÕES DO MODAL
        function carregarHistorico() {
            var val = document.getElementById('select-historico').value;
            if(val) { 
                socket.emit('solicitar_arquivo_historico', val); 
                fecharModal(); // Fecha o pop-up para ver o mapa
            }
        }
        function limparHistoricoVisual() {
            polyline_backup.setLatLngs([]);
            fecharModal();
        }

        // DESENHA ROTA DE BACKUP
        socket.on('historico_arquivo_carregado', function(pontos) {
            polyline_backup.setLatLngs(pontos);
            if(pontos.length > 0) {
                map.fitBounds(polyline_backup.getBounds());
            } else {
                alert('Arquivo selecionado está vazio ou não possui fix de GPS válido.');
            }
        });

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
                } else { marker.setLatLng(latlng); }
            } else {
                document.getElementById('gps-alert').style.display = 'block';
            }
        });

        // RECEBE DADOS DO SD (TCP)
        socket.on('historico_recebido', function(pontos) {
            for(var i=0; i<pontos.length; i++){ polyline_offline.addLatLng(pontos[i]); }
            map.fitBounds(polyline_offline.getBounds());
            var toast = document.getElementById('recovery-toast');
            toast.style.display = 'block';
            setTimeout(() => { toast.style.display = 'none'; }, 4000);
        });

        // ATUALIZA STATUS DE CONEXÃO
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

        // AO ABRIR O NAVEGADOR
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