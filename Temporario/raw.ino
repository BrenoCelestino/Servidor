#include <WiFi.h>      
#include <WiFiUdp.h>
#include <Wire.h>
#include <TinyGPS++.h>

// ==========================================
// CONFIGURAÇÕES DE PINOS (ESP32)
// ==========================================
#define GPS_RX_PIN 16  // RX2 de Hardware nativo do ESP32
#define BTN_PIN    4   // GPIO4 (Botão de Override)
#define LED_PIN    2   // LED Onboard Azul

// ==========================================
// REDE E SERVIDORES
// ==========================================
const char* ssid = "NOME_DA_SUA_REDE";
const char* password = "SENHA_DA_SUA_REDE";
const char* serverIP = "192.168.1.100"; // IP do PC ou Celular rodando o Python
const int udpPort = 8080;               // Tempo Real
const int tcpPort = 8081;               // Despejo de Memória

WiFiUDP udp;
WiFiClient tcpClient;

// ==========================================
// MÁQUINA DE ESTADOS E TIMERS
// ==========================================
enum SystemState { BOOT_DELAY, SEARCHING, OFFLINE, SENDING_HISTORY, ONLINE };
SystemState currentState = BOOT_DELAY;
SystemState lastState = BOOT_DELAY; 

bool hasEverConnected = false;
bool forceInfiniteSearch = false;

uint32_t bootTimer = 0;
uint32_t searchTimer = 0;
uint32_t offlineSaveTimer = 0;
uint32_t onlineSendTimer = 0;
uint32_t serialPrintTimer = 0; 
uint32_t tcpRetryTimer = 0; 

uint32_t btnPressStartTime = 0;
bool btnIsPressed = false;

// ==========================================
// BANCO DE DADOS NA MEMÓRIA RAM (EXPANDIDO)
// ==========================================
// A estrutura agora coleta os RAW DATA necessários para o Filtro de Kalman (EKF) no Python
struct OfflineData {
  uint16_t year;
  uint8_t month;
  uint8_t day;
  uint8_t hour;
  uint8_t minute;
  uint8_t second;
  float lat;
  float lng;
  float speed;   // m/s
  float course;  // Graus (Direção)
  float hdop;    // Qualidade do Sinal
  uint8_t sats;  // Número de Satélites
  float accX;    // Aceleração Eixo X (g)
  float accY;    // Aceleração Eixo Y (g)
  float accZ;    // Aceleração Eixo Z (g)
  float roll;    // Ângulo Fundido na Borda
  float pitch;   // Ângulo Fundido na Borda
};

// 1200 registros gravando a cada 2s = 40 MINUTOS EXATOS de "Caixa Preta"
// Gasta aproximadamente 60 KB de RAM (ESP32 suporta com tranquilidade)
const int MAX_RECORDS = 1200; 
OfflineData historyBuffer[MAX_RECORDS];
int bufferHead = 0;  
int bufferCount = 0; 

// ==========================================
// GPS E IMU
// ==========================================
TinyGPSPlus gps;
const int MPU = 0x68;
float accX, accY, accZ, gyroX, gyroY, gyroZ;
uint32_t timerIMU;

// Filtro de Kalman Simples (Usado apenas para Roll/Pitch localmente)
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

String getStateName(SystemState s) {
  switch(s) {
    case BOOT_DELAY: return "BOOT_DELAY (Estabilizando)";
    case SEARCHING: return "SEARCHING (Buscando Wi-Fi)";
    case OFFLINE: return "OFFLINE (Gravando RAM a 2s)";
    case SENDING_HISTORY: return "SENDING_HISTORY (Despejando RAM via TCP)";
    case ONLINE: return "ONLINE (Telemetria a 5Hz)";
    default: return "DESCONHECIDO";
  }
}

