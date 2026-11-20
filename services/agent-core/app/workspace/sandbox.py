"""Sandbox distribution catalogue and isolation-mode detection.

The AI computer is a sandbox: the user picks a Linux distribution AND one
of its still-maintained versions from a fixed supported list — only
popular, still-maintained releases are offered. Nothing is pre-installed:
the user installs a pair first, and only an installed pair can become the
current sandbox (sandbox_config singleton pair).

Two install methods, chosen automatically: Docker (pull + build) when a
daemon really answers, otherwise host-native — the chosen system is
installed into its own directory on this machine (see host_native.py) and
workspace commands then run inside it via chroot.

Detection is honest: container mode is reported only when the docker CLI
exists AND the daemon actually answers. Image build status is reported as
built/missing only when the daemon can be asked, otherwise unknown.
"""
import os
import shutil
import subprocess
import threading
import time

from app import models
from app.workspace import host_native

# Each version: id (selection key), label (display), maintenance_until,
# base_image (official upstream image), image (our built sandbox image),
# plus the host-native install data for its family: Debian family versions
# carry a debootstrap suite + mirror; RHEL family versions carry a dnf
# releasever + the official upstream repositories.
_UBUNTU_MIRROR = "http://archive.ubuntu.com/ubuntu"
_DEBIAN_MIRROR = "http://deb.debian.org/debian"
DISTROS = [
    {
        "id": "ubuntu",
        "name": "Ubuntu",
        "description": "软件生态最广，绝大多数 Linux 软件优先适配，兼容性最佳",
        "recommended": True,
        "family": "apt",
        "dockerfile": "deploy/sandbox/Dockerfile.ubuntu",
        "versions": [
            {"id": "24.04", "label": "24.04 LTS", "maintenance_until": "2029 年",
             "base_image": "ubuntu:24.04", "image": "agentflow/sandbox:ubuntu-24.04",
             "suite": "noble", "mirror": _UBUNTU_MIRROR},
            {"id": "22.04", "label": "22.04 LTS", "maintenance_until": "2027 年",
             "base_image": "ubuntu:22.04", "image": "agentflow/sandbox:ubuntu-22.04",
             "suite": "jammy", "mirror": _UBUNTU_MIRROR},
        ],
    },
    {
        "id": "debian",
        "name": "Debian",
        "description": "以稳定著称的老牌发行版，软件兼容性好，适合长期运行",
        "recommended": False,
        "family": "apt",
        "dockerfile": "deploy/sandbox/Dockerfile.debian",
        "versions": [
            {"id": "13", "label": "13", "maintenance_until": "2030 年",
             "base_image": "debian:13", "image": "agentflow/sandbox:debian-13",
             "suite": "trixie", "mirror": _DEBIAN_MIRROR},
            {"id": "12", "label": "12", "maintenance_until": "2028 年",
             "base_image": "debian:12", "image": "agentflow/sandbox:debian-12",
             "suite": "bookworm", "mirror": _DEBIAN_MIRROR},
        ],
    },
    {
        "id": "centos",
        "name": "CentOS",
        "description": "企业级服务器系统的主流选择，与红帽生态兼容，稳定可靠",
        "recommended": False,
        "family": "dnf",
        "dockerfile": "deploy/sandbox/Dockerfile.centos",
        "versions": [
            {"id": "stream-10", "label": "Stream 10", "maintenance_until": "2030 年",
             "base_image": "quay.io/centos/centos:stream10", "image": "agentflow/sandbox:centos-stream-10",
             "releasever": "10", "release_package": "centos-stream-release",
             "gpg_key_url": "https://www.centos.org/keys/RPM-GPG-KEY-centosofficial",
             "repos": [
                 {"id": "baseos", "name": "CentOS Stream 10 BaseOS",
                  "baseurl": "https://mirror.stream.centos.org/10-stream/BaseOS/x86_64/os/"},
                 {"id": "appstream", "name": "CentOS Stream 10 AppStream",
                  "baseurl": "https://mirror.stream.centos.org/10-stream/AppStream/x86_64/os/"},
             ]},
            {"id": "stream-9", "label": "Stream 9", "maintenance_until": "2027 年",
             "base_image": "quay.io/centos/centos:stream9", "image": "agentflow/sandbox:centos-stream-9",
             "releasever": "9", "release_package": "centos-stream-release",
             "gpg_key_url": "https://www.centos.org/keys/RPM-GPG-KEY-centosofficial",
             "repos": [
                 {"id": "baseos", "name": "CentOS Stream 9 BaseOS",
                  "baseurl": "https://mirror.stream.centos.org/9-stream/BaseOS/x86_64/os/"},
                 {"id": "appstream", "name": "CentOS Stream 9 AppStream",
                  "baseurl": "https://mirror.stream.centos.org/9-stream/AppStream/x86_64/os/"},
             ]},
        ],
    },
    {
        "id": "rocky",
        "name": "Rocky Linux",
        "description": "与红帽企业版完全兼容的社区发行版，稳定耐用，维护周期最长",
        "recommended": False,
        "family": "dnf",
        "dockerfile": "deploy/sandbox/Dockerfile.rocky",
        "versions": [
            {"id": "10", "label": "10", "maintenance_until": "2035 年",
             "base_image": "rockylinux:10", "image": "agentflow/sandbox:rocky-10",
             "releasever": "10", "release_package": "rocky-release",
             "gpg_key_url": "https://dl.rockylinux.org/pub/rocky/RPM-GPG-KEY-rockyofficial",
             "repos": [
                 {"id": "baseos", "name": "Rocky Linux 10 BaseOS",
                  "baseurl": "https://dl.rockylinux.org/pub/rocky/10/BaseOS/x86_64/os/"},
                 {"id": "appstream", "name": "Rocky Linux 10 AppStream",
                  "baseurl": "https://dl.rockylinux.org/pub/rocky/10/AppStream/x86_64/os/"},
             ]},
            {"id": "9", "label": "9", "maintenance_until": "2032 年",
             "base_image": "rockylinux:9", "image": "agentflow/sandbox:rocky-9",
             "releasever": "9", "release_package": "rocky-release",
             "gpg_key_url": "https://dl.rockylinux.org/pub/rocky/RPM-GPG-KEY-rockyofficial",
             "repos": [
                 {"id": "baseos", "name": "Rocky Linux 9 BaseOS",
                  "baseurl": "https://dl.rockylinux.org/pub/rocky/9/BaseOS/x86_64/os/"},
                 {"id": "appstream", "name": "Rocky Linux 9 AppStream",
                  "baseurl": "https://dl.rockylinux.org/pub/rocky/9/AppStream/x86_64/os/"},
             ]},
        ],
    },
]

