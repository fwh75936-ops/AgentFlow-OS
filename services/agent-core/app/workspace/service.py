"""Core logic for the Shared Linux Workspace.

Everything here operates on ONE shared environment. Functions take an open
SQLAlchemy session (``db``) so API routes, Tool Registry handlers and tests all
share the same code path — no bypass around permissions or audit logging.
"""
import functools
import hashlib
import os
import platform
import pwd
import re
import resource
import shlex
import signal
import subprocess
import threading
import time
from datetime import datetime, timedelta

from app import models
from app.workspace import config, host_native, sandbox

_STATUS_ZH = {"success": "成功", "failed": "失败", "blocked": "已拦截"}

# ---------------------------------------------------------------- events ----

def emit_event(db, event_type, agent_id=None, task_id=None, path=None, detail=""):
    ev = models.WorkspaceEvent(env_id=config.ENV_ID, event_type=event_type,
                               agent_id=agent_id, task_id=task_id, path=path,
                               detail=(detail or "")[:2000])
    db.add(ev)
    db.commit()
    db.refresh(ev)
    return ev


def list_events(db, limit=100, event_type=None):
    try:
        limit = max(1, min(int(limit or 100), 500))
    except (TypeError, ValueError):
        limit = 100
    q = db.query(models.WorkspaceEvent)
    if event_type:
        q = q.filter_by(event_type=event_type)
    return q.order_by(models.WorkspaceEvent.id.desc()).limit(limit).all()


# ------------------------------------------------------------- environment ----

def env_status(db):
    from app.workspace import desktop
    root = config.get_shared_root()
    disk_used = 0
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            try:
                disk_used += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    user = pwd.getpwuid(os.getuid()).pw_name
    sessions = db.query(models.WorkspaceSession).filter_by(
        env_id=config.ENV_ID, status="active").count()
    lease = current_lease(db)
    return {
        "env_id": config.ENV_ID,
        "name": "共享 Linux 电脑",
        "os": f"{platform.system()} {platform.release()} ({_distro()})",
        "runtime_kind": config.runtime_kind(),
        "hostname": platform.node(),
        "process_user": user,
        "is_root": os.getuid() == 0,
        "logical_root": config.LOGICAL_ROOT,
        "shared_root": root,
        "disk_used_bytes": disk_used,
        "limits": config.get_limits(),
        "active_sessions": sessions,
        "desktop": desktop.status(),
        "desktop_lease": lease,
        "security": {
            "docker_socket_mounted": os.path.exists("/var/run/docker.sock"),
            "vnc_bind": "127.0.0.1 only (compose) / localhost VNC server",
            "high_risk_commands_require_approval": True,
        },
    }


def _distro():
    try:
        with open("/etc/os-release", encoding="utf-8") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return "Linux"


# ---------------------------------------------------------------- sessions ----

_session_locks = {}
_session_locks_guard = threading.Lock()


def _session_lock(session_id):
    with _session_locks_guard:
        return _session_locks.setdefault(session_id, threading.Lock())


# File-lock/version critical sections and desktop-lease transitions are
# check-then-write sequences spread over several commits; serialise them
# process-wide (RLock: helpers call each other inside the section).
_file_guard = threading.RLock()
_lease_guard = threading.RLock()


def _guarded(guard):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            with guard:
                return fn(*a, **kw)
        return wrapper
    return deco


def _clamp_ttl(value, default, lo=1, hi=7200):
    try:
        ttl = int(value or default)
    except (TypeError, ValueError):
        ttl = default
    return max(lo, min(ttl, hi))


def create_session(db, agent_id, task_id=None):
    agent = db.query(models.Agent).get(agent_id)
    if agent is None:
        raise ValueError(f"员工 {agent_id} 不存在")
    s = models.WorkspaceSession(env_id=config.ENV_ID, agent_id=agent_id,
                                task_id=task_id, cwd="", status="active")
    db.add(s)
    db.commit()
    db.refresh(s)
    emit_event(db, "session_created", agent_id=agent_id, task_id=task_id,
               detail=f"在共享电脑打开终端会话 #{s.id}")
    return s


