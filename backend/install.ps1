# DubCut Studio - backend environment installer (Windows).
# Creates a single Python venv and installs the native backend + engine dependencies.
$ErrorActionPreference = "Continue"

$BackendDir = if ($env:DUBCUT_BACKEND_DIR) { $env:DUBCUT_BACKEND_DIR } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$Venv = if ($env:DUBCUT_VENV) { $env:DUBCUT_VENV } else { Join-Path $BackendDir ".venv" }

Write-Host "==> DubCut Studio installer"
Write-Host "    backend: $BackendDir"
Write-Host "    venv:    $Venv"
$Target = if ($env:DUBCUT_INSTALL_TARGET) { $env:DUBCUT_INSTALL_TARGET } else { "all" }
if ($Target -notin @("common", "shorts", "dubmaster", "all", "music", "videogen")) {
  Write-Error "Nieznany profil instalacji: $Target"
  exit 1
}
Write-Host "    profil:  $Target"

# ACE-Step (music) and LTX/MFLUX (videogen) engines are cloned + run via `uv`. That
# flow is implemented for macOS/Linux (install.sh); it is not yet automated on Windows.
# Fail with a clear message rather than the old "Nieznany profil" or silent no-op.
if ($Target -in @("music", "videogen")) {
  Write-Error "Automatyczna instalacja silnika '$Target' nie jest jeszcze wspierana na Windows. Zainstaluj silnik recznie i wskaz jego folder w Ustawieniach."
  exit 1
}

# Prefer the interpreter that ALREADY has the most engine deps, so the venv
# (built with --system-site-packages) can reuse them instead of re-downloading.
$Probe = @'
import importlib.util as u
mods=["yt_dlp","numpy","torch","cv2","transformers","faster_whisper","PIL","soundfile","demucs","ultralytics","fastapi","google.genai"]
print(sum(1 for m in mods if u.find_spec(m)))
'@
$Py = $null
$PyFallback = $null
$PyBestScore = -1
foreach ($cand in @("python", "python3", "py")) {
  $cmd = Get-Command $cand -ErrorAction SilentlyContinue
  if (-not $cmd) { continue }
  $src = $cmd.Source
  if (-not $PyFallback) { $PyFallback = $src }
  $score = -1
  try { $score = [int](& $src -c $Probe 2>$null) } catch { $score = -1 }
  if ($score -gt $PyBestScore) { $PyBestScore = $score; $Py = $src }
}
if (-not $Py) { $Py = $PyFallback }
if (-not $Py) {
  Write-Error "Nie znaleziono Pythona. Zainstaluj Python 3.11+ i ponow probe."
  exit 1
}
Write-Host "==> Python: $Py"
Write-Host "    Wykryto $PyBestScore z 12 kluczowych pakietow juz w systemie — zostana uzyte ponownie."

# Choose install target: system interpreter (default from app) or venv.
$NoVenv = [bool]$env:DUBCUT_NO_VENV
if ($NoVenv) {
  $VPy = if ($env:DUBCUT_PYTHON) { $env:DUBCUT_PYTHON } else { $Py }
  Write-Host "==> Tryb systemowy: doinstalowuje brakujace pakiety do $VPy (bez venv)."
} else {
  # --system-site-packages: the venv inherits already-installed packages so pip only
  # installs the gaps. An older venv without that flag is recreated so reuse works.
  $VPy = Join-Path $Venv "Scripts\python.exe"
  $Cfg = Join-Path $Venv "pyvenv.cfg"
  if (Test-Path $VPy) {
    $SeesSystem = (Test-Path $Cfg) -and (Select-String -Path $Cfg -Pattern "include-system-site-packages = true" -Quiet)
    if (-not $SeesSystem) {
      Write-Host "==> Istniejacy venv nie widzi pakietow systemowych — tworze go ponownie..."
      Remove-Item -Recurse -Force $Venv
    }
  }
  if (-not (Test-Path $VPy)) {
    Write-Host "==> Tworze srodowisko venv (z dostepem do pakietow systemowych)..."
    & $Py -m venv --system-site-packages $Venv
  }
}

# Robust pip install: plain, then --user for system interpreters on failure.
function Pipi {
  & $VPy -m pip install @args
  if ($LASTEXITCODE -ne 0 -and $NoVenv) {
    Write-Host "   (ponawiam z --user)"
    & $VPy -m pip install --user @args
  }
}

if (-not $NoVenv) {
  Write-Host "==> Aktualizuje pip..."
  & $VPy -m pip install --upgrade pip wheel setuptools | Out-Null
}

Write-Host "==> Instaluje warstwe API (FastAPI / uvicorn)..."
Pipi -r (Join-Path $BackendDir "requirements-backend.txt")

Write-Host "==> Instaluje moduly wspolne (yt-dlp, Gemini, Whisper)..."
# No --upgrade: requirements satisfied by system packages are reused as-is.
Pipi -r (Join-Path $BackendDir "requirements-common.txt")
# yt-dlp is the one exception — always pull the newest release (YouTube breaks on old builds).
Write-Host "==> Aktualizuje yt-dlp do najnowszej wersji..."
Pipi --upgrade yt-dlp

# "common" profile installs only the shared base (no heavy engine stack).
if ($Target -eq "common") {
  Write-Host "==> Gotowe. Moduly wspolne (podstawa) zainstalowane."
  exit 0
}

$ReqFile = Join-Path $BackendDir "requirements.txt"
$Label = "pelny stos silnikow (Shorty + DubMaster)"
if ($Target -eq "shorts") {
  $ReqFile = Join-Path $BackendDir "requirements-shorts.txt"
  $Label = "silniki dla Shortow / AI ViralCutter"
} elseif ($Target -eq "dubmaster") {
  $ReqFile = Join-Path $BackendDir "requirements-dubmaster.txt"
  $Label = "silniki dla DubMastera (Demucs + Qwen TTS)"
}

Write-Host "==> Instaluje: $Label"
Pipi -r $ReqFile

Write-Host "==> Gotowe. Srodowisko DubCut Studio jest zainstalowane."
