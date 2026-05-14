#include <ESP8266WiFi.h>
#include <WiFiUdp.h>
#include <Wire.h>
#include <SoftwareSerial.h>
#include <TinyGPS++.h>

// ==========================================
// CONFIGURAÇÕES DE REDE (WIFI E SERVIDOR)
// ==========================================
const char* ssid = "iotgps";
const char* password = "12345678";

const char* serverIP = "192.168.137.1"; // COLOQUE O IP DO SEU COMPUTADOR NA REDE
const int serverPort = 8080;            // Porta UDP do servidor Python/NodeJS

WiFiUDP udp;

// ==========================================
// CONFIGURAÇÕES DO GPS (NEO-6M)
// ==========================================
static const int RXPin = 12, TXPin = 13; // D6 e D7 no NodeMCU
static const uint32_t GPSBaud = 9600;
TinyGPSPlus gps;
SoftwareSerial gpsSerial(RXPin, TXPin);

// ==========================================
// CONFIGURAÇÕES DO IMU (MPU6050)
// ==========================================
const int MPU = 0x68; // Endereço I2C do MPU6050
float accX, accY, accZ;
float gyroX, gyroY, gyroZ;
float temp;

// Variáveis de tempo para o IMU e envio de dados
uint32_t timerIMU;
uint32_t timerSend;

// ==========================================
// CLASSE DO FILTRO DE KALMAN SIMPLIFICADO
// ==========================================
class SimpleKalman {
  public:
    float Q_angle = 0.001f;
    float Q_bias = 0.003f;
    float R_measure = 0.03f;
    float angle = 0.0f;
    float bias = 0.0f;
    float P[2][2] = {{0.0f, 0.0f}, {0.0f, 0.0f}};

    float getAngle(float newAngle, float newRate, float dt) {
        float rate = newRate - bias;
        angle += dt * rate;

        P[0][0] += dt * (dt*P[1][1] - P[0][1] - P[1][0] + Q_angle);
        P[0][1] -= dt * P[1][1];
        P[1][0] -= dt * P[1][1];
        P[1][1] += Q_bias * dt;

        float S = P[0][0] + R_measure;
        float K[2];
        K[0] = P[0][0] / S;
        K[1] = P[1][0] / S;

        float y = newAngle - angle;
        angle += K[0] * y;
        bias += K[1] * y;

        float P00_temp = P[0][0];
        float P01_temp = P[0][1];

        P[0][0] -= K[0] * P00_temp;
        P[0][1] -= K[0] * P01_temp;
        P[1][0] -= K[1] * P00_temp;
        P[1][1] -= K[1] * P01_temp;

        return angle;
    }
};

SimpleKalman kalmanRoll;
SimpleKalman kalmanPitch;
float kalAngleX, kalAngleY; // Ângulos finais filtrados

void setup() {
  Serial.begin(115200);
  
  // Inicia Serial do GPS
  gpsSerial.begin(GPSBaud);

  // Inicializa I2C (SDA=D2/GPIO4, SCL=D1/GPIO5 padrão do NodeMCU)
  Wire.begin();
  Wire.beginTransmission(MPU);
  Wire.write(0x6B); 
  Wire.write(0); // Acorda o MPU6050
  Wire.endTransmission(true);

  // Conecta ao Wi-Fi
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);
  Serial.print("\nConectando ao WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWiFi Conectado!");
  Serial.print("IP: "); Serial.println(WiFi.localIP());

  timerIMU = micros();
  timerSend = millis();
}

void loop() {
  // 1. LER GPS CONTINUAMENTE (NÃO BLOQUEANTE)
  // Alimenta o objeto do GPS sempre que há dados disponíveis na porta serial virtual.
  // Isso evita o "engarrafamento" e overflow do buffer.
  while (gpsSerial.available() > 0) {
    gps.encode(gpsSerial.read());
  }

  // 2. LER IMU E APLICAR KALMAN (Frequência fixa de ~100Hz)
  uint32_t dt_us = micros() - timerIMU;
  if (dt_us >= 10000) { // 10ms = 100Hz
    timerIMU = micros();
    float dt = (float)dt_us / 1000000.0; // Converte para segundos

    Wire.beginTransmission(MPU);
    Wire.write(0x3B);  // Começa no registro de aceleração
    Wire.endTransmission(false);
    Wire.requestFrom(MPU, 14, true); 

    // Lê Acelerômetro
    accX = (Wire.read() << 8 | Wire.read()) / 16384.0;
    accY = (Wire.read() << 8 | Wire.read()) / 16384.0;
    accZ = (Wire.read() << 8 | Wire.read()) / 16384.0;
    // Pula Temperatura
    temp = (Wire.read() << 8 | Wire.read()); 
    // Lê Giroscópio
    gyroX = (Wire.read() << 8 | Wire.read()) / 131.0;
    gyroY = (Wire.read() << 8 | Wire.read()) / 131.0;
    gyroZ = (Wire.read() << 8 | Wire.read()) / 131.0;

    // Calcula os ângulos brutos do acelerômetro
    float roll  = atan2(accY, accZ) * RAD_TO_DEG;
    float pitch = atan(-accX / sqrt(accY * accY + accZ * accZ)) * RAD_TO_DEG;

    // Aplica Fusão de Dados (Filtro de Kalman)
    kalAngleX = kalmanRoll.getAngle(roll, gyroX, dt);
    kalAngleY = kalmanPitch.getAngle(pitch, gyroY, dt);
  }

  // 3. ENVIAR DADOS VIA TELEMETRIA (Frequência de 5Hz)
  if (millis() - timerSend >= 200) { // Envia a cada 200ms
    timerSend = millis();
    enviarDadosTelemetria();
  }
}

// ==========================================
// FUNÇÃO PARA EMPACOTAR E ENVIAR OS DADOS
// ==========================================
void enviarDadosTelemetria() {
  // Cria um JSON com os dados consolidados
  String payload = "{";
  
  // Dados do IMU (Kalman)
  payload += "\"roll\": " + String(kalAngleX, 2) + ",";
  payload += "\"pitch\": " + String(kalAngleY, 2) + ",";
  
  // Dados do GPS
  if (gps.location.isValid()) {
    payload += "\"lat\": " + String(gps.location.lat(), 6) + ",";
    payload += "\"lng\": " + String(gps.location.lng(), 6) + ",";
    payload += "\"alt\": " + String(gps.altitude.meters(), 2) + ",";
    payload += "\"speed\": " + String(gps.speed.kmph(), 2) + ",";
    payload += "\"sats\": " + String(gps.satellites.value());
  } else {
    // Se o GPS ainda não pegou sinal (fix)
    payload += "\"lat\": 0.0, \"lng\": 0.0, \"alt\": 0.0, \"speed\": 0.0, \"sats\": 0";
  }
  
  payload += "}";

  // Envia via UDP
  udp.beginPacket(serverIP, serverPort);
  udp.write(payload.c_str());
  udp.endPacket();

  // Opcional: Imprimir na Serial para debug local
  // Serial.println(payload);
}