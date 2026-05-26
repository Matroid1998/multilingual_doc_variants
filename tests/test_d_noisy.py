from multilingual_doc_variants.stage4_idea2.variants.d_noisy import perturb


def test_perturb_changes_term():
    out = perturb("amlodipine", seed=42)
    assert out != "amlodipine"


def test_perturb_deterministic():
    a = perturb("benzene-1,2-diol", seed=7)
    b = perturb("benzene-1,2-diol", seed=7)
    assert a == b
