import json
from pathlib import Path

from multilingual_doc_variants.stage4_idea2.llm import LLMCache, _cache_key


def test_cache_key_stable_for_equivalent_payload():
    a = _cache_key({"model": "m", "system": "s", "user": "u", "temperature": 0.0, "max_output_tokens": 100})
    b = _cache_key({"max_output_tokens": 100, "user": "u", "system": "s", "model": "m", "temperature": 0.0})
    assert a == b


def test_cache_roundtrip(tmp_path: Path):
    p = tmp_path / "cache.jsonl"
    cache = LLMCache(p)
    key = "abc"
    cache.put(key, {"model": "x"}, "the response")
    assert cache.get(key) == "the response"

    # reload from disk
    cache2 = LLMCache(p)
    assert cache2.get(key) == "the response"

    # file format is JSONL
    lines = p.read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["key"] == key and rec["response"] == "the response"
