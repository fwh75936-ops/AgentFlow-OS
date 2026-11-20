from types import SimpleNamespace
from app.self_loop.engine import SelfLoop, classify_error, ErrorType

def _fail_result():
    return SimpleNamespace(passed=False, errors=["validation: bad output"])

def test_self_loop_terminates_at_max_retries():
    calls = {"n": 0}
    def execute(attempt):
        calls["n"] += 1
        return "x"
    loop = SelfLoop(max_retries=2, max_seconds=60)
    state, _out, history = loop.run(execute, lambda o: _fail_result(), lambda e, a: None)
    assert state == "FAILED"
    # identical error signature repeats -> repeated-error detection stops even
    # earlier than max_retries: initial attempt + 1 repair = 2 executions, never infinite
    assert calls["n"] == 2
    assert history[-1]["state"] == "FAILED"

def test_self_loop_completes_after_repair():
    attempts = {"n": 0}
    def execute(attempt):
        attempts["n"] += 1
        return f"attempt-{attempt}"
    def verify(output):
        return SimpleNamespace(passed=(output == "attempt-1"), errors=["validation: stale"])
    loop = SelfLoop(max_retries=3, max_seconds=60)
    state, out, history = loop.run(execute, verify, lambda e, a: None)
    assert state == "COMPLETE" and out == "attempt-1"
    assert any(h["state"] == "REPAIR" for h in history)

def test_self_loop_execute_exception_terminates():
    def execute(attempt):
        raise RuntimeError("no model provider configured")
    loop = SelfLoop(max_retries=1, max_seconds=60)
    state, _o, history = loop.run(execute, lambda o: SimpleNamespace(passed=True, errors=[]))
    assert state == "FAILED"
    assert any(h.get("error_type") == "MODEL_ERROR" for h in history)

def test_error_classification():
    assert classify_error("request timed out") == ErrorType.TIMEOUT
    assert classify_error("permission denied") == ErrorType.PERMISSION_ERROR
    assert classify_error("no eligible agent: dependency unmet") == ErrorType.DEPENDENCY_ERROR
    assert classify_error("schema validation failed") == ErrorType.VALIDATION_ERROR

def test_self_loop_empty_verify_errors_fallback_is_chinese():
    """校验返回失败但 errors 为空时，兜底文案必须是中文，且分类仍为验证错误。"""
    loop = SelfLoop(max_retries=1, max_seconds=60)
    state, _o, history = loop.run(lambda a: "x",
                                  lambda o: SimpleNamespace(passed=False, errors=[]),
                                  lambda e, a: None)
    assert state == "FAILED"
    analyze = [h for h in history if h.get("state") == "ANALYZE"]
    assert analyze, "no ANALYZE step recorded"
    assert analyze[0]["detail"] == "验证未通过"
    assert analyze[0]["error_type"] == "VALIDATION_ERROR"


def test_self_loop_failure_detail_is_chinese():
    """失败原因会原样进群聊纪要与任务错误展示，必须是中文人话而非英文原文。"""
    def execute(attempt):
        raise RuntimeError("no model provider configured")
    loop = SelfLoop(max_retries=1, max_seconds=60)
    state, _o, history = loop.run(execute, lambda o: SimpleNamespace(passed=True, errors=[]))
    assert state == "FAILED"
    assert history[-1]["detail"] == "已达最大重试次数或重复出现相同错误"
    # 校验失败路径同样走中文失败原因。
    loop2 = SelfLoop(max_retries=2, max_seconds=60)
    state2, _o2, history2 = loop2.run(lambda a: "x", lambda o: _fail_result(), lambda e, a: None)
    assert state2 == "FAILED"
    assert history2[-1]["detail"] == "已达最大重试次数或重复出现相同错误"
