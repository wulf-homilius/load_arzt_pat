# find_patient.py
# Sucht nach allen FHIR-Ressourcen eines Patienten anhand von Name (family + given).
# Gibt alle gefundenen Ressourcen mit ID und Typ aus.

import yaml
import sys
import os
import requests
from urllib.parse import urlencode

# -------------------------------------------------------
# Config laden (gleiche del_pat.yml wie del_patient.py)
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

# -------------------------------------------------------
# Suchparameter – hier anpassen falls nötig
# -------------------------------------------------------
SEARCH_FAMILY = "Lüder"
SEARCH_GIVEN  = "Carmen"

# -------------------------------------------------------
# Session
# -------------------------------------------------------
session = requests.Session()
session.auth = (username, password)
session.headers.update({"Accept": "application/fhir+json"})

# -------------------------------------------------------
# Ressourcentypen (gleiche Liste wie del_patient.py)
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
]

# -------------------------------------------------------
# Schritt 1: Patient per Name suchen
# -------------------------------------------------------
print("=" * 70)
print(f"  Suche Patient: family={SEARCH_FAMILY}, given={SEARCH_GIVEN}")
print("=" * 70)

params = urlencode({
    "family": SEARCH_FAMILY,
    "given":  SEARCH_GIVEN,
    "_count": 50,
})
url = f"{server}/Patient?{params}"

try:
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
except requests.RequestException as e:
    print(f"Fehler bei der Patientensuche: {e}")
    sys.exit(1)

bundle  = resp.json()
entries = bundle.get("entry", [])

if not entries:
    print("  Kein Patient mit diesem Namen gefunden.")
    print("  Hinweis: Probiere auch Varianten wie 'Lueder' oder 'Luder' (Umlaut-Kodierung).")
    sys.exit(0)

found_patients = []
for entry in entries:
    res = entry.get("resource", {})
    pid = res.get("id", "?")
    names = res.get("name", [])
    family = names[0].get("family", "?") if names else "?"
    given  = " ".join(names[0].get("given", [])) if names else "?"
    dob    = res.get("birthDate", "unbekannt")
    print(f"  ✓ Patient gefunden: {family}, {given}  |  ID: {pid}  |  geb.: {dob}")
    found_patients.append(res)

# -------------------------------------------------------
# Schritt 2: Alle verbleibenden Ressourcen pro Patient
# -------------------------------------------------------
for pat in found_patients:
    patient_id = pat["id"]
    names  = pat.get("name", [{}])
    family = names[0].get("family", "?")
    given  = " ".join(names[0].get("given", []))

    print()
    print("=" * 70)
    print(f"  Verbleibende Ressourcen für: {family}, {given}  [{patient_id}]")
    print("=" * 70)

    total = 0

    # Patient selbst
    print(f"  {'Patient':<26} {patient_id}")
    total += 1

    # Alle abhängigen Ressourcentypen
    for resource_type in PATIENT_COMPARTMENT_TYPES:
        search_url = f"{server}/{resource_type}?patient={patient_id}&_count=200"
        while search_url:
            try:
                r = session.get(search_url, timeout=30)
                r.raise_for_status()
            except requests.RequestException as e:
                print(f"  WARN  Suche {resource_type} fehlgeschlagen: {e}")
                break

            b = r.json()
            for entry in b.get("entry", []):
                res = entry.get("resource", {})
                rid = res.get("id", "?")
                # Zusatzinfo je nach Typ
                extra = ""
                if resource_type == "Condition":
                    code = res.get("code", {}).get("coding", [{}])[0]
                    extra = f"  ← {code.get('display', code.get('code', ''))}"
                elif resource_type == "CarePlan":
                    extra = f"  ← {res.get('status', '')} / {res.get('intent', '')}"
                elif resource_type == "Encounter":
                    extra = f"  ← {res.get('status', '')}"
                elif resource_type == "Observation":
                    code = res.get("code", {}).get("coding", [{}])[0]
                    extra = f"  ← {code.get('display', code.get('code', ''))}"
                print(f"  {resource_type:<26} {rid}{extra}")
                total += 1

            # Nächste Seite
            search_url = None
            for link in b.get("link", []):
                if link.get("relation") == "next":
                    search_url = link["url"]
                    break

    print()
    if total == 1:
        print(f"  → Nur noch der Patient selbst vorhanden (alle anderen Ressourcen gelöscht).")
    else:
        print(f"  → {total} Ressourcen noch vorhanden (inkl. Patient).")

print()
print("=" * 70)
print("  Suche abgeschlossen.")
print("=" * 70)