/*
 * Датчик двери холодильника для ESP8266 / ESP32.
 *
 * Зачем он нужен: снимать содержимое имеет смысл не по таймеру, а через пару
 * секунд после того, как дверь закрыли. В этот момент рука уже убрана, ничего
 * не качается, а содержимое только что изменилось. При закрытой двери внутри
 * темно, поэтому на время снимка включается светодиодная лента.
 *
 * Схема:
 *   геркон  -> GPIO DOOR_PIN и GND (внутренняя подтяжка к питанию)
 *              замкнут = дверь закрыта
 *   MOSFET  -> GPIO LED_PIN, затвор через резистор 100 Ом,
 *              подтяжка затвора к GND резистором 10 кОм
 *   лента   -> 5 В через MOSFET (ленту берите тёплую белую, 3–5 диодов хватает)
 *
 * Библиотеки: только штатные для ESP8266/ESP32 Arduino Core.
 */

#if defined(ESP32)
#include <HTTPClient.h>
#include <WiFi.h>
#else
#include <ESP8266HTTPClient.h>
#include <ESP8266WiFi.h>
#endif

// ---- настройки -------------------------------------------------------------

const char *WIFI_SSID = "домашний-wifi";
const char *WIFI_PASSWORD = "пароль";

// Адрес сервиса, который запущен на домашнем сервере или Raspberry Pi.
const char *SCAN_URL = "http://192.168.1.10:8000/api/scan";

const int DOOR_PIN = 4;  // геркон
const int LED_PIN = 5;   // затвор MOSFET для подсветки

// Пауза после закрытия двери: даём двери дохлопнуться, а бутылкам — перестать качаться.
const unsigned long SETTLE_MS = 2500;
// Сколько подсветка горит до и во время снимка.
const unsigned long LIGHT_WARMUP_MS = 400;
const unsigned long LIGHT_MAX_MS = 8000;
// Защита от дребезга геркона.
const unsigned long DEBOUNCE_MS = 120;
// Не чаще одного снимка в минуту, даже если дверью хлопают без остановки.
const unsigned long MIN_SCAN_INTERVAL_MS = 60000;

// ---- состояние -------------------------------------------------------------

bool doorClosed = true;
bool waitingToScan = false;
unsigned long lastChangeMs = 0;
unsigned long closedAtMs = 0;
unsigned long lastScanMs = 0;

void setup() {
  Serial.begin(115200);
  pinMode(DOOR_PIN, INPUT_PULLUP);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Подключаюсь к Wi-Fi");
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  Serial.println(WiFi.status() == WL_CONNECTED ? WiFi.localIP().toString() : "Wi-Fi недоступен");

  doorClosed = digitalRead(DOOR_PIN) == LOW;
  closedAtMs = millis();
}

void loop() {
  const unsigned long now = millis();
  const bool closedNow = digitalRead(DOOR_PIN) == LOW;

  if (closedNow != doorClosed && now - lastChangeMs > DEBOUNCE_MS) {
    doorClosed = closedNow;
    lastChangeMs = now;
    Serial.println(doorClosed ? "дверь закрыта" : "дверь открыта");
    if (doorClosed) {
      closedAtMs = now;
      waitingToScan = true;
    } else {
      // Дверь снова открыли — снимок отменяется, кадр всё равно был бы с рукой.
      waitingToScan = false;
      digitalWrite(LED_PIN, LOW);
    }
  }

  if (waitingToScan && doorClosed && now - closedAtMs >= SETTLE_MS) {
    waitingToScan = false;
    if (now - lastScanMs < MIN_SCAN_INTERVAL_MS && lastScanMs != 0) {
      Serial.println("пропускаю: снимали только что");
    } else {
      lastScanMs = now;
      takeSnapshot();
    }
  }

  delay(20);
}

void takeSnapshot() {
  digitalWrite(LED_PIN, HIGH);
  delay(LIGHT_WARMUP_MS);

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("нет Wi-Fi, пробую переподключиться");
    WiFi.reconnect();
    delay(1500);
  }

  const unsigned long startedAt = millis();
  if (WiFi.status() == WL_CONNECTED) {
    WiFiClient client;
    HTTPClient http;
    http.setTimeout(LIGHT_MAX_MS);
    if (http.begin(client, SCAN_URL)) {
      http.addHeader("Content-Type", "application/json");
      const int code = http.POST("{\"source\":\"door\"}");
      Serial.printf("сервер ответил: %d\n", code);
      http.end();
    } else {
      Serial.println("не удалось открыть соединение");
    }
  }

  // Подсветку гасим сразу после ответа, но не держим включённой дольше лимита.
  const unsigned long elapsed = millis() - startedAt;
  if (elapsed < LIGHT_MAX_MS) {
    delay(200);
  }
  digitalWrite(LED_PIN, LOW);
}
