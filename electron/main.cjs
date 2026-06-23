const { app, BrowserWindow, ipcMain, shell, dialog } = require("electron");
const fs = require("fs");
const path = require("path");
const { spawn, execFileSync } = require("child_process");
const http = require("http");
const {
  checkForUpdates,
  configureUpdater,
  downloadAndInstall,
  getUpdateState,
  refreshHistory,
} = require("./updater.cjs");

const isDev = Boolean(process.env.VITE_DEV_SERVER_URL);
const isWin = process.platform === "win32";

// Native backend runs on a single local port (no Streamlit).
const BACKEND_PORT = Number(process.env.DUBCUT_BACKEND_PORT || 8765);
const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`;

// Vendor engine sources we copy into a writable runtime dir.
const VENDOR = {
  shorts: "ShortsGenerator",
  dub: "DubMaster",
};

let backendProc = null;
let backendReady = false;
const installLogs = { backend: "" };
const installProcesses = new Map();

// --------------------------------------------------------------------------
// Paths
// --------------------------------------------------------------------------
function resourceRoot() {
  return isDev ? app.getAppPath() : process.resourcesPath;
}
function backendDir() {
  return path.join(resourceRoot(), "backend");
}
function bundledVendorDir() {
  return path.join(resourceRoot(), "vendor");
}
function runtimeRoot() {
  return path.join(app.getPath("userData"), "runtime");
}
function dataDir() {
  return path.join(app.getPath("userData"), "data");
}
// App-managed home for the big standalone AI engines (ACE-Step, VideoGenerator).
// They install here automatically — the user never points at a folder by hand.
function enginesDir() {
  return path.join(app.getPath("userData"), "engines");
}
function venvDir() {
  return path.join(runtimeRoot(), "venv");
}
function venvPython() {
  return isWin
    ? path.join(venvDir(), "Scripts", "python.exe")
    : path.join(venvDir(), "bin", "python");
}
function runtimeVendorDir(name) {
  return path.join(runtimeRoot(), "vendor", name);
}

// --------------------------------------------------------------------------
// Python interpreter + PATH resolution
//
// A packaged .app launches from launchd with a stripped PATH that omits the
// user's real Python (Homebrew / python.org framework) and ffmpeg. So we:
//   1. augment PATH for every child process we spawn, and
//   2. detect, by absolute path, the interpreter that actually has the engine
//      deps installed — and run the backend under it. This makes the readiness
//      panel reflect what's truly on the machine instead of an empty fallback.
// --------------------------------------------------------------------------
function augmentedPath() {
  const sep = isWin ? ";" : ":";
  const extra = isWin
    ? []
    : [
        "/opt/homebrew/bin",
        "/opt/homebrew/sbin",
        "/usr/local/bin",
        "/usr/local/sbin",
        "/Library/Frameworks/Python.framework/Versions/3.13/bin",
        "/Library/Frameworks/Python.framework/Versions/3.12/bin",
        "/Library/Frameworks/Python.framework/Versions/3.11/bin",
        "/Library/Frameworks/Python.framework/Versions/3.10/bin",
        path.join(app.getPath("home"), ".local", "bin"),
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
      ];
  const parts = [process.env.PATH || "", ...extra].filter(Boolean);
  return [...new Set(parts.join(sep).split(sep))].filter(Boolean).join(sep);
}

function childEnv(extra = {}) {
  return { ...process.env, PATH: augmentedPath(), ...extra };
}

// Modules probed to score an interpreter. fastapi/uvicorn first = backend-capable.
const PROBE_MODULES = [
  "fastapi", "uvicorn", "yt_dlp", "numpy", "torch", "cv2", "transformers",
  "faster_whisper", "PIL", "soundfile", "demucs", "ultralytics",
  "google.genai", "accelerate", "sentencepiece", "torchaudio", "torchcodec",
  "qwen_tts", "mlx_whisper",
];
const PROBE_SCRIPT =
  "import importlib.util as u,sys\n" +
  `mods=${JSON.stringify(PROBE_MODULES)}\n` +
  "ok=[m for m in mods if u.find_spec(m)]\n" +
  "print(len(ok));print('fastapi' in ok and 'uvicorn' in ok)";

function listPythonExecutables() {
  if (isWin) return ["python", "python3", "py"];
  const found = [];
  const add = (p) => {
    if (p && !found.includes(p) && fs.existsSync(p)) found.push(p);
  };
  // python.org framework builds (newest version first)
  const fwk = "/Library/Frameworks/Python.framework/Versions";
  try {
    const versions = fs.readdirSync(fwk).filter((v) => /^\d/.test(v)).sort().reverse();
    for (const v of versions) add(path.join(fwk, v, "bin", "python3"));
  } catch { /* no framework python */ }
  // Homebrew / /usr/local / system
  for (const base of ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"]) {
    for (const name of ["python3.13", "python3.12", "python3.11", "python3.10", "python3"]) {
      add(path.join(base, name));
    }
  }
  return found;
}

function probePython(py) {
  try {
    const out = execFileSync(py, ["-c", PROBE_SCRIPT], {
      env: childEnv(),
      timeout: 20000,
    }).toString().trim().split(/\r?\n/);
    const score = parseInt(out[0], 10);
    const backendCapable = String(out[1]).trim() === "True";
    return { score: Number.isNaN(score) ? -1 : score, backendCapable };
  } catch {
    return { score: -1, backendCapable: false };
  }
}

let _resolved = null; // { python, score, backendCapable }
function resolvePython({ force = false } = {}) {
  if (_resolved && !force) return _resolved;
  const candidates = [];
  if (fs.existsSync(venvPython())) candidates.push(venvPython());
  candidates.push(...listPythonExecutables());

  let best = null;
  for (const py of candidates) {
    const r = probePython(py);
    if (!best || r.score > best.score) best = { python: py, ...r };
    // a fully-loaded interpreter can't be beaten — stop early
    if (r.score >= PROBE_MODULES.length) break;
  }
  if (!best) {
    best = { python: candidates[0] || (isWin ? "python" : "python3"), score: -1, backendCapable: false };
  }
  _resolved = best;
  return _resolved;
}
function pythonExe() {
  return resolvePython().python;
}
function isInstalled() {
  // "installed" = we have an interpreter that can actually boot the backend.
  return resolvePython().backendCapable;
}

// --------------------------------------------------------------------------
// Runtime preparation (copy writable vendor engines)
// --------------------------------------------------------------------------
function copyDirIfMissing(source, target) {
  if (fs.existsSync(target)) return;
  if (!fs.existsSync(source)) return;
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.cpSync(source, target, {
    recursive: true,
    filter: (src) =>
      !path.basename(src).startsWith("._") &&
      !src.includes(`${path.sep}__pycache__${path.sep}`) &&
      !src.includes(`${path.sep}.venv${path.sep}`),
  });
}

function copyMissingFiles(source, target, extensions) {
  if (!fs.existsSync(source)) return;
  fs.mkdirSync(target, { recursive: true });
  const allowed = new Set(extensions.map((ext) => ext.toLowerCase()));
  for (const name of fs.readdirSync(source)) {
    if (name.startsWith("._")) continue;
    const src = path.join(source, name);
    const stat = fs.statSync(src);
    if (!stat.isFile()) continue;
    if (!allowed.has(path.extname(name).toLowerCase())) continue;
    const dst = path.join(target, name);
    if (!fs.existsSync(dst)) fs.copyFileSync(src, dst);
  }
}

function syncCodeFiles(source, target, extensions) {
  if (!fs.existsSync(source)) return;
  fs.mkdirSync(target, { recursive: true });
  const allowed = new Set(extensions.map((ext) => ext.toLowerCase()));
  for (const name of fs.readdirSync(source)) {
    if (name.startsWith("._")) continue;
    const src = path.join(source, name);
    const dst = path.join(target, name);
    const stat = fs.statSync(src);
    if (stat.isDirectory()) {
      if (["workspace", "__pycache__", ".venv"].includes(name)) continue;
      syncCodeFiles(src, dst, extensions);
      continue;
    }
    if (!stat.isFile() || !allowed.has(path.extname(name).toLowerCase())) continue;
    fs.copyFileSync(src, dst);
  }
}

function ensureRuntimeDirs() {
  fs.mkdirSync(runtimeRoot(), { recursive: true });
  fs.mkdirSync(dataDir(), { recursive: true });
  for (const name of Object.values(VENDOR)) {
    copyDirIfMissing(path.join(bundledVendorDir(), name), runtimeVendorDir(name));
  }
  const bundledShorts = path.join(bundledVendorDir(), VENDOR.shorts);
  const runtimeShorts = runtimeVendorDir(VENDOR.shorts);
  syncCodeFiles(bundledShorts, runtimeShorts, [".py"]);
  copyMissingFiles(path.join(bundledShorts, "fonts"), path.join(runtimeShorts, "fonts"), [".ttf", ".otf"]);
  copyMissingFiles(path.join(bundledShorts, "logo"), path.join(runtimeShorts, "logo"), [".png", ".jpg", ".jpeg", ".webp", ".svg"]);
  // Model assets (AI virtual camera). copyDirIfMissing only fires on a FRESH runtime, so
  // on an existing install a newly-bundled model (e.g. the YuNet face detector .onnx added
  // later) would never land in runtime → the engine silently falls back to weak Haar
  // detection. Sync them every launch: .onnx overwrites (small, may be updated); the large
  // yolov8n.pt is copied only if missing.
  syncCodeFiles(path.join(bundledShorts, "models"), path.join(runtimeShorts, "models"), [".onnx"]);
  copyMissingFiles(path.join(bundledShorts, "models"), path.join(runtimeShorts, "models"), [".pt"]);
}

// --------------------------------------------------------------------------
// Logs
// --------------------------------------------------------------------------
function appendLog(channel, text) {
  const clean = String(text).replace(/\r/g, "");
  const current = installLogs[channel] ?? "";
  installLogs[channel] = `${current}${clean}`.split("\n").slice(-800).join("\n");
  for (const win of BrowserWindow.getAllWindows()) {
    win.webContents.send("backend:log", { channel, line: clean });
  }
}

// --------------------------------------------------------------------------
// Backend lifecycle
// --------------------------------------------------------------------------
// Locate the system-installed ACE-Step engine (50+ GB checkpoints — never bundled).
// Probed by absolute path so the packaged app finds the same install the user already
// has. The user can also override the path in Settings (app.ace_dir).
function detectAceDir() {
  const fromEnv = process.env.DUBCUT_ACE_DIR;
  if (fromEnv && fs.existsSync(fromEnv)) return fromEnv;
  // System-only: the engine lives in the app-managed engines dir, installed by the
  // app itself. External-disk / sibling-folder copies are NOT auto-detected — a user
  // who really wants one points at it explicitly via the Settings advanced override.
  const c = path.join(enginesDir(), "ACE-Step-1.5");
  return fs.existsSync(path.join(c, "pyproject.toml")) ? c : "";
}

// Locate the system-installed VideoGenerator project (image + video, ~45 GB models).
function detectVideogenDir() {
  const fromEnv = process.env.DUBCUT_VIDEOGEN_DIR;
  if (fromEnv && fs.existsSync(fromEnv)) return fromEnv;
  // System-only (see detectAceDir): app-managed engines dir only, no external-disk auto-detect.
  const c = path.join(enginesDir(), "VideoGenerator");
  return fs.existsSync(path.join(c, "vendor", "ltx-2-mlx")) ? c : "";
}

function backendEnv() {
  const aceDir = detectAceDir();
  const videogenDir = detectVideogenDir();
  return childEnv({
    DUBCUT_DATA_DIR: dataDir(),
    DUBCUT_SHORTS_DIR: runtimeVendorDir(VENDOR.shorts),
    DUBCUT_DUB_DIR: runtimeVendorDir(VENDOR.dub),
    ...(aceDir ? { DUBCUT_ACE_DIR: aceDir } : {}),
    ...(videogenDir ? { DUBCUT_VIDEOGEN_DIR: videogenDir } : {}),
    DUBCUT_ENGINES_DIR: enginesDir(),
    DUBCUT_BACKEND_PORT: String(BACKEND_PORT),
    PYTHONPATH: path.join(backendDir(), "shims"),
    PYTHONUNBUFFERED: "1",
    OBJC_DISABLE_INITIALIZE_FORK_SAFETY: "YES",
    // Let unsupported MPS ops fall back to CPU per-op instead of throwing — without
    // this one stray op in NLLB's generate() dropped the whole translation session
    // to CPU (20-40x slower), which is why many languages used to take hours.
    PYTORCH_ENABLE_MPS_FALLBACK: "1",
    TOKENIZERS_PARALLELISM: "false",
    // Single source of truth for the version shown in the header — taken from
    // package.json via Electron, so it never drifts from the actual build.
    DUBCUT_VERSION: app.getVersion(),
  });
}

function pingBackend() {
  return new Promise((resolve) => {
    const req = http.get(
      { host: "127.0.0.1", port: BACKEND_PORT, path: "/api/health", timeout: 800 },
      (res) => {
        res.resume();
        resolve(res.statusCode === 200);
      },
    );
    req.on("timeout", () => {
      req.destroy();
      resolve(false);
    });
    req.on("error", () => resolve(false));
  });
}

async function waitForBackend(timeoutMs = 30000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (await pingBackend()) {
      backendReady = true;
      return true;
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  return false;
}

// POST JSON to the local backend and resolve the parsed JSON reply.
function backendPostJson(routePath, body) {
  return new Promise((resolve, reject) => {
    const payload = Buffer.from(JSON.stringify(body || {}), "utf8");
    const req = http.request(
      {
        host: "127.0.0.1",
        port: BACKEND_PORT,
        path: routePath,
        method: "POST",
        timeout: 15000,
        headers: { "Content-Type": "application/json", "Content-Length": payload.length },
      },
      (res) => {
        let data = "";
        res.on("data", (c) => (data += c));
        res.on("end", () => {
          if (res.statusCode && res.statusCode >= 200 && res.statusCode < 300) {
            try { resolve(JSON.parse(data)); } catch { reject(new Error("bad json")); }
          } else {
            reject(new Error(`backend ${res.statusCode}`));
          }
        });
      },
    );
    req.on("timeout", () => { req.destroy(); reject(new Error("timeout")); });
    req.on("error", reject);
    req.write(payload);
    req.end();
  });
}

// Read the version reported by a backend already answering on our port.
function fetchBackendVersion() {
  return new Promise((resolve) => {
    const req = http.get(
      { host: "127.0.0.1", port: BACKEND_PORT, path: "/api/health", timeout: 800 },
      (res) => {
        let data = "";
        res.on("data", (c) => (data += c));
        res.on("end", () => {
          try {
            resolve(JSON.parse(data).version || null);
          } catch {
            resolve(null);
          }
        });
      },
    );
    req.on("timeout", () => {
      req.destroy();
      resolve(null);
    });
    req.on("error", () => resolve(null));
  });
}

// Kill whatever process is holding BACKEND_PORT. Used to reclaim the port from a
// stale/orphaned backend that survived an in-place upgrade (otherwise the new UI
// silently adopts the old backend and runs out-of-date code).
function killPortListeners(port, sig = "SIGTERM") {
  if (isWin) {
    try {
      const out = execFileSync("cmd", ["/c", `netstat -ano | findstr :${port}`], {
        timeout: 4000,
      }).toString();
      const pids = new Set();
      for (const line of out.split(/\r?\n/)) {
        const m = line.trim().match(/LISTENING\s+(\d+)\s*$/);
        if (m) pids.add(m[1]);
      }
      for (const pid of pids) {
        try {
          execFileSync("taskkill", ["/PID", pid, "/F", "/T"], { timeout: 4000 });
        } catch {}
      }
    } catch {}
    return;
  }
  try {
    const out = execFileSync("lsof", ["-ti", `tcp:${port}`], { env: childEnv(), timeout: 4000 })
      .toString()
      .trim();
    for (const pid of out.split(/\s+/).filter(Boolean)) {
      try {
        process.kill(Number(pid), sig);
      } catch {}
    }
  } catch {
    /* nothing listening on the port */
  }
}

async function waitPortFree(timeoutMs = 6000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (!(await pingBackend())) return true;
    await new Promise((r) => setTimeout(r, 300));
  }
  return false;
}

// Ensure the running backend is OURS and matches this build before adopting it.
// A backend left over from a previous version (orphaned by an in-place upgrade)
// keeps holding port 8765 — the freshly-launched backend then can't bind and the
// UI ends up talking to stale code. So if anything is already on the port whose
// version differs from this app, we reclaim the port and spawn our own backend.
async function startBackendReconciled() {
  if (!isInstalled()) return;
  if (await pingBackend()) {
    const ver = await fetchBackendVersion();
    // Already OUR own current-version child → leave it (and any running jobs) alone.
    // Without this, invoking this fn again (e.g. the "Uruchom silniki" button →
    // backend:start IPC) would kill a healthy backend mid-render. A stale process
    // can't be our child (it would have lost the port → our child exited → backendProc
    // is null), so `backendProc && ver===version` reliably means "ours and current".
    if (backendProc && ver === app.getVersion()) {
      appendLog("backend", `Backend (${ver}) już działa jako nasz proces — nie restartuję.\n`);
      return;
    }
    if (ver === app.getVersion() && !backendProc) {
      // Current version, but not our child — still reclaim so we own its lifecycle
      // (and so quitting reliably stops it). Cheap restart, guarantees correctness.
      appendLog("backend", `Przejmuję backend na porcie ${BACKEND_PORT} (wersja ${ver}).\n`);
    } else if (ver !== app.getVersion()) {
      appendLog(
        "backend",
        `Nieaktualny backend na porcie ${BACKEND_PORT} (${ver || "?"} ≠ ${app.getVersion()}). Restartuję…\n`,
      );
    }
    killPortListeners(BACKEND_PORT);
    if (!(await waitPortFree(5000))) {
      killPortListeners(BACKEND_PORT, "SIGKILL");
      await waitPortFree(5000);
    }
  }
  startBackend();
}

function startBackend() {
  if (backendProc || !isInstalled()) return;
  const server = path.join(backendDir(), "server.py");
  if (!fs.existsSync(server)) {
    appendLog("backend", `Brak pliku backendu: ${server}\n`);
    return;
  }
  const py = pythonExe();
  appendLog("backend", `Uruchamianie natywnego backendu DubCut…\n    Python: ${py}\n`);
  backendProc = spawn(py, [server], {
    cwd: backendDir(),
    env: backendEnv(),
    stdio: ["ignore", "pipe", "pipe"],
  });
  backendProc.stdout.on("data", (c) => appendLog("backend", c.toString()));
  backendProc.stderr.on("data", (c) => appendLog("backend", c.toString()));
  backendProc.on("exit", (code) => {
    appendLog("backend", `\nBackend zakończył się kodem ${code ?? "?"}.\n`);
    backendProc = null;
    backendReady = false;
  });
  waitForBackend().then((ok) => {
    appendLog("backend", ok ? "Backend gotowy.\n" : "Backend nie odpowiada.\n");
    broadcastStatus();
  });
}

// Free the ACE-Step engine (port 8001) so it never lingers in RAM after we quit.
function stopAceEngine() {
  if (isWin) return;
  try {
    const out = execFileSync("lsof", ["-ti", "tcp:8001"], { env: childEnv(), timeout: 4000 })
      .toString()
      .trim();
    for (const pid of out.split(/\s+/).filter(Boolean)) {
      try {
        process.kill(Number(pid), "SIGTERM");
      } catch {}
    }
  } catch {
    /* engine not running */
  }
}

function stopBackend() {
  if (backendProc) {
    try {
      backendProc.kill();
    } catch {}
    backendProc = null;
  }
  stopAceEngine();
}

async function backendStatus() {
  const r = resolvePython();
  return {
    installed: isInstalled(),
    running: backendProc !== null,
    ready: backendReady && (await pingBackend()),
    url: BACKEND_URL,
    port: BACKEND_PORT,
    installing: installProcesses.has("backend"),
    dataDir: dataDir(),
    runtimeDir: runtimeRoot(),
    python: r.python,
    pythonScore: r.score,
  };
}

function broadcastStatus() {
  backendStatus().then((status) => {
    for (const win of BrowserWindow.getAllWindows()) {
      win.webContents.send("backend:status", status);
    }
  });
}

// --------------------------------------------------------------------------
// Installer
// --------------------------------------------------------------------------
function installScriptPath() {
  return isWin
    ? path.join(backendDir(), "install.ps1")
    : path.join(backendDir(), "install.sh");
}

function runInstaller(target = "all") {
  if (installProcesses.has("backend")) return;
  const profile = ["common", "shorts", "dubmaster", "all", "music", "videogen"].includes(target) ? target : "all";
  ensureRuntimeDirs();
  fs.mkdirSync(enginesDir(), { recursive: true });
  const script = installScriptPath();
  if (!fs.existsSync(script)) {
    appendLog("backend", `Brak instalatora: ${script}\n`);
    return;
  }
  installLogs.backend = "";
  const labels = {
    common: "moduły wspólne (podstawa)",
    shorts: "Shorty / AI ViralCutter",
    dubmaster: "DubMaster + Tekst→Audio",
    all: "pełny zestaw",
    music: "silnik muzyki (ACE-Step)",
    videogen: "silnik obrazu/wideo (LTX)",
  };
  // Install missing packages straight into the detected system interpreter, so
  // already-present modules are reused and only the gaps are downloaded.
  const targetPython = pythonExe();
  appendLog(
    "backend",
    `Start instalacji: ${labels[profile]} — doinstalowuję brakujące pakiety do:\n    ${targetPython}\n`,
  );

  const env = {
    ...childEnv(),
    DUBCUT_PYTHON: targetPython,
    DUBCUT_NO_VENV: "1",
    DUBCUT_VENV: venvDir(),
    DUBCUT_BACKEND_DIR: backendDir(),
    DUBCUT_ENGINES_DIR: enginesDir(),
    DUBCUT_INSTALL_TARGET: profile,
    CI: "1",
  };
  let child;
  if (isWin) {
    child = spawn("powershell.exe", ["-ExecutionPolicy", "Bypass", "-File", script], {
      cwd: backendDir(),
      env,
      stdio: ["ignore", "pipe", "pipe"],
    });
  } else {
    fs.chmodSync(script, 0o755);
    child = spawn("/bin/bash", [script], {
      cwd: backendDir(),
      env,
      stdio: ["ignore", "pipe", "pipe"],
    });
  }
  installProcesses.set("backend", child);
  child.stdout.on("data", (c) => appendLog("backend", c.toString()));
  child.stderr.on("data", (c) => appendLog("backend", c.toString()));
  child.on("exit", (code) => {
    installProcesses.delete("backend");
    appendLog(
      "backend",
      code === 0
        ? "\nInstalacja zakończona. Uruchamiam backend…\n"
        : `\nInstalacja zakończyła się kodem ${code ?? "?"}.\n`,
    );
    // Deps changed — re-probe so readiness + the chosen interpreter are fresh.
    resolvePython({ force: true });
    if (code === 0) {
      if (backendProc) stopBackend();
      startBackend();
    }
    broadcastStatus();
  });
  broadcastStatus();
}

const UNINSTALL_PACKAGES = {
  common: ["yt-dlp", "google-genai", "faster-whisper", "mlx-whisper", "argostranslate", "langdetect"],
  shorts: ["opencv-python", "opencv-python-headless", "ultralytics"],
  dubmaster: [
    "torchcodec",
    "demucs",
    "transformers",
    "accelerate",
    "sentencepiece",
    "qwen_tts",
  ],
};
// Foundational libraries shared with the rest of the machine — never auto-remove
// these, even on "Usuń wszystko", or we'd break the user's other Python work.
const UNINSTALL_PROTECTED = new Set([
  "numpy", "requests", "certifi", "urllib3", "idna", "charset-normalizer",
  "pillow", "torch", "torchaudio", "soundfile", "safetensors",
]);

// Standalone engines are removed by deleting their managed folder (not pip).
const ENGINE_DIRS = {
  music: "ACE-Step-1.5",
  videogen: "VideoGenerator",
};

function runUninstaller(target = "all") {
  if (installProcesses.has("backend")) return;

  // Music / Obraz·Wideo: just remove the engine folder (models included).
  if (ENGINE_DIRS[target]) {
    installLogs.backend = "";
    const dir = path.join(enginesDir(), ENGINE_DIRS[target]);
    appendLog("backend", `Odinstalowuję silnik (${target}) — usuwam folder:\n    ${dir}\n`);
    try {
      if (fs.existsSync(dir)) fs.rmSync(dir, { recursive: true, force: true });
      appendLog("backend", "Silnik usunięty. Modele w pamięci podręcznej Hugging Face zostają (współdzielone).\n");
    } catch (err) {
      appendLog("backend", `Błąd usuwania silnika: ${err?.message || err}\n`);
    }
    broadcastStatus();
    return;
  }

  const profile = ["common", "shorts", "dubmaster", "all"].includes(target) ? target : "all";
  stopBackend();
  backendReady = false;
  installLogs.backend = "";

  // Always clear the legacy app-local venv overlay if one exists.
  if (fs.existsSync(venvDir())) {
    appendLog("backend", "Usuwam stare środowisko venv aplikacji…\n");
    try {
      fs.rmSync(venvDir(), { recursive: true, force: true });
    } catch (err) {
      appendLog("backend", `Błąd usuwania venv: ${err?.message || err}\n`);
    }
  }

  let packages =
    profile === "all"
      ? [...UNINSTALL_PACKAGES.shorts, ...UNINSTALL_PACKAGES.dubmaster, "yt-dlp", "google-genai", "faster-whisper"]
      : [...(UNINSTALL_PACKAGES[profile] || [])];
  packages = packages.filter((p) => !UNINSTALL_PROTECTED.has(p.toLowerCase()));

  const py = pythonExe();
  if (!packages.length) {
    appendLog("backend", "Brak pakietów do bezpiecznego odinstalowania (współdzielone biblioteki zachowano).\n");
    resolvePython({ force: true });
    broadcastStatus();
    return;
  }
  appendLog(
    "backend",
    `Odinstalowuję moduł: ${profile} z systemowego Pythona…\n    ${py}\n` +
      "    (współdzielone biblioteki jak numpy/torch/Pillow są zachowane)\n",
  );
  const child = spawn(py, ["-m", "pip", "uninstall", "-y", ...packages], {
    cwd: backendDir(),
    env: childEnv({ DUBCUT_BACKEND_DIR: backendDir() }),
    stdio: ["ignore", "pipe", "pipe"],
  });
  installProcesses.set("backend", child);
  child.stdout.on("data", (c) => appendLog("backend", c.toString()));
  child.stderr.on("data", (c) => appendLog("backend", c.toString()));
  child.on("exit", (code) => {
    installProcesses.delete("backend");
    appendLog("backend", code === 0 ? "\nOdinstalowanie zakończone.\n" : `\nOdinstalowanie zakończyło się kodem ${code ?? "?"}.\n`);
    resolvePython({ force: true });
    startBackend();
    broadcastStatus();
  });
  broadcastStatus();
}

// --------------------------------------------------------------------------
// Window
// --------------------------------------------------------------------------
function windowStateFile() {
  return path.join(app.getPath("userData"), "window-state.json");
}
function readWindowState() {
  try {
    return JSON.parse(fs.readFileSync(windowStateFile(), "utf-8"));
  } catch {
    return {};
  }
}
function saveWindowState(win) {
  if (!win || win.isDestroyed()) return;
  const state = { ...win.getBounds(), maximized: win.isMaximized() };
  fs.mkdirSync(app.getPath("userData"), { recursive: true });
  fs.writeFileSync(windowStateFile(), JSON.stringify(state, null, 2));
}

async function createWindow() {
  const ws = readWindowState();
  const win = new BrowserWindow({
    width: ws.width ?? 1480,
    height: ws.height ?? 940,
    x: ws.x,
    y: ws.y,
    minWidth: 1080,
    minHeight: 720,
    title: "Opitkovanie AI Studio",
    backgroundColor: "#0a0a0e",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      webSecurity: false,
    },
  });
  if (ws.maximized) win.maximize();

  let saveTimer = null;
  const scheduleSave = () => {
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(() => saveWindowState(win), 350);
  };
  win.on("resize", scheduleSave);
  win.on("move", scheduleSave);
  win.on("maximize", scheduleSave);
  win.on("unmaximize", scheduleSave);
  win.on("close", () => saveWindowState(win));

  if (isDev) {
    await win.loadURL(process.env.VITE_DEV_SERVER_URL);
  } else {
    await win.loadFile(path.join(app.getAppPath(), "dist", "index.html"));
  }
}

// --------------------------------------------------------------------------
// IPC
// --------------------------------------------------------------------------
ipcMain.handle("backend:status", () => backendStatus());
ipcMain.handle("updates:status", () => getUpdateState());
ipcMain.handle("updates:check", () => checkForUpdates());
ipcMain.handle("updates:download", () => downloadAndInstall());
ipcMain.handle("updates:history", () => refreshHistory());
ipcMain.handle("backend:install", (_event, target = "all") => {
  runInstaller(target);
  return backendStatus();
});
ipcMain.handle("backend:uninstall", (_event, target = "all") => {
  runUninstaller(target);
  return backendStatus();
});
ipcMain.handle("backend:start", async () => {
  await startBackendReconciled();
  return backendStatus();
});
ipcMain.handle("backend:logs", () => installLogs.backend ?? "");
ipcMain.handle("backend:openRuntime", () => {
  ensureRuntimeDirs();
  shell.openPath(runtimeRoot());
  return backendStatus();
});

ipcMain.handle("project:chooseVideo", async () => {
  const result = await dialog.showOpenDialog({
    title: "Wybierz plik wideo",
    properties: ["openFile"],
    filters: [
      { name: "Wideo", extensions: ["mp4", "mov", "mkv", "webm", "m4v", "avi"] },
      { name: "Audio", extensions: ["mp3", "wav", "m4a", "aac", "flac"] },
      { name: "Wszystkie pliki", extensions: ["*"] },
    ],
  });
  if (result.canceled || !result.filePaths[0]) return null;
  const filePath = result.filePaths[0];
  const stat = fs.statSync(filePath);
  return {
    path: filePath,
    name: path.basename(filePath),
    size: stat.size,
    url: `file://${filePath}`,
  };
});

