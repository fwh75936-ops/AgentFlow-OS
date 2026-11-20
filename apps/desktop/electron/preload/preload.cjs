// Restricted preload bridge: the renderer gets ONLY these two functions.
// No Node APIs, no arbitrary IPC channels, no API keys pass through here.
// NOTE: sandboxed preload scripts must be CommonJS — this file is .cjs on
// purpose (an ESM preload fails to load under sandbox: "Cannot use import
// statement outside a module", found in the window-level check).
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("agentflow", {
  getBackendUrl: () => ipcRenderer.invoke("agentflow:get-backend-url"),
  backendStatus: () => ipcRenderer.invoke("agentflow:backend-status"),
  retryBackend: () => ipcRenderer.invoke("agentflow:retry-backend"),
  selectFile: () => ipcRenderer.invoke("agentflow:select-file"),
  openPath: (p) => ipcRenderer.invoke("agentflow:open-path", p),
});
