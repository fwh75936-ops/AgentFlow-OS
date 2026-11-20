"""Self-Loop Engine: EXECUTE -> VERIFY -> ANALYZE -> REPAIR -> REVERIFY -> COMPLETE/FAILED.

Hard stops: max_retries, max_seconds budget, repeated-identical-error detection.
No infinite loops are possible: every path increments the attempt counter.
"""
import time
from enum import Enum

class ErrorType(str, Enum):
    MODEL_ERROR = "MODEL_ERROR"
    TOOL_ERROR = "TOOL_ERROR"
    TIMEOUT = "TIMEOUT"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    PERMISSION_ERROR = "PERMISSION_ERROR"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"
    ENVIRONMENT_ERROR = "ENVIRONMENT_ERROR"

def classify_error(message: str) -> ErrorType:
    m = (message or "").lower()
    if "timeout" in m or "timed out" in m:
        return ErrorType.TIMEOUT
    if "permission" in m or "forbidden" in m or "not allowed" in m:
        return ErrorType.PERMISSION_ERROR
    if "no model provider" in m or "model" in m and ("provider" in m or "api" in m or "llm" in m):
        return ErrorType.MODEL_ERROR
    if "no eligible agent" in m or "dependency" in m or "missing required" in m:
        return ErrorType.DEPENDENCY_ERROR
    if "validation" in m or "schema" in m or "required field" in m or "verify" in m:
        return ErrorType.VALIDATION_ERROR
    if "tool" in m:
        return ErrorType.TOOL_ERROR
    if "environment" in m or "docker" in m or "not found" in m:
        return ErrorType.ENVIRONMENT_ERROR
    return ErrorType.MODEL_ERROR

class SelfLoop:
    def __init__(self, max_retries=3, max_seconds=600):
        self.max_retries = max_retries
        self.max_seconds = max_seconds
        self.history = []

    def run(self, execute_fn, verify_fn, repair_fn=None):
        """execute_fn(attempt)->output (may raise); verify_fn(output)->result with .passed/.errors;
        repair_fn(errors, attempt)->None prepares next attempt. Returns (final_state, output, history)."""
        start = time.time()
        attempt = 0
        last_signature = None
        repeat_count = 0
        output = None
        while True:
            if time.time() - start > self.max_seconds:
                self.history.append({"state": "FAILED", "attempt": attempt,
                                     "error_type": ErrorType.TIMEOUT.value,
                                     "detail": "已超出时间预算"})
                return "FAILED", output, self.history
            self.history.append({"state": "EXECUTE", "attempt": attempt})
            try:
                output = execute_fn(attempt)
            except Exception as e:
                et = classify_error(str(e))
                self.history.append({"state": "ANALYZE", "attempt": attempt,
                                     "error_type": et.value, "detail": str(e)[:500]})
                # Repeat detection must compare the WHOLE error, not a
                # 120-char prefix: two different failures sharing a prefix
                # are not the same error. (Truncation is for display only.)
                import hashlib
                signature = f"{et.value}:{hashlib.sha256(str(e).encode()).hexdigest()}"
                repeat_count = repeat_count + 1 if signature == last_signature else 0
                last_signature = signature
                if attempt >= self.max_retries or repeat_count >= 1:
                    self.history.append({"state": "FAILED", "attempt": attempt,
                                         "error_type": et.value,
                                         "detail": "已达最大重试次数或重复出现相同错误"})
                    return "FAILED", output, self.history
                self.history.append({"state": "REPAIR", "attempt": attempt, "error_type": et.value})
                if repair_fn:
                    repair_fn([str(e)], attempt)
                attempt += 1
                continue
            self.history.append({"state": "VERIFY" if attempt == 0 else "REVERIFY", "attempt": attempt})
            result = verify_fn(output)
            if result.passed:
                self.history.append({"state": "COMPLETE", "attempt": attempt})
                return "COMPLETE", output, self.history
            errors = list(getattr(result, "errors", []) or ["验证未通过"])
            et = classify_error("; ".join(errors))
            if et == ErrorType.MODEL_ERROR:
                et = ErrorType.VALIDATION_ERROR
            self.history.append({"state": "ANALYZE", "attempt": attempt,
                                 "error_type": et.value, "detail": "; ".join(errors)[:500]})
            import hashlib
            signature = f"{et.value}:{hashlib.sha256(';'.join(errors).encode()).hexdigest()}"
            repeat_count = repeat_count + 1 if signature == last_signature else 0
            last_signature = signature
            if attempt >= self.max_retries or repeat_count >= 1:
                self.history.append({"state": "FAILED", "attempt": attempt, "error_type": et.value,
                                     "detail": "已达最大重试次数或重复出现相同错误"})
                return "FAILED", output, self.history
            self.history.append({"state": "REPAIR", "attempt": attempt, "error_type": et.value})
            if repair_fn:
                repair_fn(errors, attempt)
            attempt += 1
