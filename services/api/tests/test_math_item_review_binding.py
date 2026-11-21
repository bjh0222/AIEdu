from app.v2.math_item_review import bind_item_reviews, DIMENSIONS


def test_item_reference_review_requires_exact_own_item_evidence():
    unit = {"unit_id": "u", "items": [{"item_id": "i", "stem_markdown": "x+1=2",
        "answer": {"final": "x=1", "reasoning": "subtract one"}}]}
    output = {"item_reviews": [{"item_id": "i", "dimensions": {
        name: {"result": "supported", "reason": "reference only", "quote": "x=1"} for name in DIMENSIONS}}]}
    bound = bind_item_reviews(output, unit, {"call_id": "review"})
    assert len(bound) == 1
    assert all(d["observation"] == "supported" and d["result"] == "undecidable" and d["score"] is None
               for d in bound[0]["dimensions"].values())
    output["item_reviews"][0]["dimensions"]["reasoning"]["quote"] = "not actually in the item"
    assert bind_item_reviews(output, unit, {})[0]["dimensions"]["reasoning"]["observation"] == "undecidable"


def test_missing_item_and_duplicate_reviews_are_unknown():
    unit = {"unit_id": "u", "items": [{"item_id": "i", "stem_markdown": "x",
        "answer": {"final": "y", "reasoning": "z"}}]}
    for output in ({}, {"item_reviews": [{"item_id": "i"}, {"item_id": "i"}]}):
        assert all(d["observation"] == "undecidable" for d in bind_item_reviews(output, unit, {})[0]["dimensions"].values())
