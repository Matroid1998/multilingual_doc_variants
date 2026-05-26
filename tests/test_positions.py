from multilingual_doc_variants.stage4_idea2.positions import compute_position_ranges, offset_position


def test_title_and_first_sentence_disjoint():
    title = "Coated steel band"
    abstract = "This invention relates to coatings. A second sentence follows."
    description = ""
    first_claim = ""
    context = ""
    ranges = compute_position_ranges(title, abstract, description, first_claim, context, lang="en")
    assert ranges.title == (0, len(title))
    # First sentence starts immediately after title + '\n' and contains the first abstract sentence
    fs_start = len(title) + 1
    full_text = "\n".join([title, abstract, description, first_claim, context])
    assert ranges.first_sentence is not None
    fs_s, fs_e = ranges.first_sentence
    assert fs_s == fs_start
    assert "This invention relates to coatings." in full_text[fs_s:fs_e]
    # Must not overrun into the second sentence
    assert "second sentence" not in full_text[fs_s:fs_e]


def test_offset_position_classifier():
    title = "T"
    abstract = "Sentence one. Sentence two."
    description = ""
    first_claim = ""
    context = "Some body context here."
    ranges = compute_position_ranges(title, abstract, description, first_claim, context, lang="en")
    assert offset_position(0, ranges) == "title"
    # Far end in context -> body
    full_len = len("\n".join([title, abstract, description, first_claim, context]))
    assert offset_position(full_len - 5, ranges) == "body"
