#!/usr/bin/env python3
import serial
import time
import sys

# === CONFIGURATION ===
MODEM_PORT = '/dev/serial0'         # UART to SIM7600 (GPIO pins)
DATA_PORT  = '/dev/ttyUSB5'         # USB serial for sensor readings
BAUD_MODEM = 115200
BAUD_DATA  = 115200
APN       = 'internet.orange.co.bw'
HOST      = 'merlet.alwaysdata.net'
ENDPOINT  = '/endpoint.php?'
SOCKET_ID = 0

# === AT-COMMAND HELPERS ===        
def send_at(ser, cmd, wait=1):
    print(f"📡 Sending AT: {cmd}")
    ser.write((cmd + '\r\n').encode())
    time.sleep(wait)
    response = ser.read_all().decode(errors='ignore')
    print(f"📩 Response: {response}")
    return response

def wait_for(ser, keyword, timeout=5):
    deadline = time.time() + timeout
    buf = ''
    while time.time() < deadline:
        buf += ser.read(ser.in_waiting or 1).decode(errors='ignore')
        if keyword in buf:
            return True
    return False

# === MODEM INIT & TCP SOCKET OPEN ===
def init_modem(ser):
    for cmd, d in [
        ('AT', 0.5), ('ATE0', 0.5),
        ('AT+CPIN?', 0.5), ('AT+CGATT?', 0.5),
        (f'AT+CGDCONT=1,"IP","{APN}"', 1),
        ('AT+NETOPEN', 2), ('AT+IPADDR', 1),
    ]:
        print(send_at(ser, cmd, d), end='')

    oc = f'AT+CIPOPEN={SOCKET_ID},"TCP","{HOST}",80'
    ser.write((oc + '\r\n').encode())
    print(f">>> Sent: {oc}")

    time.sleep(2)
    im = ser.read_all().decode(errors='ignore')
    time.sleep(3)
    late = ser.read_all().decode(errors='ignore')
    combo = im + late

    if f'+CIPOPEN: {SOCKET_ID},0' in combo:
        print("✅ TCP socket open")
    else:
        print("🚨 Socket open failed:\n", combo)
        sys.exit(1)

# === SEND ONE READING SET ===
def send_data(ser, params: dict):
    payload = (
        f"GET {ENDPOINT}" +
        '&'.join([f"{k}={v}" for k, v in params.items()]) +
        " HTTP/1.1\r\n" +
        f"Host: {HOST}\r\n" +
        "Connection: keep-alive\r\n\r\n"
    )
    print(f"🌐 Full HTTP payload:\n{payload}")
    length = len(payload)

    ser.reset_input_buffer()
    cmd = f'AT+CIPSEND={SOCKET_ID},{length}'
    ser.write((cmd + '\r\n').encode())

    if not wait_for(ser, '>', 1):
        print("⚠️ No '>' prompt; skipping")
        return

    ser.write(payload.encode())
    print(f"📤 Sent: {params}")
    ser.read_all()
    response = ser.read_all().decode(errors='ignore')
    print(f"🛬 Modem response after send: {response}")

# === MAIN LOOP ===
def main():
    try:
        modem = serial.Serial(MODEM_PORT, BAUD_MODEM, timeout=0.5)
        sensor = serial.Serial(DATA_PORT, BAUD_DATA, timeout=0.5)
    except Exception as e:
        print(f"❌ Serial open error: {e}")
        sys.exit(1)

    init_modem(modem)
    print("▶️ Reading sensor data and sending to server...")

    try:
        while True:
            line = sensor.readline().decode(errors='ignore').strip()
            if not line:
                continue

            try:
                # Example: "speed=50,temp=36.2,gear=3,fuel=80,rpm=3200"
                entries = [x.split('=') for x in line.split(',')]
                data = {k.strip().upper(): v.strip() for k, v in entries}
                send_data(modem, data)
            except Exception as e:
                print(f"⚠️ Parse error: {e} | Line: {line}")

    except KeyboardInterrupt:
        print("\n✋ Ctrl+C detected, closing...")
    finally:
        modem.write(f'AT+CIPCLOSE={SOCKET_ID}\r\n'.encode())
        modem.write(b'AT+NETCLOSE\r\n')
        modem.close()
        sensor.close()

if __name__ == '__main__':
    main()
