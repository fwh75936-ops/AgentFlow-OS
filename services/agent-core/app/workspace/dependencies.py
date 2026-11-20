"""One-click installation of common development dependencies.

The user clicks one button; the packages for the current environment are
installed for real through the workspace command channel: every install
command is written to the shared command audit log (executor: the user,
no agent session) and a workspace event is emitted on completion.
Per-package outcomes are judged from real package-manager state before
and after the install attempt — never written down in advance — so a
second click reports 已存在 for everything already present and finishes
fast.

Package names are mapped per package-manager family (apt / dnf).
"""
import contextvars
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime

from app import models
from app.workspace import config, host_native, sandbox, service

# When a host-native sandbox is the current sandbox, dependency commands
# run inside its system directory instead of on this machine: the value
# is (rootfs_path, sandbox_label) for the duration of one job / status
# computation, and every command string is wrapped with chroot.
_TARGET = contextvars.ContextVar("deps_target", default=None)


def _active_target(db):
    """The current sandbox as an install target, when it is a ready
    host-native install; otherwise None (install on this machine)."""
    active = sandbox.active_sandbox(db)
    if active and active["method"] == "host-native" \
            and host_native.rootfs_ready(active["root"]):
        return active
    return None

# key, display label, apt packages, dnf packages
CATALOG = [
    {"key": "git", "label": "Git 版本管理",
     "apt": ["git"], "dnf": ["git"]},
    {"key": "curl", "label": "curl 下载工具",
     "apt": ["curl"], "dnf": ["curl"]},
    {"key": "wget", "label": "wget 下载工具",
     "apt": ["wget"], "dnf": ["wget"]},
    {"key": "build-tools", "label": "编译工具 gcc 与 make",
     "apt": ["build-essential"], "dnf": ["gcc", "make"]},
    {"key": "python", "label": "Python 3 与 pip",
     "apt": ["python3", "python3-pip"], "dnf": ["python3", "python3-pip"]},
    {"key": "node", "label": "Node.js 与 npm",
     "apt": ["nodejs", "npm"], "dnf": ["nodejs", "npm"]},
    {"key": "jq", "label": "jq 数据处理",
     "apt": ["jq"], "dnf": ["jq"]},
    {"key": "unzip", "label": "unzip 解压工具",
     "apt": ["unzip"], "dnf": ["unzip"]},
]
_BY_KEY = {c["key"]: c for c in CATALOG}
KEYS = set(_BY_KEY)


def package_tokens(family, key):
    item = _BY_KEY[key]
    return list(item[family])


def detect_family():
    """apt | dnf | None, from what this machine actually has."""
    if shutil.which("apt-get") and shutil.which("dpkg-query"):
        return "apt"
    if shutil.which("dnf") and shutil.which("rpm"):
        return "dnf"
    return None


def run_command(command, timeout=600):
    """Single shell entry point (kept patchable for tests). Runs inside the
    shared root with the workspace command limits, like session commands.
    While a sandbox target is active, the command is wrapped to run inside
    the sandbox system directory as its root user."""
    target = _TARGET.get()
    if target:
        command = host_native.wrap_command(target[0], command)
    out, err, code, timed_out = service.run_capped(
        ["/bin/bash", "-lc", command], cwd=config.get_shared_root(),
        timeout=timeout, preexec_fn=service._apply_limits)
    if timed_out:
        return None, out, f"命令超时（{timeout} 秒）"
    return code, out, err


def _run_audited(db, command, timeout=600):
    """Run one install command through the workspace audit channel: a row
    in the shared command log (no agent session — the executor is the user)
    exactly like agent-executed commands."""
    started = datetime.utcnow()
    t0 = time.time()
    code, out, err = run_command(command, timeout=timeout)
    status = "completed" if code == 0 else "failed"
    target = _TARGET.get()
    row = models.WorkspaceCommandLog(
        env_id=config.ENV_ID, session_id=None, agent_id=None, task_id=None,
        sandbox=target[1] if target else None,
        command=command, cwd="", exit_code=code,
        stdout=out[-8000:], stderr=err[-4000:], status=status,
        started_at=started, finished_at=datetime.utcnow(),
        duration_ms=int((time.time() - t0) * 1000))
    db.add(row)
    db.commit()
    db.refresh(row)
    return code, out, err


