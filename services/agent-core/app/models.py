"""All persistent models. No seed/fake data is inserted anywhere."""
import uuid
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Boolean, Float, DateTime, ForeignKey, JSON, UniqueConstraint

from app.database import Base

def _now():
    return datetime.utcnow()

def _uuid():
    return str(uuid.uuid4())

class ModelProvider(Base):
    __tablename__ = "model_providers"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    base_url = Column(String(500), nullable=False)
    api_key = Column(Text, nullable=True)  # stored server-side only, never returned by API schemas
    # Upstream API protocol, by endpoint shape: "chat_completions"
    # (/v1/chat/completions, the OpenAI-compatible de-facto standard),
    # "responses" (OpenAI Responses, /v1/responses), "anthropic"
    # (Claude Messages, /v1/messages). Existing rows predate the column;
    # the DB default fills them as "chat_completions", which matches how
    # they were always called.
    format = Column(String(30), default="chat_completions")
    models = Column(JSON, default=list)  # user-curated model ids; only these appear in pickers
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

class Agent(Base):
    __tablename__ = "agents"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    description = Column(Text, default="")
    # Structured job duties (MetaGPT-style role clarity): three lists of
    # short Chinese phrases — {"responsible_for": [...],
    # "not_responsible_for": [...], "deliverables": [...]}. The free-text
    # description stays as the one-line summary; this is the detailed
    # breakdown shown on the profile and used for plan duty matching.
    responsibilities = Column(JSON, default=dict)
    avatar = Column(String(200), default="")
    # Configurable look, shared with the pixel AI studio — one config,
    # two renderers. {"gender": "female"|"male", "skin": 1-5,
    # "outfit": 1-10, "hair": 1-6}; see docs/APPEARANCE.md for the
    # contract. Empty dict = not configured: the UI then falls back to
    # the legacy avatar preset id above.
    appearance = Column(JSON, default=dict)
    role = Column(String(120), default="general")
    system_prompt = Column(Text, default="")
    model_provider_id = Column(Integer, ForeignKey("model_providers.id"), nullable=True)
    model_id = Column(String(200), nullable=True)
    skills = Column(JSON, default=list)
    tools = Column(JSON, default=list)
    memory_config = Column(JSON, default=dict)
    execution_environment = Column(String(50), default="sandbox")
    permissions = Column(JSON, default=dict)
    status = Column(String(30), default="active")  # active | disabled
    runtime_status = Column(String(30), default="Idle")
    version = Column(Integer, default=1)
    # Capability profile (updated from real execution feedback via EMA)
    capability_tags = Column(JSON, default=list)
    capability_scores = Column(JSON, default=dict)
    historical_success_rate = Column(Float, default=0.5)  # neutral prior, replaced by feedback
    average_latency = Column(Float, default=0.0)
    average_cost = Column(Float, default=0.0)
    current_load = Column(Float, default=0.0)
    availability = Column(Boolean, default=True)
    execution_count = Column(Integer, default=0)
    success_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

class Team(Base):
    __tablename__ = "teams"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    description = Column(Text, default="")
    leader_agent_id = Column(Integer, nullable=True)
    default_workflow_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

class TeamMember(Base):
    __tablename__ = "team_members"
    id = Column(Integer, primary_key=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False)
    role_in_team = Column(String(120), default="member")
    is_leader = Column(Boolean, default=False)

class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True)
    title = Column(String(300), nullable=False)
    description = Column(Text, default="")
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    priority = Column(Integer, default=3)  # 1..5
    scheduler_mode = Column(String(30), default="adaptive")  # fixed | round_robin | adaptive
    status = Column(String(30), default="pending")  # pending|running|completed|failed|cancelled
    result = Column(Text, nullable=True)
    error_type = Column(String(50), nullable=True)
    error_message = Column(Text, nullable=True)
    token_usage = Column(Integer, default=0)
    retry_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

class TaskNode(Base):
    __tablename__ = "task_nodes"
    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False)
    name = Column(String(200), nullable=False)
    node_type = Column(String(50), default="agent")  # plan|agent|verify
    required_capabilities = Column(JSON, default=list)
    required_tools = Column(JSON, default=list)
    dependencies = Column(JSON, default=list)  # list of TaskNode ids
    assigned_agent_id = Column(Integer, nullable=True)
    status = Column(String(30), default="pending")
    input_data = Column(Text, nullable=True)
    output_data = Column(Text, nullable=True)
    error_type = Column(String(50), nullable=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)