// ==========================================
// FILTRO INTELIGENTE DE PRECISÃO
// ==========================================
bool isGpsAccurate() {
  if (!gps.location.isValid()) return false;
  if (gps.satellites.value() < 5) return false;
  // Suspende confiabilidade de coordenada se entrou em prédio (Ricochete/Multipath)
  if (gps.hdop.isValid() && gps.hdop.hdop() > 3.0) return false;
  return true;
}

// ==========================================
// SETUP INICIAL
// ==========================================
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n\n=========================================");
  Serial.println("[SISTEMA] Iniciando Data Logger ESP32 (Raw Data / Fase 1)");
  Serial.println("=========================================");

  Serial2.begin(9600, SERIAL_8N1, GPS_RX_PIN, -1);
  
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW); 
  pinMode(BTN_PIN, INPUT_PULLDOWN);     

  Wire.begin();
  Wire.setTimeOut(150); // Previne congelamento do I2C
  Wire.beginTransmission(MPU); Wire.write(0x6B); Wire.write(0); Wire.endTransmission(true);
  Serial.println("[IMU] Modulo MPU6050 inicializado.");

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();

  bootTimer = millis();
  timerIMU = micros();
}

// ==========================================
// LOOP PRINCIPAL (NON-BLOCKING)
// ==========================================
void loop() {
  readGPS();
  readIMU();
  handleButton();
  updateLED();
  executeStateMachine();
  
  if (currentState != lastState) {
    Serial.println("\n>>> [MUDANCA] De: " + getStateName(lastState));
    Serial.println(">>> [MUDANCA] Para: " + getStateName(currentState) + "\n");
    lastState = currentState;
  }
}

// Lógica de Sensores
void readGPS() {
  while (Serial2.available() > 0) {
    gps.encode(Serial2.read());
  }
}

void readIMU() {
  uint32_t dt_us = micros() - timerIMU;
  if (dt_us >= 10000) { 
    timerIMU = micros();
    float dt = dt_us / 1000000.0; 
    
    Wire.beginTransmission(MPU); Wire.write(0x3B); 
    if(Wire.endTransmission(false) == 0) { 
      Wire.requestFrom(MPU, 14, true); 
      accX = (Wire.read() << 8 | Wire.read()) / 16384.0; accY = (Wire.read() << 8 | Wire.read()) / 16384.0; accZ = (Wire.read() << 8 | Wire.read()) / 16384.0;
      Wire.read(); Wire.read(); 
      gyroX = (Wire.read() << 8 | Wire.read()) / 131.0; gyroY = (Wire.read() << 8 | Wire.read()) / 131.0; gyroZ = (Wire.read() << 8 | Wire.read()) / 131.0;

      float roll  = atan2(accY, accZ) * RAD_TO_DEG;
      float pitch = atan(-accX / sqrt(accY * accY + accZ * accZ)) * RAD_TO_DEG;
      kalAngleX = kalmanRoll.getAngle(roll, gyroX, dt);
      kalAngleY = kalmanPitch.getAngle(pitch, gyroY, dt);
    }
  }
}

// Lógica do Botão (Override de Conexão)
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
      Serial.println("\n[BOTAO] Forcando conexao INFINITA.");
      if (currentState == OFFLINE) {
        currentState = SEARCHING;
        searchTimer = millis();
        WiFi.begin(ssid, password);
      }
    } else {
      Serial.println("\n[BOTAO] Voltando ao modo de economia (Timeout de 60 Segundos).");
    }
  }
}

