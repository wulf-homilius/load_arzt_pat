# upload_patient.py
# Laedt einen Testpatienten aus der output/-JSON neu hoch:
#   - Neue UUIDs fuer alle Ressourcen
#   - Neuer Name (family, given) aus config.yaml
#   - Neue Patienten-ID aus config.yaml (oder auto-generiert)
#   - Upload per FHIR PUT an den Server

import yaml
import json
import uuid
import os
import sys
import glob
import requests

# -------------------------------------------------------
# Config laden
# -------------------------------------------------------
script_dir = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(script_dir, "config.yaml")

try:
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
except FileNotFoundError:
    print(f"Fehler: {config_path} nicht gefunden.")
    sys.exit(1)
except yaml.YAMLError as e:
    print(f"Fehler beim Lesen der config.yaml: {e}")
    sys.exit(1)

# Server-Verbindung
server   = config["server"].rstrip("/")
username = config["username"]
password = config["password"]

# Neue Patienten-Daten
new_cfg        = config.get("new_patient", {})
new_family     = new_cfg.get("family") or "Mustermann"
new_given      = new_cfg.get("given")  or "Max"
new_patient_id = new_cfg.get("patient_id") or str(uuid.uuid4())

print("=" * 60)
print("  FHIR Patient Upload")
print("=" * 60)
print(f"  Server     : {server}")
print(f"  Neuer Name : {new_family}, {new_given}")
print(f"  Neue ID    : {new_patient_id}")

# -------------------------------------------------------
# Neueste JSON-Quelldatei laden
# -------------------------------------------------------
output_dir = os.path.join(script_dir, "output")
json_files = sorted(glob.glob(os.path.join(output_dir, "patient_*.json")))

if not json_files:
    print("\nFehler: Keine JSON-Datei im output/-Ordner gefunden.")
    print("Bitte zuerst run_extract.py ausfuehren.")
    sys.exit(1)

latest_file = json_files[-1]
print(f"  Quelldatei : {os.path.basename(latest_file)}")
print("=" * 60)

with open(latest_file, "r", encoding="utf-8") as f:
    resources = json.load(f)

# -------------------------------------------------------
# Alte Patienten-ID ermitteln
# -------------------------------------------------------
old_patient_id = None
for res in resources:
    if res.get("resourceType") == "Patient":
        old_patient_id = res["id"]
        break

if not old_patient_id:
    print("Fehler: Kein Patient in der JSON-Datei gefunden.")
    sys.exit(1)

# -------------------------------------------------------
# ID-Mapping erstellen: jede alte ID → neue UUID
# -------------------------------------------------------
id_mapping = {old_patient_id: new_patient_id}
for res in resources:
    old_id = res.get("id", "")
    if old_id and old_id != old_patient_id:
        id_mapping[old_id] = str(uuid.uuid4())

# Globales Ersetzen aller IDs im JSON-String
json_str = json.dumps(resources, ensure_ascii=False)
for old_id, new_id in id_mapping.items():
    json_str = json_str.replace(old_id, new_id)
resources = json.loads(json_str)

# -------------------------------------------------------
# Ressourcen vorbereiten und hochladen
# -------------------------------------------------------
session = requests.Session()
session.auth = (username, password)
session.headers.update({
    "Content-Type": "application/fhir+json",
    "Accept":       "application/fhir+json"
})

success = 0
errors  = 0

# Upload-Reihenfolge: Abhängigkeiten zuerst
UPLOAD_ORDER = {
    "Patient":                0,
    "Practitioner":           1,
    "Location":               2,
    "Encounter":              3,
    "QuestionnaireResponse":  4,  # vor Condition (evidence.detail)
    "Condition":              5,  # vor CarePlan (addresses)
    "CarePlan":               6,  # nach Condition, vor ServiceRequest
    "ServiceRequest":         7,  # nach CarePlan (basedOn)
    "Procedure":              8,
    "Observation":            9,
    "Communication":         10,
    "Flag":                  11,
    "RiskAssessment":        12,
}

def sort_key(res):
    return UPLOAD_ORDER.get(res.get("resourceType"), 99)

for res in sorted(resources, key=sort_key):
    resource_type = res.get("resourceType")
    resource_id   = res.get("id")

    # Patient: Name ersetzen
    if resource_type == "Patient":
        res["name"] = [{
            "use":    "official",
            "family": new_family,
            "given":  [new_given]
        }]

    # Status-Korrektur: CarePlan active→draft, ServiceRequest draft→active
    if resource_type == "CarePlan" and res.get("status") == "active":
        res["status"] = "draft"
    elif resource_type == "ServiceRequest" and res.get("status") == "draft":
        res["status"] = "active"

    # Meta bereinigen (Server setzt versionId / lastUpdated selbst)
    if "meta" in res:
        res["meta"].pop("versionId",   None)
        res["meta"].pop("lastUpdated", None)

    # FHIR PUT
    url = f"{server}/{resource_type}/{resource_id}"
    try:
        resp = session.put(url, json=res, timeout=30)
        if resp.status_code in (200, 201):
            status = "erstellt" if resp.status_code == 201 else "aktualisiert"
            print(f"  OK  {resource_type:<22} {resource_id}  ({status})")
            success += 1
        else:
            try:
                detail = resp.json().get("issue", [{}])[0].get("diagnostics", resp.text[:300])
            except Exception:
                detail = resp.text[:300]
            print(f"  ERR {resource_type:<22} HTTP {resp.status_code}: {detail}")
            errors += 1
    except requests.RequestException as e:
        print(f"  ERR {resource_type:<22} Verbindungsfehler: {e}")
        errors += 1

# -------------------------------------------------------
# Zusammenfassung
# -------------------------------------------------------
print("=" * 60)
print(f"  Ergebnis: {success} erfolgreich  |  {errors} Fehler")
print(f"  Patient:  {server}/Patient/{new_patient_id}")
print("=" * 60)