def close_session(db, session_id):
    s = db.query(models.WorkspaceSession).get(session_id)
    if s is None:
        raise ValueError(f"会话 {session_id} 不存在")
    with _session_lock(session_id):
        s.status = "closed"
        db.commit()
    with _session_locks_guard:
        _session_locks.pop(session_id, None)  # do not leak one lock per session forever
    emit_event(db, "session_closed", agent_id=s.agent_id, task_id=s.task_id,
               detail=f"关闭终端会话 #{s.id}")
    return s


def list_sessions(db, agent_id=None):
    q = db.query(models.WorkspaceSession).filter_by(env_id=config.ENV_ID)
    if agent_id:
        q = q.filter_by(agent_id=agent_id)
    return q.order_by(models.WorkspaceSession.id.desc()).all()


# ------------------------------------------------------------ command exec ----

# Commands matching any of these are high-risk: they run ONLY with explicit
# human approval (approved=True), mirroring the Tool Registry policy.
HIGH_RISK_PATTERNS = [
    r"\bsudo\b", r"\bsu\b(?=\s|$)", r"\bmkfs\b", r"\bdd\b[^|]*of=/dev/",
    r"\bmount\b", r"\bumount\b", r"\bdocker\b", r"\bpodman\b",
    r"\bshutdown\b", r"\breboot\b", r"\bpoweroff\b", r"\buseradd\b",
    r"\buserdel\b", r"\bpasswd\b", r"\bchown\b[^|]*/", r"\brm\s+-[a-z]*r[a-z]*\s+/(\s|$)",
    r">\s*/dev/(sd|nvme|hd)", r"\bchmod\s+-R\s+777\s+/",
    r"\bcurl\b[^|]*\|\s*(ba)?sh", r"\bwget\b[^|]*\|\s*(ba)?sh",
    r"\$\(", r"`", r"\beval\b", r"\brm\b[^|]*--(recursive|force)\b",
    r"\biptables\b", r"\bsystemctl\b", r"\bservice\b\s+\w+\s+(start|stop|restart)",
]


def is_high_risk(command: str) -> bool:
    return any(re.search(p, command or "") for p in HIGH_RISK_PATTERNS)


def _confine(base, rel):
    full = os.path.abspath(os.path.join(base, rel or ""))
    if full != os.path.abspath(base) and not full.startswith(os.path.abspath(base) + os.sep):
        raise ValueError(f"路径超出共享目录范围：{rel}")
    return full


def _apply_limits():
    limits = config.get_limits()
    try:
        cpu = limits["command_cpu_seconds"]
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 5))
    except (ValueError, OSError):
        pass
    try:
        mem = limits["command_memory_mb"] * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
    except (ValueError, OSError):
        pass
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    except (ValueError, OSError):
        pass


_PWD_MARKER = "__AGENTFLOW_PWD__:"


def run_capped(argv, cwd=None, env=None, timeout=60, preexec_fn=None,
               max_bytes=262144):
    """Run a command in its own process group with bounded memory.

    Only the tail of stdout/stderr is kept, so a runaway command cannot
    exhaust memory; on timeout the WHOLE group is killed so children the
    command spawned cannot outlive it. Returns
    (stdout, stderr, exit_code_or_None, timed_out).
    """
    proc = subprocess.Popen(argv, cwd=cwd, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=True, preexec_fn=preexec_fn)
    bufs = {"out": bytearray(), "err": bytearray()}

    def _drain(stream, key):
        try:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                buf = bufs[key]
                buf.extend(chunk)
                if len(buf) > max_bytes:
                    del buf[:len(buf) - max_bytes]
        except Exception:
            pass

    threads = [threading.Thread(target=_drain, args=(proc.stdout, "out"), daemon=True),
               threading.Thread(target=_drain, args=(proc.stderr, "err"), daemon=True)]
    for t in threads:
        t.start()
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            proc.wait(timeout=10)
        except Exception:
            pass
    for t in threads:
        t.join(timeout=5)
    return (bufs["out"].decode("utf-8", "replace"),
            bufs["err"].decode("utf-8", "replace"),
            None if timed_out else proc.returncode, timed_out)