// ==========================================
// MÁQUINA DE ESTADOS (CÉREBRO)
// ==========================================
void executeStateMachine() {
  switch (currentState) {
    
    case BOOT_DELAY:
      // Espera 5 segundos ao ligar o sistema
      if (millis() - bootTimer >= 5000) {
        currentState = SEARCHING;
        searchTimer = millis();
        WiFi.begin(ssid, password);
      }
      break;

    case SEARCHING:
      if (millis() - serialPrintTimer >= 2000) {
        serialPrintTimer = millis();
        float currentHdop = gps.hdop.isValid() ? gps.hdop.hdop() : 99.9;
        Serial.println("[WIFI] Buscando rede... | Sats: " + String(gps.satellites.value()) + " HDOP: " + String(currentHdop));
      }

      if (WiFi.status() == WL_CONNECTED) {
        hasEverConnected = true;
        Serial.println("\n[WIFI] Conectado! IP: " + WiFi.localIP().toString());
        currentState = (bufferCount > 0) ? SENDING_HISTORY : ONLINE;
      } 
      // Timeout de 60 segundos após ligar ou perder sinal
      else if (!forceInfiniteSearch && (millis() - searchTimer >= 60000)) {
        Serial.println("\n[WIFI] Timeout de 60s atingido.");
        Serial.println("[WIFI] Indo para OFFLINE. Antena desligada para poupar energia.");
        WiFi.disconnect();
        currentState = OFFLINE;
      }
      break;

    case OFFLINE:
      // Gravação 1 vez a cada 2 segundos no Buffer Circular
      if (millis() - offlineSaveTimer >= 2000) { 
        offlineSaveTimer = millis();
        
        if (isGpsAccurate() && gps.time.isValid()) {
          salvarRAMOffline();
        } else {
          float currentHdop = gps.hdop.isValid() ? gps.hdop.hdop() : 99.9;
          Serial.println("[STANDBY] GPS impreciso/Indoor (Sats: " + String(gps.satellites.value()) + " HDOP: " + String(currentHdop) + ")");
        }
      }
      
      if (WiFi.status() == WL_CONNECTED) {
        Serial.println("\n[WIFI] Conexao retomada!");
        currentState = (bufferCount > 0) ? SENDING_HISTORY : ONLINE;
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
        
        // Retry a cada 10 segundos se o Python estiver fechado mas o Wifi conectado
        if (bufferCount > 0 && (millis() - tcpRetryTimer >= 10000)) {
          tcpRetryTimer = millis();
          currentState = SENDING_HISTORY;
        } 
        else {
          // Operação normal em tempo real atualizando 5 vezes por segundo (200ms)
          if (millis() - onlineSendTimer >= 200) {
            onlineSendTimer = millis();
            enviarUDP();
          }
        }
      }
      break;
  }
}

// ==========================================
// ROTINAS DE PACOTE E ENVIO (EXPANDIDAS)
// ==========================================
void salvarRAMOffline() {
  int index = bufferHead;
  
  historyBuffer[index].year = gps.date.year();
  historyBuffer[index].month = gps.date.month();
  historyBuffer[index].day = gps.date.day();
  historyBuffer[index].hour = gps.time.hour();
  historyBuffer[index].minute = gps.time.minute();
  historyBuffer[index].second = gps.time.second();
  
  historyBuffer[index].lat = (float)gps.location.lat();
  historyBuffer[index].lng = (float)gps.location.lng();
  historyBuffer[index].speed = (float)gps.speed.mps();
  historyBuffer[index].course = (float)gps.course.deg();
  historyBuffer[index].hdop = (float)gps.hdop.hdop();
  historyBuffer[index].sats = (uint8_t)gps.satellites.value();
  
  historyBuffer[index].accX = accX;
  historyBuffer[index].accY = accY;
  historyBuffer[index].accZ = accZ;
  historyBuffer[index].roll = kalAngleX;
  historyBuffer[index].pitch = kalAngleY;

  bufferHead = (bufferHead + 1) % MAX_RECORDS;
  if (bufferCount < MAX_RECORDS) bufferCount++;
  
  Serial.println("[RAM GRAVANDO] Raw Data guardado: " + String(bufferCount) + "/" + String(MAX_RECORDS));
}

