// Starts/stops the Python Agent Core and tracks its real state.
//
// Dev mode: spawn uvicorn from the repo checkout (venv python if present,
// system python otherwise) — unchanged historical behaviour.
//
// Packaged mode: spawn the bundled embeddable Python
// (resources/backend/python/python.exe) running the bundled agent-core
// source (resources/backend/agent-core/run.py). All writable state lives
// in the per-user data dir: the SQLite DB (seeded once from
// resources/backend/seed/agentflow.db, never overwritten afterwards),
// workspaces, the shared workspace and sandbox rootfs dirs.
//
// State machine: idle -> starting -> ready | failed. The main process
// gates the UI on "ready"; "failed" carries a human-readable reason plus
// the backend's last log lines. Nothing here pretends a dead backend is
// alive: readiness means an actual HTTP 200 from /health.
import { spawn } from "node:child_process";
import { copyFileSync, cpSync, existsSync, mkdirSync } from "node:fs";
import http from "node:http";
import path from "node:path";

const HEALTH_URL = "http://127.0.0.1:8000/health";
const HEALTH_TIMEOUT_MS = 90000;

let proc = null;
let config = null;
let state = { status: "idle", error: null };
let logLines = [];

export function getBackendState() {
  return { status: state.status, error: state.error, log: logLines.slice(-12) };
}

function healthOnce() {
  return new Promise((resolve) => {
    const req = http.get(HEALTH_URL, { timeout: 1500 }, (res) => {
      res.resume();
      resolve(res.statusCode === 200);
    });
    req.on("error", () => resolve(false));
    req.on("timeout", () => { req.destroy(); resolve(false); });
  });
}

async function waitForHealth() {
  const deadline = Date.now() + HEALTH_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (await healthOnce()) return true;
    if (!proc) return false; // process died; exit handler set the error
    await new Promise((r) => setTimeout(r, 400));
  }
  return false;
}

function preparePackagedData(opts) {
  mkdirSync(opts.dataDir, { recursive: true });
  const dbPath = path.join(opts.dataDir, "agentflow.db");
  if (!existsSync(dbPath) && opts.seedDb && existsSync(opts.seedDb)) {
    copyFileSync(opts.seedDb, dbPath); // first run only; never overwritten
  }
  // Seed skill attachment files travel with the seed database (the
  // imported demo skills reference them); copy once, never overwrite.
  const seedSkills = opts.seedDb
    ? path.join(path.dirname(opts.seedDb), "skill_files")
    : null;
  const skillsDir = path.join(opts.dataDir, "skill_files");
  if (seedSkills && existsSync(seedSkills) && !existsSync(skillsDir)) {
    cpSync(seedSkills, skillsDir, { recursive: true });
  }
  return {
    AGENTFLOW_DB_URL: "sqlite:///" + dbPath.replace(/\\/g, "/"),
    AGENTFLOW_WORKSPACE_ROOT: path.join(opts.dataDir, "workspaces"),
    AGENTFLOW_SHARED_ROOT: path.join(opts.dataDir, "shared_workspace"),
    AGENTFLOW_SANDBOXES_DIR: path.join(opts.dataDir, "sandboxes"),
    AGENTFLOW_DATA_DIR: opts.dataDir,
    AGENTFLOW_SKILLS_DIR: skillsDir,
    PYTHONUTF8: "1",
    PYTHONIOENCODING: "utf-8",
  };
}

export function startPythonService(opts) {
  if (proc) return proc;
  config = opts;
  state = { status: "starting", error: null };
  logLines = [];

  let python;
  let args;
  const env = { ...process.env };
  if (opts.mode === "packaged") {
    Object.assign(env, preparePackagedData(opts));
    python = opts.pythonExe;
    args = [path.join(opts.coreDir, "run.py")];
    if (!existsSync(python)) {
      state = { status: "failed", error: `后端运行环境缺失，未找到内置 Python：${python}` };
      return null;
    }
    if (!existsSync(args[0])) {
      state = { status: "failed", error: `后端服务文件缺失，未找到：${args[0]}` };
      return null;
    }
  } else {
    const venvPy = path.join(opts.coreDir, ".venv", "bin", "python");
    const venvPyWin = path.join(opts.coreDir, ".venv", "Scripts", "python.exe");
    python = existsSync(venvPy) ? venvPy : existsSync(venvPyWin) ? venvPyWin : "python";
    args = ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"];
  }

  proc = spawn(python, args, { cwd: opts.coreDir, stdio: "pipe", env, windowsHide: true });
  const push = (d) => {
    const text = String(d);
    console.log("[agent-core]", text.trim());
    for (const line of text.split(/\r?\n/)) {
      if (line.trim()) {
        logLines.push(line.trim());
        if (logLines.length > 40) logLines.shift();
      }
    }
  };
  proc.stdout.on("data", push);
  proc.stderr.on("data", push);
  proc.on("error", (err) => {
    console.error(`[agent-core] failed to start (${python}): ${err.message}`);
    state = { status: "failed", error: `后端服务启动失败：${err.message}` };
    proc = null;
  });
  proc.on("exit", (code) => {
    proc = null;
    if (state.status !== "failed") {
      state = { status: "failed", error: `后端服务已退出，退出码 ${code}` };
    }
  });
  const mine = proc;
  void waitForHealth().then((ok) => {
    if (state.status !== "starting") return;
    if (ok) {
      state = { status: "ready", error: null };
    } else if (proc === mine || proc === null) {
      state = {
        status: "failed",
        error: state.error ?? "后端服务在 90 秒内没有通过健康检查，可能启动失败或端口 8000 被占用",
      };
    }
  });
  return proc;
}

export function restartPythonService() {
  stopPythonService();
  if (config) startPythonService(config);
}

export function stopPythonService() {
  if (proc) { proc.kill(); proc = null; }
}