def exec_in_session(db, session, command, task_id=None, approved=False, timeout=None):
    """Run one command in the agent's session inside the SINGLE shared env.

    Sessions are isolated by: per-session cwd, per-session lock (commands in
    one session serialise; different sessions run concurrently), and separate
    audit logs. The filesystem, OS and desktop are shared by design.
    Every execution is recorded with executor / task / time / exit code / output.
    """
    root = config.get_shared_root()
    limits = config.get_limits()
    try:
        timeout = int(timeout or limits["default_timeout_seconds"])
    except (TypeError, ValueError):
        timeout = limits["default_timeout_seconds"]
    timeout = max(1, min(timeout, 3600))
    started = datetime.utcnow()
    t0 = time.time()

    def _log(**kw):
        row = models.WorkspaceCommandLog(
            env_id=config.ENV_ID, session_id=session.id, agent_id=session.agent_id,
            task_id=task_id if task_id is not None else session.task_id,
            command=command, cwd=session.cwd or "", started_at=started, **kw)
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    if is_high_risk(command) and not approved:
        row = _log(exit_code=None, stdout="", status="blocked",
                   stderr="high-risk command requires human approval (approved=true)",
                   finished_at=datetime.utcnow(), duration_ms=0)
        emit_event(db, "command_blocked", agent_id=session.agent_id,
                   task_id=row.task_id, detail=command[:300])
        return {"log_id": row.id, "blocked": True, "exit_code": None,
                "stdout": "", "stderr": row.stderr, "status": "blocked",
                "cwd": session.cwd or ""}

    lock = _session_lock(session.id)
    with lock:
        db.refresh(session)
        if session.status != "active":
            row = _log(exit_code=None, stdout="", status="failed",
                       stderr="会话已关闭，不能再执行命令",
                       finished_at=datetime.utcnow(), duration_ms=0)
            return {"log_id": row.id, "blocked": False, "exit_code": None,
                    "stdout": "", "stderr": row.stderr, "status": "failed",
                    "cwd": session.cwd or ""}
        return _exec_in_locked_session(db, session, command, task_id, timeout,
                                       t0, _log, root)


