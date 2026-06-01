# load_Patient
aus Sensdoc virtueller Testpatient
Usage: extract-data -u <username> -p <password> -h <server> (-e <encounter-id> | -pid <patient-id>)

Parameters:
  -u     Username
  -p     Password
  -h     Server
  -e     Encounter ID
  -pid   Patient ID

Note: Either -e or -pid must be provided (not both).
.venv/
├── Scripts/          # (Windows) oder bin/ (macOS/Linux)
│   ├── python.exe    # Python-Interpreter
│   ├── pip.exe       # Paketmanager
│   └── activate.bat  # Aktivierungsskript
├── pyvenv.cfg        # Konfiguration
└── Lib/              # Installierte Pakete

 So startest du das Skript:
Speichere das Skript als run-extract.ps1
Stelle sicher, dass PSYaml installiert ist:
Install-Module -Name PSYaml -Force
Öffne PowerShell als Administrator (wichtig für Skriptausführung)
Aktiviere die Ausführung:
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
Führe das Skript aus:
.\run-extract.ps1