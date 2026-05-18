# 🚀 Data Logger Espacial: Rastreador GPS + IMU com *Store & Forward*

Um sistema profissional de telemetria e aquisição de dados espaciais focado em extrema precisão. Desenvolvido no **ESP32**, o sistema utiliza fusão de sensores local (Filtro de Kalman) e conta com uma arquitetura avançada de **Store and Forward** em Memória RAM. O dispositivo atua como uma verdadeira "Caixa Preta" durante perdas de sinal, filtrando erros de multicaminho (indoor) e sincronizando trajetos retroativos no mapa base com precisão de relógio atômico.

Projeto desenvolvido com apoio institucional/acadêmico (Laboratório HORUS - IFPB).

## ✨ Principais Funcionalidades

*   **Processamento na Borda (Edge Computing):** Filtro de Kalman rodando a 100Hz no núcleo do ESP32, fundindo dados de aceleração e giroscópio instantaneamente.
*   **Filtros de Precisão Espacial:** 
    *   *(Hardware):* Trava **HDOP (< 3.0)** e limite de Satélites no ESP32 para ignorar sinais ricocheteados em prédios (Multipath Effect).
    *   *(Software):* Filtro Anti-Ziguezague no Python (Média Móvel e Distância de Haversine) para gerar trajetos ultra-suaves típicos de apps como Waze/Strava.
*   **Caixa Preta em RAM (Buffer Circular):** Dispensando módulos SD Card físicos, o sistema utiliza a memória RAM do ESP32 para alocar até **1 HORA contínua** de dados offline (1800 registros a cada 2 segundos). Se excedido, os dados mais antigos são sobrescritos de forma segura.
*   **Dual-Protocol & TCP Retry:** Telemetria ao vivo via **UDP** (5Hz) e despejo de histórico recuperado via **TCP**. Se o servidor estiver fechado, o ESP32 preserva a RAM e tenta enviar novamente a cada 10 segundos!
*   **Dashboard UI/UX Interativo:** Painel web *dark-mode* com Cronômetro de Sessão (Uptime), **Modo Compacto** (Fullscreen), Ícone de Radar Pulsante e um Banco de Dados Local com pop-up para auditar missões do passado.

---

## 🛠️ Hardware e Diagrama de Ligações (Wiring)

Para manter o projeto industrialmente limpo e imune a falhas mecânicas, **não utilizamos módulos SD, LEDs externos ou buzzers**. Toda a sinalização visual é feita pelo LED onboard, e o firmware possui arquitetura *Anti-Crash* (`snprintf` e Watchdog Feeding) para rodar por meses ininterruptos.

*   **Controladora:** DOIT ESP32 DevKit V1 (ou similar)
*   **GPS:** Ublox NEO-6M
*   **IMU:** MPU6050
*   **Controle:** Botão Push Button Simples (Normalmente Aberto)

| Componente | Pino do Módulo | Pino ESP32 | Notas de Engenharia |
| :--- | :--- | :--- | :--- |
| **MPU6050** | VCC / GND | 3V3 / GND | Alimentação Padrão |
| | SDA | **GPIO 21** | Barramento I2C Padrão ESP32 |
| | SCL | **GPIO 22** | Barramento I2C Padrão ESP32 |
| **GPS NEO-6M** | VCC / GND | 3V3 / GND | (Use Vin/5V se seu módulo exigir) |
| | TX | **RX2 (GPIO 16)** | *O ESP32 apenas escuta o GPS via Hardware Serial 2* |
| **Botão (Override)**| Terminal 1 | **GPIO 4** | *Utiliza `INPUT_PULLDOWN` interno via software.* |
| | Terminal 2 | **3V3** | Ao apertar, injeta HIGH lógico no GPIO 4. |

---

## 🧠 Máquina de Estados e Interface Onboard (LED)

O LED Azul embutido (GPIO 2) atua como painel de diagnóstico da máquina:

| Estado da Máquina | Padrão do LED Onboard | Ação Ocorrendo |
| :--- | :--- | :--- |
| **Boot Delay** | 🌑 Apagado | Delay inicial de 5s. Estabilizando tensão do GPS/IMU. |
| **Buscando Rede** | ⚡ Pisca rápido (4s), Apaga (1s) | Tentando conectar. Timeout automático de 60 segundos. |
| **Offline (Standby / RAM)** | 🔄 Pisca Constantemente / Estrobo | Gravando na RAM a 2s (Se HDOP e Fix OK). |
| **Enviando RAM** | 🔵 Totalmente Aceso | Conexão restaurada! Despejando RAM via TCP para o Python. |
| **Online (Ao Vivo)** | 🫀 Pulso Lento (Heartbeat) | Enviando telemetria (UDP 5Hz). Segura envios de GPS nulo. |

**🔘 Override Manual (Botão GPIO 4):** Segurar por 2 segundos inverte a regra do Timeout de economia de energia, forçando o dispositivo a procurar a base Wi-Fi infinitamente até achar (ideal ao retornar para a base de operações).

---

## 💻 Servidor Python e Dashboard Interativo

O Servidor Base é uma aplicação Flask Assíncrona Multi-thread. Pode ser executado em PCs ou **diretamente no Celular** (via Pydroid 3 / Termux) utilizando o Roteador de Bolso (Hotspot) para testes outdoor.

### 🗺️ Legenda Dinâmica do Mapa:
*   🟥 **Linha Vermelha:** Rota Online (Sessão Ativa, desenhada ao vivo via UDP).
*   🟦 **Linha Azul:** Rota Offline (Recuperada do "Apagão" via despejo TCP da RAM do ESP32).
*   🟪 **Linha Roxa:** Consulta de Backup (Rota carregada a partir do banco de dados TXT diário).

### 🚀 Como Iniciar o Sistema

#### 1. Computador / Celular Base (Servidor)
1. Instale as bibliotecas necessárias:
   ```bash
   pip install flask flask-socketio
   ```
2. Execute o servidor:
   ```bash
   python servidor.py
   ```
3. Acesse o painel pelo navegador: `http://localhost:5000`. *(Obs: A pasta `Historico` e a subpasta `/Arquivos/horus.png` serão gerenciadas localmente).*

#### 2. Hardware ESP32 (Arduino IDE)
1. Instale a placa ESP32 no Gerenciador de Placas da Arduino IDE.
2. Instale a biblioteca `TinyGPSPlus`.
3. No arquivo `DataLogger_ESP32.ino`, configure sua rede:
   ```cpp
   const char* ssid = "NOME_DO_WIFI_OU_HOTSPOT";
   const char* password = "SENHA";
   const char* serverIP = "192.168.X.X"; // IP da máquina rodando o Python
   ```
4. Selecione "ESP32 Dev Module", defina a porta COM e faça o Upload. Abra o Monitor Serial (115200 baud) para acompanhar os *logs* em tempo real com informações detalhadas (incluindo o HDOP).

---

## 🗂️ Gestão de Histórico (Auditoria de Rotas)
Toda a operação (seja via pacote UDP ao vivo ou via pacote TCP recuperado) é mesclada e salva no computador dentro da pasta `Historico`, organizada por arquivos diários (Ex: `15-05-2026.txt`). 

No Dashboard, utilize o botão flutuante **(🗂️ Acessar Backups)** para abrir o Modal Suspenso. Ao selecionar uma data, o sistema plota retroativamente o histórico roxo e aplica um *Auto-Zoom Fit* no trajeto para auditoria espacial!