def _exec_in_locked_session(db, session, command, task_id, timeout, t0, log_fn, root):
    """Second half of exec_in_session, run entirely under the session lock
    so the cwd it reads and writes back cannot race another command in
    the same session (or a close)."""
    # When the current sandbox was installed host-native, the command runs
    # inside that system directory instead: chroot as the sandbox's own
    # non-privileged user agentflow, with the shared workspace bind-mounted
    # at /workspace (the same files the file tools operate on). The audit
    # row records which sandbox the command ran in.
    active = sandbox.active_sandbox(db)
    sandbox_label = None
    rootfs = None
    if active and active["method"] == "host-native":
        rootfs = active["root"]
        sandbox_label = active["label"]
        problem = None
        if not host_native.rootfs_ready(rootfs):
            problem = "当前沙箱的系统目录不存在或不完整，请在 AI 电脑页重新安装这个系统。"
        else:
            mount_ok, mount_err = host_native.ensure_workspace_mount(rootfs)
            if not mount_ok:
                problem = mount_err
        if problem:
            row = log_fn(exit_code=None, stdout="", status="failed",
                       stderr=problem, sandbox=sandbox_label,
                       finished_at=datetime.utcnow(), duration_ms=0)
            return {"log_id": row.id, "blocked": False, "exit_code": None,
                    "stdout": "", "stderr": problem, "status": "failed",
                    "cwd": session.cwd or "", "sandbox": sandbox_label}

    if rootfs:
        inner_cwd = "/workspace" + ("/" + session.cwd if session.cwd else "")
        wrapped = (f"cd {shlex.quote(inner_cwd)} 2>/dev/null || cd /workspace\n"
                   f"{command}\n__af_ec=$?; printf '\\n{_PWD_MARKER}%s\\n' \"$PWD\"; exit $__af_ec")
        argv = host_native.chroot_argv(rootfs, wrapped)
        run_cwd = root
        pwd_base = "/workspace"
        env = dict(os.environ)
        env.update({"AGENTFLOW_ENV_ID": config.ENV_ID,
                    "AGENTFLOW_AGENT_ID": str(session.agent_id),
                    "AGENTFLOW_SESSION_ID": str(session.id),
                    "AGENTFLOW_SANDBOX": sandbox_label or "",
                    "HOME": "/home/agentflow", "TERM": "xterm-256color"})
    else:
        workdir = _confine(root, session.cwd)
        os.makedirs(workdir, exist_ok=True)
        wrapped = f"{command}\n__af_ec=$?; printf '\\n{_PWD_MARKER}%s\\n' \"$PWD\"; exit $__af_ec"
        argv = ["/bin/bash", "-lc", wrapped]
        run_cwd = workdir
        pwd_base = root
        env = dict(os.environ)
        env.update({"AGENTFLOW_ENV_ID": config.ENV_ID,
                    "AGENTFLOW_AGENT_ID": str(session.agent_id),
                    "AGENTFLOW_SESSION_ID": str(session.id),
                    "HOME": root, "TERM": "xterm-256color"})
    try:
        stdout, stderr, exit_code, timed_out = run_capped(
            argv, cwd=run_cwd, env=env, timeout=timeout, preexec_fn=_apply_limits)
    except OSError as e:
        # The process could not even start (missing chroot/bash, bad
        # cwd): still an audited, failed execution — never a silent 500.
        stdout, stderr, exit_code, timed_out = "", f"命令启动失败：{e}", None, False
        status = "failed"
    if timed_out:
        exit_code, status = None, "timeout"
        stderr = (stderr + f"\ncommand timed out after {timeout}s").strip()
    elif exit_code is not None:
        status = "completed" if exit_code == 0 else "failed"

    # Recover the session cwd from the marker (supports `cd` across commands).
    new_cwd = session.cwd or ""
    lines = stdout.splitlines()
    for i in range(len(lines) - 1, -1, -1):
        line = lines[i]
        if line.strip().startswith(_PWD_MARKER):
            pwd_path = line.strip()[len(_PWD_MARKER):]
            rel = os.path.relpath(pwd_path, pwd_base)
            if rel == ".":
                new_cwd = ""
            elif rel.startswith(".."):
                stderr = (stderr + "\n[系统提示] 当前目录越界，已回到共享目录 /workspace").strip()
                new_cwd = ""
            else:
                new_cwd = rel
            del lines[i]
            stdout = "\n".join(lines)
            break
    duration_ms = int((time.time() - t0) * 1000)
    session.cwd = new_cwd
    session.last_active_at = datetime.utcnow()
    db.commit()
    row = log_fn(exit_code=exit_code, stdout=stdout[-8000:], stderr=stderr[-4000:],
               status=status, finished_at=datetime.utcnow(), duration_ms=duration_ms,
               sandbox=sandbox_label)
    where = f" · 沙箱 {sandbox_label}" if sandbox_label else ""
    emit_event(db, "command_executed", agent_id=session.agent_id, task_id=row.task_id,
               detail=f"[{_STATUS_ZH.get(status, status)} · 退出码 {exit_code}{where}] {command[:200]}")
    return {"log_id": row.id, "blocked": False, "exit_code": exit_code,
            "stdout": stdout[-8000:], "stderr": stderr[-4000:], "status": status,
            "cwd": new_cwd, "duration_ms": duration_ms, "sandbox": sandbox_label}


def list_command_logs(db, session_id=None, agent_id=None, task_id=None, limit=100):
    try:
        limit = max(1, min(int(limit or 100), 500))
    except (TypeError, ValueError):
        limit = 100
    q = db.query(models.WorkspaceCommandLog)
    if session_id:
        q = q.filter_by(session_id=session_id)
    if agent_id:
        q = q.filter_by(agent_id=agent_id)
    if task_id:
        q = q.filter_by(task_id=task_id)
    return q.order_by(models.WorkspaceCommandLog.id.desc()).limit(limit).all()


# ------------------------------------------------------------------- files ----

def _hash(content_bytes):
    return hashlib.sha256(content_bytes).hexdigest()


def _active_lock(db, path):
    now = datetime.utcnow()
    lock = (db.query(models.SharedFileLock)
            .filter_by(path=path, status="held")
            .order_by(models.SharedFileLock.id.desc()).first())
    if lock and lock.expires_at and lock.expires_at < now:
        lock.status = "expired"
        db.commit()
        return None
    return lock


