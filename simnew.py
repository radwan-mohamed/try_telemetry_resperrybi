#!/usr/bin/env python3
import serial
import time
import random
import sys

# === CONFIGURATION ===
AT_PORT   = '/dev/serial0'                   # Pi‐header UART in Pi-mode
BAUD      = 115200                            # or 115200 if that’s what your HAT needs
APN       = 'internet.orange.co.bw'         # your APN
HOST      = 'merlet.alwaysdata.net'         # your server
ENDPOINT  = '/endpoint.php?value='          # GET endpoint
SOCKET_ID = 0

# === SIMULATED SENSORS ===
SENSORS = {
    'RPM':  lambda: random.randint(0, 8000),
    'TEMP': lambda: round(random.uniform(20.0, 100.0), 1),
    'VOLT': lambda: round(random.uniform(0.0, 12.0), 2),
}

# === AT‐COMMAND HELPERS ===
def send_at(ser, cmd, wait=1):
    ser.write((cmd + '\r\n').encode())
    time.sleep(wait)  # small builtin delay to let modem echo
    return ser.read_all().decode(errors='ignore')

def wait_for(ser, keyword, timeout=5):
    deadline = time.time() + timeout
    buf = ''
    while time.time() < deadline:
        buf += ser.read(ser.in_waiting or 1).decode(errors='ignore')
        if keyword in buf:
            return True
    return False

# === MODEM INIT & SOCKET OPEN ===
def init_modem(ser):
    # Basic checks + APN + GPRS
    for cmd, d in [
        ('AT', 0.5), ('ATE0', 0.5),
        ('AT+CPIN?', 0.5), ('AT+CGATT?', 0.5),
        (f'AT+CGDCONT=1,"IP","{APN}"', 1),
        ('AT+NETOPEN', 2), ('AT+IPADDR', 1),
    ]:
        print(send_at(ser, cmd, d), end='')

    # Open one persistent TCP socket
    oc = f'AT+CIPOPEN={SOCKET_ID},"TCP","{HOST}",80'
    ser.write((oc + '\r\n').encode())
    print(f">>> Sent: {oc}")

    # Grab both the immediate OK and the later +CIPOPEN URC
    time.sleep(2)
    im = ser.read_all().decode(errors='ignore')
    time.sleep(3)
    late = ser.read_all().decode(errors='ignore')

    combo = im + late
    if f'+CIPOPEN: {SOCKET_ID},0' in combo:
        print("✅ TCP socket open")
    else:
        print("🚨 Socket open failed; dump:", repr(combo), file=sys.stderr)
        sys.exit(1)

# === SEND ONE READING ===
def send_data(ser, name, val):
    payload = (
        f'GET {ENDPOINT}{name}={val} HTTP/1.1\r\n'
        f'Host: {HOST}\r\n'
        'Connection: keep-alive\r\n'
        '\r\n'
    )
    length = len(payload)

    # clear old bytes, then send CIPSEND
    ser.reset_input_buffer()
    cmd = f'AT+CIPSEND={SOCKET_ID},{length}'
    ser.write((cmd + '\r\n').encode())

    # wait for the '>' prompt
    if not wait_for(ser, '>', 1):
        print(f"⚠️ No '>' for {name}; skipping", file=sys.stderr)
        return

    # fire off the GET in one go
    ser.write(payload.encode())
    print(f"📤 {name}={val}")

    # drop any trailing bytes (no sleep)
    ser.read_all()

# === MAIN LOOP ===
def main():
    try:
        ser = serial.Serial(AT_PORT, BAUD, timeout=0.5)
    except Exception as e:
        print(f"❌ Cannot open {AT_PORT}: {e}", file=sys.stderr)
        sys.exit(1)

    init_modem(ser)
    print("▶️ Sending readings continuously…")

    try:
        while True:
            for name, fn in SENSORS.items():
                send_data(ser, name, fn())
            # *no* sleep here → loops immediately
    except KeyboardInterrupt:
        print("\n✋ Interrupted; closing socket…")
        ser.write(f'AT+CIPCLOSE={SOCKET_ID}\r\n'.encode())
        ser.write(b'AT+NETCLOSE\r\n')
        ser.close()

if __name__ == '__main__':
    main()
