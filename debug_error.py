import json, requests, yaml, glob, uuid

with open("config.yaml", encoding="utf-8") as f:
    cfg = yaml.safe_load(f)

files = sorted(glob.glob("output/patient_*.json"))
with open(files[-1], encoding="utf-8") as f:
    resources = json.load(f)

# Erste Observation als Test
obs = next(r for r in resources if r.get("resourceType") == "Observation")
obs = json.loads(json.dumps(obs))  # deep copy
obs["id"] = str(uuid.uuid4())
if "meta" in obs:
    obs["meta"].pop("versionId", None)
    obs["meta"].pop("lastUpdated", None)

r = requests.put(
    f"{cfg['server']}/Observation/{obs['id']}",
    json=obs,
    auth=(cfg["username"], cfg["password"]),
    headers={"Content-Type": "application/fhir+json"}
)
print("HTTP:", r.status_code)
print(json.dumps(r.json(), indent=2, ensure_ascii=False))
