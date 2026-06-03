# del_patient.py
# Löscht alle FHIR-Ressourcen der in del_pat.yml aufgelisteten Patienten.
# Reihenfolge: abhängige Ressourcen zuerst, Patient zuletzt.

import yaml
import sys
import os
import requests

# -------------------------------------------------------
# Config laden
# -------------------------------------------------------
script_dir  = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(script_dir, "del_pat.yml")

try:
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
except FileNotFoundError:
    print(f"Fehler: {config_path} nicht gefunden.")
    sys.exit(1)
except yaml.YAMLError as e:
    print(f"Fehler beim Lesen der del_pat.yml: {e}")
    sys.exit(1)

server   = config["server"].rstrip("/")
username = config["username"]
password = config["password"]
patients = config.get("patients", [])

if not patients:
    print("Fehler: Keine Patienten in del_pat.yml angegeben.")
    sys.exit(1)

# -------------------------------------------------------
# Session
# -------------------------------------------------------
session = requests.Session()
session.auth = (username, password)
session.headers.update({"Accept": "application/fhir+json"})

# -------------------------------------------------------
# Ressourcentypen, die per ?patient= abfragbar sind
# (in Lösch-Reihenfolge: Abhängige zuerst, Patient zuletzt)
# -------------------------------------------------------
PATIENT_COMPARTMENT_TYPES = [
    "Communication",
    "Flag",
    "RiskAssessment",
    "CarePlan",
    "Procedure",
    "Observation",
    "Condition",
    "ServiceRequest",
    "QuestionnaireResponse",
    "Media",
    "Encounter",
    "Patient",
]


def search_resources(resource_type: str, patient_id: str) -> list[dict]:
    """Gibt alle Ressourcen eines Typs für den Patienten zurück."""
    if resource_type == "Patient":
        return [{"resourceType": "Patient", "id": patient_id}]

    resources = []
    url = f"{server}/{resource_type}?patient={patient_id}&_count=200"
    while url:
        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"    WARN  Suche {resource_type} fehlgeschlagen: {e}")
            return resources

        bundle = resp.json()
        for entry in bundle.get("entry", []):
            res = entry.get("resource", {})
            if res.get("id"):
                resources.append(res)

        # Nächste Seite
        url = None
        for link in bundle.get("link", []):
            if link.get("relation") == "next":
                url = link["url"]
                break

    return resources


def delete_resource(resource_type: str, resource_id: str) -> bool:
    url = f"{server}/{resource_type}/{resource_id}"
    try:
        resp = session.delete(url, timeout=30)
        if resp.status_code in (200, 204):
            return True
        if resp.status_code == 404:
            return True  # bereits weg
        try:
            detail = resp.json().get("issue", [{}])[0].get("diagnostics", resp.text[:200])
        except Exception:
            detail = resp.text[:200]
        print(f"    ERR  DELETE {resource_type}/{resource_id}  HTTP {resp.status_code}: {detail}")
        return False
    except requests.RequestException as e:
        print(f"    ERR  DELETE {resource_type}/{resource_id}  Verbindungsfehler: {e}")
        return False


# -------------------------------------------------------
# Hauptschleife
# -------------------------------------------------------
total_ok  = 0
total_err = 0

for pat in patients:
    patient_id = pat["id"]
    label      = f"{pat.get('family', '?')}, {pat.get('given', '?')}  [{patient_id}]"
    print("=" * 70)
    print(f"  Patient: {label}")
    print("=" * 70)

    pat_ok  = 0
    pat_err = 0

    for resource_type in PATIENT_COMPARTMENT_TYPES:
        resources = search_resources(resource_type, patient_id)
        if not resources:
            continue

        for res in resources:
            rid = res["id"]
            ok  = delete_resource(resource_type, rid)
            if ok:
                print(f"  DEL  {resource_type:<26} {rid}")
                pat_ok  += 1
                total_ok += 1
            else:
                pat_err  += 1
                total_err += 1

    print(f"  → {pat_ok} gelöscht  |  {pat_err} Fehler\n")
# -------------------------------------------------------
# Retry: fehlgeschlagene Ressourcen nochmals löschen
# -------------------------------------------------------
RETRY_RESOURCES = [
    ("CarePlan",            "ddcbf611-1da2-d05d-a56c-1f05d864af5a"),
    ("CarePlan",            "a457c53a-7d72-21d1-9291-cf86a0bac62c"),
    ("Condition",           "a735d3b6-6f7c-23b8-0516-fb32c37a8dc8"),
    ("Condition",           "740830ff-fb56-2700-3aa7-596a51cad8e2"),
    ("Condition",           "50935215-d8b7-589d-7694-6785ecd3e680"),
    ("QuestionnaireResponse","9e507822-9e59-9cc4-1234-c36c2cfff489"),
    ("Encounter",           "b6b26d9e-a45f-4aa4-9202-36c786bc1a36"),
    ("Patient",             "90b71222-186e-45e6-bf1e-afd643f2f70b"),
]

print("\n" + "=" * 70)
print("  Retry fehlgeschlagener Ressourcen")
print("=" * 70)

for resource_type, resource_id in RETRY_RESOURCES:
    ok = delete_resource(resource_type, resource_id)
    if ok:
        print(f"  DEL  {resource_type:<26} {resource_id}")
        total_ok += 1
    else:
        total_err += 1
print("=" * 70)
print(f"  Gesamt: {total_ok} gelöscht  |  {total_err} Fehler")

print("=" * 70)