ipcMain.handle("project:chooseLogo", async () => {
  ensureRuntimeDirs();
  const result = await dialog.showOpenDialog({
    title: "Wybierz logo",
    properties: ["openFile"],
    filters: [
      { name: "Obrazy", extensions: ["png", "jpg", "jpeg", "webp", "svg"] },
      { name: "Wszystkie pliki", extensions: ["*"] },
    ],
  });
  if (result.canceled || !result.filePaths[0]) return null;
  const filePath = result.filePaths[0];
  const ext = path.extname(filePath).toLowerCase();
  const base = path
    .basename(filePath, ext)
    .replace(/[^a-z0-9ąćęłńóśźż _.-]/gi, "")
    .trim()
    .replace(/\s+/g, "-")
    .slice(0, 80) || "logo";
  const targetName = `custom-${Date.now()}-${base}${ext}`;
  const targetDir = path.join(runtimeVendorDir(VENDOR.shorts), "logo");
  fs.mkdirSync(targetDir, { recursive: true });
  const target = path.join(targetDir, targetName);
  fs.copyFileSync(filePath, target);
  const stat = fs.statSync(target);
  return {
    path: `logo/${targetName}`,
    name: targetName,
    size: stat.size,
    url: `file://${target}`,
  };
});

ipcMain.handle("project:chooseWorkDir", async () => {
  const result = await dialog.showOpenDialog({
    title: "Wybierz folder roboczy (pliki tymczasowe)",
    properties: ["openDirectory", "createDirectory"],
  });
  if (result.canceled || !result.filePaths[0]) return null;
  return { path: result.filePaths[0] };
});

