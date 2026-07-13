"""
ScaleOne Weighbridge API — EXE Edition
----------------------------------------
Same auto-detect + auto-reconnect logic as before, but adapted so it works
correctly when packaged as a Windows .exe with PyInstaller:
  - Finds index.html correctly whether run as .py or as a bundled .exe
  - Auto-opens the dashboard in your default browser on startup
  - Keeps a console window open showing live logs (port search, connect, errors)
"""

import re
import sys
import time
import threading
import webbrowser
from pathlib import Path

import serial
import serial.tools.list_ports
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

app = FastAPI(title="ScaleOne Weighbridge API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BAUD_RATES = [9600, 4800, 2400, 19200, 38400, 57600, 115200]
PROBE_TIMEOUT = 2.5

serial_connection = None
lock = threading.Lock()

weight_data = {
    "weight": "0",
    "unit": "",
    "raw_data": "",
    "status": "Searching...",
    "port": "",
    "baudrate": 0,
    "last_updated": None,
}


def resource_path(filename: str) -> Path:
    """Get the correct path to a bundled file, whether running as a
    normal .py script or as a PyInstaller-built .exe."""
    if hasattr(sys, "_MEIPASS"):
        # Running inside a PyInstaller bundle
        return Path(sys._MEIPASS) / filename
    return Path(__file__).parent / filename


def available_ports():
    return [p.device for p in serial.tools.list_ports.comports()]


def looks_like_valid_data(raw: str) -> bool:
    return bool(re.search(r"[-+]?\d+\.?\d*", raw))


def extract_weight(raw: str):
    match = re.search(r"[-+]?\d+\.?\d*", raw)
    value = match.group(0) if match else raw
    unit_match = re.search(r"(kg|kgs|lb|lbs|g)\b", raw, re.IGNORECASE)
    unit = unit_match.group(0).lower() if unit_match else ""
    return value, unit


def try_port_baud(port: str, baud: int):
    try:
        ser = serial.Serial(
            port=port,
            baudrate=baud,
            timeout=1,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
        )
        time.sleep(1.5)

        start = time.time()
        while time.time() - start < PROBE_TIMEOUT:
            if ser.in_waiting:
                raw = ser.readline().decode(errors="ignore").strip()
                if raw and looks_like_valid_data(raw):
                    return ser
            time.sleep(0.1)

        ser.close()
        return None

    except Exception:
        return None


def connect():
    global serial_connection

    while serial_connection is None:
        ports = available_ports()

        if not ports:
            weight_data["status"] = "Searching... (no ports found)"
            print("No COM ports found, retrying in 3s...")
            time.sleep(3)
            continue

        for port in ports:
            for baud in BAUD_RATES:
                weight_data["status"] = f"Searching... trying {port} @ {baud}"
                print(f"Trying {port} @ {baud}")

                ser = try_port_baud(port, baud)

                if ser is not None:
                    with lock:
                        serial_connection = ser
                        weight_data["status"] = "Connected"
                        weight_data["port"] = port
                        weight_data["baudrate"] = baud

                    print(f"Connected: {port} @ {baud}")
                    return

        print("No valid weighbridge data found on any port, retrying...")
        time.sleep(3)


def read_weight():
    global serial_connection

    while True:
        if serial_connection is None:
            connect()

        try:
            if serial_connection.in_waiting:
                raw = serial_connection.readline().decode(errors="ignore").strip()

                if raw:
                    value, unit = extract_weight(raw)

                    with lock:
                        weight_data["raw_data"] = raw
                        weight_data["weight"] = value
                        weight_data["unit"] = unit
                        weight_data["status"] = "Connected"
                        weight_data["last_updated"] = time.strftime("%H:%M:%S")

        except Exception as e:
            print(f"Read error: {e}")

            try:
                serial_connection.close()
            except Exception:
                pass

            with lock:
                serial_connection = None
                weight_data["status"] = "Disconnected"

            time.sleep(2)

        time.sleep(0.05)


@app.on_event("startup")
def startup():
    threading.Thread(target=read_weight, daemon=True).start()
    # Open the dashboard automatically in the default browser
    threading.Timer(1.5, lambda: webbrowser.open("http://localhost:8000/")).start()


@app.get("/api/weight")
def weight():
    with lock:
        return dict(weight_data)


@app.get("/api/ports")
def ports():
    return {"ports": available_ports()}


@app.get("/")
def home():
    return FileResponse(resource_path("index.html"))


app.mount("/static", StaticFiles(directory=str(resource_path("."))), name="static")


if __name__ == "__main__":
    import uvicorn
    print("=" * 50)
    print(" ScaleOne Weighbridge — starting up")
    print(" Dashboard: http://localhost:8000/")
    print(" Isko band karne ke liye is window ko band karo")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=8000)
