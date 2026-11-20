"""Host-native sandbox installs: the no-Docker path that installs the
chosen system straight into its own directory on this machine.

Covered here: catalogue install metadata for both families, automatic
method selection (no Docker daemon -> host-native), honest failure when
the installer fails, workspace commands executing inside the sandbox
(chroot argv, audit label, cwd tracking), and one-click dependencies
landing inside the current sandbox. The heavy external channels
(debootstrap / dnf / real chroot subprocesses) are substituted; the one
real install on this machine is verified outside the test suite.
"""
import json
import os
import sys
import types as _types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import Base, get_db
from app.workspace import dependencies, host_native, sandbox, service
from app.workspace.routes import router


class _SyncThread:
    """Run a worker inline so install outcomes are visible immediately."""

    def __init__(self, target=None, args=(), daemon=None):
        self._target, self._args = target, args

    def start(self):
        self._target(*self._args)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///" + str(tmp_path / "sandbox.db"),
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_db] = override_get_db
    test_app.state.Session = Session
    # workers open their own session from app.database — point it at the
    # test database, run them synchronously, and keep sandboxes in tmp.
    monkeypatch.setattr("app.database.SessionLocal", Session)
    monkeypatch.setattr(sandbox, "threading",
                        _types.SimpleNamespace(Thread=_SyncThread))
    monkeypatch.setenv("AGENTFLOW_SANDBOXES_DIR", str(tmp_path / "sandboxes"))
    monkeypatch.setenv("AGENTFLOW_SHARED_ROOT", str(tmp_path / "shared"))
    return TestClient(test_app)


def _fake_install_ok(distro_id, version_id, meta, log):
    root = host_native.rootfs_path(distro_id, version_id)
    os.makedirs(os.path.join(root, "etc"), exist_ok=True)
    pretty = {"debian": "Debian GNU/Linux 12 (bookworm)",
              "ubuntu": "Ubuntu 24.04 LTS"}.get(distro_id, distro_id)
    with open(os.path.join(root, "etc", "os-release"), "w") as f:
        f.write(f'PRETTY_NAME="{pretty}"\n')
    host_native._finalize_rootfs(root, distro_id, version_id)
    log("模拟安装完成")
    return root


def _seed_agent(db):
    agent = models.Agent(name="开发工程师", role="developer", description="",
                         tools=["linux_exec"], capability_tags=["coding"],
                         capability_scores={"coding": 0.9}, status="active")
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent


# ------------------------------------------------------------- catalogue ----

def test_catalogue_carries_host_native_install_metadata():
    for d in sandbox.DISTROS:
        assert d["family"] in ("apt", "dnf")
        for v in d["versions"]:
            meta = sandbox.install_meta(d["id"], v["id"])
            assert meta["family"] == d["family"]
            if d["family"] == "apt":
                assert meta["suite"] and meta["mirror"].startswith("http")
            else:
                assert meta["releasever"] and meta["release_package"]
                assert len(meta["repos"]) >= 2
                assert all(r["baseurl"].startswith("https://") for r in meta["repos"])


def test_finalize_rootfs_creates_agentflow_user_and_marker(tmp_path):
    root = str(tmp_path / "rootfs")
    os.makedirs(os.path.join(root, "etc"))
    with open(os.path.join(root, "etc", "passwd"), "w") as f:
        f.write("root:x:0:0:root:/root:/bin/bash\n")
    with open(os.path.join(root, "etc", "group"), "w") as f:
        f.write("root:x:0:\n")
    with open(os.path.join(root, "etc", "os-release"), "w") as f:
        f.write('PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\n')
    host_native._finalize_rootfs(root, "debian", "12")
    passwd = open(os.path.join(root, "etc", "passwd")).read()
    assert "agentflow:x:1000:1000:" in passwd
    marker = json.loads(open(os.path.join(root, host_native.MARKER_NAME)).read())
    assert marker["method"] == "host-native"
    assert marker["distro"] == "debian" and marker["version"] == "12"
    assert host_native.rootfs_ready(root)
    assert host_native.os_pretty_name(root).startswith("Debian")


def test_sync_host_network_files_copies_hosts_and_resolv(tmp_path):
    # The sandbox shares the host network: static host mappings (e.g.
    # an egress proxy gateway) must reach the rootfs, or names resolve
    # to different, unreachable addresses inside the sandbox.
    root = str(tmp_path / "rootfs")
    os.makedirs(os.path.join(root, "etc"))
    host_native._sync_host_network_files(root)
    for rel in ("etc/hosts", "etc/resolv.conf"):
        copied = os.path.join(root, rel)
        assert os.path.isfile(copied)
        with open(os.path.join("/", rel), encoding="utf-8") as f:
            assert open(copied, encoding="utf-8").read() == f.read()
        assert os.stat(copied).st_mode & 0o444  # readable inside sandbox
    # Idempotent: a second sync leaves identical content.
    host_native._sync_host_network_files(root)
    with open(os.path.join("/", "etc", "hosts"), encoding="utf-8") as f:
        assert open(os.path.join(root, "etc", "hosts"),
                    encoding="utf-8").read() == f.read()


