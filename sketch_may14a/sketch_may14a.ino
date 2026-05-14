#include <ESP8266WiFi.h>
#include <WiFiUdp.h>
#include <Wire.h>
#include <SoftwareSerial.h>
#include <TinyGPS++.h>
#include <SPI.h>
#include <SD.h>

// ==========================================
// CONFIGURAÇÕES DE PINOS (HARDWARE REMAP)
// ==========================================
#define SD_CS_PIN  16  // Pino D0 (Chip Select do SD)
#define GPS_RX_PIN 0   // Pino D3 (Lê o TX do GPS)
#define BTN_PIN    15  // Pino D8 (Botão com pull-down nativo)
#define LED_PIN    2   // Pino D4 (LED Onboard Azul, invertido: LOW=ON)

// ==========================================
// REDE E SERVIDORES
// ==========================================
const char* ssid = "NOME_DA_SUA_REDE_WIFI";
const char* password = "SENHA_DA_SUA_REDE";
const char* serverIP = "192.168.1.100"; // IP do Servidor Python / Celular
const int udpPort = 8080;               // Porta para Telemetria Tempo Real
const int tcpPort = 8081;               // Porta para Despejo do Histórico (SD)

WiFiUDP udp;
WiFiClient tcpClient;

// ==========================================
// MÁQUINA DE ESTADOS E CONTROLE
// ==========================================
enum SystemState { BOOT_DELAY, SEARCHING, OFFLINE, SENDING_HISTORY, ONLINE };
SystemState currentState = BOOT_DELAY;
SystemState lastState = BOOT_DELAY; // Para rastrear mudanças no Console Serial

bool hasEverConnected = false;
bool forceInfiniteSearch = false;

uint32_t bootTimer = 0;
uint32_t searchTimer = 0;
uint32_t offlineSaveTimer = 0;
uint32_t onlineSendTimer = 0;
uint32_t serialPrintTimer = 0; // Para prints de debug intervalados

// Variáveis do Botão
uint32_t btnPressStartTime = 0;
bool btnIsPressed = false;

// ==========================================
// GPS E IMU
// ==========================================
TinyGPSPlus gps;
SoftwareSerial gpsSerial(GPS_RX_PIN, -1); // RX no D3, TX desconectado
const int MPU = 0x68;
float accX, accY, accZ, gyroX, gyroY, gyroZ;
uint32_t timerIMU;

class SimpleKalman {
  public:
    float Q_angle = 0.001f, Q_bias = 0.003f, R_measure = 0.03f;
    float angle = 0.0f, bias = 0.0f;
    float P[2][2] = {{0.0f, 0.0f}, {0.0f, 0.0f}};

    float getAngle(float newAngle, float newRate, float dt) {
        float rate = newRate - bias; angle += dt * rate;
        P[0][0] += dt * (dt*P[1][1] - P[0][1] - P[1][0] + Q_angle);
        P[0][1] -= dt * P[1][1]; P[1][0] -= dt * P[1][1]; P[1][1] += Q_bias * dt;
        float S = P[0][0] + R_measure; float K[2] = {P[0][0] / S, P[1][0] / S};
        float y = newAngle - angle; angle += K[0] * y; bias += K[1] * y;
        float P00 = P[0][0], P01 = P[0][1];
        P[0][0] -= K[0] * P00; P[0][1] -= K[0] * P01;
        P[1][0] -= K[1] * P00; P[1][1] -= K[1] * P01;
        return angle;
    }
};
SimpleKalman kalmanRoll, kalmanPitch;
float kalAngleX, kalAngleY;

// ==========================================
// FUNÇÕES AUXILIARES PARA DEBUG SERIAL
// ==========================================
String getStateName(SystemState s) {
  switch(s) {
    case BOOT_DELAY: return "BOOT_DELAY (Estabilizando Sensores)";
    case SEARCHING: return "SEARCHING (Buscando Wi-Fi)";
    case OFFLINE: return "OFFLINE (Monitoramento Interno via SD)";
    case SENDING_HISTORY: return "SENDING_HISTORY (Despejando Cartao SD via TCP)";
    case ONLINE: return "ONLINE (Enviando Telemetria UDP 5Hz)";
    default: return "DESCONHECIDO";
  }
}

// ==========================================
// FUNÇÕES DE HARDWARE INICIAL
// ==========================================
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n\n=========================================");
  Serial.println("[SISTEMA] Iniciando Data Logger Espacial");
  Serial.println("=========================================");

  gpsSerial.begin(9600);
  Serial.println("[GPS] Modulo NEO-6M aguardando conexao...");

  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, HIGH); // Apaga LED
  pinMode(BTN_PIN, INPUT);     

  // Inicializa MPU6050
  Wire.begin();
  Wire.beginTransmission(MPU); Wire.write(0x6B); Wire.write(0); Wire.endTransmission(true);
  Serial.println("[IMU] Modulo MPU6050 (Kalman Filter) inicializado.");

  // Inicializa Cartão SD
  if (!SD.begin(SD_CS_PIN)) {
    Serial.println("[SD] ATENCAO: Falha ao iniciar Cartao SD! (Desconectado/Erro Pinos)");
  } else {
    Serial.println("[SD] Cartao Micro SD inicializado e pronto para Store & Forward.");
  }

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();

  Serial.println("[ESTADO] Entrando em: " + getStateName(currentState));
  
  bootTimer = millis();
  timerIMU = micros();
}

