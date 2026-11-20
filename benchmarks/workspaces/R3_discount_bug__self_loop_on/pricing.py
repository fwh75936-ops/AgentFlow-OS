def calculate_total(prices, discount_rate=0.0):
    return round(sum(prices) * (1.0 - discount_rate), 2)
