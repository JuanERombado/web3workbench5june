param(
    [string]$Name = "Web3 Bug Bounty Workbench"
)

$ErrorActionPreference = "Stop"

python -m pip install -e .
python -m pip install pyinstaller
python -m PyInstaller `
    --noconfirm `
    --windowed `
    --name $Name `
    --collect-all PySide6 `
    --paths . `
    workbench\gui.py

Write-Host "Built dist\$Name\$Name.exe"