DEFAULT_DISTRO = "ubuntu"
DISTRO_IDS = {d["id"] for d in DISTROS}
_BY_ID = {d["id"]: d for d in DISTROS}


def default_version(distro_id):
    return _BY_ID[distro_id]["versions"][0]["id"]


def find_version(distro_id, version_id):
    distro = _BY_ID.get(distro_id)
    if distro is None:
        return None
    for v in distro["versions"]:
        if v["id"] == version_id:
            return v
    return None


def distro_family(distro_id):
    distro = _BY_ID.get(distro_id)
    return distro["family"] if distro else None


def distro_name(distro_id):
    distro = _BY_ID.get(distro_id)
    return distro["name"] if distro else distro_id


def version_label(distro_id, version_id):
    ver = find_version(distro_id, version_id)
    return ver["label"] if ver else version_id


def pair_label(distro_id, version_id):
    return f"{distro_name(distro_id)} {version_label(distro_id, version_id)}"


def install_meta(distro_id, version_id):
    """Host-native install data for one pair: family + the version's
    family-specific fields (suite/mirror or releasever/repos)."""
    distro = _BY_ID[distro_id]
    ver = find_version(distro_id, version_id)
    meta = {"family": distro["family"]}
    for key in ("suite", "mirror", "releasever", "repos",
                "release_package", "gpg_key_url"):
        if key in ver:
            meta[key] = ver[key]
    return meta


def active_sandbox(db):
    """The currently selected sandbox as an execution target, or None.

    Returns distro / version / method / label, plus root (the host-native
    system directory) when the install method is host-native."""
    distro_id, version_id = get_selection(db)
    if not distro_id:
        return None
    rec = install_record(db, distro_id, version_id)
    method = (rec.method or "") if rec else ""
    return {
        "distro": distro_id, "version": version_id, "method": method,
        "label": pair_label(distro_id, version_id),
        "root": host_native.rootfs_path(distro_id, version_id)
        if method == "host-native" else None,
    }


_PROBE_TTL_SECONDS = 15
_probe_cache = {"at": 0.0, "value": None}


def docker_state():
    """(cli_available, daemon_reachable), probed for real and briefly cached
    so page polling cannot hammer the docker CLI."""
    now = time.monotonic()
    if _probe_cache["value"] is not None and now - _probe_cache["at"] < _PROBE_TTL_SECONDS:
        return _probe_cache["value"]
    cli = shutil.which("docker") is not None
    daemon = False
    if cli:
        try:
            r = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                               capture_output=True, timeout=5)
            daemon = r.returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            daemon = False
    _probe_cache.update(at=now, value=(cli, daemon))
    return cli, daemon


def image_status(image, daemon_reachable):
    """built | missing when the daemon can be asked, otherwise unknown."""
    if not daemon_reachable:
        return "unknown"
    try:
        r = subprocess.run(["docker", "image", "inspect", image],
                           capture_output=True, timeout=5)
        return "built" if r.returncode == 0 else "missing"
    except (subprocess.TimeoutExpired, OSError):
        return "unknown"


