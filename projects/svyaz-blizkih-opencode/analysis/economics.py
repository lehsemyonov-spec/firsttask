#!/usr/bin/env python3
"""Расчёты по брифу S1 для проекта svyaz-blizkih-opencode (прогон в OpenCode).

Все исходные числа — только из брифа (S1, стр. 3, 6, 9). Всё, что не дано
(число номеров в группе, доля событий, удлинение оплаты, каналы), — помечено
как допущение и выведено диапазоном.

Единицы: подключённые группы, номера, владельцы, оплаченные месяцы.
Метрика пользы в брифе — «доля подходящих номеров» (S1, стр. 7), поэтому
единица наблюдения в пилоте — номер, а группа — единица распределения.

Запуск: python3 projects/svyaz-blizkih-opencode/analysis/economics.py
"""

RATE_PER_PAID_MONTH = 500  # руб. валовой прибыли за доп. оплаченный месяц номера (S1, стр. 9)

BASE = {
    "profit_plan": 282, "profit_est": 275, "profit_nochange": 279, "profit_target": 285,
    "active_plan": 5000, "active_est": 4000, "active_nochange": 5000, "active_target": 8000,
    "groups_plan": 7500, "groups_est": 6500, "groups_nochange": 8000, "groups_target": 12000,
    "owners_plan": 1000, "owners_est": 800, "owners_nochange": 1000, "owners_target": 2000,
    "activation_plan": 0.50, "activation_est": 0.42,
    "activation_nochange": 0.45, "activation_target": 0.60,
}

MARKET = {"households_need": 300_000, "households_available": 100_000,
          "owners_need": 180_000, "owners_available": 40_000}

NUMBERS_PER_GROUP = 2.4        # допущение: 73% групп 2–3 человека (S3)
NUMBERS_PER_GROUP_RANGE = (2.0, 3.0)
EVENT_RATE_PER_QUARTER = 0.25  # допущение: доля групп с риском исчерпания пакета за квартал
EVENT_RATE_RANGE = (0.15, 0.35)
WEEKS_PER_QUARTER = 13
WEEKS_ENROLL = 6
ALPHA_POWER_NOTE = "риск ложного вывода 0,05, шанс заметить эффект 0,8 (tools/pilot_size.py)"


def n(x, dec=0):
    s = f"{x:,.{dec}f}".replace(",", "\u00a0")
    return s.replace(".", ",") if dec else s


def pct(x):
    return f"{x * 100:.1f} %".replace(".", ",")


def mln(x):
    return f"{n(x, 2)} млн руб."


def section(title):
    print(f"\n## {title}\n")


