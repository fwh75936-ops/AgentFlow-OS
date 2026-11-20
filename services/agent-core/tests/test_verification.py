import json
from app.verification.engine import structural_verify, rule_verify, file_exists_verify

def test_structural_pass_with_required_fields():
    r = structural_verify(json.dumps({"summary": "ok", "result": 1}), required_fields=["summary", "result"])
    assert r.passed and r.score == 1.0

def test_structural_fail_missing_field():
    r = structural_verify(json.dumps({"summary": "ok"}), required_fields=["summary", "result"])
    assert not r.passed and any("result" in e for e in r.errors)

def test_structural_fail_non_json_when_fields_required():
    r = structural_verify("not json", required_fields=["a"])
    assert not r.passed

def test_structural_json_schema():
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
    assert structural_verify({"n": 3}, schema=schema).passed
    assert not structural_verify({"n": "x"}, schema=schema).passed

def test_rule_and_file_verify(tmp_path):
    assert rule_verify({"count": 5}, [{"field": "count", "op": "gte", "value": 3}]).passed
    assert not rule_verify({"count": 1}, [{"field": "count", "op": "gte", "value": 3}]).passed
    f = tmp_path / "a.txt"; f.write_text("hi")
    assert file_exists_verify([str(f)]).passed
    assert not file_exists_verify([str(tmp_path / "missing.txt")]).passed