def install_record(db, distro_id, version_id):
    return db.query(models.SandboxInstall).filter_by(
        distro=distro_id, version=version_id).first()


def install_status(db, distro_id, version_id):
    row = install_record(db, distro_id, version_id)
    return row.status if row else "not_installed"


def get_selection(db):
    """(distro_id, version_id) of the current sandbox, or (None, None).

    Nothing is selected until the user installs a distribution+version and
    sets it as current: a stored pair only counts while its install record
    says installed."""
    row = db.query(models.SandboxConfig).get(1)
    if row is None or not row.selected_distro or not row.selected_version:
        return None, None
    if find_version(row.selected_distro, row.selected_version) is None:
        return None, None
    if install_status(db, row.selected_distro, row.selected_version) != "installed":
        return None, None
    return row.selected_distro, row.selected_version


def set_selection(db, distro_id, version_id):
    row = db.query(models.SandboxConfig).get(1)
    if row is None:
        row = models.SandboxConfig(id=1, selected_distro=distro_id,
                                   selected_version=version_id)
        db.add(row)
    else:
        row.selected_distro = distro_id
        row.selected_version = version_id
    db.commit()
    db.refresh(row)
    return row.selected_distro, row.selected_version


# ------------------------------------------------------------- installing ----

_REPO_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", ".."))


def _install_out(row):
    return {"distro": row.distro, "version": row.version, "status": row.status,
            "method": row.method or "", "log": row.log or "",
            "updated_at": row.updated_at.isoformat() if row.updated_at else None}


def reconcile_interrupted(db):
    """Fail install records left in a running state by a previous process.

    Called once at application startup, when this process provably has
    no install worker running yet: any sandbox install still marked
    installing, or dependency job still marked running, was interrupted
    (restart, crash) and would otherwise block new installs forever —
    start_install refuses to start while a record says installing.
    """
    changed = False
    for row in db.query(models.SandboxInstall).filter_by(status="installing").all():
        row.status = "failed"
        note = "安装被中断：服务重启，之前的安装没有完成，请重新安装。"
        row.log = ((row.log or "") + "\n" + note)[-8000:]
        changed = True
    for job in db.query(models.DependencyInstallJob).filter_by(status="running").all():
        job.status = "failed"
        job.log = ((job.log or "") + "；安装被中断：服务重启").strip("；")
        from datetime import datetime as _dt
        job.finished_at = _dt.utcnow()
        changed = True
    if changed:
        db.commit()


_install_lock = threading.Lock()


def start_install(db, distro_id, version_id):
    # The "already installing?" check and the state flip must be atomic:
    # two concurrent calls could otherwise both pass the check and spawn
    # two workers writing the same system directory.
    with _install_lock:
        return _start_install_locked(db, distro_id, version_id)


def _start_install_locked(db, distro_id, version_id):
    """Begin installing one distribution+version, honestly.

    The install method is chosen automatically: Docker when its daemon
    really answers (pull + build the sandbox image), otherwise
    host-native — the chosen system is installed straight into its own
    system directory on this machine (debootstrap for the Debian family,
    dnf --installroot for the RHEL family). Either way a background
    worker does the real work and the record shows the real outcome."""
    row = install_record(db, distro_id, version_id)
    if row is None:
        row = models.SandboxInstall(distro=distro_id, version=version_id,
                                    status="not_installed", log="")
        db.add(row)
        db.commit()
        db.refresh(row)
    if row.status == "installing":
        return _install_out(row)
    _cli, daemon = docker_state()
    if daemon:
        row.method = "docker"
        row.status = "installing"
        row.log = "开始安装：拉取官方基础镜像并构建沙箱镜像…"
        db.commit()
        db.refresh(row)
        threading.Thread(target=_build_worker, args=(distro_id, version_id),
                         daemon=True).start()
        db.refresh(row)
        return _install_out(row)
    row.method = "host-native"
    row.status = "installing"
    row.log = "开始安装：Docker 不可用，改用本机直装，把所选系统装进独立系统目录…"
    db.commit()
    db.refresh(row)
    threading.Thread(target=_host_native_worker, args=(distro_id, version_id),
                     daemon=True).start()
    db.refresh(row)
    return _install_out(row)


def _host_native_worker(distro_id, version_id):
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        row = install_record(db, distro_id, version_id)
        if row is None:
            return
        lines = [row.log or ""]

        def log(line):
            lines.append(line)
            row.log = "\n".join(lines)[-8000:]
            db.commit()

        try:
            host_native.install_rootfs(
                distro_id, version_id, install_meta(distro_id, version_id), log)
            row.status = "installed"
        except host_native.InstallError as e:
            row.status = "failed"
            lines.append(f"安装失败：{e}")
        except Exception as e:  # unexpected: still record honestly
            row.status = "failed"
            lines.append(f"安装失败：{e}")
        row.log = "\n".join(lines)[-8000:]
        db.commit()
    finally:
        db.close()