void enviarUDP() {
  char payload[250]; // Buffer aumentado para caber todas as variáveis
  float hdopVal = gps.hdop.isValid() ? gps.hdop.hdop() : 99.9;
  
  // Se GPS ok, envia pacote de telemetria completo
  if (isGpsAccurate()) {
    snprintf(payload, sizeof(payload), 
      "{\"lat\":%.6f,\"lng\":%.6f,\"spd\":%.2f,\"crs\":%.2f,\"hdop\":%.1f,\"sats\":%d,\"accX\":%.3f,\"accY\":%.3f,\"accZ\":%.3f,\"roll\":%.2f,\"pitch\":%.2f}",
      gps.location.lat(), gps.location.lng(), gps.speed.mps(), gps.course.deg(), 
      hdopVal, gps.satellites.value(), accX, accY, accZ, kalAngleX, kalAngleY);
  } 
  // Se precisão cair, anula GPS mas mantem Inércia/HDOP fluindo
  else {
    snprintf(payload, sizeof(payload), 
      "{\"lat\":0.0,\"lng\":0.0,\"spd\":0.0,\"crs\":0.0,\"hdop\":%.1f,\"sats\":%d,\"accX\":%.3f,\"accY\":%.3f,\"accZ\":%.3f,\"roll\":%.2f,\"pitch\":%.2f}",
      hdopVal, gps.satellites.value(), accX, accY, accZ, kalAngleX, kalAngleY);
  }

  udp.beginPacket(serverIP, udpPort);
  udp.print(payload);
  udp.endPacket();

  Serial.print("[UDP TX] ");
  Serial.println(payload);
}

void enviarHistoricoTCP() {
  Serial.println("\n[TCP] Tentando conectar ao Servidor Python...");
  
  if (tcpClient.connect(serverIP, tcpPort)) {
    int startIndex = (bufferHead - bufferCount + MAX_RECORDS) % MAX_RECORDS;
    char dataLine[200]; 
    
    for (int i = 0; i < bufferCount; i++) {
      int idx = (startIndex + i) % MAX_RECORDS;
      
      // Monta a string CSV com os Raw Data p/ o EKF do Python
      snprintf(dataLine, sizeof(dataLine), 
        "%04d-%02d-%02dT%02d:%02d:%02dZ,%.6f,%.6f,%.2f,%.2f,%.1f,%d,%.3f,%.3f,%.3f,%.2f,%.2f",
        historyBuffer[idx].year, historyBuffer[idx].month, historyBuffer[idx].day,
        historyBuffer[idx].hour, historyBuffer[idx].minute, historyBuffer[idx].second,
        historyBuffer[idx].lat, historyBuffer[idx].lng, historyBuffer[idx].speed,
        historyBuffer[idx].course, historyBuffer[idx].hdop, historyBuffer[idx].sats,
        historyBuffer[idx].accX, historyBuffer[idx].accY, historyBuffer[idx].accZ,
        historyBuffer[idx].roll, historyBuffer[idx].pitch);
                    
      tcpClient.println(dataLine);
      
      readGPS(); 
      yield(); // Alimentador de Watchdog (Anti-crash)
    }
    
    bufferCount = 0; bufferHead = 0;
    Serial.println("[TCP] SUCESSO! Raw Data transmitido. RAM liberada.");
    tcpClient.stop();
  } else {
    Serial.println("[TCP ERRO] Servidor inacessivel. Preservando RAM... Retentativa em 10s.");
  }
  currentState = ONLINE; 
}

// ==========================================
// UI DE STATUS LED ONBOARD
// ==========================================
void updateLED() {
  uint32_t t = millis();
  bool ledState = LOW; // ESP32 acende o LED interno no pino 2 com HIGH
  switch (currentState) {
    case BOOT_DELAY: ledState = LOW; break;
    case SEARCHING: ledState = (t % 5000 < 4000) ? ((t % 200 < 100) ? HIGH : LOW) : LOW; break;
    case OFFLINE: ledState = (!hasEverConnected) ? ((t % 1000 < 500) ? HIGH : LOW) : ((t % 300 < 150) ? HIGH : LOW); break;
    case SENDING_HISTORY: ledState = HIGH; break;
    case ONLINE: ledState = (t % 2000 < 1000) ? HIGH : LOW; break;
  }
  digitalWrite(LED_PIN, ledState);
}