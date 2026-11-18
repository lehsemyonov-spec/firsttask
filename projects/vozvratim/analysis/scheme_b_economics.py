"""Экономика схемы Б: сервис сам платит награду нашедшему.
Все входные значения — ДОПУЩЕНИЯ (кроме отмеченных F…), данных о продажах и возвратах нет.
Запуск: python3 analysis/scheme_b_economics.py
"""
PRICE = 990            # ₽ за набор (допущение, ворота 3)
STICKERS = 3           # стикеров в наборе (допущение)
PRINT_PACK = 60        # ₽ печать 3 уникальных стикеров + упаковка (допущение; U7)
DELIVERY = 150         # ₽ доставка за счёт сервиса (допущение)
ACQ_FEE = 0.03         # эквайринг (допущение)
TAX = 0.06             # УСН «доходы» 6% (допущение; U6)
NDFL = 0.13            # НДФЛ нашедшего, сервис доплачивает сверху (F67)
FIXED_3M = 10_000      # ₽ фиксированные расходы за 3 мес. (порог ворот 2)

def margin_before_payouts(cac):
    return PRICE * (1 - ACQ_FEE - TAX) - PRINT_PACK - DELIVERY - cac

def payout_cost(n):            # нашедший получает N «на руки»
    return n / (1 - NDFL)

def breakeven_p(n, cac):       # доля стикеров с выплатой за год, при которой маржа = 0
    m = margin_before_payouts(cac)
    return m / (STICKERS * payout_cost(n)) if m > 0 else 0.0

print(f"Маржа до выплат на набор: CAC 0 → {margin_before_payouts(0):.0f} ₽; "
      f"CAC 500 → {margin_before_payouts(500):.0f} ₽; CAC 990 → {margin_before_payouts(990):.0f} ₽\n")
print("Точка безубыточности — доля стикеров с выплатой за год (одна выплата на стикер в год):")
print("| N на руки | Стоимость выплаты | CAC 0 | CAC 300 | CAC 500 |")
print("|---|---|---|---|---|")
for n in (300, 500, 1000):
    row = [f"{breakeven_p(n, c)*100:.0f}%" for c in (0, 300, 500)]
    print(f"| {n} ₽ | {payout_cost(n):.0f} ₽ | " + " | ".join(row) + " |")

print("\nОжидаемые выплаты на набор при N = 500 ₽:")
for p in (0.005, 0.02, 0.05, 0.10, 0.20):
    print(f"  p = {p*100:.1f}% → {STICKERS*p*payout_cost(500):.0f} ₽ на набор")

print(f"\nФиксированные расходы {FIXED_3M} ₽ окупаются при CAC 300 и N 500, p 2%: "
      f"{FIXED_3M / (margin_before_payouts(300) - STICKERS*0.02*payout_cost(500)):.0f} наборов")
print(f"Худший случай на набор (все 3 стикера с выплатой, N 500): {STICKERS*payout_cost(500):.0f} ₽")
