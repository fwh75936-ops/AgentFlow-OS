"""Host-native sandbox installation: a real second install path next to
Docker, for machines where Docker is unavailable (the user explicitly
allowed changing system configuration instead).

A distribution+version is installed straight into its own system
directory under data/sandboxes/<distro>-<version>/:

- Debian family (Ubuntu / Debian): debootstrap installs the chosen
  release into the directory.
- RHEL family (CentOS Stream / Rocky Linux): dnf --installroot installs
  the chosen release into the directory, using the official upstream
  repositories.

Commands later run inside that directory with chroot, as the
non-privileged user ``agentflow`` created inside it; only the shared
workspace is bind-mounted in, at /workspace. The isolation is honestly a
separate system directory on a shared kernel — not a container and not
a virtual machine — and the graphical desktop stays on the host.

Every external command goes through run_process so tests can substitute
the channel; install outcomes are always the real exit codes and output.
"""
import json
import os
import shlex
import shutil
import subprocess
from datetime import datetime

from app.workspace import config

AGENTFLOW_UID = 1000
AGENTFLOW_GID = 1000
MARKER_NAME = ".agentflow-sandbox.json"


class InstallError(Exception):
    """Raised when a host-native install cannot proceed; the message is
    the honest, user-facing reason recorded on the install row."""


# ------------------------------------------------------------ processes ----

def run_process(argv, timeout=600, env=None):
    """Single entry point for every external command (patchable in tests).
    Returns (exit_code, stdout, stderr); TimeoutExpired propagates."""
    proc = subprocess.run(argv, capture_output=True, text=True,
                          timeout=timeout, env=env)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _proxy_env():
    """Environment for network commands: this machine reaches package
    mirrors through the egress proxy variables already present in the
    process environment, and chrooted processes share the host network,
    so the same variables work inside the sandbox."""
    env = dict(os.environ)
    return env


def _sudo_prefix():
    if os.geteuid() == 0:
        return []
    return ["sudo", "-n"] if shutil.which("sudo") else []


# -------------------------------------------------------------- rootfs ----

def rootfs_path(distro_id, version_id):
    # The ids normally come from the validated catalogue, but they also
    # reach here from stored state — never let one escape the sandboxes
    # directory via separators or parent references.
    for part in (distro_id, version_id):
        if not part or "/" in str(part) or "\\" in str(part) or ".." in str(part):
            raise ValueError(f"非法的沙箱标识：{part!r}")
    return os.path.join(config.get_sandboxes_dir(), f"{distro_id}-{version_id}")


def _chown_supported(directory):
    """True when files in this directory can be chowned to arbitrary
    uid/gid — a hard requirement for a rootfs (system files belong to
    many different users). Probed for real, never assumed."""
    probe = os.path.join(directory, ".agentflow-chown-probe")
    try:
        with open(probe, "w") as f:
            f.write("")
        os.chown(probe, AGENTFLOW_UID, AGENTFLOW_GID)
        return True
    except OSError:
        return False
    finally:
        try:
            os.remove(probe)
        except OSError:
            pass


def ensure_backing_store():
    """Make sure the sandboxes directory can hold root filesystems.

    On a normal machine the data directory works as-is ("direct"). In
    restricted environments whose filesystem refuses chown (this dev VM:
    an overlay owned by another user namespace), fall back to a tmpfs
    mounted over the sandboxes directory — same paths, full ownership
    control, at the honest cost that sandboxes do not survive a machine
    reboot there. Raises InstallError when neither works."""
    base = config.get_sandboxes_dir()
    if _chown_supported(base):
        return "direct"
    if base in _mounted_points():
        return "tmpfs"
    size_mb = os.environ.get("AGENTFLOW_SANDBOX_TMPFS_MB", "3072")
    code, out, err = run_process(
        ["mount", "-t", "tmpfs", "-o", f"size={size_mb}M", "tmpfs", base],
        timeout=30)
    if code != 0:
        raise InstallError(
            "这台机器的文件系统不支持沙箱所需的用户所有权设置，"
            f"也无法挂载内存文件系统作为承载：{(err or out)[-300:]}")
    return "tmpfs"


