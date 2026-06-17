from src.scrapers.daraz.playwright_async import daraz_star_rating_from_classes


def test_daraz_star_rating_from_classes_maps_known_variants():
    assert daraz_star_rating_from_classes(
        ["_9-ogB Dy1nx", "_9-ogB Dy1nx", "_9-ogB Dy1nx", "_9-ogB Dy1nx", "_9-ogB i6t3-"]
    ) == "4.7"
    assert daraz_star_rating_from_classes(
        ["_9-ogB Dy1nx", "_9-ogB Dy1nx", "_9-ogB Dy1nx", "_9-ogB Dy1nx", "_9-ogB B4Foa"]
    ) == "4.5"
    assert daraz_star_rating_from_classes(
        ["_9-ogB Dy1nx", "_9-ogB Dy1nx", "_9-ogB Dy1nx", "_9-ogB Dy1nx", "_9-ogB TZlP8"]
    ) == "4.3"
    assert daraz_star_rating_from_classes(
        ["_9-ogB Dy1nx", "_9-ogB Dy1nx", "_9-ogB Dy1nx", "_9-ogB Dy1nx", "_9-ogB K8PID"]
    ) == "4.8"
    assert daraz_star_rating_from_classes(
        ["_9-ogB Dy1nx", "_9-ogB Dy1nx", "_9-ogB Dy1nx", "_9-ogB Dy1nx", "_9-ogB yWGJ-"]
    ) == "4.2"


def test_daraz_star_rating_from_classes_returns_none_for_unknown_icons():
    assert daraz_star_rating_from_classes([]) is None
    assert daraz_star_rating_from_classes(["_9-ogB Unknown"]) is None
