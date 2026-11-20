// Electron main process: window/tray/lifecycle + Python service management.
// Security: contextIsolation ON, nodeIntegration OFF, sandboxed preload,
// CSP injected for the app origin, IPC restricted to known channels.
import { app, BrowserWindow, ipcMain, dialog, session, Tray, Menu, nativeImage } from "electron";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { startPythonService, stopPythonService, getBackendState, restartPythonService } from "../services/pythonService.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
// electron/main -> electron -> desktop -> apps -> repo root, then services/
// (four levels up; three resolved to apps/services/... and the Python
// service silently failed to spawn — found in the window-level check).
const CORE_DIR = path.resolve(__dirname, "../../../../services/agent-core");
let win = null;
let tray = null;
let uiLoaded = false;

function backendOptions() {
  if (!app.isPackaged) return { mode: "dev", coreDir: CORE_DIR };
  const backendDir = path.join(process.resourcesPath, "backend");
  return {
    mode: "packaged",
    coreDir: path.join(backendDir, "agent-core"),
    pythonExe: path.join(backendDir, "python", "python.exe"),
    seedDb: path.join(backendDir, "seed", "agentflow.db"),
    dataDir: path.join(app.getPath("userData"), "agentflow-data"),
  };
}

function appIndexPath() {
  return path.join(__dirname, "../../dist/index.html");
}

/** Packaged builds gate the real UI on the backend's /health: the window
 * shows a loading page first and only swaps to the app once the bundled
 * Python service actually answers. If it never does, the loading page
 * renders the failure honestly (reason + backend log + retry). */
function watchBackendGate() {
  const timer = setInterval(() => {
    if (!win) { clearInterval(timer); return; }
    const s = getBackendState();
    if (s.status === "ready" && !uiLoaded) {
      uiLoaded = true;
      clearInterval(timer);
      void win.loadFile(appIndexPath());
    } else if (s.status === "failed") {
      clearInterval(timer); // loading page polls state itself and offers retry
    }
  }, 500);
}

function createWindow() {
  win = new BrowserWindow({
    width: 1360, height: 880, minWidth: 1024, minHeight: 680,
    backgroundColor: "#FFFFFF",
    icon: path.join(__dirname, "../assets/icon.png"),
    webPreferences: {
      preload: path.join(__dirname, "../preload/preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  // Content Security Policy: only local app content + local agent-core API.
  session.defaultSession.webRequest.onHeadersReceived((details, cb) => {
    cb({ responseHeaders: {
      ...details.responseHeaders,
      "Content-Security-Policy": [
        "default-src 'self'; connect-src 'self' http://127.0.0.1:8000 ws://127.0.0.1:8000; " +
        "img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'",
      ],
    } });
  });
  const devUrl = process.env.VITE_DEV_SERVER_URL;
  if (devUrl) void win.loadURL(devUrl);
  else if (app.isPackaged) void win.loadFile(path.join(__dirname, "../assets/loading.html"));
  else void win.loadFile(appIndexPath());
  win.on("closed", () => { win = null; });
}

function showWindow() {
  if (!win) { createWindow(); return; }
  if (win.isMinimized()) win.restore();
  win.show(); win.focus();
}

function createTray() {
  const icon = nativeImage.createFromPath(path.join(__dirname, "../assets/icon.png"));
  tray = new Tray(icon);
  tray.setToolTip("AgentFlow OS");
  const menu = Menu.buildFromTemplate([
    { label: "显示窗口", click: showWindow },
    { label: "隐藏窗口", click: () => { if (win) win.hide(); } },
    { type: "separator" },
    { label: "退出", click: () => app.quit() },
  ]);
  tray.setContextMenu(menu);
  tray.on("click", () => {
    if (!win) { createWindow(); return; }
    if (win.isVisible()) win.hide(); else showWindow();
  });
}

// Auto-update integration point (electron-updater).
// HONEST STATE: no publish source is configured for this project, so
// updates are DISABLED by default. They activate only for a packaged
// build (app.isPackaged) when AGENTFLOW_UPDATE_URL points at a real
// generic update feed; in every other case we log the disabled state
// instead of pretending to check for updates.
async function initAutoUpdater() {
  const feed = process.env.AGENTFLOW_UPDATE_URL;
  if (!app.isPackaged || !feed) {
    console.log(`[updater] auto-update disabled (packaged=${app.isPackaged}, ` +
      `AGENTFLOW_UPDATE_URL ${feed ? "set" : "unset"}); no publish source configured`);
    return;
  }
  try {
    const { autoUpdater } = await import("electron-updater");
    autoUpdater.setFeedURL({ provider: "generic", url: feed });
    autoUpdater.on("error", (e) => console.error("[updater]", e.message));
    autoUpdater.on("update-available", (i) => console.log("[updater] update available:", i.version));
    autoUpdater.on("update-not-available", () => console.log("[updater] already up to date"));
    void autoUpdater.checkForUpdates();
  } catch (e) {
    console.error("[updater] failed to initialise:", e.message);
  }
}

// Restricted IPC surface: only these channels exist.
ipcMain.handle("agentflow:get-backend-url", () => "http://127.0.0.1:8000");
ipcMain.handle("agentflow:backend-status", () => getBackendState());
ipcMain.handle("agentflow:retry-backend", () => {
  restartPythonService();
  uiLoaded = false;
  watchBackendGate();
  return true;
});
ipcMain.handle("agentflow:select-file", async () => {
  const r = await dialog.showOpenDialog({ properties: ["openFile"] });
  return r.canceled ? null : r.filePaths[0];
});
ipcMain.handle("agentflow:open-path", async (_e, p) => {
  const { shell } = require("electron");
  if (typeof p !== "string" || !p) return { ok: false, error: "路径为空" };
  const err = await shell.openPath(p);
  return err ? { ok: false, error: err } : { ok: true };
});

app.whenReady().then(() => {
  // Production builds drop Electron's default English application menu
  // (File/Edit/View/Window/Help) so the top of the window stays clean;
  // the tray menu (Chinese, app-owned) is unaffected.
  if (!process.env.VITE_DEV_SERVER_URL) Menu.setApplicationMenu(null);
  startPythonService(backendOptions());
  createWindow();
  if (app.isPackaged) watchBackendGate();
  createTray();
  void initAutoUpdater();
  app.on("activate", () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
});
app.on("window-all-closed", () => { if (process.platform !== "darwin") app.quit(); });
app.on("before-quit", () => stopPythonService());