ipcMain.handle("project:openPath", (_event, target) => {
  if (target && typeof target === "string" && fs.existsSync(target)) {
    shell.openPath(target);
    return true;
  }
  return false;
});

ipcMain.handle("project:revealPath", (_event, target) => {
  if (target && typeof target === "string" && fs.existsSync(target)) {
    shell.showItemInFolder(target);
    return true;
  }
  return false;
});

ipcMain.handle("project:chooseImage", async () => {
  const result = await dialog.showOpenDialog({
    title: "Wybierz obraz bazowy",
    properties: ["openFile"],
    filters: [
      { name: "Obrazy", extensions: ["png", "jpg", "jpeg", "webp"] },
      { name: "Wszystkie pliki", extensions: ["*"] },
    ],
  });
  if (result.canceled || !result.filePaths[0]) return null;
  const filePath = result.filePaths[0];
  // Let the backend copy it into the WORK folder (it owns work_root), so the base image
  // is deleted together with the clips and never orphaned in the config root.
  try {
    const r = await backendPostJson("/api/videogen/ingest-image", { path: filePath });
    if (r && r.path) return { path: r.path, name: r.name, size: r.size, url: `${BACKEND_URL}${r.url}` };
  } catch { /* backend down — fall back to a local copy */ }
  const ext = path.extname(filePath).toLowerCase() || ".png";
  const targetDir = path.join(dataDir(), "videogen_input");
  fs.mkdirSync(targetDir, { recursive: true });
  const target = path.join(targetDir, `base-${Date.now()}${ext}`);
  fs.copyFileSync(filePath, target);
  const stat = fs.statSync(target);
  return { path: target, name: path.basename(filePath), size: stat.size, url: `file://${target}` };
});