class AgentMessage(Base):
    __tablename__ = "agent_messages"
    message_id = Column(String(64), primary_key=True, default=_uuid)
    team_id = Column(Integer, nullable=True)
    task_id = Column(Integer, nullable=True)
    sender_agent_id = Column(Integer, nullable=True)
    receiver_agent_id = Column(Integer, nullable=True)
    message_type = Column(String(40), default="STATUS_UPDATE")
    content = Column(Text, default="")
    meta = Column(JSON, default=dict)
    timestamp = Column(DateTime, default=_now)

class SchedulerDecision(Base):
    __tablename__ = "scheduler_decisions"
    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, nullable=True)
    node_id = Column(Integer, nullable=True)
    mode = Column(String(30), default="adaptive")
    candidates = Column(JSON, default=list)  # [{agent_id, total, components{...}, eligible, reason}]
    selected_agent_id = Column(Integer, nullable=True)
    reason = Column(Text, default="")
    created_at = Column(DateTime, default=_now)

class VerificationRecord(Base):
    __tablename__ = "verification_records"
    verification_id = Column(String(64), primary_key=True, default=_uuid)
    task_id = Column(Integer, nullable=True)
    node_id = Column(Integer, nullable=True)
    verification_type = Column(String(50), default="structural")
    passed = Column(Boolean, default=False)
    score = Column(Float, default=0.0)
    evidence = Column(Text, default="")
    errors = Column(JSON, default=list)
    timestamp = Column(DateTime, default=_now)

class ExecutionSnapshot(Base):
    __tablename__ = "execution_snapshots"
    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, nullable=False)
    state = Column(JSON, default=dict)
    created_at = Column(DateTime, default=_now)

class Skill(Base):
    __tablename__ = "skills"
    id = Column(Integer, primary_key=True)
    name = Column(String(160), nullable=False)
    description = Column(Text, default="")
    instructions = Column(Text, default="")
    required_tools = Column(JSON, default=list)
    permissions = Column(JSON, default=dict)
    version = Column(String(30), default="1.0.0")
    enabled = Column(Boolean, default=True)
    trusted = Column(Boolean, default=False)  # imported skills are untrusted by default
    source = Column(String(30), default="manual")  # manual | import
    files = Column(JSON, default=list)  # bundled files: [{path, size}] under data/skill_files/<id>/
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

class MCPServer(Base):
    __tablename__ = "mcp_servers"
    id = Column(Integer, primary_key=True)
    name = Column(String(160), nullable=False)
    transport = Column(String(30), default="stdio")  # stdio | streamable_http
    command_or_url = Column(String(500), default="")
    args = Column(JSON, default=list)  # command arguments (stdio); the
    # legacy convention packs the whole command line into command_or_url
    # and leaves args empty — connect() supports both.
    env = Column(JSON, default=dict)  # extra environment variables for the server process
    enabled = Column(Boolean, default=True)
    tools = Column(JSON, default=list)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

class MemoryItem(Base):
    __tablename__ = "memory_items"
    id = Column(Integer, primary_key=True)
    agent_id = Column(Integer, nullable=True)
    scope = Column(String(30), default="long_term")  # short_term|long_term|task|team
    title = Column(String(300), default="")   # document title (knowledge imports)
    source = Column(String(60), default="")   # upload | paste | workspace | "" (legacy notes)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=_now)

class Workflow(Base):
    __tablename__ = "workflows"
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    graph = Column(JSON, default=dict)  # {nodes:[], edges:[]}
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

class WorkflowRun(Base):
    """One real execution of a saved Workflow graph: the graph was compiled
    to a Task + TaskNode DAG and run through the standard task runner.
    node_map maps graph node id -> task_node id; status mirrors the Task."""
    __tablename__ = "workflow_runs"
    id = Column(Integer, primary_key=True)
    workflow_id = Column(Integer, ForeignKey("workflows.id"), nullable=False)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=True)
    status = Column(String(30), default="pending")  # pending|running|completed|failed
    graph_snapshot = Column(JSON, default=dict)
    node_map = Column(JSON, default=dict)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_now)
    finished_at = Column(DateTime, nullable=True)

# ---- Shared Linux Workspace (additive only) ----
# Every agent shares ONE Linux environment (env_id is a constant, never
# per-agent). These tables only add state; no existing table is altered.

