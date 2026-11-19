#!/usr/bin/env python3
"""Размер пилота для сравнения двух долей — и успеем ли его набрать.

Только стандартная библиотека Python 3.9+.

Сколько нужно, чтобы заметить изменение доли (например, доля ограничений 12 % → 9 %):
  python tools/pilot_size.py --base 12 --target 9
Если распределяем целые группы (семьи, команды, магазины), а считаем людей или номера:
  python tools/pilot_size.py --base 12 --target 9 --group-size 2.5 --icc 0.1
Успеем ли набрать, если впервые подходящих групп появляется 100 в неделю:
  python tools/pilot_size.py --base 12 --target 9 --group-size 2.5 --icc 0.1 --per-week 100
Какое изменение вообще можно заметить, если в каждой группе сравнения будет 800 единиц:
  python tools/pilot_size.py --base 12 --units 800

Доли можно писать как 12 или 0.12. Расчёт — нормальное приближение для двух долей,
двусторонняя проверка; это оценка порядка величины, а не гарантия. Допущения (обычная
частота, сходство внутри группы, темп) записывай в facts.md как допущения.
"""
from __future__ import annotations

import argparse
import math
import sys
from statistics import NormalDist

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def share(text: str) -> float:
    try:
        x = float(text.replace(",", ".").rstrip("%"))
    except ValueError:
        raise argparse.ArgumentTypeError(f"нужно число: {text}")
    p = x / 100 if x > 1 else x
    if not 0 < p < 1:
        raise argparse.ArgumentTypeError(f"доля должна быть между 0 и 1 (или 0 и 100 %): {x}")
    return p


def n_per_arm(p1: float, p2: float, alpha: float = 0.05, power: float = 0.8) -> int:
    """Единиц (людей, номеров) в каждой группе сравнения, чтобы заметить p1 → p2."""
    if p1 == p2:
        raise ValueError("исходная и целевая доли совпадают — замечать нечего")
    z = NormalDist().inv_cdf
    za, zb = z(1 - alpha / 2), z(power)
    pbar = (p1 + p2) / 2
    num = za * math.sqrt(2 * pbar * (1 - pbar)) + zb * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))
    return math.ceil(num ** 2 / (p1 - p2) ** 2)


def design_effect(group_size: float, icc: float) -> float:
    """Во сколько раз больше единиц нужно, если распределяем целые группы похожих единиц."""
    return 1 + (group_size - 1) * icc


def detectable(p1: float, units: int, alpha: float, power: float, up: bool) -> float | None:
    """Наименьшая заметная целевая доля при заданном числе единиц в группе сравнения."""
    lo, hi = (p1, 0.9999) if up else (0.0001, p1)
    if n_per_arm(p1, hi if up else lo, alpha, power) > units:
        return None
    for _ in range(60):  # двоичный поиск: ближе к p1 — нужно больше единиц
        mid = (lo + hi) / 2
        enough = n_per_arm(p1, mid, alpha, power) <= units
        if up:
            hi, lo = (mid, lo) if enough else (hi, mid)
        else:
            lo, hi = (mid, hi) if enough else (lo, mid)
    return hi if up else lo


def pct(p: float) -> str:
    return f"{p * 100:.1f}".rstrip("0").rstrip(".") + " %"


def main() -> int:
    ap = argparse.ArgumentParser(description="Размер пилота для двух долей и темп набора")
    ap.add_argument("--base", type=share, required=True, help="обычная доля события (без изменения)")
    ap.add_argument("--target", type=share, help="наименьшая важная доля после изменения")
    ap.add_argument("--units", type=int, help="вместо --target: единиц в каждой группе сравнения → что заметим")
    ap.add_argument("--up", action="store_true", help="для --units: ищем рост доли (по умолчанию — снижение)")
    ap.add_argument("--alpha", type=float, default=0.05, help="риск ложного вывода о пользе (по умолчанию 0.05)")
    ap.add_argument("--power", type=float, default=0.8, help="шанс заметить настоящий эффект (по умолчанию 0.8)")
    ap.add_argument("--group-size", type=float, default=1.0, help="единиц в одной распределяемой группе (семья…)")
    ap.add_argument("--icc", type=float, default=0.0, help="сходство единиц внутри группы, 0…1 (допущение)")
    ap.add_argument("--per-week", type=float, help="сколько впервые подходящих групп появляется в неделю")
    a = ap.parse_args()
    if (a.target is None) == (a.units is None):
        ap.error("укажите ровно одно: --target или --units")
    if a.target is not None and a.target == a.base:
        ap.error("исходная и целевая доли совпадают — замечать нечего")
    if a.group_size < 1 or not 0 <= a.icc <= 1:
        ap.error("--group-size не меньше 1, --icc от 0 до 1")
    de = design_effect(a.group_size, a.icc)
    grouped = a.group_size > 1

    print(f"Обычная доля: {pct(a.base)}. Проверка двусторонняя, риск ложного вывода {a.alpha:g}, "
          f"шанс заметить эффект {a.power:g}.")
    if a.units is not None:
        effective = int(a.units / de)
        p2 = detectable(a.base, effective, a.alpha, a.power, a.up)
        if p2 is None:
            print(f"При {a.units} единицах в группе сравнения изменение заметить нельзя ни при каком эффекте.")
            return 0
        print(f"При {a.units} единицах в каждой группе сравнения"
              + (f" (с учётом групп по {a.group_size:g} — как {effective} независимых)" if grouped else "")
              + f" заметим изменение до {pct(p2)} и сильнее: это {abs(p2 - a.base) * 100:.1f} п.п., "
              f"или {abs(p2 - a.base) / a.base:.0%} от исходного.")
        print("Меньшие изменения такой пилот не отличит от случайности.")
        return 0

    n = n_per_arm(a.base, a.target, a.alpha, a.power)
    print(f"Изменение {pct(a.base)} → {pct(a.target)}: {abs(a.target - a.base) * 100:.1f} п.п., "
          f"или {abs(a.target - a.base) / a.base:.0%} от исходного.")
    print(f"Нужно единиц в каждой группе сравнения (если все независимы): {n}, всего {2 * n}.")
    total_groups = None
    if grouped:
        need = math.ceil(n * de)
        groups = math.ceil(need / a.group_size)
        total_groups = 2 * groups
        print(f"Распределяем группы по {a.group_size:g} единицы, сходство внутри {a.icc:g}: нужно в {de:.2f} раза "
              f"больше единиц — {need} в каждой группе сравнения, то есть групп: {groups} в каждой, всего {total_groups}.")
    if a.per_week:
        total = total_groups if total_groups is not None else 2 * n
        weeks = math.ceil(total / a.per_week)
        print(f"Если впервые подходящих {'групп' if grouped else 'единиц'} {a.per_week:g} в неделю, набор займёт "
              f"около {weeks} нед. К этому добавьте срок наблюдения за последними включёнными.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