def rootfs_ready(root):
    return os.path.isfile(os.path.join(root, MARKER_NAME)) and \
        os.path.isfile(os.path.join(root, "etc", "os-release"))


def os_pretty_name(root):
    try:
        with open(os.path.join(root, "etc", "os-release"), encoding="utf-8") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return ""


def _read(root, rel):
    try:
        with open(os.path.join(root, rel), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _write(root, rel, content):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _mounted_points():
    points = set()
    try:
        with open("/proc/mounts", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    points.add(parts[1].replace("\\040", " "))
    except OSError:
        pass
    return points


def _unmount_tree(root):
    """Unmount anything mounted at or below root (deepest first) so the
    directory can be removed safely — a bind-mounted workspace must never
    be traversed by a delete."""
    prefix = os.path.abspath(root)
    targets = sorted(
        (p for p in _mounted_points()
         if p == prefix or p.startswith(prefix + os.sep)),
        key=len, reverse=True)
    for target in targets:
        try:
            run_process(["umount", target], timeout=30)
        except Exception:
            try:
                run_process(["umount", "-l", target], timeout=30)
            except Exception:
                pass


def _clean_rootfs(root):
    if os.path.isdir(root):
        _unmount_tree(root)
        prefix = os.path.abspath(root)
        still = [p for p in _mounted_points()
                 if p == prefix or p.startswith(prefix + os.sep)]
        if still:
            # Deleting through a live bind mount would erase the SHARED
            # files behind it. Refuse and say so instead.
            raise InstallError(
                f"系统目录下仍有挂载未能卸载（{'、'.join(still)}），已停止清理以保护共享文件，请稍后重试。")
        shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root, exist_ok=True)


# --------------------------------------------------------- host tooling ----

def _ensure_host_tool(tool, apt_package, log):
    """Make sure a host tool exists, installing it with apt when missing
    (the user authorized changing system configuration for this)."""
    if shutil.which(tool):
        return
    log(f"这台机器上还没有 {tool}，正在安装 {apt_package}…")
    if not shutil.which("apt-get"):
        raise InstallError(f"需要 {tool} 才能安装，但这台机器上没有 apt 可用，无法自动补齐。")
    prefix = _sudo_prefix()
    if os.geteuid() != 0 and not prefix:
        raise InstallError(f"需要 {tool} 才能安装，但当前进程没有管理员权限，也没有可用的 sudo。")
    env = _proxy_env()
    env["DEBIAN_FRONTEND"] = "noninteractive"
    code, out, err = run_process(prefix + ["apt-get", "update"], timeout=600, env=env)
    if code != 0:
        log(f"刷新软件包列表失败，继续尝试安装：{(err or out)[-300:]}")
    code, out, err = run_process(prefix + ["apt-get", "install", "-y", apt_package],
                                 timeout=900, env=env)
    if code != 0 or not shutil.which(tool):
        raise InstallError(f"自动安装 {apt_package} 失败：{(err or out)[-400:]}")


# ------------------------------------------------------- debian family ----

def _debootstrap_argv(meta, root):
    argv = ["debootstrap", "--variant=minbase", meta["suite"], root, meta["mirror"]]
    script = f"/usr/share/debootstrap/scripts/{meta['suite']}"
    if not os.path.exists(script):
        # Some suites ship no script file of their own on this host (every
        # Ubuntu suite is served by the shared gutsy script).
        fallback = "/usr/share/debootstrap/scripts/gutsy"
        if os.path.exists(fallback):
            argv.append(fallback)
    return argv


def _install_debian_family(meta, root, log):
    _ensure_host_tool("debootstrap", "debootstrap", log)
    argv = _debootstrap_argv(meta, root)
    log(f"正在把 {meta['suite']} 装进独立系统目录，这一步要下载系统文件，请稍等…")
    env = _proxy_env()
    try:
        code, out, err = run_process(argv, timeout=2400, env=env)
    except subprocess.TimeoutExpired:
        raise InstallError("安装超时（40 分钟），已终止。网络较慢时可以稍后重新安装。")
    combined = (out + err)
    if code != 0:
        # No silent downgrade: an OS installed without signature
        # verification cannot be trusted, so a keyring problem is a
        # hard failure with an actionable message, never a quiet retry
        # with --no-check-gpg.
        if ("gpg" in combined.lower() or "key" in combined.lower()
                or "signature" in combined.lower()):
            raise InstallError(
                "签名校验未通过：这台机器缺少目标系统仓库的签名密钥，"
                "为保证安装的系统可信，已停止安装（不会改用不校验签名的方式）。"
                f"原始错误：{combined[-300:]}")
        raise InstallError(f"debootstrap 安装失败：{combined[-500:]}")
    log("系统文件安装完成。")


# --------------------------------------------------------- rhel family ----

def _fetch(url, timeout=60):
    code, out, err = run_process(
        ["curl", "-fsSL", url], timeout=timeout, env=_proxy_env())
    if code != 0:
        raise InstallError(f"下载失败 {url}：{(err or out)[-200:]}")
    return out


def _install_rhel_family(meta, root, log):
    _ensure_host_tool("dnf", "dnf", log)
    import tempfile
    repos_dir = tempfile.mkdtemp(prefix="agentflow-repos-")
    try:
        key_url = meta.get("gpg_key_url")
        if not key_url:
            raise InstallError("这个发行版没有配置官方签名密钥地址，为保证可信已停止安装。")
        # No unverified fallback: if the official signing key cannot be
        # fetched, the install stops instead of proceeding gpgcheck=0.
        key_text = _fetch(key_url)
        key_path = os.path.join(repos_dir, "RPM-GPG-KEY")
        with open(key_path, "w", encoding="utf-8") as f:
            f.write(key_text)
        gpg_line = f"gpgcheck=1\ngpgkey=file://{key_path}"
        sections = []
        for repo in meta["repos"]:
            sections.append(
                f"[{repo['id']}]\nname={repo['name']}\nbaseurl={repo['baseurl']}\n"
                f"enabled=1\n{gpg_line}\n")
        repo_file = os.path.join(repos_dir, "agentflow-bootstrap.repo")
        with open(repo_file, "w", encoding="utf-8") as f:
            f.write("\n".join(sections))
        packages = [meta["release_package"], "bash", "coreutils", "dnf"]
        argv = ["dnf", "--installroot", root,
                "--releasever", str(meta["releasever"]),
                f"--setopt=reposdir={repos_dir}",
                "--setopt=install_weak_deps=False",
                "-y", "install"] + packages
        log("正在用官方仓库把系统装进独立系统目录，这一步要下载系统文件，请稍等…")
        try:
            code, out, err = run_process(argv, timeout=2400, env=_proxy_env())
        except subprocess.TimeoutExpired:
            raise InstallError("安装超时（40 分钟），已终止。网络较慢时可以稍后重新安装。")
        if code != 0:
            raise InstallError(f"dnf 安装失败：{(err or out)[-500:]}")
        log("系统文件安装完成。")
    finally:
        shutil.rmtree(repos_dir, ignore_errors=True)


# ------------------------------------------------------------ finalize ----

def _ensure_agentflow_user(root):
    """Create the non-privileged user agentflow (uid/gid 1000) inside the
    rootfs by editing its account files directly — deterministic, and it
    works even in a minbase system without useradd tooling in place."""
    passwd = _read(root, "etc/passwd")
    if not any(line.startswith("agentflow:") for line in passwd.splitlines()):
        if passwd and not passwd.endswith("\n"):
            passwd += "\n"
        passwd += f"agentflow:x:{AGENTFLOW_UID}:{AGENTFLOW_GID}:AgentFlow:/home/agentflow:/bin/bash\n"
        _write(root, "etc/passwd", passwd)
    group = _read(root, "etc/group")
    if not any(line.startswith("agentflow:") for line in group.splitlines()):
        if group and not group.endswith("\n"):
            group += "\n"
        group += f"agentflow:x:{AGENTFLOW_GID}:\n"
        _write(root, "etc/group", group)
    shadow = _read(root, "etc/shadow")
    if shadow and not any(line.startswith("agentflow:") for line in shadow.splitlines()):
        if not shadow.endswith("\n"):
            shadow += "\n"
        shadow += "agentflow:!:19000:0:99999:7:::\n"
        _write(root, "etc/shadow", shadow)
    home = os.path.join(root, "home", "agentflow")
    os.makedirs(home, exist_ok=True)
    try:
        os.chown(home, AGENTFLOW_UID, AGENTFLOW_GID)
    except OSError:
        pass


def _ensure_sandbox_apt_config(root):
    """Keep apt downloads running as the sandbox root user.

    apt normally drops to its _apt user to fetch packages. On machines
    whose network出口 only serves the primary user, those downloads are
    refused (the sandbox root shares the primary user's identity, the
    _apt user does not), so pin the download sandbox user to root.
    Idempotent; apt-family rootfs only."""
    apt_conf_dir = os.path.join(root, "etc", "apt", "apt.conf.d")
    if not os.path.isdir(apt_conf_dir):
        return
    path = os.path.join(apt_conf_dir, "99agentflow-sandbox")
    try:
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                f.write('Acquire::Sandbox::User "root";\n')
        # Files created under a restrictive umask land as 660 and apt
        # (dropping to _apt to read its config) cannot open them at all.
        os.chmod(path, 0o644)
    except OSError:
        pass


def _sync_host_network_files(root):
    """Copy the host's name-resolution files into the rootfs.

    The sandbox shares the host's network, so it needs the host's
    resolver data — not just /etc/resolv.conf but also /etc/hosts.
    Hosts routinely carry static mappings (internal gateways, egress
    proxies) that DNS alone does not reproduce: without them a name
    can resolve to a different, unreachable address inside the
    sandbox than on the host, and downloads fail for no visible
    reason. Idempotent; called at install time and again on every
    exec so already-installed sandboxes heal themselves."""
    for rel in ("etc/resolv.conf", "etc/hosts"):
        try:
            with open(os.path.join("/", rel), encoding="utf-8") as f:
                content = f.read()
        except OSError:
            continue
        if not content:
            continue
        path = os.path.join(root, rel)
        try:
            existing = ""
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as f:
                    existing = f.read()
            if existing != content:
                _write(root, rel, content)
            os.chmod(path, 0o644)
        except OSError:
            pass


def _finalize_rootfs(root, distro_id, version_id):
    # The rootfs top directory becomes "/" after chroot: it must stay
    # traversable by the unprivileged sandbox user. A directory created
    # under a restrictive umask (e.g. 770) would lock agentflow out of
    # every binary inside, so pin the mode explicitly.
    try:
        os.chmod(root, 0o755)
    except OSError:
        pass
    _ensure_agentflow_user(root)
    _ensure_sandbox_apt_config(root)
    os.makedirs(os.path.join(root, "workspace"), exist_ok=True)
    _sync_host_network_files(root)
    _write(root, "etc/hostname", "agentflow-sandbox\n")
    # Drop downloaded package archives: the package manager can fetch
    # them again, and they would otherwise double the sandbox footprint.
    for cache_dir in (os.path.join(root, "var", "cache", "apt", "archives"),
                      os.path.join(root, "var", "cache", "dnf")):
        if os.path.isdir(cache_dir):
            shutil.rmtree(cache_dir, ignore_errors=True)
    marker = {"distro": distro_id, "version": version_id, "method": "host-native",
              "installed_at": datetime.utcnow().isoformat() + "Z"}
    _write(root, MARKER_NAME, json.dumps(marker, ensure_ascii=False, indent=2))


def install_rootfs(distro_id, version_id, meta, log):
    """Install one distribution+version into its system directory.
    meta carries the family-specific install data from the catalogue.
    log is a callable receiving progress lines. Raises InstallError."""
    if os.geteuid() != 0 and not _sudo_prefix():
        raise InstallError("安装系统需要管理员权限：当前进程不是管理员，也没有可用的 sudo。")
    if ensure_backing_store() == "tmpfs":
        log("这台机器的文件系统不支持沙箱所需的用户隔离，系统改装到内存文件系统上；"
            "机器重启后沙箱需要重新安装。")
    root = rootfs_path(distro_id, version_id)
    _clean_rootfs(root)
    try:
        if meta["family"] == "apt":
            _install_debian_family(meta, root, log)
        else:
            _install_rhel_family(meta, root, log)
        if not os.path.isfile(os.path.join(root, "etc", "os-release")):
            raise InstallError("安装命令结束了，但系统目录里没有找到系统标识文件，安装不完整。")
        _finalize_rootfs(root, distro_id, version_id)
    except Exception:
        # Leave no half-installed system behind (any failure, not only
        # InstallError): clear the directory so a later retry starts
        # clean and rootfs_ready stays false.
        _clean_rootfs(root)
        raise
    log(f"已就绪：{os_pretty_name(root) or distro_id}，系统目录 {root}")
    return root


# ------------------------------------------------------------ execution ----

def ensure_workspace_mount(root):
    """Bind-mount the shared workspace at <root>/workspace so sandbox
    commands see the very same files as the host tools. Returns
    (ok, error). proc/dev binds are best-effort extras."""
    target = os.path.join(root, "workspace")
    os.makedirs(target, exist_ok=True)
    # Same traversal guard as _finalize_rootfs, applied on every exec so
    # sandboxes installed before this fix heal themselves.
    try:
        os.chmod(root, 0o755)
    except OSError:
        pass
    _sync_host_network_files(root)
    # The shared workspace is shared by design: the sandbox user is a
    # different uid, so the top directory must be world-writable for it
    # to create files under /workspace (top level only, not recursive).
    shared_root = config.get_shared_root()
    try:
        if not (os.stat(shared_root).st_mode & 0o002):
            os.chmod(shared_root, 0o777)
    except OSError:
        pass
    mounted = _mounted_points()
    if target not in mounted:
        code, out, err = run_process(
            ["mount", "--bind", config.get_shared_root(), target], timeout=30)
        if code != 0:
            return False, f"无法把共享目录挂入沙箱：{(err or out)[-300:]}"
    proc_target = os.path.join(root, "proc")
    if proc_target not in _mounted_points():
        try:
            run_process(["mount", "-t", "proc", "proc", proc_target], timeout=15)
        except Exception:
            pass
    dev_target = os.path.join(root, "dev")
    if dev_target not in _mounted_points():
        try:
            run_process(["mount", "--bind", "/dev", dev_target], timeout=15)
        except Exception:
            pass
    _ensure_dev_nodes(root)
    _ensure_sandbox_apt_config(root)
    return True, ""


def _ensure_dev_nodes(root):
    """Guarantee usable /dev/null & friends inside the rootfs.

    A minbase rootfs ships placeholder regular files for device nodes,
    and the /dev bind above is best-effort: when it does not take, an
    unprivileged user cannot even redirect to /dev/null. Fall back to
    real nodes (mknod), then per-file binds, then writable placeholders.
    """
    import stat as stat_mod

    wanted = {"null": (1, 3), "zero": (1, 5), "full": (1, 7),
              "random": (1, 8), "urandom": (1, 9), "tty": (5, 0)}
    dev_dir = os.path.join(root, "dev")
    for name, (major, minor) in wanted.items():
        path = os.path.join(dev_dir, name)
        try:
            if os.path.exists(path) and stat_mod.S_ISCHR(os.stat(path).st_mode):
                continue
        except OSError:
            pass
        try:
            if os.path.lexists(path):
                os.remove(path)
            os.mknod(path, stat_mod.S_IFCHR | 0o666, os.makedev(major, minor))
            continue
        except OSError:
            pass
        host_node = os.path.join("/dev", name)
        try:
            if not os.path.lexists(path):
                open(path, "a").close()
            code, _out, _err = run_process(
                ["mount", "--bind", host_node, path], timeout=15)
            if code == 0:
                continue
        except Exception:
            pass
        try:
            os.chmod(path, 0o666)
        except OSError:
            pass


def chroot_argv(root, inner_script, userspec=None):
    """argv running inner_script with /bin/bash inside the sandbox rootfs.
    Default user is agentflow (1000:1000); pass userspec='0:0' for
    system administration such as package installs."""
    spec = userspec if userspec is not None else f"{AGENTFLOW_UID}:{AGENTFLOW_GID}"
    return ["chroot", f"--userspec={spec}", root, "/bin/bash", "-lc", inner_script]


def wrap_command(root, command, userspec="0:0"):
    """A host shell string that runs command inside the rootfs — used by
    the dependency installer, whose channel is a host shell string."""
    return (f"chroot --userspec={userspec} {shlex.quote(root)} "
            f"/bin/bash -lc {shlex.quote(command)}")
