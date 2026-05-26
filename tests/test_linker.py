from multilingual_doc_variants.stage2_link.dictionary import build_dictionaries
from multilingual_doc_variants.stage2_link.link import link_document


def _aliases(rows):
    return {cid: {lang: terms for lang, terms in by_lang.items()} for cid, by_lang in rows.items()}


def test_longest_match_wins():
    aliases = _aliases({
        "CHEBI:1": {"en": ["ethanol"]},
        "CHEBI:2": {"en": ["ethanol amine"]},
    })
    dicts = build_dictionaries(aliases)
    text = "We mixed ethanol amine with water."
    mentions = link_document(text, "en", dicts, aliases)
    assert len(mentions) == 1
    assert mentions[0]["chebi_id"] == "CHEBI:2"
    assert mentions[0]["surface"] == "ethanol amine"


def test_offsets_relative_to_original():
    aliases = _aliases({"CHEBI:1": {"en": ["caffeine"]}})
    dicts = build_dictionaries(aliases)
    text = "Pure CAFFEINE was added."
    mentions = link_document(text, "en", dicts, aliases)
    assert len(mentions) == 1
    m = mentions[0]
    assert text[m["start"] : m["end"]] == "CAFFEINE"


def test_zh_no_casefold():
    aliases = _aliases({"CHEBI:1": {"zh": ["咖啡因"]}})
    dicts = build_dictionaries(aliases)
    text = "样品中含有咖啡因。"
    mentions = link_document(text, "zh", dicts, aliases)
    assert len(mentions) == 1
    assert mentions[0]["surface"] == "咖啡因"
