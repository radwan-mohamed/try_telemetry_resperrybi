#!/usr/bin/env python3
import serial
import time
import sys
import traceback

# === CONFIGURATION ===
MODEM_PORT = '/dev/serial0'         # UART to SIM7600 (GPIO pins)
DATA_PORT  = '/dev/ttyUSB0'         # USB serial for sensor readings
BAUD_MODEM = 115200
BAUD_DATA  = 115200
APN        = 'internet.orange.co.bw'
HOST       = 'merlet.alwaysdata.net'
ENDPOINT   = '/endpoint.php?'
SOCKET_ID  = 0

# Keys we expect from the sensor (uppercase)
EXPECTED_KEYS = {'SPEED', 'TEMP', 'GEAR', 'FUEL', 'RPM'}

# === AT-COMMAND HELPERS ===
def send_at(ser, cmd, wait=1):
    print(f"📡 Sending AT: {cmd}")
    ser.write((cmd + '\r\n').encode())
    time.sleep(wait)
    resp = ser.read_all().decode(errors='ignore')
    print(f"📩 Response: {resp.strip()}")
    return resp

def wait_for(ser, keyword, timeout=5):
    print(f"⏳ Waiting for '{keyword}' (up to {timeout}s)...")
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
        ('AT',      0.5),
        ('ATE0',    0.5),
        ('AT+CPIN?',0.5),
        ('AT+CGATT?',0.5),
        (f'AT+CGDCONT=1,"IP","{APN}"', 1),
        ('AT+NETOPEN', 2),
        ('AT+IPADDR',  1),
    ]
    for cmd, wait in steps:
        send_at(ser, cmd, wait)

    open_cmd = f'AT+CIPOPEN={SOCKET_ID},"TCP","{HOST}",80'
    send_at(ser, open_cmd, 2)

    # read any extra data
    time.sleep(1)
    combo = ser.read_all().decode(errors='ignore')
    print(f"📶 CIPOPEN response:\n{combo.strip()}")
    if f'+CIPOPEN: {SOCKET_ID},0' in combo:
        print("✅ TCP socket open")
    else:
        print("🚨 Socket open failed, exiting")
        sys.exit(1)

# === SEND A BATCH OF READINGS ===
def send_data(ser, params: dict):
    # build GET request path
    query = '&'.join(f"{k}={v}" for k, v in params.items())
    payload = (
        f"GET {ENDPOINT}{query} HTTP/1.1\r\n"
        f"Host: {HOST}\r\n"
        "Connection: keep-alive\r\n\r\n"
    )
    print(f"\n🌐 Full HTTP payload:\n{payload.strip()}")

    # clear buffer & request send
    ser.reset_input_buffer()
    cmd = f'AT+CIPSEND={SOCKET_ID},{len(payload)}'
    ser.write((cmd + '\r\n').encode())

    # wait for prompt (retry once if necessary)
    if not wait_for(ser, '>', timeout=2):
        print("⚠️ No '>' prompt; retrying once…")
        time.sleep(0.5)
        if not wait_for(ser, '>', timeout=2):
            print("❌ Still no prompt; skipping this batch")
            return

    # send payload
    print("🚀 Sending payload…")
    ser.write(payload.encode())

    # read the response
    time.sleep(1)
    resp = ser.read_all().decode(errors='ignore')
    print(f"🛬 Modem response:\n{resp.strip()}")
    print(f"✅ Sent batch: {params}\n")

# === MAIN LOOP ===
def main():
    try:
        print(f"🔌 Opening ports: modem={MODEM_PORT}, sensor={DATA_PORT}")
        modem  = serial.Serial(MODEM_PORT, BAUD_MODEM, timeout=0.5)
        sensor = serial.Serial(DATA_PORT,  BAUD_DATA,  timeout=0.5)
    except Exception as e:
        print(f"❌ Failed to open serial ports: {e}")
        sys.exit(1)

    init_modem(modem)
    print("▶️ Entering main loop… collecting sensor readings.\n")

    buffer = {}  # holds the latest values

    try:
        while True:
            raw = sensor.readline().decode(errors='ignore').strip()
            if not raw:
                continue

            print(f"🧾 Raw sensor line: {raw}")
            try:
                if '=' not in raw:
                    print("⚠️ Skipping malformed line")
                    continue
                key, val = raw.split('=', 1)
                key = key.strip().upper()
                val = val.strip()

                if key not in EXPECTED_KEYS:
                    print(f"⚠️ Unknown key '{key}'; skipping")
                    continue
                if not val:
                    print(f"⚠️ Empty value for '{key}'; skipping")
                    continue

                buffer[key] = val
                print(f"🔄 Buffer state: {buffer}")

                # once we have them all, send and reset
                if EXPECTED_KEYS.issubset(buffer.keys()):
                    send_data(modem, buffer)
                    buffer.clear()
                    time.sleep(0.5)

            except Exception as e:
                print(f"⚠️ Parse error: {e} | Line: {raw}")
                traceback.print_exc()

    except KeyboardInterrupt:
        print("\n✋ Interrupted by user, shutting down…")

    finally:
        print("🧹 Closing connections…")
        try:
            modem.write(f'AT+CIPCLOSE={SOCKET_ID}\r\n'.encode())
            modem.write(b'AT+NETCLOSE\r\n')
        except:
            pass
        modem.close()
        sensor.close()
        print("✅ Clean exit.")

if __name__ == '__main__':
    main()