class WorkspaceSession(Base):
    """One agent's independent terminal session inside the shared environment."""
    __tablename__ = "workspace_sessions"
    id = Column(Integer, primary_key=True)
    env_id = Column(String(60), default="shared-linux-1", nullable=False)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False)
    task_id = Column(Integer, nullable=True)
    cwd = Column(String(500), default="")  # relative to the shared root; "" == /workspace
    status = Column(String(30), default="active")  # active | closed
    created_at = Column(DateTime, default=_now)
    last_active_at = Column(DateTime, default=_now)

class WorkspaceCommandLog(Base):
    """Audit record for every command: executor, task, time, exit code, output."""
    __tablename__ = "workspace_command_logs"
    id = Column(Integer, primary_key=True)
    env_id = Column(String(60), default="shared-linux-1", nullable=False)
    sandbox = Column(String(120), nullable=True, default=None)  # sandbox label when the command ran inside one, else None
    session_id = Column(Integer, ForeignKey("workspace_sessions.id"), nullable=True)
    agent_id = Column(Integer, nullable=True)
    task_id = Column(Integer, nullable=True)
    command = Column(Text, default="")
    cwd = Column(String(500), default="")
    exit_code = Column(Integer, nullable=True)  # None while blocked / timed out w/o code
    stdout = Column(Text, default="")
    stderr = Column(Text, default="")
    status = Column(String(30), default="completed")  # completed|failed|blocked|timeout
    started_at = Column(DateTime, default=_now)
    finished_at = Column(DateTime, nullable=True)
    duration_ms = Column(Integer, default=0)

class DesktopLease(Base):
    """Single-holder lease for the shared graphical desktop (mouse/keyboard)."""
    __tablename__ = "desktop_leases"
    id = Column(Integer, primary_key=True)
    env_id = Column(String(60), default="shared-linux-1", nullable=False)
    holder_type = Column(String(20), default="agent")  # agent | user
    holder_agent_id = Column(Integer, nullable=True)
    status = Column(String(30), default="held")  # held|released|expired|force_taken
    acquired_at = Column(DateTime, default=_now)
    expires_at = Column(DateTime, nullable=True)
    last_heartbeat_at = Column(DateTime, default=_now)
    released_at = Column(DateTime, nullable=True)

class DesktopLeaseQueue(Base):
    """Agents waiting for the desktop control lease, FIFO."""
    __tablename__ = "desktop_lease_queue"
    id = Column(Integer, primary_key=True)
    env_id = Column(String(60), default="shared-linux-1", nullable=False)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False)
    ttl_seconds = Column(Integer, default=120)
    status = Column(String(30), default="waiting")  # waiting|granted|cancelled
    requested_at = Column(DateTime, default=_now)

class WorkspaceEvent(Base):
    """Collaboration feed: file changes, sessions, commands, lease changes, conflicts."""
    __tablename__ = "workspace_events"
    id = Column(Integer, primary_key=True)
    env_id = Column(String(60), default="shared-linux-1", nullable=False)
    event_type = Column(String(50), default="info")
    agent_id = Column(Integer, nullable=True)
    task_id = Column(Integer, nullable=True)
    path = Column(String(600), nullable=True)
    detail = Column(Text, default="")
    created_at = Column(DateTime, default=_now)

class SharedFileMeta(Base):
    """Per-file version + hash inside the shared root, for optimistic conflict checks."""
    __tablename__ = "shared_file_meta"
    path = Column(String(600), primary_key=True)  # relative to shared root
    version = Column(Integer, default=1)
    content_hash = Column(String(64), default="")
    size = Column(Integer, default=0)
    last_modified_by_agent_id = Column(Integer, nullable=True)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

class SharedFileLock(Base):
    """Advisory write lock on one shared path (conflict coordination)."""
    __tablename__ = "shared_file_locks"
    id = Column(Integer, primary_key=True)
    path = Column(String(600), nullable=False)
    agent_id = Column(Integer, nullable=False)
    session_id = Column(Integer, nullable=True)
    status = Column(String(30), default="held")  # held|released|expired
    acquired_at = Column(DateTime, default=_now)
    expires_at = Column(DateTime, nullable=True)

class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    mode = Column(String(30), default="adaptive")
    config = Column(JSON, default=dict)
    metrics = Column(JSON, nullable=True)  # null until a real run produces metrics
    status = Column(String(30), default="created")
    created_at = Column(DateTime, default=_now)


