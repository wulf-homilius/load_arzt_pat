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
from datetime import datetime, timezone, timedelta
import re
import time

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
# Quelldatei bestimmen
# -------------------------------------------------------
output_dir = os.path.join(script_dir, "output")
source_file_cfg = config.get("source_file", "").strip()

if source_file_cfg:
    # Erst direkt (z.B. "output/patient_22.json"), dann im output/-Ordner
    latest_file = os.path.join(script_dir, source_file_cfg)
    if not os.path.isfile(latest_file):
        fallback = os.path.join(output_dir, os.path.basename(source_file_cfg))
        if os.path.isfile(fallback):
            latest_file = fallback
        else:
            print(f"\nFehler: Quelldatei nicht gefunden.")
            print(f"  Gesucht: {latest_file}")
            print(f"  Gesucht: {fallback}")
            sys.exit(1)
else:
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
# Ausnahme: Practitioner und Location existieren bereits
# auf dem Server mit ihren Original-IDs → nicht remappen,
# damit der SEMPA-Analyzer sie kennt und korrekt rechnet
# -------------------------------------------------------
KEEP_ORIGINAL_ID_TYPES = {"Practitioner", "Location"}

id_mapping = {old_patient_id: new_patient_id}
for res in resources:
    old_id = res.get("id", "")
    rt     = res.get("resourceType", "")
    if old_id and old_id != old_patient_id and rt not in KEEP_ORIGINAL_ID_TYPES:
        id_mapping[old_id] = str(uuid.uuid4())

# Globales Ersetzen aller IDs im JSON-String
json_str = json.dumps(resources, ensure_ascii=False)
for old_id, new_id in id_mapping.items():
    json_str = json_str.replace(old_id, new_id)
resources = json.loads(json_str)

# -------------------------------------------------------
# Datum-Verschiebung: CarePlan.period.start → heute
# alle ServiceRequest-Termine relativ dazu verschieben
# -------------------------------------------------------
today_utc = datetime.now(timezone.utc)
today_date_str = today_utc.strftime("%Y-%m-%d")   # z.B. "2026-06-04"

# -------------------------------------------------------
# Alle historischen Daten → heute
# Aufnahme, Anamnese, Assessment, Befunde, Planung:
#   jeder ISO-Timestamp dessen Datumsteil ≤ heute ist
#   bekommt heute als Datum (Uhrzeit bleibt erhalten).
# Echte Zukunftstermine (ServiceRequest-Events > heute)
#   bleiben unverändert.
# -------------------------------------------------------
def collapse_past_dates(s: str) -> str:
    def replacer(m):
        d = m.group(1)
        return m.group(0).replace(d, today_date_str, 1) if d <= today_date_str else m.group(0)
    return re.sub(r'(\d{4}-\d{2}-\d{2})(?=T)', replacer, s)

json_str = collapse_past_dates(json_str)
resources = json.loads(json_str)

# Anzahl verschobener Ressource-Typen zur Info ausgeben
print(f"  INF Datum-Kollaps         alle Vergangenheitsdaten → {today_date_str}")

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

# -------------------------------------------------------
# Hilfsfunktionen
# -------------------------------------------------------
SEMPA_CS = "http://nursit-institute.com/fhir/StructureDefinition/sempadiag-cs"
ASSESSMENT_QR_URL = "CareITAssessmentSempaAkut"
SERVER_GENERATES  = set()                # nichts mehr überspringen
PHASE2_TYPES      = {"CarePlan", "ServiceRequest"}
PHASE4_TYPES      = {"Flag", "RiskAssessment"}  # nach SEMPA: überschreiben falsche Werte

# Encounter-ID für Observation-Referenz vorausberechnen
enc_id = next((r["id"] for r in resources
               if r.get("resourceType") == "Encounter"), None)

def is_sempa_condition(res):
    """SeMPA-Pflegediagnose: vom Server nach SEMPA-Analyse generiert."""
    return (res.get("resourceType") == "Condition" and any(
        c.get("system") == SEMPA_CS
        for c in res.get("code", {}).get("coding", [])))

