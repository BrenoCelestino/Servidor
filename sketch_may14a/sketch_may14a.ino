#include <WiFi.h>      // Biblioteca nativa do ESP32
#include <WiFiUdp.h>
#include <Wire.h>
#include <TinyGPS++.h>

// ==========================================
// CONFIGURAÇÕES DE PINOS (ESP32)
// ==========================================
#define GPS_RX_PIN 16  // RX2 nativo do ESP32
#define BTN_PIN    4   // Usando GPIO4 para o botão
#define LED_PIN    2   // LED Onboard Azul padrão do ESP32 (Normalmente HIGH = ON)

// ==========================================
// REDE E SERVIDORES
// ==========================================
const char* ssid = "NOME_DA_SUA_REDE";
const char* password = "SENHA_DA_SUA_REDE";
const char* serverIP = "192.168.1.100"; // Mude para o IP do seu PC/Celular
const int udpPort = 8080;               
const int tcpPort = 8081;               

WiFiUDP udp;
WiFiClient tcpClient;

// ==========================================
// MÁQUINA DE ESTADOS E CONTROLE
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

uint32_t btnPressStartTime = 0;
bool btnIsPressed = false;

// ==========================================
// BANCO DE DADOS NA MEMÓRIA RAM (BUFFER CIRCULAR)
// ==========================================
struct OfflineData {
  uint16_t year;
  uint8_t month;
  uint8_t day;
  uint8_t hour;
  uint8_t minute;
  uint8_t second;
  float lat;
  float lng;
  float roll;
  float pitch;
};

// ESP32 tem muita RAM livre. 1800 registros a cada 2s = 1 HORA exata de "Caixa Preta"!
const int MAX_RECORDS = 1800; 
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
    case OFFLINE: return "OFFLINE (Gravando na RAM a 2s)";
    case SENDING_HISTORY: return "SENDING_HISTORY (Despejando RAM via TCP)";
    case ONLINE: return "ONLINE (Enviando UDP a 5Hz)";
    default: return "DESCONHECIDO";
  }
}

// ==========================================
// SETUP
// ==========================================
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n\n=========================================");
  Serial.println("[SISTEMA] Iniciando Data Logger ESP32 (Modo RAM - 1 Hora)");
  Serial.println("=========================================");

  // ESP32: Usando porta Serial 2 via Hardware (Muito mais rápido e estável que SoftwareSerial)
  Serial2.begin(9600, SERIAL_8N1, GPS_RX_PIN, -1);
  
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW); // Apagado
  
  // ESP32 tem resistor de pull-down ativável via software!
  pinMode(BTN_PIN, INPUT_PULLDOWN);     

  Wire.begin();
  Wire.beginTransmission(MPU); Wire.write(0x6B); Wire.write(0); Wire.endTransmission(true);
  Serial.println("[IMU] Modulo MPU6050 inicializado.");

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();

  bootTimer = millis();
  timerIMU = micros();
}

// ==========================================
// LOOP PRINCIPAL
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

// ==========================================
// LEITURA DE SENSORES
// ==========================================
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
    Wire.beginTransmission(MPU); Wire.write(0x3B); Wire.endTransmission(false);
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

// ==========================================
// BOTÃO
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
      Serial.println("\n[BOTAO] Forcando conexao INFINITA.");
      if (currentState == OFFLINE) {
        currentState = SEARCHING;
        searchTimer = millis();
        WiFi.begin(ssid, password);
      }
    } else {
      Serial.println("\n[BOTAO] Voltando ao modo de economia 30s.");
    }
  }
}