# ---- Smart collaboration (additive only — new tables, no old table altered) ----

class ResourceLease(Base):
    """Generic resource lease (desktop/file/git/port/browser) reusing the
    desktop-lease expiry/grant pattern. Independent of desktop_leases and
    shared_file_locks; none of the three mechanisms writes to the others."""
    __tablename__ = "resource_leases"
    id = Column(Integer, primary_key=True)
    resource_type = Column(String(30), nullable=False)  # desktop|file|git|port|browser
    resource_key = Column(String(600), nullable=False)
    holder_agent_id = Column(Integer, nullable=True)
    task_id = Column(Integer, nullable=True)
    status = Column(String(30), default="held")  # held|released|expired
    acquired_at = Column(DateTime, default=_now)
    expires_at = Column(DateTime, nullable=True)
    released_at = Column(DateTime, nullable=True)

class ArtifactVerification(Base):
    """State-aware verification: snapshots SharedFileMeta version+sha256 at
    verification time. When the file later changes, the record becomes stale
    and must no longer be reported as verifying the current artifact."""
    __tablename__ = "artifact_verifications"
    id = Column(Integer, primary_key=True)
    artifact_path = Column(String(600), nullable=False)
    artifact_version = Column(Integer, nullable=True)
    source_hash = Column(String(64), default="")
    test_run_id = Column(String(120), nullable=True)
    task_id = Column(Integer, nullable=True)
    node_id = Column(Integer, nullable=True)
    passed = Column(Boolean, default=False)
    status = Column(String(30), default="current")  # current|stale
    evidence = Column(Text, default="")
    created_at = Column(DateTime, default=_now)

class ReviewVerification(Base):
    """Reviewer cross-verification (verification layer 4): an independent
    Agent with the 'review' capability re-checks an artifact produced by
    another Agent. Snapshots SharedFileMeta version+sha256 exactly like
    ArtifactVerification, so a later write flips the review to stale too.
    A linked VerificationRecord (type='reviewer') is written for every
    review; its evidence JSON names reviewer_agent_id + artifact version."""
    __tablename__ = "review_verifications"
    id = Column(Integer, primary_key=True)
    artifact_path = Column(String(600), nullable=False)
    artifact_version = Column(Integer, nullable=True)
    source_hash = Column(String(64), default="")
    reviewer_agent_id = Column(Integer, nullable=False)
    author_agent_id = Column(Integer, nullable=True)
    task_id = Column(Integer, nullable=True)
    passed = Column(Boolean, default=False)
    status = Column(String(30), default="current")  # current|stale
    checks = Column(JSON, default=list)
    comment = Column(Text, default="")
    verification_record_id = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=_now)

class ProactiveRule(Base):
    __tablename__ = "proactive_rules"
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    event_type = Column(String(50), nullable=False)  # test_failed|file_changed|task_timeout|build_failed
    level = Column(String(10), default="L2")  # L1 reminder | L2 suggestion | L4 high-risk approval
    action = Column(Text, default="")
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_now)

class ProactiveSuggestion(Base):
    __tablename__ = "proactive_suggestions"
    id = Column(Integer, primary_key=True)
    rule_id = Column(Integer, ForeignKey("proactive_rules.id"), nullable=True)
    source_event_id = Column(Integer, nullable=True)
    event_type = Column(String(50), default="")
    level = Column(String(10), default="L2")
    title = Column(String(300), default="")
    detail = Column(Text, default="")
    action = Column(Text, default="")
    status = Column(String(30), default="pending")  # pending|approved|rejected|executed
    created_at = Column(DateTime, default=_now)
    decided_at = Column(DateTime, nullable=True)

class SandboxConfig(Base):
    """Singleton row (id=1): which Linux distribution the user picked for
    the AI computer sandbox. A preference record only — switching it never
    restarts the running shared environment, never touches shared files,
    and takes effect when a container sandbox is started under Docker."""
    __tablename__ = "sandbox_config"
    id = Column(Integer, primary_key=True)
    selected_distro = Column(String(60), default=None, nullable=True)
    selected_version = Column(String(60), default=None, nullable=True)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

