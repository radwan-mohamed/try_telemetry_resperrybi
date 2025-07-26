#!/usr/bin/env python3
import serial
import time
import sys
import json
import traceback

# === CONFIGURATION ===
MODEM_PORT = '/dev/serial0'         # UART to SIM7600 (GPIO pins)
DATA_PORT  = '/dev/ttyUSB0'         # USB serial from STM32
BAUD_MODEM = 115200
BAUD_DATA  = 115200
APN        = 'internet.orange.co.bw'
HOST       = 'merlet.alwaysdata.net'
ENDPOINT   = '/api/data'
SOCKET_ID  = 0

# === AT-COMMAND HELPERS ===
def send_at(ser, cmd, wait=1):
    print(f"📡 Sending AT: {cmd}")
    ser.write((cmd + '\r\n').encode())
    time.sleep(wait)
    resp = ser.read_all().decode(errors='ignore')
    print(f"📩 Response: {resp.strip()}")
    return resp

def wait_for(ser, keyword, timeout=5):
    print(f"⏳ Waiting for '{keyword}' (up to {timeout}s)…")
    deadline = time.time() + timeout
    buf = ''
    while time.time() < deadline:
        buf += ser.read(ser.in_waiting or 1).decode(errors='ignore')
        if keyword in buf:
            print(f"✅ Got '{keyword}'")
            return True
    print(f"❌ Timeout waiting for '{keyword}'")
    return False

# === MODEM INIT & SOCKET OPEN ===
def init_modem(ser):
    print("🔧 Initializing modem…")
    steps = [
        ('AT',       0.5),
        ('ATE0',     0.5),
        ('AT+CPIN?', 0.5),
        ('AT+CGATT?',0.5),
        (f'AT+CGDCONT=1,"IP","{APN}"', 1),
        ('AT+NETOPEN', 2),
        ('AT+IPADDR',  1),
    ]   
    for cmd, w in steps:
        send_at(ser, cmd, w)

    send_at(ser, f'AT+CIPCLOSE={SOCKET_ID}', wait=2)
    # Open TCP socket
    open_cmd = f'AT+CIPOPEN={SOCKET_ID},"TCP","{HOST}",80'
    send_at(ser, open_cmd, wait=5)

    # Check result
    resp = send_at(ser, '', wait=0)  # flush buffer
    if f'+CIPOPEN: {SOCKET_ID},0' in resp:
        print("✅ TCP socket open")
    else:
        print("🚨 Socket open failed:\n", resp)
        sys.exit(1)

# === SEND SENSOR DATA AS JSON ===
def send_json_data(ser, params: dict):
    # Build JSON payload list
    json_list = []
    for k, v in params.items():
        key = k.strip().lower()
        if not v:
            print(f"⚠️ Empty value for '{k}', skipping")
            continue
        try:
            val = float(v)
        except ValueError:
            print(f"⚠️ Invalid number '{v}' for '{k}', skipping")
            continue

        # standardize
        if key == 'temp':
            key = 'temperature'

        json_list.append({"sensor_type": key, "value": val})

    if not json_list:
        print("🤷 No valid data to send.")
        return

    json_str = json.dumps(json_list)
    http = (
        f"POST {ENDPOINT} HTTP/1.1\r\n"
        f"Host: {HOST}\r\n"
        "Connection: keep-alive\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(json_str)}\r\n"
        "\r\n"
        f"{json_str}"
    )
    print(f"\n🌐 Full HTTP payload:\n{http.strip()}")   

    # Send via AT+CIPSEND
    ser.reset_input_buffer()
    cmd = f'AT+CIPSEND={SOCKET_ID},{len(http.encode())}'
    ser.write((cmd + '\r\n').encode())

    if not wait_for(ser, '>', timeout=3):
        print("⚠️ No '>' prompt; skipping send.")
        return

    ser.write(http.encode())
    print(f"📤 Sent JSON: {json_str}")

    time.sleep(1)
    resp = ser.read_all().decode(errors='ignore')
    print(f"🛬 Modem response:\n{resp.strip()}\n")

# === MAIN LOOP ===
def main():
    try:
        print(f"🔌 Opening ports: modem={MODEM_PORT}, sensor={DATA_PORT}")
        modem  = serial.Serial(MODEM_PORT, BAUD_MODEM, timeout=1)
        sensor = serial.Serial(DATA_PORT,  BAUD_DATA,  timeout=1)
    except Exception as e:
        print(f"❌ Could not open serial ports: {e}")
        sys.exit(1)

    init_modem(modem)
    print("\n▶️ Reading sensor data and sending JSON…")

    try:
        while True:
            raw = sensor.readline().decode(errors='ignore').strip()
            if not raw:
                time.sleep(0.2)
                continue

            print(f"\n📨 From STM32: {raw}")
            try:
                parts = [p.split('=') for p in raw.split(',')]
                data = {k.strip(): v.strip() for k, v in parts if len(p) == 2}
                if data:
                    send_json_data(modem, data)
                else:
                    print("⚠️ Malformed line; no key=value pairs found")
            except Exception as e:
                print(f"⚠️ Processing error: {e}")
                traceback.print_exc()

            time.sleep(1)

    except KeyboardInterrupt:
        print("\n✋ Interrupted. Cleaning up…")
    finally:
        send_at(modem, f'AT+CIPCLOSE={SOCKET_ID}', 2)
        send_at(modem, 'AT+NETCLOSE', 1)
        modem.close()
        sensor.close()
        print("✅ Shutdown complete.")

if __name__ == '__main__':
    main()
