/*
 * CD3217-Analyzer — ESP32 standalone unit
 * Milestone 1 SPIKE
 *
 * Brings up a WiFi SoftAP (SSID "cd3217-analyzer") + mDNS (cd3217.local)
 * and serves a web UI at http://cd3217.local that runs a hardware I2C scan
 * of the CD3217 breakout and lists all ACK-ing addresses.
 *
 * This validates the full chain:  web UI -> I2C -> CD3217 chip
 * before any Windows-side (ESP32Adapter) work.
 *
 * Targets: ESP32-S3 (primary), C6, C3, classic ESP32.
 * WiFi creds below are defaults; edit or override with build flags.
 */

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClient.h>
#include <ESPmDNS.h>
#include <WebServer.h>
#include <Wire.h>
#include <ArduinoJson.h>

// ---- Board config (from platformio.ini build flags) --------------------------
#ifndef BOARD_NAME
#define BOARD_NAME "generic-esp32"
#endif
#ifndef I2C_SDA_GPIO
#define I2C_SDA_GPIO 21
#endif
#ifndef I2C_SCL_GPIO
#define I2C_SCL_GPIO 22
#endif

// ---- WiFi ---------------------------------------------------------------
// Default SoftAP profile. Later milestones allow STA join to 192.168.50.0/24.
#ifndef AP_SSID
#define AP_SSID "cd3217-analyzer"
#endif
#ifndef AP_PASS
#define AP_PASS "cd3217analyzer"   // >= 8 chars required by ESP32 SoftAP
#endif
#define MDNS_HOST "cd3217"

WebServer server(80);

