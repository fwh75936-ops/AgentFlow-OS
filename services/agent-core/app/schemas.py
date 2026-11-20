"""Pydantic schemas. api_key is write-only: accepted on create/update, never returned."""
from typing import Any, Optional
from pydantic import BaseModel, field_validator


def normalize_appearance(v):
    """Clamp an appearance config to the shared contract (docs/APPEARANCE.md):
    {"gender": "female"|"male", "skin": 1-5, "outfit": 1-10, "hair": 1-6}.
    Empty / non-dict input stays {} — "not configured", the UI falls back
    to the legacy avatar preset. Out-of-range numbers fall back per-field
    to the defaults instead of rejecting the whole save."""
    if not isinstance(v, dict) or not v:
        return {}

    def _clamp(x, lo, hi, default):
        try:
            n = int(x)
        except (TypeError, ValueError):
            return default
        return n if lo <= n <= hi else default

    return {
        "gender": v.get("gender") if v.get("gender") in ("female", "male") else "female",
        "skin": _clamp(v.get("skin"), 1, 5, 2),
        "outfit": _clamp(v.get("outfit"), 1, 10, 1),
        "hair": _clamp(v.get("hair"), 1, 6, 1),
    }

class ProviderIn(BaseModel):
    name: str
    base_url: str
    api_key: Optional[str] = None
    format: str = "chat_completions"
    enabled: bool = True

class ProviderOut(BaseModel):
    id: int
    name: str
    base_url: str
    format: str = "chat_completions"
    enabled: bool
    has_api_key: bool = False
    class Config:
        from_attributes = True

class AgentIn(BaseModel):
    name: str
    description: str = ""
    responsibilities: dict = {}
    avatar: str = ""
    appearance: dict = {}

    @field_validator("appearance", mode="before")
    @classmethod
    def _normalize_appearance(cls, v):
        return normalize_appearance(v)

    # The JSON columns are nullable in storage: rows written before the
    # columns existed, restored backups, or rows built by hand can hold
    # NULL (SQLAlchemy's default=list only fires when the value is
    # omitted, not when None is stored). The API contract is that these
    # fields are ALWAYS a list/dict — a stored None must serialize as
    # the empty value, never as null (the desktop UI reads .length /
    # .map on them directly and a null white-screens the page).
    @field_validator("skills", "tools", "capability_tags", mode="before")
    @classmethod
    def _none_list_to_empty(cls, v):
        return [] if v is None else v

    @field_validator("memory_config", "permissions", "capability_scores",
                     mode="before")
    @classmethod
    def _none_dict_to_empty(cls, v):
        return {} if v is None else v

    @field_validator("responsibilities", mode="before")
    @classmethod
    def _normalize_responsibilities(cls, v):
        # Same NULL hazard, plus a shape hazard: chief.ensure_team
        # creates agents without duties, so stored rows hold {} with
        # none of the three lists. Fill any missing/non-list list with
        # [] so consumers can always read all three keys.
        if v is None:
            v = {}
        if not isinstance(v, dict):
            return v
        out = dict(v)
        for key in ("responsible_for", "not_responsible_for", "deliverables"):
            if not isinstance(out.get(key), list):
                out[key] = []
        return out
    role: str = "general"
    system_prompt: str = ""
    model_provider_id: Optional[int] = None
    model_id: Optional[str] = None
    skills: list = []
    tools: list = []
    memory_config: dict = {}
    execution_environment: str = "sandbox"
    permissions: dict = {}
    capability_tags: list = []
    capability_scores: dict = {}

class AgentOut(AgentIn):
    id: int
    status: str
    runtime_status: str
    # Derived at read time in the API layer from real execution records
    # (see _agent_activity in api/routes.py): idle | model_calling |
    # tool_calling | running | failed | disabled. Never stored, never
    # fabricated — each value names the record it was derived from.
    activity_status: str = "idle"
    version: int
    # None until the agent has at least one real execution record: the
    # stored 0.5 is a neutral PRIOR for scheduler scoring, not a measured
    # rate, and must not be presented as one (see _agent_out).
    historical_success_rate: Optional[float] = None
    average_latency: float
    average_cost: float
    current_load: float
    availability: bool
    execution_count: int
    success_count: int
    class Config:
        from_attributes = True

class AgentToolsIn(BaseModel):
    """Full replacement list of tool ids granted to one agent."""
    tools: list = []

class TeamIn(BaseModel):
    name: str
    description: str = ""
    leader_agent_id: Optional[int] = None
    member_agent_ids: list = []

class TaskIn(BaseModel):
    title: str
    description: str = ""
    team_id: Optional[int] = None
    priority: int = 3
    scheduler_mode: str = "adaptive"

    @field_validator("priority")
    @classmethod
    def _priority_range(cls, v):
        # The model contract is 1..5 (the scheduler normalizes on that
        # range); out-of-range values used to be stored verbatim and
        # only surfaced as nonsense scores at execution time.
        if not isinstance(v, int) or not 1 <= v <= 5:
            raise ValueError("priority 必须在 1 到 5 之间")
        return v

    @field_validator("scheduler_mode")
    @classmethod
    def _mode_known(cls, v):
        if v not in ("fixed", "round_robin", "adaptive", "sacas"):
            raise ValueError(f"不支持的调度模式：{v}")
        return v

class NodeAssignIn(BaseModel):
    # None clears the pin and hands the choice back to the scheduler.
    agent_id: Optional[int] = None

class SkillIn(BaseModel):
    name: str
    description: str = ""
    instructions: str = ""
    required_tools: list = []
    permissions: dict = {}
    version: str = "1.0.0"
    enabled: bool = True
    trusted: bool = False
    # Import-managed metadata; accepted on create, ignored on update so a
    # manual edit can never rewrite a skill's provenance or file list.
    source: str = "manual"
    files: list = []

class MCPIn(BaseModel):
    name: str
    transport: str = "stdio"
    command_or_url: str = ""
    args: list = []
    env: dict = {}
    enabled: bool = True
    tools: list = []

class WorkflowIn(BaseModel):
    name: str
    graph: dict = {}

class MemoryIn(BaseModel):
    agent_id: Optional[int] = None
    scope: str = "long_term"
    title: str = ""
    source: str = ""
    content: str

# ---- Shared Linux Workspace ----
class WorkspaceSessionIn(BaseModel):
    agent_id: int
    task_id: Optional[int] = None

class WorkspaceExecIn(BaseModel):
    command: str
    task_id: Optional[int] = None
    approved: bool = False
    timeout: Optional[int] = None

class WorkspaceFileWriteIn(BaseModel):
    agent_id: int
    path: str
    content: str = ""
    expected_version: Optional[int] = None
    task_id: Optional[int] = None

class WorkspaceFileActionIn(BaseModel):
    agent_id: int
    path: str
    task_id: Optional[int] = None

class WorkspaceLockIn(BaseModel):
    agent_id: int
    path: str
    ttl_seconds: int = 300

class DesktopLeaseIn(BaseModel):
    agent_id: int
    ttl_seconds: Optional[int] = None

class DesktopReleaseIn(BaseModel):
    agent_id: Optional[int] = None
    holder_type: str = "agent"  # agent | user

class SandboxSelectIn(BaseModel):
    distro: str
    version: Optional[str] = None

class DepsInstallIn(BaseModel):
    packages: Optional[list] = None

class EvaluationIn(BaseModel):
    name: str
    mode: str = "adaptive"
    config: dict = {}