def package_present(family, token):
    """Real presence check against the package database."""
    if family == "apt":
        code, out, _ = run_command(
            f"dpkg-query -W -f='${{Status}}' {token}", timeout=30)
        return code == 0 and "install ok installed" in out
    code, _, _ = run_command(f"rpm -q {token}", timeout=30)
    return code == 0


def _sudo_prefix():
    if os.geteuid() == 0:
        return ""
    return "sudo -n " if shutil.which("sudo") else ""


def _install_command(family, tokens, in_rootfs=False):
    joined = " ".join(tokens)
    prefix = "" if in_rootfs else _sudo_prefix()
    if family == "apt":
        return f"{prefix}env DEBIAN_FRONTEND=noninteractive apt-get install -y {joined}"
    return f"{prefix}dnf install -y {joined}"


def _update_command(family, in_rootfs=False):
    prefix = "" if in_rootfs else _sudo_prefix()
    if family == "apt":
        return f"{prefix}apt-get update"
    return f"{prefix}dnf makecache"


# ------------------------------------------------------------------ jobs ----

def _job_out(job):
    return {"id": job.id, "status": job.status,
            "package_manager": job.package_manager,
            "sandbox": getattr(job, "sandbox", None),
            "initiated_by": job.initiated_by,
            "results": job.results or [], "log": job.log or "",
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None}


def latest_job(db):
    return db.query(models.DependencyInstallJob).order_by(
        models.DependencyInstallJob.id.desc()).first()


def status_payload(db):
    target = _active_target(db)
    family = sandbox.distro_family(target["distro"]) if target else detect_family()
    job = latest_job(db)
    job_results = {}
    # A finished job's results describe the target it ran against (this
    # machine or one specific sandbox). Only reuse them for that same
    # target — otherwise probe live, so switching sandboxes never shows
    # another environment's package state as current.
    target_label = target["label"] if target else None
    if job and job.results and getattr(job, "sandbox", None) == target_label:
        job_results = {r["key"]: r for r in job.results}
    token = _TARGET.set((target["root"], target["label"])) if target else None
    packages = []
    try:
        for item in CATALOG:
            tokens = item[family] if family else []
            entry = {"key": item["key"], "label": item["label"],
                     "packages": tokens, "status": "unknown", "detail": ""}
            if family:
                if item["key"] in job_results:
                    entry["status"] = job_results[item["key"]]["status"]
                    entry["detail"] = job_results[item["key"]].get("detail", "")
                else:
                    present = all(package_present(family, t) for t in tokens)
                    entry["status"] = "exists" if present else "missing"
            packages.append(entry)
    finally:
        if token is not None:
            _TARGET.reset(token)
    return {"family": family, "packages": packages,
            "job": _job_out(job) if job else None,
            "sandbox": {"distro": target["distro"], "version": target["version"],
                        "label": target["label"]} if target else None}


_job_lock = threading.Lock()


def start_job(db, keys=None):
    # Check-and-create under a lock: two concurrent clicks must not both
    # pass the "already running" check and start competing installs.
    with _job_lock:
        running = db.query(models.DependencyInstallJob).filter_by(
            status="running").order_by(models.DependencyInstallJob.id.desc()).first()
        if running:
            return _job_out(running)
        target = _active_target(db)
        family = sandbox.distro_family(target["distro"]) if target else detect_family()
        selected = [c for c in CATALOG if keys is None or c["key"] in keys]
        job = models.DependencyInstallJob(
            status="running", package_manager=family or "", initiated_by="user",
            sandbox=target["label"] if target else None,
            # Pin the target the job was created for: if the user switches
            # the current sandbox before the worker runs, the job must not
            # silently install into the NEW environment.
            target_distro=target["distro"] if target else "",
            target_version=target["version"] if target else "",
            results=[{"key": c["key"], "label": c["label"],
                      "packages": c[family] if family else [],
                      "status": "pending", "detail": ""} for c in selected],
            log="")
        db.add(job)
        db.commit()
        db.refresh(job)
    threading.Thread(target=_run_job_worker, args=(job.id,), daemon=True).start()
    return _job_out(job)