static const char index_html[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CD3217 Analyzer</title>
<style>
  body{font-family:system-ui,sans-serif;background:#111;color:#eee;margin:0;padding:16px}
  h1{font-size:20px;margin:0 0 4px}
  .sub{color:#888;font-size:12px;margin-bottom:16px}
  .card{background:#1b1b1b;border:1px solid #333;border-radius:12px;padding:16px;margin-bottom:16px}
  button{background:#2b6cb0;color:#fff;border:0;padding:10px 18px;border-radius:8px;font-size:15px;cursor:pointer}
  button:disabled{background:#444;cursor:not-allowed}
  .row{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #252525}
  .addr{font-family:monospace;color:#7fd0ff}
  .hot{color:#ff9f43}
  .empty{color:#999;font-style:italic}
  #status{color:#7fd0ff;font-family:monospace;font-size:13px;margin-top:12px}
</style>
</head><body>
<h1>CD3217 Analyzer</h1>
<div class="sub">board: __BOARD__ &middot; AP: __AP__ &middot; IP: __IP__</div>

<div class="card">
  <div style="font-weight:600;margin-bottom:8px">I2C bus scan</div>
  <button id="scanBtn" onclick="runScan()">Scan I2C bus</button>
  <div id="scanResults"></div>
  <div id="status">Idle.</div>
</div>

<script>
function showStatus(m){document.getElementById('status').textContent=m;}
async function runScan(){
  const btn=document.getElementById('scanBtn');
  btn.disabled=true; showStatus('Scanning 0x08-0x77 ...');
  const results=document.getElementById('scanResults');
  results.innerHTML='<div class="empty">scanning...</div>';
  try{
    const r=await fetch('/api/scan');
    const j=await r.json();
    if(j.addresses && j.addresses.length){
      let known=j.known||{};
      results.innerHTML='<div class="row"><span>ACK found ('+j.addresses.length+')</span><span style="color:#7fd0ff">KNOWN ACE2</span></div>'
        + j.addresses.map(a=>{
            const ref=known[a]?(' &middot; '+known[a]):'';
            const mark=known[a]?'hot':'addr';
            return '<div class="row"><span class="addr">0x'+a.toString(16).toUpperCase().padStart(2,'0')+'</span>'
                 +'<span class="'+mark+'">'+(known[a]?'ACE2':'-')+'</span></div>';
          }).join('');
      showStatus('Done in '+j.ms+' ms');
    }else{
      results.innerHTML='<div class="empty">No addresses ACKed (0x08-0x77).</div>';
      showStatus('Done.');
    }
  }catch(e){results.innerHTML='<div class="empty">error: '+e+'</div>';showStatus('Error');}
  btn.disabled=false;
}
</script>
</body></html>
)rawliteral";

// Known ACE2 addresses (subset from cd3217_analyzer/registers.py) for the UI.
static const char *knownForAddr(uint8_t addr) {
  switch (addr) {
    case 0x38: return "ACE2 Port1 (GND)";
    case 0x3F: return "ACE2 Port1 (float)";
    case 0x3B: return "ACE2 Port1 OTP";
    case 0x3A: return "ACE2 Port1 OTP";
    case 0x3C: return "ACE2 Port1 OTP";
    case 0x2F: return "ACE2 Port2 (float)";
    case 0x28: return "ACE2 Port2 (GND)";
    case 0x2B: return "ACE2 Port2 OTP";
    case 0x2A: return "ACE2 Port2 OTP";
    default:   return nullptr;
  }
}

static void sendIndex() {
  String html = index_html;
  html.replace("__BOARD__", BOARD_NAME);
  html.replace("__AP__", AP_SSID);
  html.replace("__IP__", WiFi.softAPIP().toString());
  server.send(200, "text/html", html);
}

static void apiScan() {
  JsonDocument doc;
  JsonArray addrs = doc["addresses"].to<JsonArray>();
  JsonObject known = doc["known"].to<JsonObject>();

  uint32_t t0 = millis();
  for (int a = 0x08; a <= 0x77; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) {
      addrs.add(a);
      const char *kn = knownForAddr(a);
      if (kn) known[String(a)] = kn;
    }
  }
  doc["ms"] = millis() - t0;

  String out;
  serializeJson(doc, out);
  server.send(200, "application/json", out);
}

static void apiHealth() {
  JsonDocument doc;
  doc["ok"] = true;
  doc["board"] = BOARD_NAME;
  doc["sda"] = I2C_SDA_GPIO;
  doc["scl"] = I2C_SCL_GPIO;
  doc["ip"] = WiFi.softAPIP().toString();
  doc["ota"] = false;   // M6
  doc["otpstore"] = false;  // M3
  String out;
  serializeJson(doc, out);
  server.send(200, "application/json", out);
}

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.printf("\n[boot] CD3217-Analyzer M1 spike, board=%s\n", BOARD_NAME);
  Serial.printf("[boot] I2C SDA=%d SCL=%d\n", I2C_SDA_GPIO, I2C_SCL_GPIO);

  // ---- I2C (hardware) -------------------------------------------------------
  Wire.begin(I2C_SDA_GPIO, I2C_SCL_GPIO, 100000);
  Serial.println("[i2c] Wire.begin() ok (100kHz)");

  // ---- WiFi SoftAP ----------------------------------------------------------
  WiFi.mode(WIFI_AP);
  bool okAp = WiFi.softAP(AP_SSID, AP_PASS);
  Serial.printf("[wifi] SoftAP %s -> %s\n", okAp ? "OK" : "FAIL", AP_SSID);
  delay(500);
  Serial.printf("[wifi] IP %s\n", WiFi.softAPIP().toString().c_str());

  // ---- mDNS ---------------------------------------------------------------
  MDNS.begin(MDNS_HOST);
  MDNS.addService("http", "tcp", 80);
  Serial.printf("[mdns] http://%s.local\n", MDNS_HOST);

  // ---- HTTP routes ---------------------------------------------------------
  server.on("/", HTTP_GET, sendIndex);
  server.on("/api/scan", HTTP_GET, apiScan);
  server.on("/api/health", HTTP_GET, apiHealth);
  server.begin();
  Serial.println("[http] server up");
}

void loop() {
  server.handleClient();
}