def _run_docker(args, timeout=1800):
    r = subprocess.run(["docker"] + args, capture_output=True, text=True,
                       timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _build_worker(distro_id, version_id):
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        row = install_record(db, distro_id, version_id)
        if row is None:
            return
        try:
            _build_worker_inner(db, row, distro_id, version_id)
        except Exception as e:  # never leave the record stuck "installing"
            db.rollback()
            row.status = "failed"
            row.log = ((row.log or "") + f"\n安装失败：{e}")[-4000:]
            db.commit()
    finally:
        db.close()


def _build_worker_inner(db, row, distro_id, version_id):
    distro = _BY_ID[distro_id]
    ver = find_version(distro_id, version_id)
    log_parts = []
    try:
        code, out = _run_docker(["pull", ver["base_image"]], timeout=1200)
        log_parts.append(f"$ docker pull {ver['base_image']}\n{out[-1500:]}")
        if code != 0:
            row.status = "failed"
            row.log = "\n".join(log_parts)[-4000:]
            db.commit()
            return
        dockerfile = os.path.join(_REPO_ROOT, distro["dockerfile"])
        context = os.path.join(_REPO_ROOT, "deploy", "sandbox")
        code, out = _run_docker(
            ["build", "-t", ver["image"], "-f", dockerfile,
             "--build-arg", f"BASE_IMAGE={ver['base_image']}", context],
            timeout=1800)
        log_parts.append(f"$ docker build -t {ver['image']} …\n{out[-2500:]}")
        row.status = "installed" if code == 0 else "failed"
    except subprocess.TimeoutExpired:
        row.status = "failed"
        log_parts.append("安装超时，已终止。")
    except OSError as e:
        row.status = "failed"
        log_parts.append(f"无法执行 docker 命令：{e}")
    row.log = "\n".join(log_parts)[-4000:]
    db.commit()


def _mode_note(mode):
    if mode == "container":
        return "当前是容器沙箱，AI 电脑运行在 Docker 容器里，与这台机器本身隔离。"
    if mode == "host_sandbox":
        return ("当前是本机沙箱：所选系统装在这台机器的一个独立系统目录里运行，"
                "与这台电脑的文件隔离，共用系统内核。终端命令会进入这个沙箱系统内运行；"
                "图形桌面仍是这台电脑的桌面，不在沙箱系统里运行。")
    return ("当前是本机模式，AI 电脑直接运行在这台机器上。这台机器没有可用的 Docker，"
            "安装系统时会自动改走本机直装：把所选系统装进一个独立系统目录作为本机沙箱；"
            "接入 Docker 后则按容器沙箱安装。")


def switch_note(mode):
    if mode == "container":
        return "已设为当前沙箱，将在容器沙箱下次启动时生效，当前运行中的会话和共享文件不受影响。"
    if mode == "host_sandbox":
        return "已设为当前沙箱，终端命令将进入这个沙箱系统内运行，共享文件保持同一份。"
    return "已设为当前沙箱。本机模式下仅记录选择，接入 Docker 环境后将按所选系统启动容器沙箱。"


def sandbox_info(db):
    cli, daemon = docker_state()
    sel_distro, sel_version = get_selection(db)
    installs = {(r.distro, r.version): r for r in db.query(models.SandboxInstall).all()}
    sel_rec = installs.get((sel_distro, sel_version)) if sel_distro else None
    sel_method = (sel_rec.method or "") if sel_rec else ""
    if sel_method == "host-native":
        mode = "host_sandbox"
    elif daemon:
        mode = "container"
    else:
        mode = "host"
    distros = []
    for d in DISTROS:
        item = {k: d[k] for k in ("id", "name", "description", "recommended", "dockerfile")}
        versions = []
        for v in d["versions"]:
            ver = dict(v)
            rec = installs.get((d["id"], v["id"]))
            ver["install_status"] = rec.status if rec else "not_installed"
            ver["install_method"] = (rec.method or "") if rec else ""
            ver["install_log"] = (rec.log or "")[-500:] if rec else ""
            ver["selected"] = d["id"] == sel_distro and v["id"] == sel_version
            versions.append(ver)
        item["versions"] = versions
        item["selected"] = d["id"] == sel_distro
        distros.append(item)
    return {
        "distros": distros,
        "selected_distro": sel_distro,
        "selected_version": sel_version,
        "selected_method": sel_method,
        "isolation_mode": mode,
        "docker": {"cli_available": cli, "daemon_reachable": daemon},
        "note": _mode_note(mode),
    }