def prepare(res, sempa_ids=None):
    """Ressource für Upload vorbereiten (in-place)."""
    rt  = res.get("resourceType")
    rid = res.get("id")

    if rt == "Encounter":
        midnight = today_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        res.setdefault("period", {})["start"] = midnight.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    if rt == "Patient":
        res["name"] = [{"use": "official", "family": new_family, "given": [new_given]}]

    if rt == "CarePlan":
        res["status"] = "active"
        if sempa_ids:
            res["addresses"] = [{"reference": f"Condition/{c}"} for c in sempa_ids]
        # KEIN activity-Array: nursit findet ServiceRequests über
        # ServiceRequest?based-on=CarePlan/<id> selbst.
        # activity-Einträge triggern das Lock "keine Änderungen möglich".

    # ServiceRequest: status + category aus Quelldaten übernehmen (active/controlled)
    # Das entspricht dem Originalpatient und zeigt in Maßnahmendokumentation
    # UND (bei aktivem CarePlan) in der Planungsübersicht.

    if rt == "Observation" and "encounter" not in res and enc_id:
        res["encounter"] = {"reference": f"Encounter/{enc_id}"}

    if "meta" in res:
        res["meta"].pop("versionId",   None)
        res["meta"].pop("lastUpdated", None)

def put(res):
    """Ressource per PUT hochladen. Gibt True/False zurück."""
    rt  = res.get("resourceType")
    rid = res.get("id")
    is_trigger = (rt == "QuestionnaireResponse"
                  and ASSESSMENT_QR_URL in res.get("questionnaire", "")
                  and res.get("status") == "completed")
    label = rt + (" ← SEMPA-Trigger" if is_trigger else "")
    try:
        r = session.put(f"{server}/{rt}/{rid}", json=res, timeout=30)
        if r.status_code in (200, 201):
            print(f"  OK  {label:<42} ({'erstellt' if r.status_code==201 else 'aktuell.'})")
            return True
        detail = ""
        try:   detail = r.json().get("issue",[{}])[0].get("diagnostics", r.text[:250])
        except: detail = r.text[:250]
        print(f"  ERR {label:<42} HTTP {r.status_code}: {detail}")
        return False
    except requests.RequestException as e:
        print(f"  ERR {label:<42} Verbindungsfehler: {e}")
        return False

# Upload-Reihenfolge
UPLOAD_ORDER = {
    "Patient": 0, "Practitioner": 1, "Location": 2, "Encounter": 3,
    "QuestionnaireResponse": 4, "Condition": 5,
    "Procedure": 8, "Observation": 9, "Communication": 10,
}

def sort_key(res):
    rt = res.get("resourceType", "")
    if (rt == "QuestionnaireResponse"
            and ASSESSMENT_QR_URL in res.get("questionnaire", "")
            and res.get("status") == "completed"):
        return 997          # Assessment-QR ZULETZT in Phase 1
    return UPLOAD_ORDER.get(rt, 99)

# -------------------------------------------------------
# PHASE 1 – Alles außer SeMPA-Conditions, CarePlan, ServiceRequest
#   → Assessment-QR (completed) kommt ganz am Schluss
#   → triggert SEMPA-Analyse auf dem Server
# -------------------------------------------------------
print("─" * 60)
print("  Phase 1: Basis-Ressourcen + Assessment (SEMPA-Trigger)")
print("─" * 60)

for res in sorted(resources, key=sort_key):
    rt = res.get("resourceType")
    if rt in SERVER_GENERATES:
        print(f"  ---  {rt:<42} (Server generiert)")
        continue
    if rt in PHASE2_TYPES or rt in PHASE4_TYPES or is_sempa_condition(res):
        continue   # → Phase 2 / Phase 4
    prepare(res)
    if put(res): success += 1
    else:        errors  += 1

# -------------------------------------------------------
# Warten + Polling: SEMPA-Analyzer braucht Zeit
# Bis zu 30 Sek. auf server-generierte SeMPA-Conditions warten.
# Fallback: eigene SeMPA-Conditions hochladen.
# -------------------------------------------------------
print("─" * 60)
sempa_ids = []