def list_files(db, sub=""):
    root = config.get_shared_root()
    base = _confine(root, sub)
    if not os.path.isdir(base):
        raise ValueError(f"目录不存在：{sub or '/'}")
    out = []
    stack = [base]
    while stack and len(out) < 500:
        current = stack.pop()
        try:
            entries = sorted(os.scandir(current), key=lambda e: e.name)
        except OSError:
            continue
        subdirs = []
        for entry in entries:
            if len(out) >= 500:
                break
            if entry.name in ("__pycache__", ".git"):
                continue
            try:
                st = entry.stat(follow_symlinks=False)
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue  # one broken entry must not fail the whole listing
            rel = os.path.relpath(entry.path, root)
            meta = db.query(models.SharedFileMeta).get(rel)
            out.append({"path": rel, "name": entry.name, "is_dir": is_dir,
                        "size": 0 if is_dir else st.st_size,
                        "version": meta.version if meta else None,
                        "content_hash": meta.content_hash if meta else None,
                        "last_modified_by_agent_id": meta.last_modified_by_agent_id if meta else None})
            if is_dir:
                subdirs.append(entry.path)
        stack.extend(reversed(subdirs))
    return out


def read_file(db, path):
    root = config.get_shared_root()
    full = _confine(root, path)
    if not os.path.isfile(full):
        raise ValueError(f"文件不存在：{path}")
    with open(full, "rb") as f:
        raw = f.read(10 * 1024 * 1024 + 1)
    if len(raw) > 10 * 1024 * 1024:
        raise ValueError(f"文件过大，无法读取：{path}（上限 10MB）")
    meta = db.query(models.SharedFileMeta).get(path)
    return {"path": path, "content": raw.decode("utf-8", errors="replace"),
            "version": meta.version if meta else None,
            "content_hash": _hash(raw),
            "size": len(raw)}


def file_hash_on_disk(path):
    """sha256 of the shared file as it exists on disk right now, or None
    when it is missing, unreadable, or over the read limit. Freshness
    checks must consult this — not only SharedFileMeta — because writes
    through a terminal session bypass write_file entirely and never
    update the metadata row; a meta-only probe would keep reporting a
    changed artifact as verified."""
    try:
        root = config.get_shared_root()
        full = _confine(root, path)
        if not os.path.isfile(full):
            return None
        with open(full, "rb") as f:
            raw = f.read(10 * 1024 * 1024 + 1)
        if len(raw) > 10 * 1024 * 1024:
            return None
        return _hash(raw)
    except Exception:
        return None


class ConflictError(Exception):
    """Raised when a write collides with a lock or a newer version."""


@_guarded(_file_guard)
def write_file(db, agent_id, path, content, expected_version=None, task_id=None):
    root = config.get_shared_root()
    full = _confine(root, path)
    lock = _active_lock(db, path)
    if lock and lock.agent_id != agent_id:
        emit_event(db, "file_conflict", agent_id=agent_id, task_id=task_id, path=path,
                   detail=f"写入被拦截：文件已被员工 {lock.agent_id} 锁定")
        raise ConflictError(f"文件已被员工 {lock.agent_id} 锁定：{path}")
    meta = db.query(models.SharedFileMeta).get(path)
    if expected_version is not None and meta and meta.version != expected_version:
        emit_event(db, "file_conflict", agent_id=agent_id, task_id=task_id, path=path,
                   detail=f"版本已过期：期望第 {expected_version} 版，当前第 {meta.version} 版")
        raise ConflictError(
            f"文件版本冲突：{path}，期望第 {expected_version} 版，当前第 {meta.version} 版")
    existed = os.path.exists(full)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    raw = (content or "").encode("utf-8")
    tmp = f"{full}.tmp-{os.getpid()}-{threading.get_ident()}"
    with open(tmp, "wb") as f:
        f.write(raw)
    os.replace(tmp, full)  # atomic: readers never see a truncated file
    if meta is None:
        meta = models.SharedFileMeta(path=path)
        db.add(meta)
    meta.version = (meta.version or 0) + 1
    meta.content_hash = _hash(raw)
    meta.size = len(raw)
    meta.last_modified_by_agent_id = agent_id
    db.commit()
    emit_event(db, "file_modified" if existed else "file_created",
               agent_id=agent_id, task_id=task_id, path=path,
               detail=f"第 {meta.version} 版 · 校验和 {meta.content_hash[:12]} · {len(raw)} 字节")
    return {"path": path, "version": meta.version, "content_hash": meta.content_hash,
            "size": len(raw)}


