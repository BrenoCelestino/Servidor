# 🚀 Data Logger Espacial: Rastreador GPS + IMU com *Store & Forward*

Um sistema de engenharia e telemetria para aquisição de dados espaciais focado em extrema precisão. Utiliza fusão de sensores local (Filtro de Kalman) e conta com uma arquitetura avançada de **Store and Forward**, capaz de atuar como "Caixa Preta" durante perdas de sinal e sincronizar trajetos retroativos com precisão de relógio atômico.

## ✨ Principais Funcionalidades

*   **Processamento na Borda (Edge Computing):** O Filtro de Kalman é processado diretamente no ESP8266 a 100Hz, fundindo dados de aceleração e giroscópio sem sobrecarregar a rede.
*   **Dual-Protocol Network:** Telemetria ao vivo via **UDP** (sem gargalos) e despejo de histórico recuperado via **TCP** (garantia de entrega).
*   **Store and Forward (Caixa Preta):** Ao perder conexão, salva dados no Cartão SD a 1Hz. Ao reconectar, descarrega e preenche o "apagão" no mapa base.
*   **Banco de Dados Perpétuo:** O servidor Python espelha e mescla automaticamente os dados recebidos (online e atrasados) em arquivos TXT diários na pasta `Historico`.
*   **Dashboard Interativo UI/UX:** Painel web dark-mode com acompanhamento instantâneo e um Pop-up flutuante para auditoria de viagens do passado.

---

## 🛠️ Hardware e Diagrama de Ligações (Wiring)

Para manter o projeto industrialmente enxuto, **não utilizamos LEDs externos ou buzzers**. Toda a sinalização visual é feita pelo LED onboard do ESP8266, e os conflitos de barramento (SPI) foram resolvidos com o mapeamento abaixo:

*   **Controladora:** NodeMCU ESP8266 (Amica CP2102)
*   **GPS:** Ublox NEO-6M
*   **IMU:** MPU6050
*   **Armazenamento:** Módulo Leitor de Cartão Micro SD (SPI)
*   **Controle:** Botão Push Button Simples

| Componente | Pino do Módulo | Pino NodeMCU | Notas de Engenharia |
| :--- | :--- | :--- | :--- |
| **MPU6050** | VCC / GND | 3V3 / GND | Barramento I2C Padrão |
| | SCL / SDA | **D1** / **D2** | |
| **Cartão SD** | VCC / GND | Vin(5V) / GND | Módulos SD operam melhor em 5V |
| | MISO / MOSI | **D6** / **D7** | Barramento SPI |
| | SCK | **D5** | Barramento SPI |
| | CS (Chip Select) | **D0** (GPIO16) | *Alocado no D0 para evitar falha de Boot no ESP* |
| **GPS NEO-6M** | VCC / GND | 3V3 / GND | |
| | TX | **D3** (GPIO0) | *O NodeMCU apenas escuta o GPS (1 via)* |
| **Botão (Override)**| Terminal 1 e 2 | **D8** ➔ **3V3** | *O D8 possui pull-down nativo (Não precisa resistor)* |

---

## 🧠 Máquina de Estados e Interface Onboard (LED)

O LED Azul embutido atua como diagnóstico do sistema:

| Estado da Máquina | Padrão do LED Onboard | Ação Ocorrendo |
| :--- | :--- | :--- |
| **Boot Delay** | 🌑 Apagado | Delay de 5s. Estabilizando tensão do GPS/IMU. |
| **Buscando Rede** | ⚡ Pisca rápido (4s), Apaga (1s) | Tentando conectar. Timeout automático de 30 segundos. |
| **Offline (S/ Histórico)** | 🔄 Pisca Constantemente (1Hz) | Modo economia. Gravando no SD se houver fix satelital. |
| **Offline (Caiu a rede)** | ⚠️ Oscilação Rápida (Strobe) | Caiu a rede durante a missão. Gravando no SD. |
| **Enviando SD** | 🔵 Totalmente Aceso | Restaurou conexão. Despejando SD via TCP para o Python. |
| **Online (Ao Vivo)** | 🫀 Pulso Lento (Heartbeat) | Enviando telemetria em tempo real a 5Hz (UDP). |

**🔘 Override Manual (Botão D8):** Segurar por 2 segundos inverte a regra do Timeout de 30s, forçando o dispositivo a procurar a base Wi-Fi infinitamente até achar (ideal ao retornar para a base de operações).

---

## 💻 Servidor Python e Dashboard Interativo

O Servidor Base é uma aplicação Flask Assíncrona Multi-thread que atua em três frentes: Escuta UDP, Escuta TCP e WebSockets para a Interface.

### Legenda Dinâmica do Mapa:
*   🟥 **Linha Vermelha:** Rota Online (Desenhada ao vivo a 5Hz via UDP).
*   🟦 **Linha Azul Pontilhada:** Rota Recuperada (Buracos de sinal preenchidos pelo TCP após reconexão).
*   🟪 **Linha Roxa (Backup):** Rota do Banco de Dados (Gerada ao consultar um registro do passado no Pop-up).

### Como Iniciar o Sistema

#### 1. Computador / Celular Base (Servidor)
1. Instale as bibliotecas necessárias:
   ```bash
   pip install flask flask-socketio
   ```
2. Execute o servidor:
   ```bash
   python servidor.py
   ```
3. Acesse o painel pelo navegador: `http://localhost:5000`.

#### 2. Hardware (Arduino IDE)
1. Conecte o NodeMCU, abra a IDE a 115200 baud.
2. Instale a biblioteca `TinyGPSPlus`.
3. No arquivo `DataLogger_Espacial.ino`, configure sua rede:
   ```cpp
   const char* ssid = "NOME_DO_WIFI_OU_HOTSPOT";
   const char* password = "SENHA";
   const char* serverIP = "192.168.X.X"; // IP do computador ou celular rodando o Python
   ```
4. Faça o Upload. O Monitor Serial possui um Console Completo detalhando todas as mudanças de modo, 100% legível.

---

## 🗂️ Gestão de Histórico (Auditoria de Rotas)
Toda a operação (seja via pacote UDP ao vivo ou via pacote TCP recuperado) é mesclada e salva no computador dentro da pasta `Historico`, organizada por arquivos diários (Ex: `15-05-2026.txt`). 

No canto inferior do mapa, há um botão flutuante **(🗂️ Acessar Backups)**. Ao clicar, o cenário escurece, um Modal de seleção se abre e você pode carregar missões do passado para avaliar a eficiência geométrica das rotas com um *zoom fit* automático!