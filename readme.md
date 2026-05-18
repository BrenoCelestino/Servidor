# 🚀 Data Logger Espacial: Rastreador GPS + IMU com *Store & Forward*

Um sistema profissional de telemetria e aquisição de dados espaciais focado em extrema precisão. Desenvolvido no **ESP32**, o sistema utiliza fusão de sensores local (Filtro de Kalman) e conta com uma arquitetura avançada de **Store and Forward** em Memória RAM, capaz de atuar como "Caixa Preta" durante perdas de sinal e sincronizar trajetos retroativos perfeitamente com precisão de relógio atômico.

Desenvolvido com apoio institucional / acadêmico (Laboratório HORUS - IFPB).

## ✨ Principais Funcionalidades

*   **Processamento na Borda (Edge Computing):** O Filtro de Kalman é processado pelo núcleo do ESP32 a 100Hz, fundindo dados de aceleração e giroscópio instantaneamente.
*   **Dual-Protocol Network:** Telemetria ao vivo via **UDP** (sem gargalos, a 5Hz) e despejo de histórico recuperado via **TCP** (com garantia de integridade).
*   **Caixa Preta em RAM (Buffer Circular):** Dispensando o uso de módulos SD Card externos, o sistema utiliza a vasta memória RAM do ESP32 para alocar até **1 HORA contínua** de dados offline (1800 registros a cada 2 segundos). Se o tempo for excedido, os dados mais antigos são sobrescritos para evitar travamentos.
*   **Banco de Dados Perpétuo:** O servidor Python espelha e mescla automaticamente os dados recebidos em arquivos TXT diários na pasta local `Historico`.
*   **Dashboard UI/UX Interativo:** Painel web *dark-mode* com acompanhamento instantâneo, **Modo Compacto** (Fullscreen map) e um Pop-up flutuante para auditoria de viagens passadas gravadas no PC.

---

## 🛠️ Hardware e Diagrama de Ligações (Wiring)

Para manter o projeto industrialmente limpo e imune a falhas mecânicas, **não utilizamos módulos SD, LEDs externos ou buzzers**. Toda a sinalização visual é feita pelo LED onboard do ESP32, e a comunicação GPS utiliza a Porta Serial de Hardware nativa.

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

O LED Azul embutido (GPIO 2) atua como painel de diagnóstico de rede da máquina:

| Estado da Máquina | Padrão do LED Onboard | Ação Ocorrendo |
| :--- | :--- | :--- |
| **Boot Delay** | 🌑 Apagado | Delay inicial de 5s. Estabilizando tensão do GPS/IMU. |
| **Buscando Rede** | ⚡ Pisca rápido (4s), Apaga (1s) | Tentando conectar. Timeout automático de 30 segundos. |
| **Offline (S/ Histórico)** | 🔄 Pisca Constantemente (1Hz) | Modo economia. Gravando na RAM a 2s se houver Fix satelital. |
| **Offline (Caiu a rede)** | ⚠️ Oscilação Rápida (Strobe) | Caiu a rede durante a missão. Gravando na RAM. |
| **Enviando RAM** | 🔵 Totalmente Aceso | Conexão restaurada! Despejando RAM via TCP para o Python. |
| **Online (Ao Vivo)** | 🫀 Pulso Lento (Heartbeat) | Operação Normal. Enviando telemetria em tempo real (UDP 5Hz). |

**🔘 Override Manual (Botão GPIO 4):** Segurar por 2 segundos inverte a regra do Timeout de economia de energia, forçando o dispositivo a procurar a base Wi-Fi infinitamente até achar (ideal ao retornar para a base de operações).

---

## 💻 Servidor Python e Dashboard Interativo

O Servidor Base é uma aplicação Flask Assíncrona Multi-thread que atua em três frentes simultâneas: Escuta UDP, Escuta TCP e gerencia WebSockets para a Interface. Pode ser executado em PCs ou **diretamente no Celular** (via Pydroid 3 / Termux) configurando o Roteador de Bolso (Hotspot).

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
3. Acesse o painel pelo navegador: `http://localhost:5000`. *(Obs: A pasta `Historico` e a subpasta de imagens serão gerenciadas pelo código).*

#### 2. Hardware ESP32 (Arduino IDE)
1. Instale a placa ESP32 no Gerenciador de Placas da Arduino IDE.
2. Instale a biblioteca `TinyGPSPlus`.
3. No arquivo `DataLogger_ESP32.ino`, configure sua rede:
   ```cpp
   const char* ssid = "NOME_DO_WIFI_OU_HOTSPOT";
   const char* password = "SENHA";
   const char* serverIP = "192.168.X.X"; // IP da máquina rodando o Python
   ```
4. Selecione "ESP32 Dev Module", defina a porta COM e faça o Upload. Abra o Monitor Serial (115200 baud) para acompanhar o bootleg e a telemetria do sistema em detalhes.

---
*Projeto arquitetado para altíssima resiliência em campo, eliminando dependência de conexões ininterruptas e armazenamentos mecânicos (SD).*