def test_chroot_argv_and_wrap_command():
    argv = host_native.chroot_argv("/sb/debian-12", "echo hi")
    assert argv[:3] == ["chroot", "--userspec=1000:1000", "/sb/debian-12"]
    wrapped = host_native.wrap_command("/sb/debian-12", "apt-get update")
    assert wrapped.startswith("chroot --userspec=0:0 /sb/debian-12")
    assert "apt-get update" in wrapped


# ------------------------------------------------------ install methods ----

def test_install_without_docker_goes_host_native(client, monkeypatch):
    monkeypatch.setattr(sandbox, "docker_state", lambda: (True, False))
    monkeypatch.setattr(host_native, "install_rootfs", _fake_install_ok)
    r = client.post("/api/workspace/sandbox/install",
                    json={"distro": "debian", "version": "12"})
    assert r.status_code == 200
    body = r.json()
    assert body["method"] == "host-native"
    assert body["status"] == "installed"
    data = client.get("/api/workspace/sandbox").json()
    v12 = [v for d in data["distros"] if d["id"] == "debian"
           for v in d["versions"] if v["id"] == "12"][0]
    assert v12["install_status"] == "installed"
    assert v12["install_method"] == "host-native"
    # selectable now, and selecting it reports the host-sandbox mode
    r = client.put("/api/workspace/sandbox", json={"distro": "debian", "version": "12"})
    assert r.status_code == 200
    info = r.json()
    assert info["isolation_mode"] == "host_sandbox"
    assert info["selected_method"] == "host-native"
    assert "独立系统目录" in info["note"]


def test_host_native_install_failure_is_honest(client, monkeypatch):
    monkeypatch.setattr(sandbox, "docker_state", lambda: (True, False))

    def failing(distro_id, version_id, meta, log):
        raise host_native.InstallError("下载系统文件失败，网络不可用。")

    monkeypatch.setattr(host_native, "install_rootfs", failing)
    r = client.post("/api/workspace/sandbox/install",
                    json={"distro": "ubuntu", "version": "24.04"})
    body = r.json()
    assert body["method"] == "host-native"
    assert body["status"] == "failed"
    assert "网络不可用" in body["log"]
    data = client.get("/api/workspace/sandbox").json()
    v = [v for d in data["distros"] if d["id"] == "ubuntu"
         for v in d["versions"] if v["id"] == "24.04"][0]
    assert v["install_status"] == "failed"
    r = client.put("/api/workspace/sandbox",
                   json={"distro": "ubuntu", "version": "24.04"})
    assert r.status_code == 400


# ------------------------------------------------------------ execution ----

def _seed_active_sandbox(client, monkeypatch):
    _fake_install_ok("debian", "12", {}, lambda line: None)
    db = client.app.state.Session()
    try:
        db.add(models.SandboxInstall(distro="debian", version="12",
                                     status="installed", method="host-native",
                                     log="seeded"))
        db.add(models.SandboxConfig(id=1, selected_distro="debian",
                                    selected_version="12"))
        db.commit()
        return _seed_agent(db)
    finally:
        db.close()


def test_exec_runs_inside_current_sandbox(client, monkeypatch):
    agent = _seed_active_sandbox(client, monkeypatch)
    monkeypatch.setattr(host_native, "ensure_workspace_mount",
                        lambda root: (True, ""))
    captured = {}

    def fake_capped(argv, cwd=None, env=None, timeout=60, preexec_fn=None, **kw):
        captured["argv"] = argv
        return ("Debian GNU/Linux 12\nok\n__AGENTFLOW_PWD__:/workspace/sub\n",
                "", 0, False)

    monkeypatch.setattr(service, "run_capped", fake_capped)
    db = client.app.state.Session()
    try:
        session = service.create_session(db, agent.id)
        result = service.exec_in_session(db, session, "cat /etc/os-release; cd sub")
        assert captured["argv"][0] == "chroot"
        assert "--userspec=1000:1000" in captured["argv"]
        assert result["exit_code"] == 0
        assert result["cwd"] == "sub"
        assert result["sandbox"] == "Debian 12"
        row = db.query(models.WorkspaceCommandLog).order_by(
            models.WorkspaceCommandLog.id.desc()).first()
        assert row.sandbox == "Debian 12"
        assert row.status == "completed"
    finally:
        db.close()


def test_exec_without_sandbox_stays_on_host(client, monkeypatch):
    db = client.app.state.Session()
    try:
        agent = _seed_agent(db)
        captured = {}

        def fake_capped(argv, cwd=None, env=None, timeout=60, preexec_fn=None, **kw):
            captured["argv"] = argv
            return (f"ok\n__AGENTFLOW_PWD__:{cwd}\n", "", 0, False)

        monkeypatch.setattr(service, "run_capped", fake_capped)
        session = service.create_session(db, agent.id)
        result = service.exec_in_session(db, session, "echo ok")
        assert captured["argv"][0] == "/bin/bash"
        assert result["sandbox"] is None
        row = db.query(models.WorkspaceCommandLog).order_by(
            models.WorkspaceCommandLog.id.desc()).first()
        assert row.sandbox is None
    finally:
        db.close()


