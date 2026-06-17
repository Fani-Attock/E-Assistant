from src.ml.cf.infer_cf import CFRecommender


def test_cf_normalize_scores():
    out = CFRecommender.normalize_scores({"a": 2.0, "b": 4.0, "c": 3.0})
    assert out["a"] == 0.0
    assert out["b"] == 1.0
    assert out["c"] == 0.5

