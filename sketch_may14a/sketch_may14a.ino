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
const char* serverIP = "192.168.1.100"; // IP do Servidor Python
const int udpPort = 8080;               // Porta para Telemetria Tempo Real
const int tcpPort = 8081;               // Porta para Despejo do Histórico (SD)

WiFiUDP udp;
WiFiClient tcpClient;

// ==========================================
// MÁQUINA DE ESTADOS E CONTROLE
// ==========================================
enum SystemState { BOOT_DELAY, SEARCHING, OFFLINE, SENDING_HISTORY, ONLINE };
SystemState currentState = BOOT_DELAY;

bool hasEverConnected = false;
bool forceInfiniteSearch = false;

uint32_t bootTimer = 0;
uint32_t searchTimer = 0;
uint32_t offlineSaveTimer = 0;
uint32_t onlineSendTimer = 0;

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
// FUNÇÕES DE HARDWARE INICIAL
// ==========================================
void setup() {
  Serial.begin(115200);
  gpsSerial.begin(9600);

  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, HIGH); // Apaga LED
  pinMode(BTN_PIN, INPUT);     // D8 já tem Pull-Down físico

  // Inicializa MPU6050
  Wire.begin();
  Wire.beginTransmission(MPU); Wire.write(0x6B); Wire.write(0); Wire.endTransmission(true);

  // Inicializa Cartão SD
  if (!SD.begin(SD_CS_PIN)) {
    Serial.println("Erro no Cartao SD!");
  } else {
    Serial.println("Cartao SD Inicializado.");
  }

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();

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

  // Se segurar por 2 segundos
  if (btnIsPressed && (millis() - btnPressStartTime >= 2000)) {
    forceInfiniteSearch = !forceInfiniteSearch; // Inverte o modo
    btnIsPressed = false; // Reseta para não disparar múltiplas vezes
    
    if (forceInfiniteSearch && currentState == OFFLINE) {
      currentState = SEARCHING;
      searchTimer = millis();
      WiFi.begin(ssid, password);
    }
  }
}

// ==========================================
// LÓGICA: MÁQUINA DE ESTADOS
// ==========================================
void executeStateMachine() {
  switch (currentState) {
    
    case BOOT_DELAY:
      // Espera 5 segundos após ligar para compensar pico dos sensores
      if (millis() - bootTimer >= 5000) {
        currentState = SEARCHING;
        searchTimer = millis();
        WiFi.begin(ssid, password);
      }
      break;

    case SEARCHING:
      if (WiFi.status() == WL_CONNECTED) {
        hasEverConnected = true;
        // Se houver arquivo no SD, manda. Senão, vai direto pra Online.
        if (SD.exists("/historico.txt")) {
          currentState = SENDING_HISTORY;
        } else {
          currentState = ONLINE;
        }
      } 
      // Se passar 30 segundos e não conectou (e não foi forçado pelo botão)
      else if (!forceInfiniteSearch && (millis() - searchTimer >= 30000)) {
        WiFi.disconnect();
        currentState = OFFLINE;
      }
      break;

    case OFFLINE:
      // Grava no SD 1 vez por segundo, apenas SE o GPS estiver com cobertura
      if (millis() - offlineSaveTimer >= 1000) {
        offlineSaveTimer = millis();
        if (gps.location.isValid() && gps.time.isValid()) {
          salvarSDOffline();
        }
      }
      // Se por acaso voltar a conectar (ex: se o roteador religar na cara dele)
      if (WiFi.status() == WL_CONNECTED) {
        currentState = SD.exists("/historico.txt") ? SENDING_HISTORY : ONLINE;
      }
      break;

    case SENDING_HISTORY:
      // Envia o SD via TCP (Garante entrega do histórico)
      enviarHistoricoTCP(); 
      break;

    case ONLINE:
      // Se perder a conexão
      if (WiFi.status() != WL_CONNECTED) {
        currentState = SEARCHING;
        searchTimer = millis();
        forceInfiniteSearch = false; // Volta a respeitar os 30s
        WiFi.begin(ssid, password);
      } else {
        // Se conectado, envia 5 vezes por segundo (5Hz) via UDP
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
    // String ISO8601 Date/Time + Dados (Otimizado em formato CSV)
    String data = String(gps.date.year()) + "-" + String(gps.date.month()) + "-" + String(gps.date.day()) + "T" +
                  String(gps.time.hour()) + ":" + String(gps.time.minute()) + ":" + String(gps.time.second()) + "Z," +
                  String(gps.location.lat(), 6) + "," + String(gps.location.lng(), 6) + "," + 
                  String(kalAngleX, 2) + "," + String(kalAngleY, 2);
    dataFile.println(data);
    dataFile.close();
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
}

void enviarHistoricoTCP() {
  if (tcpClient.connect(serverIP, tcpPort)) {
    File dataFile = SD.open("/historico.txt", FILE_READ);
    if (dataFile) {
      while (dataFile.available()) {
        tcpClient.write(dataFile.read());
        readGPS(); // Mantém o buffer do GPS vazio enquanto faz o upload
      }
      dataFile.close();
      SD.remove("/historico.txt"); // Limpa o cartão após envio bem sucedido
    }
    tcpClient.stop();
  }
  currentState = ONLINE; // Terminou, vai para o tempo real
}

// ==========================================
// LÓGICA: UI DE STATUS LED ONBOARD
// ==========================================
void updateLED() {
  uint32_t t = millis();
  bool ledState = HIGH; // HIGH = Apagado, LOW = Aceso no NodeMCU

  switch (currentState) {
    case BOOT_DELAY:
      ledState = HIGH; // Apagado
      break;

    case SEARCHING:
      // Piscar rápido 4s, parar 1s
      if (t % 5000 < 4000) {
        ledState = (t % 200 < 100) ? LOW : HIGH;
      } else {
        ledState = HIGH;
      }
      break;

    case OFFLINE:
      if (!hasEverConnected) {
        // Piscar constantemente
        ledState = (t % 1000 < 500) ? LOW : HIGH;
      } else {
        // Oscilar rápido (Lost connection)
        ledState = (t % 300 < 150) ? LOW : HIGH;
      }
      break;

    case SENDING_HISTORY:
      ledState = LOW; // Fica Aceso direto enquanto faz o upload do arquivo
      break;

    case ONLINE:
      // Oscilando devagar (Heartbeat suave)
      ledState = (t % 2000 < 1000) ? LOW : HIGH;
      break;
  }
  digitalWrite(LED_PIN, ledState);
}