@_guarded(_file_guard)
def delete_file(db, agent_id, path, task_id=None):
    root = config.get_shared_root()
    full = _confine(root, path)
    lock = _active_lock(db, path)
    if lock and lock.agent_id != agent_id:
        raise ConflictError(f"文件已被员工 {lock.agent_id} 锁定：{path}")
    if not os.path.isfile(full):
        raise ValueError(f"文件不存在：{path}")
    os.remove(full)
    meta = db.query(models.SharedFileMeta).get(path)
    if meta:
        db.delete(meta)
    db.commit()
    emit_event(db, "file_deleted", agent_id=agent_id, task_id=task_id, path=path, detail="")
    return {"deleted": path}


@_guarded(_file_guard)
def acquire_file_lock(db, agent_id, path, ttl_seconds=300, session_id=None):
    ttl_seconds = _clamp_ttl(ttl_seconds, 300, lo=1, hi=86400)
    lock = _active_lock(db, path)
    if lock and lock.agent_id != agent_id:
        raise ConflictError(f"文件已被员工 {lock.agent_id} 锁定：{path}")
    if lock:
        lock.expires_at = datetime.utcnow() + timedelta(seconds=ttl_seconds)
        db.commit()
        return lock
    lock = models.SharedFileLock(path=path, agent_id=agent_id, session_id=session_id,
                                 status="held",
                                 expires_at=datetime.utcnow() + timedelta(seconds=ttl_seconds))
    db.add(lock)
    db.commit()
    db.refresh(lock)
    emit_event(db, "file_locked", agent_id=agent_id, path=path,
               detail=f"文件锁 #{lock.id} · 有效期 {ttl_seconds} 秒")
    return lock


@_guarded(_file_guard)
def release_file_lock(db, agent_id, path):
    lock = _active_lock(db, path)
    if lock is None:
        return None
    if lock.agent_id != agent_id:
        raise ConflictError(f"文件已被员工 {lock.agent_id} 锁定：{path}")
    lock.status = "released"
    db.commit()
    emit_event(db, "file_unlocked", agent_id=agent_id, path=path, detail=f"文件锁 #{lock.id}")
    return lock


# ------------------------------------------------------------ desktop lease ----

def _expire_lease_if_needed(db, lease, grant=True):
    if lease and lease.status == "held" and lease.expires_at and lease.expires_at < datetime.utcnow():
        lease.status = "expired"
        lease.released_at = datetime.utcnow()
        db.commit()
        emit_event(db, "desktop_lease_expired", agent_id=lease.holder_agent_id,
                   detail=f"桌面租约 #{lease.id} 已到期")
        # The lease handed to the queue head IS the current lease now;
        # returning None here made callers report the desktop as free
        # (and hand it out a second time) while someone already held it.
        return _grant_next_waiter(db) if grant else None
    return lease


def _held_lease(db, grant_on_expire=True):
    lease = (db.query(models.DesktopLease)
             .filter_by(env_id=config.ENV_ID, status="held")
             .order_by(models.DesktopLease.id.desc()).first())
    return _expire_lease_if_needed(db, lease, grant=grant_on_expire)


def _grant_next_waiter(db):
    # Grant to the first waiter who can still hold the desktop: an
    # agent queued long ago may since have been disabled or deleted,
    # and handing it control would strand the desktop until expiry.
    nxt = None
    for candidate in (db.query(models.DesktopLeaseQueue)
                      .filter_by(env_id=config.ENV_ID, status="waiting")
                      .order_by(models.DesktopLeaseQueue.id.asc()).all()):
        agent = db.query(models.Agent).get(candidate.agent_id)
        if agent is not None and agent.status == "active":
            nxt = candidate
            break
        candidate.status = "skipped"
        db.commit()
    if nxt is None:
        return None
    nxt.status = "granted"
    lease = models.DesktopLease(
        env_id=config.ENV_ID, holder_type="agent", holder_agent_id=nxt.agent_id,
        status="held", acquired_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(seconds=nxt.ttl_seconds),
        last_heartbeat_at=datetime.utcnow())
    db.add(lease)
    db.commit()
    db.refresh(lease)
    emit_event(db, "desktop_lease_granted", agent_id=nxt.agent_id,
               detail=f"桌面租约 #{lease.id} 已从等待队列授予")
    return lease