def _run_job_worker(job_id):
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        job = db.query(models.DependencyInstallJob).get(job_id)
        if job is not None:
            run_job(db, job)
    except Exception:
        # Never leave a job stuck "running": converge it to failed.
        try:
            db.rollback()
            job = db.query(models.DependencyInstallJob).get(job_id)
            if job is not None and job.status == "running":
                job.status = "failed"
                job.log = ((job.log or "") + "\n安装过程出现异常，已停止。")[-8000:]
                job.finished_at = datetime.utcnow()
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


def _pinned_target(db, distro_id, version_id):
    """The install target for one specific distro+version, or None when
    that sandbox is not a ready host-native install anymore."""
    rec = sandbox.install_record(db, distro_id, version_id)
    method = (rec.method or "") if rec else ""
    if method != "host-native":
        return None
    root = host_native.rootfs_path(distro_id, version_id)
    if not host_native.rootfs_ready(root):
        return None
    return {"distro": distro_id, "version": version_id, "method": method,
            "label": sandbox.pair_label(distro_id, version_id), "root": root}


def run_job(db, job):
    """Execute one dependency install job synchronously, updating results
    after every package so polling clients see real progress. When a
    host-native sandbox is current, everything runs inside it."""
    pinned = getattr(job, "target_distro", None)
    if pinned is not None:
        if pinned == "":
            target = None  # created for this machine itself
        else:
            target = _pinned_target(db, pinned, getattr(job, "target_version", None))
            if target is None:
                job.status = "failed"
                job.log = "这个安装任务创建时选定的沙箱已不可用，已停止，未安装到其他环境。"
                job.finished_at = datetime.utcnow()
                db.commit()
                return job
    else:
        target = _active_target(db)
    token = _TARGET.set((target["root"], target["label"])) if target else None
    try:
        return _run_job(db, job, target)
    finally:
        if token is not None:
            _TARGET.reset(token)


def _run_job(db, job, target):
    in_rootfs = target is not None
    family = sandbox.distro_family(target["distro"]) if target \
        else (job.package_manager or detect_family())
    keys = [r["key"] for r in (job.results or [])]
    log_parts = []
    if not family:
        job.status = "failed"
        job.log = "这台机器上没有检测到 apt 或 dnf 包管理器，无法安装依赖。"
        job.finished_at = datetime.utcnow()
        db.commit()
        return job
    code, out, err = _run_audited(db, _update_command(family, in_rootfs), timeout=300)
    log_parts.append(f"刷新软件包列表退出码 {code}")
    if code != 0:
        log_parts.append(f"刷新失败，继续尝试逐个安装：{(err or out)[-200:]}")
    results = []
    for key in keys:
        item = _BY_KEY[key]
        tokens = item[family]
        entry = {"key": key, "label": item["label"], "packages": tokens,
                 "status": "installing", "detail": ""}
        if all(package_present(family, t) for t in tokens):
            entry["status"] = "exists"
            entry["detail"] = "已经装好，无需重复安装"
        else:
            code, out, err = _run_audited(db, _install_command(family, tokens, in_rootfs),
                                          timeout=900)
            if all(package_present(family, t) for t in tokens):
                entry["status"] = "installed"
                entry["detail"] = "本次新安装"
            else:
                entry["status"] = "failed"
                entry["detail"] = (err or out)[-300:] or f"安装命令退出码 {code}"
        results.append(entry)
        job.results = list(results) + [
            {"key": r["key"], "label": r["label"], "packages": r["packages"],
             "status": "pending", "detail": ""}
            for r in (job.results or []) if r["key"] not in {x["key"] for x in results}]
        db.commit()
    job.results = results
    ok = sum(1 for r in results if r["status"] in ("exists", "installed"))
    failed = sum(1 for r in results if r["status"] == "failed")
    new = sum(1 for r in results if r["status"] == "installed")
    job.status = "completed" if ok > 0 else "failed"
    job.log = "；".join(log_parts + [f"共 {len(results)} 项：新安装 {new} 项，失败 {failed} 项"])
    job.finished_at = datetime.utcnow()
    db.commit()
    where = f"，装进当前沙箱 {target['label']}" if target else ""
    service.emit_event(
        db, "command_executed",
        detail=f"你一键安装常用依赖完成{where}：新安装 {new} 项，已存在 {ok - new} 项，失败 {failed} 项")
    return job
