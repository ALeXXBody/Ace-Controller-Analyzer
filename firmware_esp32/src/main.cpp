/*
 * CD3217-Analyzer — ESP32 / RP2040 standalone unit
 *
 * Brings up a WiFi SoftAP (SSID "cd3217-analyzer") + mDNS (cd3217.local)
 * and serves a web UI at http://cd3217.local that runs a hardware I2C scan
 * of the CD3217 breakout and lists all ACK-ing addresses.
 *
 * Boards with WiFi  (ESP32-S3/C3/classic, Pico W, Pico 2 W): the full web UI
 * (I2C scan + SPI flash tools).
 * Wired boards (RP2040-Zero, Pico 1, Pico 2): no WiFi hardware, so they run
 * I2C + SPI over the USB-CDC bridge (see bridge.h / spi_flash.h).
 *
 * This validates the full chain:  web UI -> I2C -> CD3217 chip
 * before any Windows-side (ESP32Adapter / PicoAdapter) work.
 *
 * Board config comes from platformio.ini build flags:
 *   -DCD3217_BOARD=\"...\"  -DI2C_SDA_GPIO=n  -DI2C_SCL_GPIO=n
 *   -DPIN_SPI_SCK=n -DPIN_SPI_MISO=n -DPIN_SPI_MOSI=n -DPIN_SPI_CS=n
 *   -DCD3217_HAS_WIFI (set only for WiFi-capable boards)
 */

#include <Arduino.h>
#include <Wire.h>
#include <ArduinoJson.h>
#include "bridge.h"
#include "spi_flash.h"

#ifdef CD3217_HAS_WIFI
#include <WiFi.h>
#include <WiFiClient.h>
#ifndef ARDUINO_ARCH_RP2040
#include <ESPmDNS.h>      // ESP32 only; Pico core has MDNS via different API
#endif
#include <WebServer.h>
#endif

// ---- Board config (from platformio.ini build flags) --------------------------
#ifndef CD3217_BOARD
#define CD3217_BOARD "generic-mcu"
#endif
#ifndef I2C_SDA_GPIO
#define I2C_SDA_GPIO 20
#endif
#ifndef I2C_SCL_GPIO
#define I2C_SCL_GPIO 21
#endif

