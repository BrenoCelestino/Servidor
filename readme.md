# 🚀 Data Logger Espacial: Rastreador GPS + IMU

Um sistema profissional de telemetria e aquisição de dados espaciais (Data Logger) com arquitetura **Store and Forward**. Desenvolvido para o monitoramento de variáveis dinâmicas com foco na captação precisa e fundida de geolocalização (GPS) e inércia (Acelerômetro/Giroscópio), capaz de recuperar trajetos perdidos em áreas sem cobertura de rede.

## 📋 Visão Geral do Sistema

O projeto é dividido em duas frentes atuando simultaneamente:
1. **Hardware Edge (Dispositivo):** Processa atitudes inerciais localmente através de um **Filtro de Kalman**, monitora a cobertura satelital e gere seu estado de rede. Se estiver sem conexão Wi-Fi, atua como uma "Caixa Preta", salvando dados sincronizados via relógio atômico em um Cartão SD.
2. **Servidor Base (Dashboard):** Um servidor assíncrono Multi-thread Python que recebe telemetria ao vivo via **UDP** e faz o download de históricos retidos via **TCP**. Conta com um painel interativo (Leaflet/OpenStreetMap) que desenha duas rotas: online (em tempo real) e offline (backfill retroativo).

---

## 🛠️ Hardware Utilizado e Diagrama de Ligações

*   **Placa Controladora:** NodeMCU ESP8266 (Amica CP2102)
*   **Módulo GPS:** Ublox NEO-6M
*   **IMU (Unidade de Medida Inercial):** MPU6050
*   **Armazenamento:** Módulo Leitor de Cartão Micro SD (Interface SPI)
*   **Controle:** Botão Push Button (Normalmente Aberto)

### Tabela de Pinagem (Wiring)

| Componente | Pino do Módulo | Pino NodeMCU | Notas Importantes |
| :--- | :--- | :--- | :--- |
| **MPU6050** | VCC / GND | 3V3 / GND | Barramento I2C Padrão |
| | SCL | **D1** (GPIO5) | |
| | SDA | **D2** (GPIO4) | |
| **Cartão SD** | VCC / GND | Vin(5V) / GND | Barramento SPI Padrão |
| | MISO | **D6** (GPIO12) | |
| | MOSI | **D7** (GPIO13) | |
| | SCK | **D5** (GPIO14) | |
| | CS | **D0** (GPIO16) | *Alocado no D0 para evitar falhas de Boot no ESP8266* |
| **NEO-6M GPS** | VCC / GND | 3V3 / GND | Usar 5V (Vin) se o módulo exigir |
| | TX | **D3** (GPIO0) | O NodeMCU apenas "escuta" o GPS |
| | RX | Desconectado | Não há necessidade de escrita no módulo |
| **Botão (Push)** | Terminal 1 | **D8** (GPIO15) | O D8 possui pull-down nativo (GND) |
| | Terminal 2 | **3V3** | Ativa a porta com nível Lógico HIGH ao apertar |

---

## 🧠 Lógica de Estados e Sinais Visuais (LED Onboard)

O dispositivo **não utiliza LEDs externos**. Toda a comunicação visual com o operador é feita através do minúsculo **LED azul embutido** na placa NodeMCU (ligado ao GPIO2), operando como um painel de diagnóstico em tempo real:

| Estado do Dispositivo | Padrão Visual do LED Onboard | Significado |
| :--- | :--- | :--- |
| **Boot Delay** | 🌑 Totalmente Apagado | Pausa de 5 segundos ao ligar. Aguardando estabilização elétrica do IMU e da antena do GPS. |
| **Buscando Rede** | ⚡ Piscar rápido 4s, apaga 1s | Procurando a rede Wi-Fi configurada. Possui timeout de 30 segundos para poupar bateria. |
| **Offline (Nunca conectou)** | 🔄 Pisca Constantemente (1Hz) | Caiu no timeout sem achar servidor. Operando como gravação cega no Cartão SD (se houver fix de satélite). |
| **Offline (Perdeu conexão)**| ⚠️ Oscilação Rápida (Estroboscópio) | A conexão existia, mas caiu. Dispositivo ativou gravação de emergência no Cartão SD. |
| **Enviando Histórico** | 🔵 Totalmente Aceso | Restaurou rede e encontrou arquivo no SD. Fazendo upload TCP para o servidor. |
| **Online (Telemetria)** | 🫀 Pulso Lento (Heartbeat) | Operação Normal. Enviando dados a 5 vezes por segundo para o dashboard. |

### Função do Botão (Override)
Se o dispositivo passar de 30 segundos procurando rede, ele "desiste" e entra em modo economia de energia (Offline).
*   **Pressionar por 2 Segundos:** Força o dispositivo a ligar a antena e procurar a rede infinitamente até conectar (Ideal para descarregar dados ao voltar para a base). Pressionar novamente cancela o comando.

---

## 💻 Instruções de Instalação e Execução

### Passo 1: Preparando o Servidor Python (Computador)
1. Instale o Python 3 na sua máquina.
2. Abra o terminal e instale as dependências:
   ```bash
   pip install flask flask-socketio
   ```
3. Execute o servidor:
   ```bash
   python servidor.py
   ```
4. O terminal exibirá `🚀 SERVIDOR DE TELEMETRIA ESPACIAL INICIADO!`.
5. Abra o navegador e acesse: `http://localhost:5000`

### Passo 2: Preparando o Hardware (Arduino IDE)
1. Conecte o NodeMCU ao computador via USB.
2. Na Arduino IDE, instale o pacote de placas `ESP8266 by ESP8266 Community`.
3. Instale a biblioteca `TinyGPSPlus` (por Mikal Hart) através do Gerenciador de Bibliotecas.
4. Abra o arquivo `DataLogger_Espacial.ino` e altere as **três variáveis principais** no topo do código:
   ```cpp
   const char* ssid = "NOME_DA_SUA_REDE_WIFI";
   const char* password = "SENHA_DA_SUA_REDE";
   const char* serverIP = "192.168.1.100"; // Substitua pelo IP local do seu Computador
   ```
5. Faça o upload do código para a placa.

---

## ⚙️ Fluxo de Dados e Arquitetura de Rede

Este sistema utiliza duas vias de rede distintas para garantir eficiência máxima:

1. **Tempo Real (A Via UDP - Porta 8080):** 
   Quando online, a placa envia a inércia fundida e o GPS **5 vezes por segundo**. O protocolo UDP foi escolhido pois não exige verificação de entrega, impedindo que o Filtro de Kalman ou a porta serial engarrafem. No painel, esse trajeto desenha a **linha Vermelha Contínua**.
   
2. **Backfill de Histórico (A Via TCP - Porta 8081):**
   Ao conectar (ou reconectar), a placa verifica a existência do arquivo `/historico.txt` no Cartão SD. Caso exista, a placa suspende a telemetria ao vivo e abre uma conexão robusta **TCP**. O arquivo é descarregado 100% no servidor, apagado do SD, e o servidor desenha uma **linha Azul Pontilhada** retroativa no mapa usando a "hora atômica" capturada pelo satélite no momento exato do apagão.