def test_exec_reports_failure_when_sandbox_mount_fails(client, monkeypatch):
    agent = _seed_active_sandbox(client, monkeypatch)
    monkeypatch.setattr(host_native, "ensure_workspace_mount",
                        lambda root: (False, "无法把共享目录挂入沙箱：权限不足"))
    db = client.app.state.Session()
    try:
        session = service.create_session(db, agent.id)
        result = service.exec_in_session(db, session, "echo hi")
        assert result["status"] == "failed"
        assert "挂入沙箱" in result["stderr"]
        assert result["sandbox"] == "Debian 12"
    finally:
        db.close()


# ------------------------------------------------------------ dependencies ----

def test_dependencies_install_into_current_sandbox(client, monkeypatch):
    _seed_active_sandbox(client, monkeypatch)
    state = {"installed": set()}
    seen = []

    def fake_capped(argv, cwd=None, env=None, timeout=60, preexec_fn=None, **kw):
        command = argv[2] if len(argv) > 2 else ""
        seen.append(command)
        assert "chroot" in command  # every command must enter the sandbox
        if "dpkg-query" in command:
            token = command.split()[-1].strip("'")
            if token in state["installed"]:
                return ("install ok installed", "", 0, False)
            return ("", "", 1, False)
        if "apt-get update" in command:
            return ("ok", "", 0, False)
        if "apt-get install" in command:
            for token in ("jq",):
                if token in command:
                    state["installed"].add(token)
            return ("done", "", 0, False)
        return ("", "", 0, False)

    monkeypatch.setattr(service, "run_capped", fake_capped)
    db = client.app.state.Session()
    try:
        payload = dependencies.status_payload(db)
        assert payload["sandbox"]["label"] == "Debian 12"
        assert payload["family"] == "apt"
        job = models.DependencyInstallJob(
            status="running", package_manager="apt", initiated_by="user",
            results=[{"key": "jq", "label": "jq 数据处理", "packages": ["jq"],
                      "status": "pending", "detail": ""}])
        db.add(job)
        db.commit()
        db.refresh(job)
        dependencies.run_job(db, job)
        assert job.status == "completed"
        assert job.results[0]["status"] == "installed"
        audits = db.query(models.WorkspaceCommandLog).filter(
            models.WorkspaceCommandLog.command.contains("apt-get install")).all()
        assert audits and all(a.sandbox == "Debian 12" for a in audits)
    finally:
        db.close()


def test_status_payload_ignores_results_from_another_target(client, monkeypatch):
    """A finished job that ran against this machine must not make the
    current sandbox look stocked: statuses are re-probed live."""
    _seed_active_sandbox(client, monkeypatch)

    def fake_capped(argv, cwd=None, env=None, timeout=60, preexec_fn=None, **kw):
        command = argv[2] if len(argv) > 2 else ""
        assert "chroot" in command
        return ("", "", 1, False)

    monkeypatch.setattr(service, "run_capped", fake_capped)
    db = client.app.state.Session()
    try:
        stale = models.DependencyInstallJob(
            status="completed", package_manager="apt", initiated_by="user",
            sandbox=None,
            results=[{"key": "jq", "label": "jq 数据处理", "packages": ["jq"],
                      "status": "exists", "detail": "已经装好，无需重复安装"}])
        db.add(stale)
        db.commit()
        payload = dependencies.status_payload(db)
        jq = next(p for p in payload["packages"] if p["key"] == "jq")
        assert jq["status"] == "missing"
    finally:
        db.close()


def test_ensure_dev_nodes_never_raises_and_leaves_null(tmp_path):
    root = tmp_path / "rootfs"
    (root / "dev").mkdir(parents=True)
    (root / "dev" / "null").write_text("")
    host_native._ensure_dev_nodes(str(root))
    assert (root / "dev" / "null").exists()


def test_ensure_sandbox_apt_config_written_once(tmp_path):
    root = tmp_path / "rootfs"
    (root / "etc" / "apt" / "apt.conf.d").mkdir(parents=True)
    host_native._ensure_sandbox_apt_config(str(root))
    conf = root / "etc" / "apt" / "apt.conf.d" / "99agentflow-sandbox"
    assert 'Acquire::Sandbox::User "root"' in conf.read_text()
    host_native._ensure_sandbox_apt_config(str(root))  # idempotent
    bare = tmp_path / "bare"
    bare.mkdir()
    host_native._ensure_sandbox_apt_config(str(bare))  # no apt tree: no-op
    assert not (bare / "etc").exists()


def test_reconcile_interrupted_fails_stale_running_records(client):
    db = client.app.state.Session()
    try:
        row = models.SandboxInstall(
            distro="rocky", version="9", status="installing",
            method="host-native", log="开始安装")
        job = models.DependencyInstallJob(
            status="running", package_manager="apt", initiated_by="user",
            results=[], log="")
        db.add(row)
        db.add(job)
        db.commit()
        sandbox.reconcile_interrupted(db)
        db.refresh(row)
        db.refresh(job)
        assert row.status == "failed"
        assert "中断" in row.log
        assert job.status == "failed"
        assert job.finished_at is not None
    finally:
        db.close()
