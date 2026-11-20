"""Sandbox distribution + version choice with the install-first flow:
nothing is pre-installed, only installed pairs can become the current
sandbox, and installs without Docker fail honestly. Plus the one-click
common-dependencies feature (package mapping for both families, status
payload shape, and a job run through a simulated command channel).

Against a temp SQLite via a minimal app mounting only the real workspace
router — no mocked database. Docker detection is substituted where a
deterministic branch is needed, because this dev machine's daemon is
unreachable.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import Base, get_db
from app.workspace import dependencies, sandbox
from app.workspace.routes import router

EXPECTED = {
    "ubuntu": ["24.04", "22.04"],
    "debian": ["13", "12"],
    "centos": ["stream-10", "stream-9"],
    "rocky": ["10", "9"],
}


@pytest.fixture()
def client(tmp_path):
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
    return TestClient(test_app)


def _seed_installed(client, distro, version):
    db = client.app.state.Session()
    try:
        db.add(models.SandboxInstall(distro=distro, version=version,
                                     status="installed", log="seeded"))
        db.commit()
    finally:
        db.close()


def test_get_sandbox_catalogue_defaults_to_nothing_installed_or_selected(client):
    r = client.get("/api/workspace/sandbox")
    assert r.status_code == 200
    data = r.json()
    assert [d["id"] for d in data["distros"]] == list(EXPECTED.keys())
    for d in data["distros"]:
        assert [v["id"] for v in d["versions"]] == EXPECTED[d["id"]]
        assert d["name"] and d["description"]
        for v in d["versions"]:
            assert v["label"] and v["maintenance_until"]
            assert v["base_image"] and v["image"]
            assert v["install_status"] == "not_installed"
            assert v["selected"] is False
    assert data["selected_distro"] is None
    assert data["selected_version"] is None


def test_isolation_mode_field_present_and_consistent(client):
    data = client.get("/api/workspace/sandbox").json()
    assert data["isolation_mode"] in ("container", "host")
    assert set(data["docker"].keys()) == {"cli_available", "daemon_reachable"}
    assert data["note"]
    if not data["docker"]["daemon_reachable"]:
        assert data["isolation_mode"] == "host"


def test_put_uninstalled_combo_400_telling_user_to_install_first(client):
    r = client.put("/api/workspace/sandbox", json={"distro": "rocky", "version": "9"})
    assert r.status_code == 400
    assert "先安装" in r.json()["detail"]
    data = client.get("/api/workspace/sandbox").json()
    assert data["selected_distro"] is None


def test_put_installed_combo_becomes_current_and_persists(client):
    _seed_installed(client, "rocky", "9")
    r = client.put("/api/workspace/sandbox", json={"distro": "rocky", "version": "9"})
    assert r.status_code == 200
    body = r.json()
    assert body["selected_distro"] == "rocky" and body["selected_version"] == "9"
    assert body["message"]
    again = client.get("/api/workspace/sandbox").json()
    assert (again["selected_distro"], again["selected_version"]) == ("rocky", "9")
    selected = [(d["id"], v["id"]) for d in again["distros"]
                for v in d["versions"] if v["selected"]]
    assert selected == [("rocky", "9")]


def test_put_invalid_combinations_400(client):
    # version that does not exist at all
    r = client.put("/api/workspace/sandbox", json={"distro": "ubuntu", "version": "20.04"})
    assert r.status_code == 400
    # cross-distro mismatch: 24.04 is Ubuntu's, not Debian's
    r = client.put("/api/workspace/sandbox", json={"distro": "debian", "version": "24.04"})
    assert r.status_code == 400
    # distro removed from the supported catalogue
    r = client.put("/api/workspace/sandbox", json={"distro": "arch", "version": "rolling"})
    assert r.status_code == 400


def test_install_without_docker_goes_host_native_and_fails_honestly(client, monkeypatch):
    """No Docker daemon no longer means instant failure: the install goes
    host-native. With the installer simulated to fail, the record must
    show the honest failure and stay unselectable. The host-native path
    itself is covered in depth by test_sandbox_host_native.py."""
    import types as _types

    from app.workspace import host_native

    class _SyncThread:
        def __init__(self, target=None, args=(), daemon=None):
            self._target, self._args = target, args

        def start(self):
            self._target(*self._args)

    monkeypatch.setattr(sandbox, "docker_state", lambda: (True, False))
    monkeypatch.setattr("app.database.SessionLocal", client.app.state.Session)
    monkeypatch.setattr(sandbox, "threading",
                        _types.SimpleNamespace(Thread=_SyncThread))

    def failing(distro_id, version_id, meta, log):
        raise host_native.InstallError("模拟的直装失败原因")

    monkeypatch.setattr(host_native, "install_rootfs", failing)
    r = client.post("/api/workspace/sandbox/install",
                    json={"distro": "debian", "version": "13"})
    assert r.status_code == 200
    body = r.json()
    assert body["method"] == "host-native"
    assert body["status"] == "failed"
    assert "模拟的直装失败原因" in body["log"]
    data = client.get("/api/workspace/sandbox").json()
    debian13 = [v for d in data["distros"] if d["id"] == "debian"
                for v in d["versions"] if v["id"] == "13"][0]
    assert debian13["install_status"] == "failed"
    # a failed install must not become selectable either
    r = client.put("/api/workspace/sandbox", json={"distro": "debian", "version": "13"})
    assert r.status_code == 400


def test_container_mode_reported_when_daemon_reachable(client, monkeypatch):
    monkeypatch.setattr(sandbox, "docker_state", lambda: (True, True))
    data = client.get("/api/workspace/sandbox").json()
    assert data["isolation_mode"] == "container"
    assert data["docker"] == {"cli_available": True, "daemon_reachable": True}


# ------------------------------------------------------- dependencies ----

def test_dependency_package_mapping_both_families():
    assert dependencies.package_tokens("apt", "build-tools") == ["build-essential"]
    assert dependencies.package_tokens("dnf", "build-tools") == ["gcc", "make"]
    assert dependencies.package_tokens("apt", "python") == ["python3", "python3-pip"]
    assert dependencies.package_tokens("dnf", "python") == ["python3", "python3-pip"]
    assert dependencies.package_tokens("apt", "node") == ["nodejs", "npm"]
    assert dependencies.package_tokens("dnf", "git") == ["git"]


def test_dependencies_status_structure(client):
    r = client.get("/api/workspace/sandbox/dependencies")
    assert r.status_code == 200
    data = r.json()
    assert data["family"] in ("apt", "dnf", None)
    assert data["job"] is None
    keys = [p["key"] for p in data["packages"]]
    assert keys == [c["key"] for c in dependencies.CATALOG]
    for p in data["packages"]:
        assert p["label"] and isinstance(p["packages"], list)
        assert p["status"] in ("exists", "missing", "unknown",
                               "pending", "installing", "installed", "failed")


def test_dependencies_job_with_simulated_command_channel(client, monkeypatch):
    """git is already present, jq gets installed by the (simulated) package
    manager; outcomes must be judged from the simulated real state, the
    install command must land in the shared audit log."""
    state = {"installed": {"git"}}

    def fake_run(command, timeout=600):
        if command.startswith("dpkg-query"):
            token = command.split()[-1]
            if token in state["installed"]:
                return 0, "install ok installed", ""
            return 1, "", "no packages found"
        if "apt-get update" in command:
            return 0, "ok", ""
        if "apt-get install" in command:
            for token in command.split():
                state["installed"].add(token)
            return 0, "done", ""
        return 0, "", ""

    monkeypatch.setattr(dependencies, "run_command", fake_run)
    monkeypatch.setattr(dependencies, "detect_family", lambda: "apt")

    db = client.app.state.Session()
    try:
        job = models.DependencyInstallJob(
            status="running", package_manager="apt", initiated_by="user",
            results=[{"key": k, "label": k, "packages": [], "status": "pending",
                      "detail": ""} for k in ("git", "jq")])
        db.add(job)
        db.commit()
        db.refresh(job)
        dependencies.run_job(db, job)
        assert job.status == "completed"
        by_key = {r["key"]: r for r in job.results}
        assert by_key["git"]["status"] == "exists"
        assert by_key["jq"]["status"] == "installed"
        audits = db.query(models.WorkspaceCommandLog).filter(
            models.WorkspaceCommandLog.command.contains("apt-get install")).all()
        assert len(audits) == 1 and audits[0].agent_id is None
        assert audits[0].exit_code == 0
    finally:
        db.close()