// ==========================================
// MÁQUINA DE ESTADOS
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
      if (millis() - serialPrintTimer >= 2000) {
        serialPrintTimer = millis();
        Serial.println("[WIFI] Buscando rede... | Sats Fixados: " + String(gps.satellites.value()));
      }

      if (WiFi.status() == WL_CONNECTED) {
        hasEverConnected = true;
        Serial.println("\n[WIFI] Conectado! IP: " + WiFi.localIP().toString());
        
        if (bufferCount > 0) {
          Serial.println("[RAM] Dados pendentes na memoria (" + String(bufferCount) + " registros). Iniciando envio.");
          currentState = SENDING_HISTORY;
        } else {
          currentState = ONLINE;
        }
      } 
      else if (!forceInfiniteSearch && (millis() - searchTimer >= 30000)) {
        Serial.println("\n[WIFI] Timeout de 30s atingido. Indo para OFFLINE (Memoria RAM).");
        WiFi.disconnect();
        currentState = OFFLINE;
      }
      break;

    case OFFLINE:
      if (millis() - offlineSaveTimer >= 2000) { // A CADA 2 SEGUNDOS
        offlineSaveTimer = millis();
        
        if (gps.location.isValid() && gps.time.isValid()) {
          salvarRAMOffline();
        } else {
          Serial.println("[RAM OFF] Aguardando sinal GPS... Sats: " + String(gps.satellites.value()));
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
  historyBuffer[index].roll = kalAngleX;
  historyBuffer[index].pitch = kalAngleY;

  bufferHead = (bufferHead + 1) % MAX_RECORDS;
  if (bufferCount < MAX_RECORDS) {
    bufferCount++;
  }
  
  Serial.println("[RAM GRAVANDO] Buffer: " + String(bufferCount) + "/" + String(MAX_RECORDS) + " (Sats: " + String(gps.satellites.value()) + ")");
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
  udp.print(payload);
  udp.endPacket();
  
  Serial.println("[UDP TX] " + payload);
}

void enviarHistoricoTCP() {
  Serial.println("\n[TCP] Conectando ao Servidor para despejo de RAM...");
  
  if (tcpClient.connect(serverIP, tcpPort)) {
    Serial.println("[TCP] Conectado! Enviando " + String(bufferCount) + " registros...");
    
    int startIndex = (bufferHead - bufferCount + MAX_RECORDS) % MAX_RECORDS;
    
    for (int i = 0; i < bufferCount; i++) {
      int idx = (startIndex + i) % MAX_RECORDS;
      
      String data = String(historyBuffer[idx].year) + "-" + String(historyBuffer[idx].month) + "-" + String(historyBuffer[idx].day) + "T" +
                    String(historyBuffer[idx].hour) + ":" + String(historyBuffer[idx].minute) + ":" + String(historyBuffer[idx].second) + "Z," +
                    String(historyBuffer[idx].lat, 6) + "," + String(historyBuffer[idx].lng, 6) + "," + 
                    String(historyBuffer[idx].roll, 2) + "," + String(historyBuffer[idx].pitch, 2);
                    
      tcpClient.println(data);
      
      readGPS(); // Mantém o GPS respirando
      delay(2); 
    }
    
    bufferCount = 0;
    bufferHead = 0;
    
    Serial.println("[TCP] Sucesso! Memoria RAM liberada.");
    tcpClient.stop();
  } else {
    Serial.println("[TCP ERRO] Servidor inacessivel! Os dados foram mantidos na RAM.");
  }
  
  currentState = ONLINE; 
}

// ==========================================
// UI DE STATUS LED ONBOARD (ESP32)
// ==========================================
void updateLED() {
  uint32_t t = millis();
  bool ledState = LOW; // ESP32 geralmente acende o LED com HIGH

  switch (currentState) {
    case BOOT_DELAY: ledState = LOW; break;
    case SEARCHING: ledState = (t % 5000 < 4000) ? ((t % 200 < 100) ? HIGH : LOW) : LOW; break;
    case OFFLINE: ledState = (!hasEverConnected) ? ((t % 1000 < 500) ? HIGH : LOW) : ((t % 300 < 150) ? HIGH : LOW); break;
    case SENDING_HISTORY: ledState = HIGH; break;
    case ONLINE: ledState = (t % 2000 < 1000) ? HIGH : LOW; break;
  }
  digitalWrite(LED_PIN, ledState);
}