def main():
    print("# Расчёты по брифу: связь для близких и собственных устройств")
    print("\nИсточник чисел: S1, стр. 3 (показатели), стр. 6 (привлечение), стр. 7 (метрика пользы),")
    print("стр. 9 (прибыль). Допущения помечены; каждое — кандидат в unknowns.md.")
    print("Единицы: группы, номера, владельцы, оплаченные месяцы. Единица наблюдения пилота — номер.")

    section("1. Разрывы планового года")
    gap_profit = BASE["profit_target"] - BASE["profit_nochange"]
    gap_groups = BASE["groups_target"] - BASE["groups_nochange"]
    gap_owners = BASE["owners_target"] - BASE["owners_nochange"]
    print(f"- Прибыль: план базового года {n(BASE['profit_plan'])} млн, оценка {n(BASE['profit_est'])} млн; "
          f"без изменений {n(BASE['profit_nochange'])} млн, цель {n(BASE['profit_target'])} млн "
          f"→ разрыв к цели {n(gap_profit)} млн руб.")
    print(f"- Группы: оценка базы {n(BASE['groups_est'])}, без изменений {n(BASE['groups_nochange'])}, "
          f"цель {n(BASE['groups_target'])} → разрыв {n(gap_groups)} сверх прогноза; "
          f"чистый прирост к базе {n(BASE['groups_target'] - BASE['groups_est'])}.")
    print(f"- Владельцы: оценка базы {n(BASE['owners_est'])}, без изменений {n(BASE['owners_nochange'])}, "
          f"цель {n(BASE['owners_target'])} → разрыв {n(gap_owners)}.")

    section("2. Два разных разрыва: подключения и оплаченные месяцы")
    need_months = gap_profit * 1_000_000 / RATE_PER_PAID_MONTH
    print(f"- Разрыв по прибыли: {n(need_months)} дополнительных оплаченных месяцев номеров "
          f"по {n(RATE_PER_PAID_MONTH)} руб. — если других вкладов и новых прямых расходов нет.")
    print("- Разрыв по подключениям: 4 000 групп сверх прогноза. Это другой разрыв: он требует "
          "привлечения, а не удержания.")
    print("- Ниже считаем вклад удержания отдельно от вклада новых групп: смешивать их нельзя.")

    section("3. Удержание: сколько нужно и что даёт семья")
    numbers_nochange = BASE["groups_nochange"] * NUMBERS_PER_GROUP
    numbers_target = BASE["groups_target"] * NUMBERS_PER_GROUP
    print(f"Допущение: {n(NUMBERS_PER_GROUP, 1)} номера на группу (диапазон "
          f"{n(NUMBERS_PER_GROUP_RANGE[0], 1)}–{n(NUMBERS_PER_GROUP_RANGE[1], 1)}).")
    print(f"- Номеров: без изменений {n(numbers_nochange)}, при 12 000 группах {n(numbers_target)}.")
    req_at_nochange = need_months / numbers_nochange
    req_at_target = need_months / numbers_target
    print(f"- Требуемое удлинение оплаты: {n(req_at_nochange, 3)} мес./номер при 8 000 группах "
          f"и {n(req_at_target, 3)} мес./номер при 12 000 группах.")
    print("  Это требование, а не прогноз: из него нельзя выводить ожидаемый эффект.")
    print("\nСколько разрыва закрывает удлинение оплаты (19 200 номеров):")
    for extra in (0.3, 0.5, 0.75):
        print(f"  +{n(extra, 1)} мес./номер = {mln(numbers_nochange * extra * RATE_PER_PAID_MONTH / 1e6)}")

    section("4. Удержание: владельцы при том же допущении")
    owner_numbers = (BASE["owners_target"] - BASE["owners_nochange"]) * 2  # допущение: 2 номера
    print(f"- Прирост 1 000 владельцев × 2 номера = {n(owner_numbers)} номеров (допущение).")
    for extra in (0.3, 0.5, 0.75, 3.0):
        print(f"  +{n(extra, 1)} мес./номер = {mln(owner_numbers * extra * RATE_PER_PAID_MONTH / 1e6)}")
    print("  При равном эффекте на номер владельцы дают в 9,6 раза меньше семей "
          "(2 000 номеров против 19 200).")

    section("5. Новые группы: вклад в прибыль и цена привлечения")
    print("Допущение: новая группа платит в среднем 4,5–6,5 месяца в плановом году "
          "(подключение равномерно в течение года), 2,4 номера.")
    for months in (4.5, 6.5):
        gross = gap_groups * NUMBERS_PER_GROUP * months * RATE_PER_PAID_MONTH / 1e6
        print(f"  {n(gap_groups)} групп × {n(NUMBERS_PER_GROUP, 1)} × {n(months, 1)} мес. "
              f"= {mln(gross)} валовой прибыли до затрат на привлечение (УТОЧНИТЬ по U7).")
    print("  Цена привлечения одной группы в брифе не дана (U7): чистый вклад = валовая прибыль "
          "минус затраты на привлечение, и он может быть близок к нулю.")
    print("  Возможна и каннибализация: участник группы мог платить сам — тогда прирост "
          "подключений не равен приросту выручки (U7).")

    section("6. Активность")
    active_base = BASE["active_est"] / BASE["groups_est"]
    active_nochange = BASE["active_nochange"] / BASE["groups_nochange"]
    active_target = BASE["active_target"] / BASE["groups_target"]
    print(f"- Доля активных: база {pct(active_base)}, без изменений {pct(active_nochange)}, "
          f"цель {pct(active_target)}.")
    print(f"- 12 000 групп при доле {pct(active_nochange)} дают "
          f"{n(BASE['groups_target'] * active_nochange)} активных — на "
          f"{n(BASE['active_target'] - BASE['groups_target'] * active_nochange)} меньше цели.")
    print(f"- Значит, нужен рост доли активных на "
          f"{n((active_target - active_nochange) * 100, 1)} п.п. сверх прироста групп.")
    print(f"- Активация: база {pct(BASE['activation_est'])}, без изменений {pct(BASE['activation_nochange'])}, "
          f"цель {pct(BASE['activation_target'])} (+{n((BASE['activation_target'] - BASE['activation_est']) * 100)} п.п.).")

    section("7. Охват доступных рынков")
    print(f"- Группы: {n(BASE['groups_target'])} из {n(MARKET['households_available'])} доступных "
          f"домохозяйств = {n(BASE['groups_target'] / MARKET['households_available'] * 100, 1)} %.")
    print(f"- Владельцы: {n(BASE['owners_target'])} из {n(MARKET['owners_available'])} = "
          f"{n(BASE['owners_target'] / MARKET['owners_available'] * 100, 1)} %.")
    print(f"- Прирост групп к базе {n(BASE['groups_target'] - BASE['groups_est'])} — это "
          f"{n((BASE['groups_target'] - BASE['groups_est']) / MARKET['households_available'] * 100, 1)} % "
          "доступной базы за год.")

    section("8. Мощность пилота: единица наблюдения — номер")
    print(f"Проверка двух долей, {ALPHA_POWER_NOTE}. Единица — номер; группа — единица распределения.")
    print(f"Допущение: {n(NUMBERS_PER_GROUP, 1)} номера на группу, сходство внутри группы ICC 0,1 "
          "(поправка ×1,14).")
    print("\n| Групп всего | Номеров на вариант | Различимый эффект |")
    print("|---|---|---|")
    for groups_total in (300, 600, 900, 1558):
        per_arm = groups_total * NUMBERS_PER_GROUP / 2
        p2 = detectable(12, per_arm)
        print(f"| {n(groups_total)} | {n(per_arm)} | 12 % → {n(p2, 1)} % "
              f"({n(12 - p2, 1)} п.п.) |")
    print("\nДля эффекта 3 п.п. нужно 1 638 номеров на вариант (3 276 номеров); "
          "с поправкой на группы — 1 868 на вариант, то есть 1 558 групп всего.")
    per_week_groups = BASE["groups_est"] * EVENT_RATE_PER_QUARTER / WEEKS_PER_QUARTER
    print(f"При потоке {n(per_week_groups)} групп/нед. набор 1 558 групп займёт "
          f"{n(1558 / per_week_groups, 1)} недель; 600 групп — {n(600 / per_week_groups, 1)} недель.")
    print(f"За {WEEKS_ENROLL} недель доступно {n(per_week_groups * WEEKS_ENROLL)} групп — "
          "этого хватает на 600, но не на 1 558.")
    print("Важно: 25 % групп за квартал — это распространённость события; доля впервые подходящих "
          "меньше (U1), поэтому поток может оказаться ниже.")

    section("9. Предел вложений")
    print("Если годовой вклад = C, при окупаемости за 12 месяцев без дисконтирования "
          "предельные вложения примерно равны C.")
    for contribution in (0.3, 1, 3, 6):
        print(f"- вклад {contribution} млн руб./год → вложения до {contribution} млн руб.")
    print("\nСтоимость разработки в брифе не дана (U4); пока сметы нет, порог «окупается» "
          "нельзя считать проверенным.")

    section("10. Сценарии, а не прогноз")
    print("Удлинение оплаты — неизвестная (U3). Ниже — сценарии, а не оценка эффекта:")
    for extra in (0.3, 0.5, 0.75):
        print(f"- {n(extra, 1)} мес./номер → {mln(numbers_nochange * extra * RATE_PER_PAID_MONTH / 1e6)}; "
              f"цель 6 млн закрывается: {'да' if numbers_nochange * extra * RATE_PER_PAID_MONTH / 1e6 >= 6 else 'нет'}.")
    print("Верхний сценарий (0,75) — это пересчёт требования при 2,0 номера на группу, "
          "а не независимая оценка. Поэтому «цель закрывается» здесь не доказательство.")


def detectable(base_pct, per_arm):
    """Наименьшая различимая доля (в %) при данной выборке — обратная к pilot_size.py."""
    z = 1.959964 + 0.841621  # alpha 0.05 двусторонняя, power 0.8
    p1 = base_pct / 100
    lo, hi = 0.0, base_pct
    for _ in range(100):
        mid = (lo + hi) / 2
        p2 = mid / 100
        # требуемая выборка на вариант для эффекта p1 → p2
        need = z ** 2 * (p1 * (1 - p1) + p2 * (1 - p2)) / (p1 - p2) ** 2
        if need <= per_arm:
            lo = mid          # такой эффект различим при этой выборке
        else:
            hi = mid          # эффект слишком мал для такой выборки
    return lo


if __name__ == "__main__":
    main()
