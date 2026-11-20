from pricing import calculate_total

def test_no_discount():
    assert calculate_total([10.0, 20.0]) == 30.0

def test_discount_applied():
    assert calculate_total([100.0], discount_rate=0.10) == 90.0

def test_discount_multiple_items():
    assert calculate_total([50.0, 50.0], discount_rate=0.25) == 75.0