// ==========================================
// LOOP PRINCIPAL (NÃO BLOQUEANTE)
// ==========================================
void loop() {
  readGPS();
  readIMU();
  handleButton();
  updateLED();
  executeStateMachine();
  
  // Imprime MUDANÇA DE ESTADO no console quando ocorrer
  if (currentState != lastState) {
    Serial.println("\n>>> [MUDANCA DE MODO] De: " + getStateName(lastState));
    Serial.println(">>> [MUDANCA DE MODO] Para: " + getStateName(currentState) + "\n");
    lastState = currentState;
  }
}

// ==========================================
// LÓGICA: LEITURA DE SENSORES
// ==========================================
void readGPS() {
  while (gpsSerial.available() > 0) {
    gps.encode(gpsSerial.read());
  }
}

void readIMU() {
  uint32_t dt_us = micros() - timerIMU;
  if (dt_us >= 10000) { // 100Hz
    timerIMU = micros();
    float dt = dt_us / 1000000.0; 
    Wire.beginTransmission(MPU); Wire.write(0x3B); Wire.endTransmission(false);
    Wire.requestFrom(MPU, 14, true); 
    accX = (Wire.read() << 8 | Wire.read()) / 16384.0; accY = (Wire.read() << 8 | Wire.read()) / 16384.0; accZ = (Wire.read() << 8 | Wire.read()) / 16384.0;
    Wire.read(); Wire.read(); // Ignora Temp
    gyroX = (Wire.read() << 8 | Wire.read()) / 131.0; gyroY = (Wire.read() << 8 | Wire.read()) / 131.0; gyroZ = (Wire.read() << 8 | Wire.read()) / 131.0;

    float roll  = atan2(accY, accZ) * RAD_TO_DEG;
    float pitch = atan(-accX / sqrt(accY * accY + accZ * accZ)) * RAD_TO_DEG;
    kalAngleX = kalmanRoll.getAngle(roll, gyroX, dt);
    kalAngleY = kalmanPitch.getAngle(pitch, gyroY, dt);
  }
}

// ==========================================
// LÓGICA: BOTÃO (2 SEGUNDOS)
// ==========================================
void handleButton() {
  bool isHigh = (digitalRead(BTN_PIN) == HIGH);
  if (isHigh && !btnIsPressed) {
    btnIsPressed = true;
    btnPressStartTime = millis();
  } else if (!isHigh && btnIsPressed) {
    btnIsPressed = false;
  }

  if (btnIsPressed && (millis() - btnPressStartTime >= 2000)) {
    forceInfiniteSearch = !forceInfiniteSearch; 
    btnIsPressed = false; 
    
    if (forceInfiniteSearch) {
      Serial.println("\n[MANUAL OVERRIDE] Botao pressionado! Forcando conexao INFINITA.");
      if (currentState == OFFLINE) {
        currentState = SEARCHING;
        searchTimer = millis();
        WiFi.begin(ssid, password);
      }
    } else {
      Serial.println("\n[MANUAL OVERRIDE] Botao pressionado! Voltando ao modo de economia 30s.");
    }
  }
}

