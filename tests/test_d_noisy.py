"""Sanity tests for variant D's rule clause (the perturbation itself is LLM-generated)."""
from multilingual_doc_variants.stage4_idea2.build import _rule_clause_d


def test_rule_clause_mentions_perturbation_examples():
    clause = _rule_clause_d("en")
    assert "hyphen" in clause.lower()
    assert "case" in clause.lower()
    assert "same term" in clause.lower()


def test_rule_clause_pins_source_language():
    en_clause = _rule_clause_d("en")
    fr_clause = _rule_clause_d("fr")
    assert "(en)" in en_clause
    assert "(fr)" in fr_clause
