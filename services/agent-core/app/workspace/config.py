"""Runtime configuration for the shared workspace. Read dynamically so tests
and deployments can override via environment variables at any time."""
import os

ENV_ID = "shared-linux-1"
LOGICAL_ROOT = "/workspace"

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def get_shared_root() -> str:
    """Physical directory backing the logical /workspace. One root for all agents."""
    root = os.environ.get("AGENTFLOW_SHARED_ROOT",
                          os.path.join(_BASE_DIR, "data", "shared_workspace"))
    os.makedirs(root, exist_ok=True)
    return os.path.abspath(root)


def get_sandboxes_dir() -> str:
    """Physical directory holding host-native sandbox root filesystems,
    one subdirectory per installed distribution+version."""
    d = os.environ.get("AGENTFLOW_SANDBOXES_DIR",
                       os.path.join(_BASE_DIR, "data", "sandboxes"))
    os.makedirs(d, exist_ok=True)
    return os.path.abspath(d)


def get_runtime_dir() -> str:
    d = os.path.join(_BASE_DIR, "data", "desktop_runtime")
    os.makedirs(d, exist_ok=True)
    return d


def get_limits() -> dict:
    """Resource limits. In Docker these are ALSO enforced by compose
    (cpus/mem_limit/pids_limit); in local mode CPU time and address space are
    enforced per command via rlimit, process count is reported, not faked."""
    return {
        "cpu_cores": float(os.environ.get("AGENTFLOW_WS_CPUS", "2")),
        "memory_mb": int(os.environ.get("AGENTFLOW_WS_MEMORY_MB", "2048")),
        "command_cpu_seconds": int(os.environ.get("AGENTFLOW_WS_CMD_CPU_SECONDS", "120")),
        "command_memory_mb": int(os.environ.get("AGENTFLOW_WS_CMD_MEMORY_MB", "2048")),
        "disk_quota_mb": int(os.environ.get("AGENTFLOW_WS_DISK_MB", "4096")),
        "max_processes": int(os.environ.get("AGENTFLOW_WS_PIDS", "256")),
        "default_timeout_seconds": int(os.environ.get("AGENTFLOW_WS_TIMEOUT", "60")),
        "desktop_lease_ttl_seconds": int(os.environ.get("AGENTFLOW_WS_LEASE_TTL", "120")),
    }


def runtime_kind() -> str:
    """Honest deployment descriptor: 'docker' only when actually containerised
    (or explicitly declared), otherwise 'local-process' on the host Linux."""
    if os.environ.get("AGENTFLOW_WORKSPACE_RUNTIME"):
        return os.environ["AGENTFLOW_WORKSPACE_RUNTIME"]
    if os.path.exists("/.dockerenv"):
        return "docker"
    return "local-process"
