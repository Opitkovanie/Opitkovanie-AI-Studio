const { contextBridge, ipcRenderer, webUtils } = require("electron");

contextBridge.exposeInMainWorld("dubcut", {
  // Native backend lifecycle
  getStatus: () => ipcRenderer.invoke("backend:status"),
  getUpdateStatus: () => ipcRenderer.invoke("updates:status"),
  checkUpdates: () => ipcRenderer.invoke("updates:check"),
  downloadUpdate: () => ipcRenderer.invoke("updates:download"),
  getUpdateHistory: () => ipcRenderer.invoke("updates:history"),
  install: (target = "all") => ipcRenderer.invoke("backend:install", target),
  uninstall: (target = "all") => ipcRenderer.invoke("backend:uninstall", target),
  startBackend: () => ipcRenderer.invoke("backend:start"),
  getLogs: () => ipcRenderer.invoke("backend:logs"),
  openRuntime: () => ipcRenderer.invoke("backend:openRuntime"),
  chooseVideo: () => ipcRenderer.invoke("project:chooseVideo"),
  chooseLogo: () => ipcRenderer.invoke("project:chooseLogo"),
  chooseVoiceSample: () => ipcRenderer.invoke("project:chooseVoiceSample"),
  chooseImage: () => ipcRenderer.invoke("project:chooseImage"),
  chooseWorkDir: () => ipcRenderer.invoke("project:chooseWorkDir"),
  openPath: (path) => ipcRenderer.invoke("project:openPath", path),
  revealPath: (path) => ipcRenderer.invoke("project:revealPath", path),
  // Electron 42 removed File.path — webUtils resolves the absolute path of a dropped file.
  getPathForFile: (file) => {
    try {
      return webUtils.getPathForFile(file);
    } catch {
      return "";
    }
  },

  onLog: (callback) => {
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on("backend:log", listener);
    return () => ipcRenderer.removeListener("backend:log", listener);
  },
  onStatus: (callback) => {
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on("backend:status", listener);
    return () => ipcRenderer.removeListener("backend:status", listener);
  },
  onUpdateStatus: (callback) => {
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on("updates:status", listener);
    return () => ipcRenderer.removeListener("updates:status", listener);
  },
});
