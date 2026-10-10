from datetime import datetime
from decimal import Decimal

def money(v):
    return f"{Decimal(v):,.0f}".replace(",", " ") + " so'm"

def parse_money(text):
    return Decimal(text.replace(" ","").replace(",", "."))

def parse_deadline(text):
    if text.strip().lower() in {"-","yo'q","yoq","skip"}:
        return None
    return datetime.strptime(text.strip(), "%d.%m.%Y").date()
