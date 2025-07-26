#!/usr/bin/env python3
import serial
import time
import sys
import json

# === CONFIGURATION ===
MODEM_PORT = '/dev/serial0'         # UART to SIM7600 (GPIO pins)
DATA_PORT  = '/dev/ttyUSB5'         # Updated: USB serial from STM32
BAUD_MODEM = 115200
BAUD_DATA  = 115200
APN       = 'internet.orange.co.bw' # Your carrier's APN
HOST      = 'merlet.alwaysdata.net'
ENDPOINT  = '/api/data'             # Updated: The endpoint for our Node.js server
SOCKET_ID = 0

# === AT-COMMAND HELPERS ===
def send_at(ser, cmd, wait=1):
    """Sends an AT command and returns the response."""
    print(f"📡 Sending AT: {cmd}")
    ser.write((cmd + '\r\n').encode())
    time.sleep(wait)
    response = ser.read_all().decode(errors='ignore')
    print(f"📩 Response: {response}")
    return response

def wait_for(ser, keyword, timeout=5):
    """Waits for a specific keyword in the serial buffer."""
    deadline = time.time() + timeout
    buf = ''
    while time.time() < deadline:
        buf += ser.read(ser.in_waiting or 1).decode(errors='ignore')
        if keyword in buf:
            return True
    print(f"⏰ Timed out waiting for '{keyword}'")
    return False

# === MODEM INIT & TCP SOCKET OPEN ===
def init_modem(ser):
    """Initializes the modem by checking each step and opens a TCP socket."""
    print("--- Modem Initialization ---")
    
    # Check basic communication and SIM status
    send_at(ser, "ATE0") # Echo off
        
    response = send_at(ser, "AT+CPIN?")
    if "+CPIN: READY" not in response:
        print("🚨 SIM card not ready. Check SIM card.")
        print(f"Response was: {response}")
        sys.exit(1)
    print("✅ SIM Ready.")

    # Check GPRS/LTE network attachment
    # It might take a few tries to attach
    for i in range(3):
        response = send_at(ser, "AT+CGATT?")
        if "+CGATT: 1" in response:
            print("✅ Attached to GPRS/LTE network.")
            break
        print(f"⏳ Not attached to network yet (attempt {i+1}/3), waiting...")
        time.sleep(3)
    else: # This else belongs to the for loop, runs if loop finishes without break
        print("🚨 Failed to attach to GPRS/LTE network.")
        sys.exit(1)

    # Configure APN and open network bearer
    send_at(ser, f'AT+CGDCONT=1,"IP","{APN}"')
    
    # Close network bearer just in case it was open before
    send_at(ser, "AT+NETCLOSE", wait=1)

    response = send_at(ser, "AT+NETOPEN", wait=5) # This can take a while
    if "+NETOPEN: 0" not in response:
        print("🚨 Failed to open network bearer. Check APN and signal strength.")
        sys.exit(1)
    print("✅ Network bearer open.")

    send_at(ser, "AT+IPADDR") # Just to see our IP

    # Finally, open the TCP socket
    print("--- Opening TCP Socket ---")
    oc = f'AT+CIPOPEN={SOCKET_ID},"TCP","{HOST}",80'
    response = send_at(ser, oc, wait=8) # This can also take a while
    
    # if f"+CIPOPEN: {SOCKET_ID},0" in response:
    #     print("✅ TCP socket open successfully.")
    # else:
    #     print(f"🚨 Socket open failed. Final attempt failed. Response:\n{response}")
    #     sys.exit(1)

# === SEND SENSOR DATA AS JSON ===
def send_json_data(ser, params: dict):
    """Transforms sensor data to JSON and sends it via HTTP POST."""
    
    # 1. Transform the flat dictionary into a list of objects
    json_payload_list = []
    for key, value in params.items():
        try:
            sensor_type = key.strip().lower()
            # Standardize sensor names if necessary (e.g., temp -> temperature)
            if sensor_type == "temp":
                sensor_type = "temperature"
            
            json_payload_list.append({
                "sensor_type": sensor_type,
                "value": float(value)  # Convert value to a number
            })
        except (ValueError, TypeError):
            print(f"⚠️ Could not process key-value pair: {key}={value}. Skipping.")
            continue
    
    if not json_payload_list:
        print("🤷 No valid data to send.")
        return

    # 2. Create the JSON string
    json_string = json.dumps(json_payload_list)
    
    # 3. Construct the full HTTP POST request
    http_payload = (
        f"POST {ENDPOINT} HTTP/1.1\r\n"
        f"Host: {HOST}\r\n"
        f"Connection: keep-alive\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(json_string)}\r\n"
        f"\r\n"
        f"{json_string}"
    )

    print(f"🌐 Full HTTP payload:\n{http_payload}")
    payload_length = len(http_payload.encode('utf-8'))

    # 4. Send the data using AT commands
    ser.reset_input_buffer()
    cmd = f'AT+CIPSEND={SOCKET_ID},{payload_length}'
    ser.write((cmd + '\r\n').encode())

    if not wait_for(ser, '>'):
        print("⚠️ No '>' prompt from modem; skipping send.")
        return

    ser.write(http_payload.encode('utf-8'))
    print(f"📤 Sent JSON: {json_string}")
    
    # Wait for and print the server's response
    time.sleep(3) # Increased wait for server response
    response = ser.read_all().decode(errors='ignore')
    print(f"🛬 Modem response after send:\n{response}")

# === MAIN LOOP ===
def main():
    """Main function to initialize devices and start the data loop."""
    try:
        modem = serial.Serial(MODEM_PORT, BAUD_MODEM, timeout=1)
        sensor = serial.Serial(DATA_PORT, BAUD_DATA, timeout=1)
    except Exception as e:
        print(f"❌ Serial port open error: {e}")
        sys.exit(1)

    init_modem(modem)
    print("\n▶️ Reading sensor data from STM32 and sending to server...")

    try:
        while True:
            line = sensor.readline().decode(errors='ignore').strip()
            if not line:
                time.sleep(1) # Wait if no data
                continue

            print(f"\n📨 Received from STM32: {line}")
            try:
                # Example line: "speed=50,temp=36.2,gear=3,fuel=80,rpm=3200"
                entries = [x.split('=') for x in line.split(',')]
                data_dict = {k.strip(): v.strip() for k, v in entries if len(k.strip()) > 0}
                if data_dict:
                    send_json_data(modem, data_dict)
                else:
                    print("⚠️ Received empty or malformed data.")
            except Exception as e:
                print(f"⚠️ Data processing error: {e} on line: '{line}'")
            
            time.sleep(2) # Delay between sends

    except KeyboardInterrupt:
        print("\n✋ Ctrl+C detected, closing connection...")
    finally:
        send_at(modem, f'AT+CIPCLOSE={SOCKET_ID}', 2)
        send_at(modem, 'AT+NETCLOSE', 1)
        modem.close()
        sensor.close()
        print("✅ Cleanly shut down.")

if __name__ == '__main__':
    main() 