@_guarded(_lease_guard)
def current_lease(db):
    lease = _held_lease(db)
    queue = (db.query(models.DesktopLeaseQueue)
             .filter_by(env_id=config.ENV_ID, status="waiting")
             .order_by(models.DesktopLeaseQueue.id.asc()).all())
    return {
        "holder_type": lease.holder_type if lease else None,
        "holder_agent_id": lease.holder_agent_id if lease else None,
        "lease_id": lease.id if lease else None,
        "acquired_at": lease.acquired_at.isoformat() if lease and lease.acquired_at else None,
        "expires_at": lease.expires_at.isoformat() if lease and lease.expires_at else None,
        "queue": [{"queue_id": q.id, "agent_id": q.agent_id,
                   "requested_at": q.requested_at.isoformat() if q.requested_at else None}
                  for q in queue],
    }


@_guarded(_lease_guard)
def acquire_desktop_lease(db, agent_id, ttl_seconds=None):
    ttl = _clamp_ttl(ttl_seconds, config.get_limits()["desktop_lease_ttl_seconds"])
    lease = _held_lease(db)
    if lease is not None:
        if lease.holder_type == "agent" and lease.holder_agent_id == agent_id:
            lease.expires_at = datetime.utcnow() + timedelta(seconds=ttl)
            lease.last_heartbeat_at = datetime.utcnow()
            db.commit()
            return {"state": "held", "lease_id": lease.id, "expires_at": lease.expires_at.isoformat()}
        existing = (db.query(models.DesktopLeaseQueue)
                    .filter_by(env_id=config.ENV_ID, agent_id=agent_id, status="waiting").first())
        if existing is None:
            existing = models.DesktopLeaseQueue(env_id=config.ENV_ID, agent_id=agent_id,
                                                ttl_seconds=ttl, status="waiting")
            db.add(existing)
            db.commit()
            db.refresh(existing)
            emit_event(db, "desktop_lease_waiting", agent_id=agent_id,
                       detail=f"已排队等待桌面控制，当前持有者：{lease.holder_agent_id or lease.holder_type}")
        queue = (db.query(models.DesktopLeaseQueue)
                 .filter_by(env_id=config.ENV_ID, status="waiting")
                 .order_by(models.DesktopLeaseQueue.id.asc()).all())
        ids = [q.id for q in queue]
        position = ids.index(existing.id) + 1 if existing.id in ids else len(ids)
        return {"state": "waiting", "queue_id": existing.id, "position": position,
                "holder_agent_id": lease.holder_agent_id, "holder_type": lease.holder_type}
    lease = models.DesktopLease(env_id=config.ENV_ID, holder_type="agent",
                                holder_agent_id=agent_id, status="held",
                                acquired_at=datetime.utcnow(),
                                expires_at=datetime.utcnow() + timedelta(seconds=ttl),
                                last_heartbeat_at=datetime.utcnow())
    db.add(lease)
    db.commit()
    db.refresh(lease)
    emit_event(db, "desktop_lease_acquired", agent_id=agent_id,
               detail=f"桌面租约 #{lease.id} · 有效期 {ttl} 秒")
    return {"state": "held", "lease_id": lease.id, "expires_at": lease.expires_at.isoformat()}


@_guarded(_lease_guard)
def renew_desktop_lease(db, agent_id, ttl_seconds=None):
    ttl = _clamp_ttl(ttl_seconds, config.get_limits()["desktop_lease_ttl_seconds"])
    lease = _held_lease(db)
    if lease is None or lease.holder_type != "agent" or lease.holder_agent_id != agent_id:
        raise ValueError("该员工并未持有桌面控制权")
    lease.expires_at = datetime.utcnow() + timedelta(seconds=ttl)
    lease.last_heartbeat_at = datetime.utcnow()
    db.commit()
    return {"state": "held", "lease_id": lease.id, "expires_at": lease.expires_at.isoformat()}