// ---- WiFi (only compiled for WiFi-capable boards) ----------------------------
#ifdef CD3217_HAS_WIFI
#ifndef AP_SSID
#define AP_SSID "cd3217-analyzer"
#endif
#ifndef AP_PASS
#define AP_PASS "cd3217analyzer"   // >= 8 chars required by softAP
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
  .tabs{display:flex;gap:6px;margin-bottom:-1px}
  .tab{padding:9px 20px;border-radius:10px 10px 0 0;background:#151515;border:1px solid #333;
       border-bottom:none;cursor:pointer;color:#888;font-size:14px;user-select:none}
  .tab.active{background:#1b1b1b;color:#7fd0ff;border-color:#2b6cb0;border-bottom:1px solid #1b1b1b}
  .card{background:#1b1b1b;border:1px solid #333;border-radius:12px;padding:16px;margin-bottom:16px}
  button{background:#2b6cb0;color:#fff;border:0;padding:10px 18px;border-radius:8px;
         font-size:15px;cursor:pointer;margin:4px 8px 4px 0}
  button:disabled{background:#444;cursor:not-allowed}
  button.danger{background:#a03030}
  input[type=file]{color:#ccc;margin:4px 0;font-size:14px}
  .row{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #252525}
  .addr{font-family:monospace;color:#7fd0ff}
  .hot{color:#ff9f43}
  .empty{color:#999;font-style:italic}
  #status{color:#7fd0ff;font-family:monospace;font-size:13px;margin-top:12px}
  .info{font-family:monospace;font-size:13px;margin-top:8px;min-height:18px;color:#9fd59f}
  .info.err{color:#ff9f43}
  .bar{background:#252525;border-radius:6px;height:14px;margin-top:10px;overflow:hidden}
  .bar>div{background:#2b6cb0;height:100%;width:0%;transition:width .15s}
</style>
</head><body>
<h1>CD3217 Analyzer</h1>
<div class="sub">board: __BOARD__ &middot; AP: __AP__ &middot; IP: __IP__</div>

<div class="tabs">
  <div class="tab active" id="tab-i2c" onclick="showTab('i2c')">I2C Scan</div>
  <div class="tab" id="tab-spi" onclick="showTab('spi')">SPI Flash</div>
</div>

<div id="panel-i2c">
  <div class="card">
    <div style="font-weight:600;margin-bottom:8px">I2C bus scan</div>
    <button id="scanBtn" onclick="runScan()">Scan I2C bus</button>
    <div id="scanResults"></div>
    <div id="status">Idle.</div>
  </div>
</div>

<div id="panel-spi" style="display:none">
  <div class="card">
    <div style="font-weight:600;margin-bottom:8px">SPI flash chip</div>
    <button id="detBtn" onclick="spiDetect()">Detect chip</button>
    <div class="info" id="chipInfo">Not detected.</div>
  </div>
  <div class="card">
    <div style="font-weight:600;margin-bottom:8px">Read chip &rarr; file</div>
    <button id="readBtn" onclick="spiReadAll()" disabled>Read whole chip</button>
    <div class="bar"><div id="readBar"></div></div>
  </div>
  <div class="card">
    <div style="font-weight:600;margin-bottom:8px">Write file &rarr; chip</div>
    <input type="file" id="spiFile"><br>
    <button id="writeBtn" onclick="spiWriteFile()" disabled>Write (erase + program + verify)</button>
    <div class="bar"><div id="writeBar"></div></div>
    <div class="info" id="writeInfo"></div>
  </div>
  <div class="card">
    <div style="font-weight:600;margin-bottom:8px">Erase</div>
    <button class="danger" id="eraseBtn" onclick="spiEraseChip()" disabled>Erase whole chip</button>
    <div class="info" id="eraseInfo"></div>
  </div>
</div>

<script>
function $(id){return document.getElementById(id);}
function setBar(id,pct){$(id).style.width=Math.min(100,pct).toFixed(1)+'%';}
function showStatus(m){$('status').textContent=m;}

function showTab(t){
  $('panel-i2c').style.display = t==='i2c'?'block':'none';
  $('panel-spi').style.display = t==='spi'?'block':'none';
  $('tab-i2c').classList.toggle('active', t==='i2c');
  $('tab-spi').classList.toggle('active', t==='spi');
}

// ── I2C scan ──────────────────────────────────────────────────────────────
async function runScan(){
  const btn=$('scanBtn');
  btn.disabled=true; showStatus('Scanning 0x08-0x77 ...');
  const results=$('scanResults');
  results.innerHTML='<div class="empty">scanning...</div>';
  try{
    const r=await fetch('/api/scan');
    const j=await r.json();
    if(j.addresses && j.addresses.length){
      let known=j.known||{};
      results.innerHTML='<div class="row"><span>ACK found ('+j.addresses.length+')</span><span></span></div>'
        + j.addresses.map(a=>{
            const ref=known[a]?(' &middot; '+known[a]):'';
            const mark=known[a]?'hot':'addr';
            return '<div class="row"><span class="addr">0x'+a.toString(16).toUpperCase().padStart(2,'0')+'</span>'
                 +'<span class="'+mark+'">'+(known[a]?'ACE2':'-')+ref+'</span></div>';
          }).join('');
      showStatus('Done in '+j.ms+' ms');
    }else{
      results.innerHTML='<div class="empty">No addresses ACKed (0x08-0x77).</div>';
      showStatus('Done.');
    }
  }catch(e){results.innerHTML='<div class="empty">error: '+e+'</div>';showStatus('Error');}
  btn.disabled=false;
}

// ── SPI flash ─────────────────────────────────────────────────────────────
const CHIPS = {
  "EF4014":"Winbond W25Q80 (1MB)","EF4015":"Winbond W25Q16 (2MB)",
  "EF4016":"Winbond W25Q32 (4MB)","EF4017":"Winbond W25Q64 (8MB)",
  "EF4018":"Winbond W25Q128 (16MB)",
  "9D4014":"ISSI IS25LP080 (1MB)","9D6014":"ISSI IS25WP080 (1MB)",
  "C84014":"GD25Q80 (1MB)","C84015":"GD25Q16 (2MB)",
  "204014":"Micron M25P80 (1MB)","20BA14":"Micron MT25Q80 (1MB)"
};
let chip=null;   // {mfr,type,cap,size,name}

function hex2(n){return n.toString(16).toUpperCase().padStart(2,'0');}

async function spiDetect(){
  const b=$('detBtn'); b.disabled=true;
  const el=$('chipInfo'); el.classList.remove('err');
  try{
    const j=await (await fetch('/api/spi/jedec')).json();
    if(!j.mfr && !j.type && !j.cap){
      el.textContent='No flash chip detected (ID 00 00 00) — check clip wiring / power.';
      el.classList.add('err'); chip=null;
    }else{
      const id=hex2(j.mfr)+hex2(j.type)+hex2(j.cap);
      const name=CHIPS[id]||('Unknown (0x'+id+')');
      const size=(j.cap>=0x14&&j.cap<=0x19)?(2**j.cap):0;
      chip={mfr:j.mfr,type:j.type,cap:j.cap,size:size,name:name};
      el.textContent='JEDEC '+hex2(j.mfr)+' '+hex2(j.type)+' '+hex2(j.cap)
        +'  ·  '+name+(size?('  ·  '+(size/1048576)+' MB'):'');
    }
  }catch(e){el.textContent='Detect failed: '+e; el.classList.add('err'); chip=null;}
  b.disabled=false;
  const on=!!chip;
  $('readBtn').disabled=!on;
  $('eraseBtn').disabled=!on;
  $('writeBtn').disabled=!on;
}

async function waitIdle(){
  for(let i=0;i<2400;i++){          // up to ~2 min (chip erase)
    const j=await (await fetch('/api/spi/status')).json();
    if(!j.busy) return true;
    await new Promise(r=>setTimeout(r,50));
  }
  return false;
}

async function spiReadAll(){
  if(!chip||!chip.size){$('chipInfo').textContent='Unknown chip size — cannot read all.';return;}
  const btn=$('readBtn'); btn.disabled=true; setBar('readBar',0);
  const total=chip.size, CH=4096;
  const out=new Uint8Array(total);
  try{
    for(let off=0; off<total; off+=CH){
      const len=Math.min(CH,total-off);
      const r=await fetch('/api/spi/read?addr='+off+'&len='+len);
      if(!r.ok) throw new Error('HTTP '+r.status+' at 0x'+off.toString(16));
      out.set(new Uint8Array(await r.arrayBuffer()), off);
      setBar('readBar',(off+len)/total*100);
    }
    const blob=new Blob([out],{type:'application/octet-stream'});
    const a=document.createElement('a');
    a.href=URL.createObjectURL(blob);
    a.download='flash_dump_'+Date.now()+'.bin';
    a.click(); URL.revokeObjectURL(a.href);
    setBar('readBar',100);
  }catch(e){alert('Read failed: '+e); setBar('readBar',0);}
  btn.disabled=false;
}

async function spiWriteFile(){
  const f=$('spiFile').files[0];
  const info=$('writeInfo'); info.classList.remove('err');
  if(!f){info.textContent='Choose a .bin file first.';return;}
  const btn=$('writeBtn'); btn.disabled=true; setBar('writeBar',0);
  try{
    const data=new Uint8Array(await f.arrayBuffer());
    // 1) erase sectors covering the file span
    const sectors=Math.ceil(data.length/4096);
    for(let s=0;s<sectors;s++){
      const r=await fetch('/api/spi/erase?addr='+(s*4096),{method:'POST'});
      if(!r.ok) throw new Error('erase failed @sector '+s);
      if(!await waitIdle()) throw new Error('flash stayed busy (erase)');
      setBar('writeBar',(s+1)/sectors*30);
    }
    // 2) program pages (256 B, hex-encoded body)
    for(let off=0; off<data.length; off+=256){
      const page=data.subarray(off,Math.min(off+256,data.length));
      let hex=''; for(let i=0;i<page.length;i++) hex+=hex2(page[i]);
      const r=await fetch('/api/spi/write?addr='+off,{method:'POST',body:hex});
      if(!r.ok) throw new Error('program failed @0x'+off.toString(16));
      setBar('writeBar',30+(off/data.length)*55);
    }
    // 3) verify
    let bad=0;
    for(let off=0; off<data.length; off+=4096){
      const len=Math.min(4096,data.length-off);
      const buf=new Uint8Array(await (await fetch('/api/spi/read?addr='+off+'&len='+len)).arrayBuffer());
      for(let i=0;i<len;i++) if(buf[i]!==data[off+i]) bad++;
      setBar('writeBar',85+(off/data.length)*15);
    }
    setBar('writeBar',100);
    if(bad){info.textContent='VERIFY FAILED — '+bad+' bytes mismatch.';info.classList.add('err');}
    else info.textContent='OK — wrote '+data.length+' bytes, verified clean.';
  }catch(e){info.textContent='Write failed: '+e;info.classList.add('err');setBar('writeBar',0);}
  btn.disabled=false;
}

async function spiEraseChip(){
  if(!confirm('Erase the ENTIRE flash chip? This cannot be undone.')) return;
  const btn=$('eraseBtn'); btn.disabled=true;
  const info=$('eraseInfo'); info.classList.remove('err');
  try{
    await fetch('/api/spi/erase',{method:'POST'});
    if(!await waitIdle()){info.textContent='Timed out waiting for erase.';info.classList.add('err');}
    else info.textContent='Chip erased.';
  }catch(e){info.textContent='Erase failed: '+e;info.classList.add('err');}
  btn.disabled=false;
}
</script>
</body></html>
)rawliteral";

// Known ACE2 addresses (subset from cd3217_analyzer/registers.py) for the UI.
static const char *knownForAddr(uint8_t addr) {  switch (addr) {
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
  html.replace("__BOARD__", CD3217_BOARD);
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
  doc["board"] = CD3217_BOARD;
  doc["sda"] = I2C_SDA_GPIO;
  doc["scl"] = I2C_SCL_GPIO;
  doc["spi_sck"] = PIN_SPI_SCK;
  doc["spi_miso"] = PIN_SPI_MISO;
  doc["spi_mosi"] = PIN_SPI_MOSI;
  doc["spi_cs"] = PIN_SPI_CS;
  doc["ip"] = WiFi.softAPIP().toString();
  doc["ota"] = false;        // M6
  doc["otpstore"] = false;   // M3
  String out;
  serializeJson(doc, out);
  server.send(200, "application/json", out);
}

// ---- SPI flash endpoints -----------------------------------------------------

static uint8_t hexVal(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return 0xFF;
}

static void apiSpiJedec() {
  uint8_t id[3];
  SpiFlash::jedec(id);
  JsonDocument doc;
  doc["ok"] = true;
  doc["mfr"] = id[0];
  doc["type"] = id[1];
  doc["cap"] = id[2];
  String out;
  serializeJson(doc, out);
  server.send(200, "application/json", out);
}

static void apiSpiRead() {
  uint32_t addr = strtoul(server.arg("addr").c_str(), nullptr, 10);
  uint32_t len = strtoul(server.arg("len").c_str(), nullptr, 10);
  if (len == 0 || len > 4096) {
    server.send(400, "application/json", "{\"ok\":false,\"error\":\"bad len\"}");
    return;
  }
  static uint8_t buf[4096];
  SpiFlash::read(addr, buf, len);
  server.setContentLength(len);
  server.send(200, "application/octet-stream", "");
  server.sendContent((const char *)buf, len);
}

static void apiSpiWrite() {
  uint32_t addr = strtoul(server.arg("addr").c_str(), nullptr, 10);
  String body = server.arg("plain");   // hex-encoded page (≤256 bytes)
  if (body.length() == 0 || (body.length() & 1) || body.length() / 2 > 256) {
    server.send(400, "application/json", "{\"ok\":false,\"error\":\"bad body\"}");
    return;
  }
  static uint8_t buf[256];
  size_t n = body.length() / 2;
  for (size_t i = 0; i < n; i++) {
    uint8_t hi = hexVal(body.charAt(2 * i));
    uint8_t lo = hexVal(body.charAt(2 * i + 1));
    if (hi == 0xFF || lo == 0xFF) {
      server.send(400, "application/json", "{\"ok\":false,\"error\":\"bad hex\"}");
      return;
    }
    buf[i] = (hi << 4) | lo;
  }
  bool ok = SpiFlash::writePage(addr, buf, n);
  server.send(200, "application/json", ok ? "{\"ok\":true}"
                                          : "{\"ok\":false,\"error\":\"write failed\"}");
}

static void apiSpiErase() {
  if (server.hasArg("addr")) {
    SpiFlash::eraseSector(strtoul(server.arg("addr").c_str(), nullptr, 10));
  } else {
    SpiFlash::eraseChip();
  }
  server.send(200, "application/json", "{\"ok\":true}");
}

static void apiSpiStatus() {
  JsonDocument doc;
  doc["busy"] = SpiFlash::busy();
  doc["sr"] = SpiFlash::readStatus();
  String out;
  serializeJson(doc, out);
  server.send(200, "application/json", out);
}
#endif // CD3217_HAS_WIFI

UsbBridge bridge;   // USB-CDC serial bridge (all boards)

void setup() {
  Serial.begin(115200);
  // Arduino-Pico USB-CDC drops ALL output unless the host has asserted DTR.
  // Ignore flow control so the boot banner is always visible and the bridge
  // replies even if the host doesn't pulse DTR on connect.
#ifdef ARDUINO_ARCH_RP2040
  Serial.ignoreFlowControl(true);
#endif
  delay(300);
  Serial.printf("\n[boot] CD3217-Analyzer M1 spike, board=%s\n", CD3217_BOARD);
  Serial.printf("[boot] I2C SDA=%d SCL=%d\n", I2C_SDA_GPIO, I2C_SCL_GPIO);
  Serial.printf("[boot] SPI SCK=%d MISO=%d MOSI=%d CS=%d\n",
                PIN_SPI_SCK, PIN_SPI_MISO, PIN_SPI_MOSI, PIN_SPI_CS);
  bridge.begin();
  Serial.println("[bridge] USB-CDC bridge ready (0x05 INFO / 0x10 SPIXFR / 0x01 SCAN ...)");

  // ---- I2C (hardware) -------------------------------------------------------
#ifdef ARDUINO_ARCH_RP2040
  // RP2040 Arduino core: set pins then begin() (no begin(sda,scl,freq) here).
  Wire.setSDA(I2C_SDA_GPIO);
  Wire.setSCL(I2C_SCL_GPIO);
  Wire.begin();
  Serial.println("[i2c] Wire.setSDA/setSCL/begin() ok (100kHz)");
#else
  Wire.begin(I2C_SDA_GPIO, I2C_SCL_GPIO, 100000);
  Serial.println("[i2c] Wire.begin() ok (100kHz)");
#endif

  // ---- SPI (flash backend) --------------------------------------------------
  SpiFlash::begin();
  Serial.println("[spi] flash backend ready (2MHz mode0)");

#ifdef CD3217_HAS_WIFI
  // ---- WiFi SoftAP ----------------------------------------------------------
  WiFi.mode(WIFI_AP);
  bool okAp = WiFi.softAP(AP_SSID, AP_PASS);
  Serial.printf("[wifi] SoftAP %s -> %s\n", okAp ? "OK" : "FAIL", AP_SSID);
  delay(500);
  Serial.printf("[wifi] IP %s\n", WiFi.softAPIP().toString().c_str());

#ifndef ARDUINO_ARCH_RP2040
  // ---- mDNS (ESP32) ---------------------------------------------------------
  MDNS.begin(MDNS_HOST);
  MDNS.addService("http", "tcp", 80);
  Serial.printf("[mdns] http://%s.local\n", MDNS_HOST);
#else
  // Pico core: mDNS re-enabled in M6/OTA pass (API differs); use IP for now.
  Serial.printf("[mdns] use AP IP (mDNS deferred)\n");
#endif

  // ---- HTTP routes ----------------------------------------------------------
  server.on("/", HTTP_GET, sendIndex);
  server.on("/api/scan", HTTP_GET, apiScan);
  server.on("/api/health", HTTP_GET, apiHealth);
  server.on("/api/spi/jedec", HTTP_GET, apiSpiJedec);
  server.on("/api/spi/read", HTTP_GET, apiSpiRead);
  server.on("/api/spi/write", HTTP_POST, apiSpiWrite);
  server.on("/api/spi/erase", HTTP_POST, apiSpiErase);
  server.on("/api/spi/status", HTTP_GET, apiSpiStatus);
  server.begin();
  Serial.println("[http] server up (I2C scan + SPI flash tools)");
#else
  Serial.println("[wifi] NONE — wired board (USB bridge: I2C + SPI)");
#endif
}

void loop() {
  bridge.poll();                       // USB-CDC bridge (all boards)
#ifdef CD3217_HAS_WIFI
  server.handleClient();
#endif
  delay(5);
}