if enc_id:
    MAX_ATTEMPTS  = 6
    POLL_INTERVAL = 5   # Sekunden je Versuch

    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"  Suche SeMPA-Diagnosen (Versuch {attempt}/{MAX_ATTEMPTS}) ...")
        try:
            r = session.get(
                f"{server}/Condition",
                params={"encounter": f"Encounter/{enc_id}", "_count": 100},
                timeout=30
            )
            if r.status_code == 200:
                for entry in r.json().get("entry", []):
                    cond = entry.get("resource", {})
                    if any(c.get("system") == SEMPA_CS
                           for c in cond.get("code", {}).get("coding", [])):
                        if cond["id"] not in sempa_ids:
                            sempa_ids.append(cond["id"])
                            disp = (cond.get("code", {}).get("coding", [{}])[0]
                                       .get("display", "?"))
                            print(f"  INF SeMPA-Condition  {cond['id'][:8]}…  {disp}")
        except requests.RequestException as e:
            print(f"  WRN Abfrage-Fehler: {e}")

        if sempa_ids:
            print(f"  INF {len(sempa_ids)} SeMPA-Diagnosen vom Server übernommen")
            break
        if attempt < MAX_ATTEMPTS:
            time.sleep(POLL_INTERVAL)

    # Fallback: SEMPA hat keine Conditions erstellt → eigene hochladen
    if not sempa_ids:
        print("  WRN SEMPA hat keine Diagnosen erstellt → lade eigene SeMPA-Conditions hoch")
        for res in sorted(resources, key=sort_key):
            if not is_sempa_condition(res):
                continue
            prepare(res)
            if put(res):
                sempa_ids.append(res.get("id"))
                success += 1
            else:
                errors += 1
        print(f"  INF {len(sempa_ids)} eigene SeMPA-Diagnosen hochgeladen")
else:
    print("  WRN Encounter-ID nicht gefunden")

# -------------------------------------------------------
# PHASE 2 – CarePlan (mit Server-Condition-IDs) + ServiceRequests
# -------------------------------------------------------
print("─" * 60)
print("  Phase 2: CarePlan + ServiceRequests")
print("─" * 60)

careplan_id = None
for res in sorted(resources, key=lambda r: {"CarePlan": 0, "ServiceRequest": 1}
                                            .get(r.get("resourceType"), 99)):
    rt = res.get("resourceType")
    if rt not in PHASE2_TYPES:
        continue
    prepare(res, sempa_ids=sempa_ids)
    if rt == "CarePlan":
        careplan_id = res.get("id")
    if put(res): success += 1
    else:        errors  += 1

# -------------------------------------------------------
# PHASE 3 – CarePlan aktivieren
# SEMPA setzt den CarePlan nach unserem Upload asynchron
# auf "draft" zurück (versionId steigt auf 2).
# Wir lesen den aktuellen Stand vom Server und setzen
# status explizit auf "active" – NACH SEMPA-Verarbeitung.
# -------------------------------------------------------
print("─" * 60)
print("  Phase 3: CarePlan aktivieren (SEMPA-Nachbearbeitung abwarten)")
print("─" * 60)

if careplan_id:
    ACTIVATE_WAIT    = 8    # Sek. warten bevor wir lesen
    ACTIVATE_RETRIES = 4    # Versuche, falls SEMPA noch läuft

    for attempt in range(1, ACTIVATE_RETRIES + 1):
        print(f"  Warte {ACTIVATE_WAIT} Sek. auf SEMPA-Abschluss (Versuch {attempt}) ...")
        time.sleep(ACTIVATE_WAIT)

        try:
            r = session.get(f"{server}/CarePlan/{careplan_id}", timeout=30)
            if r.status_code != 200:
                print(f"  WRN CarePlan GET fehlgeschlagen: HTTP {r.status_code}")
                continue

            server_cp = r.json()
            current_status = server_cp.get("status", "?")
            version        = server_cp.get("meta", {}).get("versionId", "?")
            print(f"  INF CarePlan  status={current_status}  versionId={version}")

            if current_status == "active":
                print("  INF CarePlan ist bereits active – kein Patch nötig")
                success += 1
                break

            # Status auf active setzen, activity NICHT hinzufügen
            server_cp["status"] = "active"
            server_cp.pop("activity", None)   # sicherstellen dass kein activity-Lock
            # Meta bereinigen
            server_cp.get("meta", {}).pop("versionId",   None)
            server_cp.get("meta", {}).pop("lastUpdated", None)
            r2 = session.put(
                f"{server}/CarePlan/{careplan_id}",
                json=server_cp,
                timeout=30
            )
            if r2.status_code in (200, 201):
                new_version = r2.json().get("meta", {}).get("versionId", "?")
                print(f"  OK  CarePlan aktiviert → status=active  versionId={new_version}")
                success += 1
                break
            else:
                try:
                    detail = r2.json().get("issue", [{}])[0].get("diagnostics", r2.text[:200])
                except Exception:
                    detail = r2.text[:200]
                print(f"  ERR CarePlan-Aktivierung HTTP {r2.status_code}: {detail}")
                errors += 1
                break

        except requests.RequestException as e:
            print(f"  ERR Verbindungsfehler: {e}")
            errors += 1
            break
