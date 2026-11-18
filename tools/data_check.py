#!/usr/bin/env python3
"""Паспорт таблицы: проверка данных перед анализом.

Только стандартная библиотека. Читает CSV/TSV и XLSX.

  python tools/data_check.py <файл> [--sheet ИМЯ] [--header N] [--dump] [--out путь.md]

  --header N  номер строки заголовков (1 — первая строка файла); по умолчанию ищется сам
  --dump      вместо паспорта — весь лист markdown-таблицей с адресами ячеек (для прайсов,
              форм и «табличных документов», на которые нужно ссылаться: «S3, лист 1, B4»)

Для каждого столбца: заполненность, тип, диапазон, уникальные значения, период (для дат),
и предупреждения: пропуски, смешанные типы, дубли строк, десятичная запятая, проценты > 100,
отрицательные значения в суммах, выбросы. Результат — markdown, который агент сохраняет
в projects/<slug>/analysis/ и на который ссылается в sources.md (комментарий) и facts.md.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import re
import statistics
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
DATE_HINT = re.compile(r"дата|date|день|day|месяц|month|период|period|time|время", re.I)
MONEY_HINT = re.compile(r"руб|₽|rub|usd|\$|сумм|выручк|доход|revenue|price|цен|стоим|cost|arpu", re.I)
PCT_HINT = re.compile(r"%|доля|процент|percent|rate|конверс|удержан|retention|churn|отток", re.I)
DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y", "%Y-%m", "%m/%d/%Y", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S", "%d.%m.%Y %H:%M")
PHONE = re.compile(r"(?:\+7|\b8)[\s\-(]*\d{3}[\s\-)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}\b")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
MONEY_IN_TEXT = re.compile(r"^(?:от|до)?\s*\d[\d\s]*(?:[.,]\d+)?\s*(?:р|руб|₽)\.?\s*(?:/\s*\S+)?$", re.I)
MISSING = {"", "-", "—", "n/a", "na", "nan", "null", "none", "#н/д", "#n/a"}


# ---------- чтение ----------

def read_csv(path: Path) -> list[list[str]]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1251"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
    sample = text[:5000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return [row for row in csv.reader(io.StringIO(text), dialect)]


def col_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref).group(0)
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_xlsx(path: Path, sheet: str | None) -> tuple[list[list[str]], list[str]]:
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        targets = {r.get("Id"): r.get("Target") for r in rels}
        sheets = [(s.get("name"), targets[s.get(REL_NS)]) for s in wb.find("m:sheets", NS)]
        names = [n for n, _ in sheets]
        chosen = next((s for s in sheets if s[0] == sheet), None) if sheet else sheets[0]
        if chosen is None:
            sys.exit(f"Лист «{sheet}» не найден. Листы: {', '.join(names)}")
        names = [chosen[0]] + [n for n in names if n != chosen[0]]
        target = chosen[1].lstrip("/")
        target = target if target.startswith("xl/") else "xl/" + target
        root = ET.fromstring(z.read(target))
    rows = []
    for r in root.iter(f"{{{NS['m']}}}row"):
        cells = {}
        for c in r.findall("m:c", NS):
            t = c.get("t")
            v = c.find("m:v", NS)
            if t == "s" and v is not None:
                val = shared[int(v.text)]
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(f"{{{NS['m']}}}t"))
            else:
                val = v.text if v is not None else ""
            cells[col_index(c.get("r"))] = val or ""
        if cells:
            width = max(cells) + 1
            rows.append([cells.get(i, "") for i in range(width)])
    return rows, names


# ---------- типы ----------

def to_number(s: str):
    t = s.strip().replace(" ", "").replace(" ", "")
    t = re.sub(r"[₽$€%]|руб\.?", "", t, flags=re.I)
    comma = False
    if re.fullmatch(r"-?\d+,\d+", t):
        t, comma = t.replace(",", "."), True
    try:
        return float(t), comma
    except ValueError:
        return None, False


def to_date(s: str, header: str):
    t = s.strip()
    for f in DATE_FORMATS:
        try:
            return dt.datetime.strptime(t, f).date()
        except ValueError:
            pass
    if DATE_HINT.search(header):
        n, _ = to_number(t)
        if n is not None and 20000 < n < 60000:  # серийная дата Excel
            return dt.date(1899, 12, 30) + dt.timedelta(days=int(n))
    return None


def safe(s: str, limit: int = 0) -> str:
    """Ячейка для markdown: без переводов строк и «|», телефоны и e-mail скрыты."""
    s = re.sub(r"[\s\u2028\u2029]+", " ", s).strip()
    s = EMAIL.sub("[e-mail скрыт]", PHONE.sub("[телефон скрыт]", s)).replace("|", "/")
    return s[:limit] + "…" if limit and len(s) > limit else s


def col_letter(i: int) -> str:
    out = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        out = chr(65 + r) + out
    return out


def prepare(rows: list[list[str]]) -> tuple[list[list[str]], list[int], dict]:
    """Убрать полностью пустые столбцы; вернуть строки, исходные номера столбцов и сведения о форме листа."""
    used = sorted({i for r in rows for i, c in enumerate(r) if c.strip()})
    total = max((len(r) for r in rows), default=0)
    slim = [[r[i] if i < len(r) else "" for i in used] for r in rows]
    blocks, in_block = 0, False
    for r in slim:
        filled = any(c.strip() for c in r)
        blocks += filled and not in_block
        in_block = filled
    nonempty = [r for r in slim if any(c.strip() for c in r)]
    single = sum(1 for r in nonempty if sum(1 for c in r if c.strip()) == 1)
    pii = any(PHONE.search(c) or EMAIL.search(c) for r in rows for c in r)
    return slim, used, {"dropped": total - len(used), "blocks": blocks, "single": single,
                        "rows": len(nonempty), "pii": pii}


def find_header(rows: list[list[str]]) -> int:
    """Индекс строки заголовков: первая строка, заполненная хотя бы наполовину от самой полной (среди первых 20)."""
    counts = [sum(1 for c in r if c.strip()) for r in rows[:20]]
    if not counts:
        return 0
    need = max(2, -(-max(counts) // 2))
    return next((i for i, k in enumerate(counts) if k >= need), 0)


def dump(rows: list[list[str]], name: str) -> str:
    slim, used, info = prepare(rows)
    out = [f"# Лист целиком: {name}", "",
           f"Непустых строк: {info['rows']}, столбцов с данными: {len(used)}. Адреса ячеек — как в Excel. "
           f"Сформировано: {dt.datetime.now():%Y-%m-%d %H:%M} (`tools/data_check.py --dump`).", ""]
    if info["pii"]:
        out += ["> Телефоны и e-mail в выводе скрыты.", ""]
    out += ["| Строка | " + " | ".join(col_letter(i) for i in used) + " |", "|---|" + "---|" * len(used)]
    for n, r in enumerate(slim, 1):
        if any(c.strip() for c in r):
            out.append(f"| {n} | " + " | ".join(safe(c) for c in r) + " |")
    return "\n".join(out) + "\n"


def fmt(x: float) -> str:
    s = f"{x:,.0f}" if abs(x) >= 1000 or x == int(x) else f"{x:,.4g}"
    return s.replace(",", " ")


def profile(rows: list[list[str]], name: str, header_row: int | None = None) -> str:
    slim, used, info = prepare(rows)
    numbered = [(n, r) for n, r in enumerate(slim, 1) if any(c.strip() for c in r)]
    if not numbered:
        return f"# Паспорт данных: {name}\n\nТаблица пуста.\n"
    if header_row is not None:
        h_idx = next((i for i, (n, _) in enumerate(numbered) if n >= header_row), 0)
    else:
        h_idx = find_header([r for _, r in numbered])
    above = numbered[:h_idx]
    h_num, h_cells = numbered[h_idx]
    rows = [r for _, r in numbered[h_idx:]]
    header = [safe(h) or f"столбец_{col_letter(used[i])}" for i, h in enumerate(h_cells)]
    seen_h: dict[str, int] = {}
    for i, h in enumerate(header):
        seen_h[h] = seen_h.get(h, 0) + 1
        if seen_h[h] > 1:
            header[i] = f"{h} ({col_letter(used[i])})"
    data = rows[1:]
    width = max(len(header), max((len(r) for r in data), default=0))
    header += [f"столбец_{i + 1}" for i in range(len(header), width)]
    data = [r + [""] * (width - len(r)) for r in data]
    n = len(data)

    out = [f"# Паспорт данных: {name}", "",
           f"Строк данных: **{n}**, столбцов: **{width}**, заголовки — строка {h_num} файла. "
           f"Сформировано: {dt.datetime.now():%Y-%m-%d %H:%M} (`tools/data_check.py`).", ""]
    warnings = []
    if info["dropped"]:
        warnings.append(f"Пустых столбцов отброшено: {info['dropped']} (форматирование листа без данных)")
    if above:
        warnings.append(f"Над заголовками {len(above)} строк(и) — шапка или пояснения: "
                        + "; ".join(f"стр. {num}: «{safe(' '.join(c for c in r if c.strip()), 60)}»"
                                    for num, r in above[:3])
                        + ". Если заголовки определены неверно — укажите --header N")
    if info["blocks"] >= 3 or (info["rows"] >= 6 and info["single"] / info["rows"] > 0.3):
        warnings.append(f"Похоже на документ в виде таблицы (прайс, форма, отчёт), а не на таблицу данных: "
                        f"блоков через пустые строки — {info['blocks']}, строк с одной ячейкой — "
                        f"{info['single']} из {info['rows']}. Паспорт по столбцам здесь ненадёжен: "
                        "выгрузите лист целиком (--dump) и ссылайтесь на адреса ячеек")
    if info["pii"]:
        warnings.append("В таблице есть телефоны или e-mail — в паспорте они скрыты; "
                        "в рабочие файлы проекта не переносить")

    seen, dups = set(), 0
    for r in data:
        key = tuple(c.strip() for c in r)
        dups += key in seen
        seen.add(key)
    if dups:
        warnings.append(f"Полных дублей строк: {dups}")

    out += ["| Столбец | Заполнено | Тип | Диапазон / значения | Уникальных |", "|---|---|---|---|---|"]
    for i, h in enumerate(header):
        vals = [r[i].strip() for r in data]
        present = [v for v in vals if v.lower() not in MISSING]
        miss = n - len(present)
        nums, dates, comma_cnt = [], [], 0
        for v in present:
            d = to_date(v, h)
            if d and not re.fullmatch(r"-?\d+([.,]\d+)?", v) or (d and DATE_HINT.search(h)):
                dates.append(d)
                continue
            x, comma = to_number(v)
            if x is not None:
                nums.append(x)
                comma_cnt += comma
        k = len(present)
        if k and len(dates) >= 0.8 * k:
            typ = "дата"
            rng = f"{min(dates)} … {max(dates)}"
        elif k and len(nums) >= 0.8 * k:
            typ = "число"
            mean = statistics.fmean(nums)
            rng = f"{fmt(min(nums))} … {fmt(max(nums))}, среднее {fmt(mean)}"
            if len(nums) >= 8:
                q = statistics.quantiles(nums, n=4)
                iqr = q[2] - q[0]
                outl = [x for x in nums if iqr and (x < q[0] - 3 * iqr or x > q[2] + 3 * iqr)]
                if outl:
                    warnings.append(f"«{h}»: {len(outl)} сильных выброса(ов), напр. {fmt(outl[0])}")
            if PCT_HINT.search(h) and max(nums) > 100:
                warnings.append(f"«{h}»: похоже на долю/процент, но есть значения > 100 — проверить единицы")
            if PCT_HINT.search(h) and 0 <= min(nums) and max(nums) <= 1:
                warnings.append(f"«{h}»: значения 0…1 — это доли, не проценты")
            if MONEY_HINT.search(h) and min(nums) < 0:
                warnings.append(f"«{h}»: отрицательные суммы — возвраты или ошибка?")
            if comma_cnt:
                warnings.append(f"«{h}»: десятичная запятая в {comma_cnt} значениях (прочитано корректно)")
        else:
            typ = "текст"
            money = [v for v in present if MONEY_IN_TEXT.match(v)]
            if len(money) >= max(2, 0.3 * k):
                units = sorted({re.sub(r"^[^/]*", "", v).strip() or "р. без «за что»" for v in money})
                warnings.append(f"«{h}»: {len(money)} сумм записаны текстом с единицами ("
                                + ", ".join(units[:5]) + ") — перед расчётами привести к числу и одной единице")
            freq = {}
            for v in present:
                freq[v] = freq.get(v, 0) + 1
            top = sorted(freq.items(), key=lambda x: -x[1])[:3]
            rng = "; ".join(f"{safe(v, 25)} ({c})" for v, c in top)
        mixed = k - len(nums) - len(dates) if typ != "текст" else 0
        if mixed > 0:
            warnings.append(f"«{h}»: {mixed} значений не того типа ({typ}) — смешанные данные")
        if miss:
            warnings.append(f"«{h}»: пропусков {miss} из {n} ({miss / n:.0%})")
        unit = ""
        if MONEY_HINT.search(h):
            unit = " · деньги?"
        elif PCT_HINT.search(h):
            unit = " · доля?"
        out.append(f"| {h} | {k}/{n} | {typ}{unit} | {safe(rng)} | {len(set(present))} |")

    out += ["", "## Предупреждения", ""]
    out += [f"- {w}" for w in warnings] or ["- нет"]
    out += ["", "## Перед выводами проверить", "",
            "- Единицы измерения (рубли/тыс., с НДС или без, доли или проценты).",
            "- Период и что значит одна строка (день, пользователь, заказ).",
            "- Не менялась ли методика подсчёта внутри периода.", ""]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="Паспорт таблицы: проверка перед анализом")
    ap.add_argument("file")
    ap.add_argument("--sheet")
    ap.add_argument("--header", type=int, help="номер строки заголовков (с 1)")
    ap.add_argument("--dump", action="store_true", help="весь лист markdown-таблицей с адресами ячеек")
    ap.add_argument("--out")
    a = ap.parse_args()
    path = Path(a.file)
    if not path.exists():
        sys.exit(f"Файл не найден: {path}")
    name = path.name
    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        rows, sheets = read_xlsx(path, a.sheet)
        name += f" — лист «{sheets[0]}» (листы: {', '.join(sorted(sheets, key=lambda x: x != sheets[0]))})"
    elif path.suffix.lower() in {".csv", ".tsv", ".txt"}:
        rows = read_csv(path)
    else:
        sys.exit("Поддерживаются CSV/TSV/TXT и XLSX. Старый .xls сохраните как .xlsx или CSV.")
    report = dump(rows, name) if a.dump else profile(rows, name, a.header)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(report, encoding="utf-8")
        print(f"Паспорт сохранён: {a.out}")
    else:
        print(report)


if __name__ == "__main__":
    main()
