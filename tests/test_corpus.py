from multilingual_doc_variants.corpus import CorpusRow, JOIN_SEP, TEXT_FIELDS, load_rows


def test_concat_and_offsets():
    rows = load_rows()
    assert rows, "expected corpus to load"
    r = rows[0]
    expected = JOIN_SEP.join([r.title, r.abstract, r.description, r.first_claim, r.context])
    assert r.text == expected

    spans = r.field_spans
    # title span at start
    assert spans["title"] == (0, len(r.title))
    # last field ends at len(text)
    assert spans["context"][1] == len(r.text)


def test_field_text_slices_match():
    rows = load_rows()
    r = next(x for x in rows if x.abstract)
    s, e = r.field_spans["abstract"]
    assert r.text[s:e] == r.abstract
