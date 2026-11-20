"""Verification Engine: structural / rule / file / tool (real pytest) layers."""
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field

@dataclass
class VerificationResult:
    verification_type: str
    passed: bool
    score: float
    evidence: str = ""
    errors: list = field(default_factory=list)

def structural_verify(output, schema=None, required_fields=None):
    """Check JSON parseability, required fields and (optionally) JSON Schema."""
    errors = []
    data = output
    if isinstance(output, str):
        try:
            data = json.loads(output)
        except Exception:
            # Plain-text output is acceptable only when no schema/fields are demanded.
            if schema is not None or required_fields:
                return VerificationResult("structural", False, 0.0,
                                          "output is not valid JSON", ["output is not valid JSON"])
            return VerificationResult("structural", True, 1.0, "plain-text output, no schema required")
    if required_fields:
        if not isinstance(data, dict):
            # Required fields can only live on a JSON object; a list or
            # scalar must not slip past the check by not being a dict.
            errors.append("output is not a JSON object; required fields cannot be checked")
        else:
            for f in required_fields:
                if f not in data or data[f] in (None, ""):
                    errors.append(f"missing required field: {f}")
    if schema is not None:
        try:
            import jsonschema
            jsonschema.validate(data, schema)
        except Exception as e:  # jsonschema.ValidationError or missing lib
            errors.append(f"schema violation: {e}")
    passed = not errors
    return VerificationResult("structural", passed, 1.0 if passed else 0.0,
                              "structural checks passed" if passed else "; ".join(errors), errors)

def rule_verify(output, rules):
    """Rules: [{field, op: eq|contains|gte|lte|nonempty, value}]."""
    errors = []
    data = output
    if isinstance(output, str):
        try:
            data = json.loads(output)
        except Exception:
            data = {"_text": output}
    for r in rules or []:
        field_name, op, expected = r.get("field"), r.get("op", "eq"), r.get("value")
        actual = data.get(field_name) if isinstance(data, dict) else None
        if op == "eq":
            ok = actual == expected
        elif op == "contains":
            ok = expected in str(actual or "")
        elif op in ("gte", "lte"):
            # A missing field is not zero, and comparing incompatible
            # types is a failed rule — never an exception or a pass.
            if actual is None:
                ok = False
            else:
                try:
                    ok = (actual >= expected) if op == "gte" else (actual <= expected)
                except TypeError:
                    ok = False
        elif op == "nonempty":
            ok = bool(actual)
        else:
            # An unknown (e.g. misspelled) operator must fail loudly in
            # the result, not pass by default.
            ok = False
        if not ok:
            errors.append(f"rule failed: {field_name} {op} {expected!r} (actual={actual!r})")
    passed = not errors
    return VerificationResult("rule", passed, 1.0 if passed else 0.0,
                              "rules passed" if passed else "; ".join(errors), errors)

def file_exists_verify(paths):
    missing = [p for p in (paths or []) if not os.path.exists(p)]
    passed = not missing
    return VerificationResult("structural", passed, 1.0 if passed else 0.0,
                              f"checked {len(paths or [])} paths",
                              [f"file not found: {p}" for p in missing])

def tool_verify_pytest(cwd, timeout=120):
    """Tool verification: really run pytest in `cwd` and keep raw output as evidence."""
    if not os.path.isdir(cwd):
        return VerificationResult("tool", False, 0.0, "", [f"directory not found: {cwd}"])
    import signal
    proc = subprocess.Popen([sys.executable, "-m", "pytest", "-q"],
                            cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # Kill the whole process group: tests may have spawned servers or
        # workers that would otherwise outlive the "timed out" verdict.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            proc.wait(timeout=10)
        except Exception:
            pass
        return VerificationResult("tool", False, 0.0, "", [f"pytest timed out after {timeout}s"])
    evidence = (out or "")[-4000:] + (err or "")[-1000:]
    passed = proc.returncode == 0
    return VerificationResult("tool", passed, 1.0 if passed else 0.0, evidence,
                              [] if passed else [f"pytest exited with code {proc.returncode}"])