@_guarded(_lease_guard)
def release_desktop_lease(db, agent_id=None, holder_type=None):
    lease = _held_lease(db)
    if lease is None:
        return {"state": "free"}
    if lease.holder_type == "agent" and holder_type != "user" \
            and lease.holder_agent_id != agent_id:
        # agent_id=None must not slip through: an anonymous caller could
        # otherwise release any agent's desktop lease (and trigger the
        # queue grant) without naming the holder at all.
        raise ValueError("该员工并未持有桌面控制权")
    if lease.holder_type == "user" and holder_type != "user":
        raise ValueError("桌面正由用户控制，只有用户本人可以交还")
    lease.status = "released"
    lease.released_at = datetime.utcnow()
    db.commit()
    emit_event(db, "desktop_lease_released", agent_id=lease.holder_agent_id,
               detail=f"桌面租约 #{lease.id} 已释放")
    granted = _grant_next_waiter(db)
    return {"state": "released", "granted_agent_id": granted.holder_agent_id if granted else None}


@_guarded(_lease_guard)
def force_takeover_desktop(db, ttl_seconds=3600):
    """User forcibly takes desktop control: current agent lease is revoked,
    queued agents keep waiting, the user holds the lease until release."""
    lease = _held_lease(db, grant_on_expire=False)
    if lease is not None:
        lease.status = "force_taken"
        lease.released_at = datetime.utcnow()
        db.commit()
        emit_event(db, "desktop_force_takeover", agent_id=lease.holder_agent_id,
                   detail=f"桌面租约 #{lease.id} 已被用户收回")
    user_lease = models.DesktopLease(
        env_id=config.ENV_ID, holder_type="user", holder_agent_id=None, status="held",
        acquired_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(seconds=int(ttl_seconds)),
        last_heartbeat_at=datetime.utcnow())
    db.add(user_lease)
    db.commit()
    db.refresh(user_lease)
    return {"state": "held_by_user", "lease_id": user_lease.id,
            "expires_at": user_lease.expires_at.isoformat()}


# ------------------------------------------------------------- file watcher ----

_watcher_started = False
_watcher_snapshot = {}


def _scan_shared_root():
    root = config.get_shared_root()
    snap = {}
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
        for name in files:
            full = os.path.join(dirpath, name)
            try:
                st = os.stat(full)
                snap[os.path.relpath(full, root)] = (st.st_mtime_ns, st.st_size)
            except OSError:
                pass
    return snap


def _watcher_loop():
    from app.database import SessionLocal
    global _watcher_snapshot
    _watcher_snapshot = _scan_shared_root()
    while True:
        time.sleep(2.0)
        try:
            new = _scan_shared_root()
            old = _watcher_snapshot
            changes = []
            for p in sorted(set(new) - set(old)):
                changes.append(("file_created", p))
            for p in sorted(set(old) - set(new)):
                changes.append(("file_deleted", p))
            for p in sorted(set(new) & set(old)):
                if new[p] != old[p]:
                    changes.append(("file_modified", p))
            _watcher_snapshot = new
            if changes:
                db = SessionLocal()
                try:
                    for etype, p in changes:
                        # API writes already emit precise events with the agent id;
                        # watcher events are marked as external/filesystem-detected.
                        recent = (db.query(models.WorkspaceEvent)
                                  .filter_by(event_type=etype, path=p)
                                  .order_by(models.WorkspaceEvent.id.desc()).first())
                        if recent and recent.agent_id is not None and \
                                (datetime.utcnow() - recent.created_at).total_seconds() < 3:
                            continue
                        emit_event(db, etype, agent_id=None, path=p,
                                   detail="由文件系统监听发现")
                finally:
                    db.close()
        except Exception:
            pass  # watcher must never take the API down; events resume next poll


def start_watcher():
    global _watcher_started
    if _watcher_started:
        return
    _watcher_started = True
    threading.Thread(target=_watcher_loop, daemon=True, name="workspace-watcher").start()
