"""Shared Linux Workspace: ONE Linux environment shared by every agent.

Design invariants (round 2 spec):
- Exactly one environment, id ``shared-linux-1``. Agents never get their own
  container/VM; they get independent terminal *sessions* inside this one env.
- The shared filesystem root is logically ``/workspace``. In the Docker
  deployment it literally is ``/workspace`` (single volume). In local dev it
  maps to ``services/agent-core/data/shared_workspace`` (override with
  ``AGENTFLOW_SHARED_ROOT``).
- All execution goes through the unified Tool Registry permission model and
  is audit-logged (executor, task, time, exit code, output).
"""
