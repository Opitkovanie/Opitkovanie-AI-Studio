const { app, net } = require("electron");
const { autoUpdater } = require("electron-updater");

const UPDATE_REPOSITORY = "Opitkovanie/Opitkovanie-AI-Studio";
const RELEASES_API_URL = `https://api.github.com/repos/${UPDATE_REPOSITORY}/releases?per_page=12`;

const initialState = () => ({
  status: app.isPackaged ? "idle" : "development",
  currentVersion: app.getVersion(),
  latestVersion: null,
  releaseDate: null,
  releaseNotes: "",
  history: [],
  progress: null,
  message: app.isPackaged ? "" : "Aktualizacje są dostępne w zainstalowanej aplikacji.",
});

let state = initialState();
let isConfigured = false;
let emitState = () => {};

function normaliseNotes(notes) {
  if (Array.isArray(notes)) return notes.map((note) => note.note || "").filter(Boolean).join("\n\n");
  return notes || "";
}

function releaseSummary(release) {
  return {
    version: String(release.tag_name || release.name || "").replace(/^v/i, ""),
    name: release.name || release.tag_name || "",
    publishedAt: release.published_at || null,
    notes: release.body || "",
    url: release.html_url || "",
  };
}

function publishState(patch) {
  state = { ...state, ...patch };
  emitState({ ...state });
  return state;
}

function fetchReleaseHistory() {
  return new Promise((resolve, reject) => {
    const request = net.request({ method: "GET", url: RELEASES_API_URL });
    request.setHeader("Accept", "application/vnd.github+json");
    request.setHeader("User-Agent", "Opitkovanie-AI-Studio");
    request.setHeader("X-GitHub-Api-Version", "2022-11-28");
    let body = "";
    request.on("response", (response) => {
      response.on("data", (chunk) => { body += chunk.toString(); });
      response.on("end", () => {
        if (response.statusCode < 200 || response.statusCode >= 300) {
          reject(new Error(`GitHub Releases zwrócił HTTP ${response.statusCode}.`));
          return;
        }
        try {
          resolve(JSON.parse(body).filter((release) => !release.draft).map(releaseSummary));
        } catch {
          reject(new Error("Nie udało się odczytać historii wydań GitHub."));
        }
      });
    });
    request.on("error", reject);
    request.end();
  });
}

async function refreshHistory() {
  try {
    const history = await fetchReleaseHistory();
    publishState({ history });
    return history;
  } catch (error) {
    // The actual updater still remains the source of truth for available builds.
    publishState({ message: error.message });
    return state.history;
  }
}

function configureUpdater(onState) {
  emitState = onState || (() => {});
  if (isConfigured || !app.isPackaged) return;
  isConfigured = true;
  autoUpdater.autoDownload = false;
  autoUpdater.autoInstallOnAppQuit = false;
  autoUpdater.fullChangelog = true;

  autoUpdater.on("checking-for-update", () => publishState({ status: "checking", message: "Sprawdzam dostępność aktualizacji…", progress: null }));
  autoUpdater.on("update-available", (info) => publishState({
    status: "available",
    latestVersion: info.version,
    releaseDate: info.releaseDate || null,
    releaseNotes: normaliseNotes(info.releaseNotes),
    message: "Nowa wersja jest gotowa do pobrania.",
  }));
  autoUpdater.on("update-not-available", (info) => publishState({
    status: "current",
    latestVersion: info.version || app.getVersion(),
    releaseDate: info.releaseDate || null,
    releaseNotes: normaliseNotes(info.releaseNotes),
    message: "Masz najnowszą wersję aplikacji.",
  }));
  autoUpdater.on("download-progress", (progress) => publishState({
    status: "downloading",
    progress: Math.round(progress.percent),
    message: `Pobieranie aktualizacji: ${Math.round(progress.percent)}%`,
  }));
  autoUpdater.on("update-downloaded", (info) => publishState({
    status: "downloaded",
    latestVersion: info.version,
    releaseDate: info.releaseDate || state.releaseDate,
    releaseNotes: normaliseNotes(info.releaseNotes) || state.releaseNotes,
    progress: 100,
    message: "Aktualizacja pobrana. Aplikacja uruchomi się ponownie.",
  }));
  autoUpdater.on("error", (error) => publishState({ status: "error", message: error.message || "Nie udało się sprawdzić aktualizacji.", progress: null }));
}

async function checkForUpdates() {
  if (!app.isPackaged) return publishState(initialState());
  configureUpdater(emitState);
  refreshHistory();
  try {
    await autoUpdater.checkForUpdates();
  } catch (error) {
    publishState({ status: "error", message: error.message || "Nie udało się sprawdzić aktualizacji." });
  }
  return state;
}

async function downloadAndInstall() {
  if (!app.isPackaged) return publishState(initialState());
  if (state.status !== "available") return state;
  try {
    await autoUpdater.downloadUpdate();
    autoUpdater.quitAndInstall();
  } catch (error) {
    publishState({ status: "error", message: error.message || "Nie udało się pobrać aktualizacji." });
  }
  return state;
}

module.exports = {
  checkForUpdates,
  configureUpdater,
  downloadAndInstall,
  getUpdateState: () => ({ ...state }),
  refreshHistory,
};
