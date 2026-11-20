def calculate_total(prices, discount_rate=0.0):
    """BUG: discount_rate is silently ignored."""
    return round(sum(prices), 2)