class SandboxInstall(Base):
    """Install state of one sandbox distribution+version. Nothing is
    pre-installed: a row appears when the user starts an install, and its
    status is the real outcome of the docker pull/build (or the honest
    failure reason when Docker is unavailable). Only installed pairs may
    be selected as the current sandbox."""
    __tablename__ = "sandbox_installs"
    __table_args__ = (UniqueConstraint("distro", "version"),)
    id = Column(Integer, primary_key=True)
    distro = Column(String(60), nullable=False)
    version = Column(String(60), nullable=False)
    method = Column(String(20), default="")  # docker | host-native
    status = Column(String(30), default="not_installed")  # not_installed|installing|installed|failed
    log = Column(Text, default="")
    updated_at = Column(DateTime, default=_now, onupdate=_now)

class DependencyInstallJob(Base):
    """One user-triggered 'install common dependencies' run against the
    current environment. results holds one entry per catalog item with its
    real per-package outcome (exists|installed|failed), judged from actual
    package-manager output, never written down in advance."""
    __tablename__ = "dependency_install_jobs"
    id = Column(Integer, primary_key=True)
    status = Column(String(30), default="running")  # running|completed|failed
    package_manager = Column(String(20), default="")  # apt | dnf
    sandbox = Column(String(120), nullable=True)  # target sandbox label; None = this machine
    target_distro = Column(String(60), nullable=True)  # pinned at creation; "" = this machine
    target_version = Column(String(60), nullable=True)
    initiated_by = Column(String(20), default="user")
    results = Column(JSON, default=list)
    log = Column(Text, default="")
    created_at = Column(DateTime, default=_now)
    finished_at = Column(DateTime, nullable=True)

class PendingFileChange(Base):
    """A shared-file write an agent submitted through the tool registry,
    staged for user review instead of landing directly (Pilot-style staged
    diff). The shared file itself is only touched when the user accepts:
    acceptance replays the write through workspace.service.write_file with
    the staged base_version as the expected version, so the normal version
    increment (and the artifact-verification staleness that follows from
    it) applies unchanged. Rejection discards the change and leaves an
    event record; the file is never modified."""
    __tablename__ = "pending_file_changes"
    id = Column(Integer, primary_key=True)
    path = Column(String(500), nullable=False)
    agent_id = Column(Integer, nullable=True)
    task_id = Column(Integer, nullable=True)
    base_version = Column(Integer, nullable=True)  # SharedFileMeta.version at staging time (None = file did not exist)
    original_content = Column(Text, nullable=True)  # None = file did not exist
    new_content = Column(Text, default="")
    diff_summary = Column(JSON, default=dict)  # {added_lines, removed_lines}
    diff_text = Column(Text, default="")
    status = Column(String(30), default="pending")  # pending|accepted|rejected|superseded
    created_at = Column(DateTime, default=_now)
    decided_at = Column(DateTime, nullable=True)

class ChiefMission(Base):
    __tablename__ = "chief_missions"
    id = Column(Integer, primary_key=True)
    goal = Column(Text, default="")
    team_id = Column(Integer, nullable=True)
    task_id = Column(Integer, nullable=True)
    chief_agent_id = Column(Integer, nullable=True)
    status = Column(String(30), default="planned")  # planned|running|completed|failed
    plan = Column(JSON, default=dict)
    report = Column(JSON, nullable=True)
    llm_called = Column(Boolean, default=False)
    created_at = Column(DateTime, default=_now)
    finished_at = Column(DateTime, nullable=True)

class AppSetting(Base):
    """Single-row application settings (id=1): proxy mode/address and
    new-agent defaults. One JSON document so fields can grow without
    migrations; secrets are not stored here (provider keys stay in
    model_providers)."""
    __tablename__ = "app_settings"
    id = Column(Integer, primary_key=True)
    data = Column(JSON, default=dict)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

class BrowserConfig(Base):
    """Single-row (id=1) configuration for the shared-desktop browser:
    homepage, search engine, download directory and profile persistence.
    The browser itself is a real Chrome/Chromium on the shared desktop;
    this row only steers how it is launched and where its profile lives."""
    __tablename__ = "browser_config"
    id = Column(Integer, primary_key=True)
    homepage = Column(String(500), default="https://www.baidu.com")
    search_engine = Column(String(30), default="baidu")  # baidu|bing|google|duckduckgo|sogou
    download_dir = Column(String(500), default="")       # "" = shared workspace root
    persist_profile = Column(Boolean, default=True)
    profile_per_agent = Column(Boolean, default=False)
    updated_at = Column(DateTime, default=_now, onupdate=_now)
