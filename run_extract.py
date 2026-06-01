# run_extract.py

import yaml
import subprocess
import sys
import os

# Pfad zur YAML-Datei
config_path = "config.yaml"

# YAML laden
try:
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
except FileNotFoundError:
    print(f"Fehler: Datei {config_path} nicht gefunden.")
    sys.exit(1)
except yaml.YAMLError as e:
    print(f"Fehler beim Lesen der YAML-Datei: {e}")
    sys.exit(1)

# Prüfen, ob entweder encounter_id oder patient_id vorhanden ist
if config.get("encounter_id") and config.get("patient_id"):
    print("Fehler: Nur entweder -e (encounter_id) oder -pid (patient_id) darf angegeben werden, nicht beide.")
    sys.exit(1)
if not config.get("encounter_id") and not config.get("patient_id"):
    print("Fehler: Entweder -e (encounter_id) oder -pid (patient_id) muss angegeben werden.")
    sys.exit(1)

# Pfad zur exe (immer relativ zum Skript)
script_dir = os.path.dirname(os.path.abspath(__file__))
exe_path = os.path.join(script_dir, "extract-data.exe")

# Kommando bauen
command = [
    exe_path,
    "-u", config["username"],
    "-p", config["password"],
    "-h", config["server"]
]

if config.get("encounter_id"):
    command += ["-e", str(config["encounter_id"])]
elif config.get("patient_id"):
    command += ["-pid", str(config["patient_id"])]

# Ausführen
print("Starte:", " ".join(command))
try:
    result = subprocess.run(command, check=True, text=True, capture_output=True)
    print("Erfolg:", result.stdout)
except subprocess.CalledProcessError as e:
    print("Fehler:", e.stderr)
    sys.exit(1)