else:
    print("  WRN CarePlan-ID nicht bekannt – Phase 3 übersprungen")

# -------------------------------------------------------
# PHASE 4 – Flag + RiskAssessment nach SEMPA-Abschluss
#
# Problem: UI nutzt  _sort=-date → neueste Version gewinnt.
# SEMPA generiert RiskAssessment mit aktuellem Timestamp
# (z.B. 15:07 UTC). Unser Upload hat 11:22 aus Quelldaten
# → SEMPA-Version wird vom Frontend verwendet.
#
# Lösung:
# 1. Warten bis SEMPA seinen RiskAssessment erstellt hat
# 2. Dann Flag + RiskAssessment mit occurrenceDateTime=JETZT
#    hochladen → unser Timestamp ist neuer → UI nimmt unsere
# -------------------------------------------------------
print("─" * 60)
print("  Phase 4: Warten auf SEMPA-Abschluss → Flag + RiskAssessment")
print("─" * 60)

phase4_now = datetime.now(timezone.utc)  # Zeitpunkt für Timestamps

if enc_id:
    for attempt in range(1, 9):
        time.sleep(5)
        try:
            r = session.get(
                f"{server}/RiskAssessment",
                params={"encounter": f"Encounter/{enc_id}", "_count": 1},
                timeout=30
            )
            if r.status_code == 200 and r.json().get("total", 0) > 0:
                print(f"  INF SEMPA abgeschlossen – RiskAssessment gefunden ({attempt*5} Sek.)")
                break
        except requests.RequestException:
            pass
        print(f"  Warte auf SEMPA-RiskAssessment ... ({attempt*5} Sek.)")

    time.sleep(3)   # kleiner Puffer nach SEMPA-Abschluss
    phase4_now = datetime.now(timezone.utc)   # Timestamp nach SEMPA

# -------------------------------------------------------
# PHASE 5 – ServiceRequests re-uploaden
# SEMPA könnte basedOn auf eine eigene CarePlan-ID
# umgestellt haben → Planungsübersicht findet "Keine Aufgaben".
# Wir laden ServiceRequests erneut hoch und erzwingen
# basedOn → unseren aktivierten CarePlan (careplan_id).
# -------------------------------------------------------
print("─" * 60)
print("  Phase 5: ServiceRequests re-uploaden (basedOn neu verankern)")
print("─" * 60)

if careplan_id:
    for res in sorted(resources, key=sort_key):
        rt = res.get("resourceType")
        if rt != "ServiceRequest":
            continue
        # basedOn explizit auf unseren aktivierten CarePlan setzen
        res["basedOn"] = [{"reference": f"CarePlan/{careplan_id}"}]
        prepare(res)
        if put(res): success += 1
        else:        errors  += 1
else:
    print("  WRN careplan_id nicht bekannt – Phase 5 übersprungen")

for res in resources:
    rt = res.get("resourceType")
    if rt not in PHASE4_TYPES:
        continue
    prepare(res)

    # Timestamps auf JETZT setzen → neuester Eintrag → UI-Sortierung gewinnt
    if rt == "RiskAssessment":
        res["occurrenceDateTime"] = phase4_now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        # Identifier-Wert ebenfalls aktualisieren
        for ident in res.get("identifier", []):
            if "risk-analysis" in ident.get("system", ""):
                ident["value"] = phase4_now.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    if rt == "Flag":
        # latestCareITAssessmentSempaAkutDate auf jetzt setzen
        for coding in res.get("code", {}).get("coding", []):
            if "AssessmentSempaAkutDate" in coding.get("system", ""):
                coding["code"] = phase4_now.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    if put(res): success += 1
    else:        errors  += 1

# -------------------------------------------------------
# Zusammenfassung
# -------------------------------------------------------
print("=" * 60)
print(f"  Ergebnis: {success} erfolgreich  |  {errors} Fehler")
print(f"  Patient:  {server}/Patient/{new_patient_id}")
print("=" * 60)