// ==========================================
// LÓGICA: MÁQUINA DE ESTADOS
// ==========================================
void executeStateMachine() {
  switch (currentState) {
    
    case BOOT_DELAY:
      if (millis() - bootTimer >= 5000) {
        currentState = SEARCHING;
        searchTimer = millis();
        WiFi.begin(ssid, password);
      }
      break;

    case SEARCHING:
      // Status de Debug a cada 2 segundos no terminal
      if (millis() - serialPrintTimer >= 2000) {
        serialPrintTimer = millis();
        Serial.println("[WIFI] Buscando rede... | Sats Fixados: " + String(gps.satellites.value()));
      }

      if (WiFi.status() == WL_CONNECTED) {
        hasEverConnected = true;
        Serial.println("\n[WIFI] Conectado com Sucesso! IP: " + WiFi.localIP().toString());
        
        if (SD.exists("/historico.txt")) {
          Serial.println("[SD] Arquivo /historico.txt encontrado. Iniciando recuperacao de dados.");
          currentState = SENDING_HISTORY;
        } else {
          Serial.println("[SD] Nenhum trajeto pendente no SD. Entrando em modo Tempo Real.");
          currentState = ONLINE;
        }
      } 
      else if (!forceInfiniteSearch && (millis() - searchTimer >= 30000)) {
        Serial.println("\n[WIFI] Timeout de 30s atingido. Desligando antena para poupar energia.");
        WiFi.disconnect();
        currentState = OFFLINE;
      }
      break;

    case OFFLINE:
      if (millis() - offlineSaveTimer >= 1000) {
        offlineSaveTimer = millis();
        
        // Verifica se o GPS tem sinal para gravar no SD
        if (gps.location.isValid() && gps.time.isValid()) {
          salvarSDOffline();
        } else {
          Serial.println("[SD OFF] Aguardando sinal GPS... Sats: " + String(gps.satellites.value()));
        }
      }
      
      if (WiFi.status() == WL_CONNECTED) {
        Serial.println("\n[WIFI] Conexao retomada espontaneamente!");
        currentState = SD.exists("/historico.txt") ? SENDING_HISTORY : ONLINE;
      }
      break;

    case SENDING_HISTORY:
      enviarHistoricoTCP(); 
      break;

    case ONLINE:
      if (WiFi.status() != WL_CONNECTED) {
        Serial.println("\n[WIFI] CONEXAO PERDIDA! Retomando buscas...");
        currentState = SEARCHING;
        searchTimer = millis();
        forceInfiniteSearch = false; 
        WiFi.begin(ssid, password);
      } else {
        if (millis() - onlineSendTimer >= 200) {
          onlineSendTimer = millis();
          enviarUDP();
        }
      }
      break;
  }
}

// ==========================================
// ROTINAS DE ARMAZENAMENTO E ENVIO
// ==========================================
void salvarSDOffline() {
  File dataFile = SD.open("/historico.txt", FILE_WRITE);
  if (dataFile) {
    String data = String(gps.date.year()) + "-" + String(gps.date.month()) + "-" + String(gps.date.day()) + "T" +
                  String(gps.time.hour()) + ":" + String(gps.time.minute()) + ":" + String(gps.time.second()) + "Z," +
                  String(gps.location.lat(), 6) + "," + String(gps.location.lng(), 6) + "," + 
                  String(kalAngleX, 2) + "," + String(kalAngleY, 2);
    dataFile.println(data);
    dataFile.close();
    Serial.println("[SD GRAVANDO] 1Hz -> Rota isolada salva com sucesso! (Sats: " + String(gps.satellites.value()) + ")");
  } else {
    Serial.println("[SD ERRO] Falha ao tentar escrever no /historico.txt!");
  }
}

void enviarUDP() {
  String payload = "{\"roll\":" + String(kalAngleX, 2) + ",\"pitch\":" + String(kalAngleY, 2);
  if (gps.location.isValid()) {
    payload += ",\"lat\":" + String(gps.location.lat(), 6) + ",\"lng\":" + String(gps.location.lng(), 6) + 
               ",\"sats\":" + String(gps.satellites.value());
  } else {
    payload += ",\"lat\":0.0,\"lng\":0.0,\"sats\":0";
  }
  payload += "}";

  udp.beginPacket(serverIP, udpPort);
  udp.write(payload.c_str());
  udp.endPacket();

  // Print no console (Não polui muito porque está a 115200 baud)
  Serial.println("[UDP TX] " + payload);
}

void enviarHistoricoTCP() {
  Serial.println("\n[TCP] Abrindo conexao com Servidor Base (Porta " + String(tcpPort) + ")...");
  
  if (tcpClient.connect(serverIP, tcpPort)) {
    Serial.println("[TCP] Conectado! Enviando historico de voo/trajeto...");
    
    File dataFile = SD.open("/historico.txt", FILE_READ);
    if (dataFile) {
      while (dataFile.available()) {
        tcpClient.write(dataFile.read());
        readGPS(); // Mantém o buffer do GPS vazio para não travar
      }
      dataFile.close();
      
      SD.remove("/historico.txt"); 
      Serial.println("[TCP] Sucesso! Transferencia completa. SD Card limpo.");
    }
    tcpClient.stop();
  } else {
    Serial.println("[TCP ERRO] Servidor inacessivel! Tentarei novamente mais tarde.");
  }
  
  currentState = ONLINE; 
}

// ==========================================
// LÓGICA: UI DE STATUS LED ONBOARD
// ==========================================
void updateLED() {
  uint32_t t = millis();
  bool ledState = HIGH; 

  switch (currentState) {
    case BOOT_DELAY: ledState = HIGH; break;
    case SEARCHING: ledState = (t % 5000 < 4000) ? ((t % 200 < 100) ? LOW : HIGH) : HIGH; break;
    case OFFLINE: ledState = (!hasEverConnected) ? ((t % 1000 < 500) ? LOW : HIGH) : ((t % 300 < 150) ? LOW : HIGH); break;
    case SENDING_HISTORY: ledState = LOW; break;
    case ONLINE: ledState = (t % 2000 < 1000) ? LOW : HIGH; break;
  }
  digitalWrite(LED_PIN, ledState);
}