ipcMain.handle("project:chooseVoiceSample", async () => {
  ensureRuntimeDirs();
  const result = await dialog.showOpenDialog({
    title: "Wybierz próbkę głosu",
    properties: ["openFile"],
    filters: [
      { name: "Audio / wideo z głosem", extensions: ["wav", "mp3", "m4a", "aac", "flac", "mp4", "mov", "mkv", "webm"] },
      { name: "Wszystkie pliki", extensions: ["*"] },
    ],
  });
  if (result.canceled || !result.filePaths[0]) return null;
  const filePath = result.filePaths[0];
  const ext = path.extname(filePath).toLowerCase() || ".wav";
  const base = path
    .basename(filePath, ext)
    .replace(/[^a-z0-9ąćęłńóśźż _.-]/gi, "")
    .trim()
    .replace(/\s+/g, "-")
    .slice(0, 80) || "voice-sample";
  const targetName = `custom-${Date.now()}-${base}${ext}`;
  const targetDir = path.join(runtimeVendorDir(VENDOR.shorts), "workspace", "voice_samples");
  fs.mkdirSync(targetDir, { recursive: true });
  const target = path.join(targetDir, targetName);
  fs.copyFileSync(filePath, target);
  const stat = fs.statSync(target);
  return {
    path: target,
    name: targetName,
    size: stat.size,
    url: `file://${target}`,
  };
});

// --------------------------------------------------------------------------
// App lifecycle
// --------------------------------------------------------------------------
app.whenReady().then(async () => {
  ensureRuntimeDirs();
  await startBackendReconciled();
  await createWindow();
  configureUpdater((update) => {
    for (const win of BrowserWindow.getAllWindows()) win.webContents.send("updates:status", update);
  });
  // Checking starts after the UI exists so its status is visible immediately.
  checkForUpdates();
});

app.on("window-all-closed", () => {
  stopBackend();
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", stopBackend);

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});
