#!/usr/bin/env python3
"""Стратег продуктов — CLI для проектов агента.

Только стандартная библиотека Python 3.9+. Зависимостей нет.

Команды:
  new <slug> [--title T]          создать проект из шаблонов
  list                            список проектов
  status <slug>                   где остановились, журнал, затраты, проверки
  set <slug> [--step N] [--status S] [--next TEXT]   обновить state.md
  check <slug> [--step N]         проверить критерии готовности шагов 1..N
  log <slug> "текст" [--step N]   добавить запись в журнал
  cost <slug> add --step N --what T [--model M] [--tokens N] [--usd X] [--human-min N]
  cost <slug> summary             итог затрат
  report <slug>                   собрать report.md для руководителя
  impact <slug> <ID> [--mark П] [--status устарел|отозван|действует]
                                  что зависит от S…/F…/O…; --status меняет статус факта в facts.md
  metrics <slug>                  показатели качества проекта (для сравнения и отчёта)
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
PROJECTS = Path(os.environ.get("STRATEGIST_PROJECTS", ROOT / "projects"))

STEPS = {
    1: "Постановка задачи",
    2: "Сбор материалов",
    3: "Исследование",
    4: "Варианты",
    5: "Стратегия",
    6: "План проверки и итог",
}
CLAIM_TYPES = {"факт", "вывод", "гипотеза", "прогноз", "синтетика"}
FACT_STATUSES = ("действует", "устарел", "отозван")
INACTIVE_DECISIONS = ("пересмотр", "отмен", "заменен", "заменён")
COMMENT = re.compile(r"<!--.*?-->", re.S)
RANGE = re.compile(r"\b([FSOUQD])(\d+)\s*(?:–|—|-|…|\.\.\.?)\s*\1?(\d+)\b")


def expand_ranges(text: str) -> str:
    """«F1–F4» / «F1-4» → «F1, F2, F3, F4», чтобы проверялись все номера диапазона."""
    def repl(m):
        a, b = int(m.group(2)), int(m.group(3))
        if b <= a or b - a > 50:
            return m.group(0)
        return ", ".join(f"{m.group(1)}{i}" for i in range(a, b + 1))
    return RANGE.sub(repl, text)


class _Refs:
    """Поиск ссылок с учётом диапазонов: F_REF.findall("F1–F3") → [F1, F2, F3]."""
    def __init__(self, letter: str):
        self.rx = re.compile(rf"\b{letter}\d+\b")

    def findall(self, text: str) -> list[str]:
        return self.rx.findall(expand_ranges(text or ""))

    def search(self, text: str):
        return self.rx.search(expand_ranges(text or ""))


F_REF = _Refs("F")
S_REF = _Refs("S")
U_REF = _Refs("U")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ---------- утилиты ----------

def now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def today() -> str:
    return dt.date.today().isoformat()


def project_dir(slug: str) -> Path:
    p = PROJECTS / slug
    if not p.is_dir():
        sys.exit(f"Проект не найден: {p}. Создайте: python tools/strategist.py new {slug}")
    return p


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def clean(text: str) -> str:
    return COMMENT.sub("", text)


def is_sep(line: str) -> bool:
    return bool(re.fullmatch(r"\|[\s:\-|]+\|", line.strip()))


def split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_tables(text: str) -> list[list[dict]]:
    """Все таблицы в тексте -> список таблиц, каждая — список строк-словарей."""
    tables, block = [], []
    for line in clean(text).splitlines() + [""]:
        if line.strip().startswith("|"):
            block.append(line)
            continue
        if len(block) >= 2 and is_sep(block[1]):
            header = split_row(block[0])
            rows = []
            for raw in block[2:]:
                cells = split_row(raw)
                if not any(cells):
                    continue
                rows.append({h: (cells[i] if i < len(cells) else "") for i, h in enumerate(header)})
            tables.append(rows)
        block = []
    return tables


def first_table(text: str) -> list[dict]:
    t = parse_tables(text)
    return t[0] if t else []


def sections(text: str, level: int = 2) -> dict[str, str]:
    """Заголовки уровня level -> содержимое до следующего такого же заголовка."""
    mark = "#" * level + " "
    out, name, buf = {}, None, []
    for line in clean(text).splitlines():
        if line.startswith(mark):
            if name is not None:
                out[name] = "\n".join(buf)
            name, buf = line[len(mark):].strip(), []
        elif name is not None:
            buf.append(line)
    if name is not None:
        out[name] = "\n".join(buf)
    return out


def has_content(body: str) -> bool:
    """Есть ли в разделе что-то кроме пустых таблиц и пустых маркеров."""
    for line in clean(body).splitlines():
        s = line.strip()
        if not s or s.startswith("|") or s.startswith("#"):
            continue
        if re.fullmatch(r"[-*]\s*", s) or re.fullmatch(r"\*\*[^*]+:\*\*\s*", s):
            continue
        return True
    return any(parse_tables(body))


def step_of(st: dict) -> int:
    """Номер шага из state.md; терпит «3», «шаг 3», «3 из 6»; иначе 1."""
    m = re.search(r"[1-6]", str(st.get("step", "")))
    return int(m.group(0)) if m else 1


def read_state(p: Path) -> dict:
    st = {}
    for line in read(p / "state.md").splitlines():
        if line.startswith("## "):
            break
        m = re.match(r"^(\w+):\s*(.*)$", line)
        if m:
            st[m.group(1)] = m.group(2).strip()
    return st


def write_state(p: Path, updates: dict) -> None:
    path = p / "state.md"
    lines = read(path).splitlines()
    seen = set()
    for i, line in enumerate(lines):
        if line.startswith("## "):
            break
        m = re.match(r"^(\w+):", line)
        if m and m.group(1) in updates:
            lines[i] = f"{m.group(1)}: {updates[m.group(1)]}"
            seen.add(m.group(1))
    for k, v in updates.items():
        if k not in seen:
            lines.insert(0, f"{k}: {v}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------- проверки шагов ----------

class Result:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def err(self, msg):
        self.errors.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)


def require_sections(r: Result, fname: str, text: str, names: list[str]) -> dict:
    secs = sections(text)
    if not text:
        r.err(f"{fname}: файл отсутствует")
        return secs
    for n in names:
        if n not in secs:
            r.err(f"{fname}: нет раздела «{n}»")
        elif not has_content(secs[n]):
            r.err(f"{fname}: раздел «{n}» пуст")
    return secs


def facts_index(p: Path) -> dict[str, dict]:
    return {row.get("ID", ""): row for row in first_table(read(p / "facts.md")) if row.get("ID")}


def sources_index(p: Path) -> dict[str, dict]:
    return {row.get("ID", ""): row for row in first_table(read(p / "sources.md")) if row.get("ID")}


def fact_status(row: dict) -> str:
    """Статус утверждения: «действует», если колонки нет или она пуста (старые проекты не ломаются)."""
    raw = (row.get("Статус") or "").strip().lower()
    if not raw:
        return "действует"
    word = re.split(r"[\s:;,(]", raw, maxsplit=1)[0]
    return word if word in FACT_STATUSES else raw


def stale_roots(facts: dict, fid: str, seen: set | None = None) -> dict[str, str]:
    """Неактуальные (устарел/отозван) утверждения, на которые fid опирается цепочкой: {ID: статус}."""
    seen = seen if seen is not None else set()
    if fid in seen or fid not in facts:
        return {}
    seen.add(fid)
    out = {}
    for ref in F_REF.findall(facts[fid].get("Основание", "")):
        if ref in facts and fact_status(facts[ref]) in ("устарел", "отозван"):
            out[ref] = fact_status(facts[ref])
        out.update(stale_roots(facts, ref, seen))
    return out


def unknowns_index(p: Path) -> dict[str, dict]:
    return {row.get("ID", ""): row for row in first_table(read(p / "unknowns.md")) if row.get("ID")}


def bullets(body: str) -> list[str]:
    return [m.group(1).strip() for m in re.finditer(r"^\s*(?:[-*]|\d+[.)])\s+(\S.*)$", clean(body), re.M)]


def check_unknown_refs(r: Result, fname: str, text: str, unknowns: dict) -> set[str]:
    refs = set(U_REF.findall(clean(text)))
    for u in sorted(refs - set(unknowns), key=lambda x: int(x[1:])):
        r.err(f"{fname}: ссылка на несуществующий пробел {u}")
    return refs


def synthetic_roots(facts: dict, fid: str, seen: set | None = None) -> set[str]:
    """Синтетические утверждения, на которые fid опирается прямо или цепочкой."""
    seen = seen if seen is not None else set()
    if fid in seen or fid not in facts:
        return set()
    seen.add(fid)
    row = facts[fid]
    if row.get("Тип", "").lower() == "синтетика":
        return {fid}
    out = set()
    for ref in F_REF.findall(row.get("Основание", "")):
        out |= synthetic_roots(facts, ref, seen)
    return out


def check_status(r: Result, fname: str, ref: str, facts: dict, soft: bool = False) -> None:
    """Ссылка на отозванное — ошибка, на устаревшее — предупреждение; soft — всё как предупреждение."""
    status = fact_status(facts[ref])
    if status == "отозван":
        (r.warn if soft else r.err)(f"{fname}: ссылка на отозванный {ref} — уберите или замените на действующий факт")
    elif status == "устарел":
        r.warn(f"{fname}: ссылка на устаревший {ref} — проверьте, держится ли вывод, или обновите факт")
    else:
        bad = stale_roots(facts, ref)
        if bad:
            r.warn(f"{fname}: {ref} опирается на неактуальные " + ", ".join(f"{k} ({v})" for k, v in sorted(bad.items()))
                   + " — пересмотрите его")


def check_refs(r: Result, fname: str, text: str, facts: dict, forbid_synthetic: bool = False,
               soft_status: bool = False) -> None:
    for ref in sorted(set(F_REF.findall(clean(text))), key=lambda x: int(x[1:])):
        if ref not in facts:
            r.err(f"{fname}: ссылка на несуществующий факт {ref}")
            continue
        check_status(r, fname, ref, facts, soft_status)
        if not forbid_synthetic:
            continue
        typ = facts[ref].get("Тип", "").lower()
        roots = synthetic_roots(facts, ref)
        if typ == "синтетика":
            r.err(f"{fname}: {ref} — синтетика; она не может быть доказательством")
        elif roots and typ != "гипотеза":
            r.err(f"{fname}: {ref} ({typ}) опирается на синтетику {', '.join(sorted(roots))} — "
                  "это не доказательство; смените тип на «гипотеза» или найдите реальный источник")


def gate1(p: Path, r: Result) -> None:
    text = read(p / "brief.md")
    secs = require_sections(r, "brief.md", text, [
        "Вопрос", "Решение, которое будет принято", "Цель и критерий успеха",
        "Получатель результата", "Ограничения", "Доступные данные", "Согласование",
    ])
    if "Согласование" in secs and "согласовано" not in secs["Согласование"].lower():
        r.err("brief.md: бриф не согласован человеком (нужна строка «согласовано: …»)")


def gate2(p: Path, r: Result) -> None:
    rows = first_table(read(p / "sources.md"))
    if not rows:
        r.err("sources.md: нет ни одного источника")
    for row in rows:
        sid = row.get("ID", "")
        if not re.fullmatch(r"S\d+", sid):
            r.err(f"sources.md: неверный ID «{sid}» (нужно S1, S2…)")
        if not row.get("Ссылка или путь"):
            r.err(f"sources.md: {sid} без ссылки или пути")
        if not row.get("Дата"):
            r.warn(f"sources.md: {sid} без даты")
        if row.get("Надёжность", "").lower() not in {"высокая", "средняя", "низкая"}:
            r.warn(f"sources.md: {sid} — надёжность не указана (высокая/средняя/низкая)")
        if "projects/" in row.get("Ссылка или путь", "") and "materials/" not in row.get("Ссылка или путь", ""):
            r.err(f"sources.md: {sid} ссылается на рабочий файл агента — это не источник")


def gate3(p: Path, r: Result) -> None:
    plan = first_table(read(p / "research-plan.md"))
    if not plan:
        r.err("research-plan.md: нет вопросов исследования")
    for row in plan:
        if not row.get("Как ответ меняет решение"):
            r.err(f"research-plan.md: {row.get('ID', '?')} — не указано, как ответ меняет решение")
    facts = facts_index(p)
    sources = sources_index(p)
    if not facts:
        r.err("facts.md: нет ни одного утверждения")
    for fid, row in facts.items():
        typ = row.get("Тип", "").lower()
        basis = row.get("Основание", "")
        if not re.fullmatch(r"F\d+", fid):
            r.err(f"facts.md: неверный ID «{fid}»")
        if typ not in CLAIM_TYPES:
            r.err(f"facts.md: {fid} — неизвестный тип «{typ}» (допустимо: {', '.join(sorted(CLAIM_TYPES))})")
        status = fact_status(row)
        if status not in FACT_STATUSES:
            r.err(f"facts.md: {fid} — неизвестный статус «{status}» (допустимо: {', '.join(FACT_STATUSES)})")
        elif status == "действует":
            bad = stale_roots(facts, fid)
            if bad:
                r.warn(f"facts.md: {fid} действует, но опирается на неактуальные "
                       + ", ".join(f"{k} ({v})" for k, v in sorted(bad.items())) + " — пересмотрите")
        if typ == "факт":
            refs = S_REF.findall(basis)
            if not refs:
                r.err(f"facts.md: {fid} — факт без источника S…")
            for s in refs:
                if s not in sources:
                    r.err(f"facts.md: {fid} ссылается на несуществующий источник {s}")
        if typ == "вывод":
            refs = F_REF.findall(basis)
            if not refs:
                r.err(f"facts.md: {fid} — вывод без ссылок на F…")
            for f in refs:
                if f not in facts:
                    r.err(f"facts.md: {fid} ссылается на несуществующий {f}")
        if typ in {"гипотеза", "прогноз"} and not basis:
            r.warn(f"facts.md: {fid} — {typ} без основания/допущений")
        if typ in {"факт", "вывод", "прогноз"}:
            roots = synthetic_roots(facts, fid) - {fid}
            if roots:
                r.err(f"facts.md: {fid} ({typ}) опирается на синтетику {', '.join(sorted(roots))} — "
                      "из ответов ИИ-респондентов получаются только гипотезы; смените тип на «гипотеза»")
    unknowns = unknowns_index(p)
    if not unknowns:
        r.err("unknowns.md: пробелы не названы")
    situation_check(p, r, facts, unknowns)


SITUATION_SECTIONS = ["Главное", "Что известно", "На чём держится решение", "Что спорно", "Чего не знаем"]


def situation_check(p: Path, r: Result, facts: dict, unknowns: dict) -> None:
    """Картина ситуации: читаемая страница поверх реестров, без новых сведений и без синтетики как знания."""
    text = read(p / "situation.md")
    secs = require_sections(r, "situation.md", text, SITUATION_SECTIONS)
    if not text:
        return
    check_refs(r, "situation.md", text, facts, forbid_synthetic=True)
    known = secs.get("Что известно", "")
    known_refs = F_REF.findall(known)
    if has_content(known) and not known_refs:
        r.err("situation.md: в «Что известно» нет ссылок на реестр [F…]")
    for ref in sorted(set(known_refs), key=lambda x: int(x[1:])):
        typ = facts.get(ref, {}).get("Тип", "").lower()
        if typ in {"гипотеза", "прогноз"}:
            r.warn(f"situation.md: {ref} — {typ}, а стоит в «Что известно»; перенесите в «Что спорно»")
    for name in ("Что известно", "На чём держится решение"):
        for b in bullets(secs.get(name, "")):
            if not F_REF.search(b):
                r.warn(f"situation.md: «{name}» — пункт без ссылки [F…]: «{b[:60]}»")
    mentioned = check_unknown_refs(r, "situation.md", text, unknowns)
    critical = [u for u, row in unknowns.items() if "высок" in row.get("Влияние на решение", "").lower()]
    missing = [u for u in critical if u not in mentioned]
    if missing:
        r.warn(f"situation.md: критичные пробелы не упомянуты в «Чего не знаем»: {', '.join(missing)}")


def gate4(p: Path, r: Result) -> None:
    text = read(p / "options.md")
    facts = facts_index(p)
    secs = sections(text)
    opts = {k: v for k, v in secs.items() if re.match(r"O\d+\b", k)}
    filled = {}
    for name, body in opts.items():
        sub = sections(body, 3)
        if not has_content(body.split("###")[0]) and not any(has_content(v) for v in sub.values()):
            continue  # пустая заготовка из шаблона
        filled[name] = (body, sub)
    if len(filled) < 3:
        r.err(f"options.md: заполнено вариантов {len(filled)}, нужно не меньше 3")
    if not any("[статус-кво]" in n.lower() for n in filled):
        r.err("options.md: нет варианта «оставить как есть» с пометкой [статус-кво]")
    if not any("[закрыть]" in n.lower() for n in filled):
        r.err("options.md: нет варианта «закрыть / свернуть направление» с пометкой [закрыть] — "
              "он обязателен; если он заведомо слабый, так и покажите в возражениях")
    if len([n for n in filled if "[статус-кво]" not in n.lower() and "[закрыть]" not in n.lower()]) < 1:
        r.err("options.md: кроме «оставить как есть» и «закрыть» нужен хотя бы один вариант изменений")
    for name, (body, sub) in filled.items():
        oid = name.split(".")[0]
        title = re.sub(r"^O\d+\.?", "", name).strip()
        if not title:
            r.warn(f"options.md: {oid} без названия")
        obj = sub.get("Возражения", "")
        if not re.search(r"^\s*[-*]\s+\S", obj, re.M):
            r.err(f"options.md: {oid} — нет возражений")
        if not F_REF.search(sub.get("Опора", "")):
            r.warn(f"options.md: {oid} — в «Опоре» нет ссылок на факты")
    check_refs(r, "options.md", text, facts, forbid_synthetic=True)
    choice = secs.get("Выбор", "")
    m = re.search(r"Выбрано человеком:\s*(O\d+)", choice)
    if not m:
        r.err("options.md: человек ещё не выбрал вариант (строка «Выбрано человеком: O…»)")
    elif not any(n.startswith(m.group(1) + ".") or n.startswith(m.group(1) + " ") for n in filled):
        r.err(f"options.md: выбран {m.group(1)}, но такого заполненного варианта нет")


def gate5(p: Path, r: Result) -> None:
    facts = facts_index(p)
    ps = read(p / "product-strategy.md")
    require_sections(r, "product-strategy.md", ps, [
        "Аудитория и её задача", "Альтернативы", "Выбранное направление",
        "Осознанные отказы", "Экономика: польза, затраты, доходы",
    ])
    check_refs(r, "product-strategy.md", ps, facts, forbid_synthetic=True)
    cs = read(p / "comms-strategy.md")
    secs = require_sections(r, "comms-strategy.md", cs, [
        "Аудитория и ситуация", "Барьер понимания или выбора", "Ключевое сообщение",
        "Подтверждение сообщения", "Роль каналов", "Проверка обещаний",
    ])
    check_refs(r, "comms-strategy.md", cs, facts, forbid_synthetic=True)
    promises = first_table(secs.get("Проверка обещаний", ""))
    if not promises:
        r.err("comms-strategy.md: таблица «Проверка обещаний» пуста")
    for row in promises:
        promise = row.get("Обещание", "?")
        if row.get("Чем выполняется в продукте", "").strip("—-– ") == "":
            r.err(f"comms-strategy.md: обещание «{promise}» не подкреплено продуктом")
        if "не подтвержд" in row.get("Статус", "").lower():
            r.err(f"comms-strategy.md: обещание «{promise}» не подтверждено — убрать или переформулировать")


def gate6(p: Path, r: Result) -> None:
    require_sections(r, "validation-plan.md", read(p / "validation-plan.md"), [
        "Ожидаемое изменение", "С чем сравниваем", "Срок наблюдения",
        "Нежелательные последствия", "Критерий решения", "Связь с пробелами",
    ])
    dec = first_table(read(p / "decisions.md"))
    if not dec:
        r.err("decisions.md: решение не записано")
    for row in dec:
        if not row.get("Основания"):
            r.err(f"decisions.md: {row.get('ID', '?')} без оснований")
        if not row.get("Условие пересмотра"):
            r.err(f"decisions.md: {row.get('ID', '?')} без условия пересмотра")
    facts = facts_index(p)
    for row in dec:
        did = row.get("ID", "?")
        inactive = any(k in row.get("Статус", "").lower() for k in INACTIVE_DECISIONS)
        refs = [f for f in F_REF.findall(row.get("Основания", "")) if f in facts]
        revoked = [f for f in refs if fact_status(facts[f]) == "отозван"]
        if revoked and not inactive:
            r.err(f"decisions.md: {did} опирается на отозванные {', '.join(revoked)} — "
                  "поставьте статус «пересмотреть» и обсудите с человеком")
    check_refs(r, "decisions.md", read(p / "decisions.md"), facts, soft_status=True)
    summary_check(p, r)
    deliverable_check(p, r)


FORMAT_SECTION = "Формат результата"
STOPWORDS = {"и", "в", "во", "на", "по", "для", "с", "со", "к", "о", "об", "от", "до", "из", "за", "а", "или", "the", "and"}


def format_parts(p: Path) -> list[str]:
    """Части итогового документа, которые задал заказчик (brief.md › «Формат результата»).
    Пусто — формат не задан («по умолчанию» или раздела нет): требуется только обычный отчёт."""
    body = sections(read(p / "brief.md")).get(FORMAT_SECTION, "")
    items = bullets(body)
    if not items or "по умолчанию" in clean(body).lower() and len(items) < 2:
        return []
    return [re.sub(r"\s+[—–-]\s.*$|:\s.*$", "", i).strip(" *«»\"") or i for i in items]


def key_words(text: str) -> set[str]:
    text = re.sub(r"^(?:часть\s*)?\d+[.)]?\s*", "", text.lower().replace("ё", "е"))
    return {w[:6] for w in re.findall(r"[a-zа-я0-9]+", text) if w not in STOPWORDS and (len(w) > 2 or w.isdigit())}


def part_matches(part: str, heading: str) -> bool:
    """Раздел соответствует части, если в заголовке есть больше половины её значимых слов (по основе)."""
    need = key_words(part)
    return bool(need) and len(need & key_words(heading)) * 2 > len(need)


def deliverable_check(p: Path, r: Result) -> None:
    """Если заказчик задал структуру результата — deliverable.md собран строго по ней (любое число частей)."""
    parts = format_parts(p)
    text = read(p / "deliverable.md")
    if not parts:
        if text and has_content(text):
            check_refs(r, "deliverable.md", text, facts_index(p), forbid_synthetic=True)
        return
    if not text or not has_content(text):
        r.err(f"deliverable.md: заказчик задал формат результата ({len(parts)} част.), а документ не собран")
        return
    secs = sections(text)
    headings = list(secs)
    used = set()
    order = []
    for i, part in enumerate(parts, 1):
        hit = next((h for h in headings if h not in used and part_matches(part, h)), None)
        if hit is None:
            r.err(f"deliverable.md: нет раздела для части {i} из {len(parts)} «{part}» (заголовок `## …`)")
            continue
        used.add(hit)
        order.append(headings.index(hit))
        if not has_content(secs[hit]):
            r.err(f"deliverable.md: раздел «{hit}» пуст")
    if order != sorted(order):
        r.warn("deliverable.md: части идут не в том порядке, что в «Формате результата»")
    check_refs(r, "deliverable.md", text, facts_index(p), forbid_synthetic=True)
    check_unknown_refs(r, "deliverable.md", text, unknowns_index(p))


SUMMARY_SECTIONS = ["Рекомендация", "Основания", "Риски", "Что решить сейчас"]


def summary_check(p: Path, r: Result) -> None:
    """Резюме на первой странице отчёта: рекомендация, 3 основания с фактами, 3 риска, что решить сейчас."""
    text = read(p / "summary.md")
    secs = require_sections(r, "summary.md", text, SUMMARY_SECTIONS)
    if not text:
        return
    check_refs(r, "summary.md", text, facts_index(p), forbid_synthetic=True)
    check_unknown_refs(r, "summary.md", text, unknowns_index(p))
    basis = bullets(secs.get("Основания", ""))
    for b in basis:
        if not F_REF.search(b):
            r.err(f"summary.md: основание без ссылки на факт [F…]: «{b[:60]}»")
    for name, items in (("Основания", basis), ("Риски", bullets(secs.get("Риски", "")))):
        if items and len(items) < 3:
            r.warn(f"summary.md: в «{name}» {len(items)} пункт(а), ожидается 3")
    m = re.search(r"Выбрано человеком:\s*(O\d+)", sections(read(p / "options.md")).get("Выбор", ""))
    if m and not re.search(rf"\b{m.group(1)}\b", secs.get("Рекомендация", "")):
        r.warn(f"summary.md: в «Рекомендации» не назван выбранный человеком вариант {m.group(1)}")


GATES = {1: gate1, 2: gate2, 3: gate3, 4: gate4, 5: gate5, 6: gate6}


def run_checks(p: Path, upto: int) -> tuple[bool, list[tuple[int, Result]]]:
    results, ok = [], True
    for n in range(1, upto + 1):
        r = Result()
        GATES[n](p, r)
        results.append((n, r))
        ok = ok and not r.errors
    return ok, results


def print_checks(results) -> None:
    for n, r in results:
        mark = "OK " if not r.errors else "ОШИБКИ"
        print(f"Шаг {n} ({STEPS[n]}): {mark}")
        for e in r.errors:
            print(f"   ✗ {e}")
        for w in r.warnings:
            print(f"   ! {w}")


# ---------- затраты ----------

def cost_rows(p: Path) -> list[dict]:
    return first_table(read(p / "cost.md"))


def num(s: str) -> float:
    s = (s or "").replace("~", "").replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def cost_summary(p: Path) -> str:
    rows = cost_rows(p)
    if not rows:
        return "Затраты не записаны."
    by_step: dict[str, list[float]] = {}
    for row in rows:
        acc = by_step.setdefault(row.get("Шаг", "?"), [0, 0, 0])
        acc[0] += num(row.get("Токены"))
        acc[1] += num(row.get("USD"))
        acc[2] += num(row.get("Минуты человека"))
    lines = ["| Шаг | Токены | USD | Минуты человека |", "|---|---|---|---|"]
    tot = [0, 0, 0]
    for step in sorted(by_step, key=lambda s: (len(s), s)):
        t, u, m = by_step[step]
        tot = [tot[0] + t, tot[1] + u, tot[2] + m]
        lines.append(f"| {step} | {int(t)} | {u:.2f} | {int(m)} |")
    lines.append(f"| **Итого** | **{int(tot[0])}** | **{tot[1]:.2f}** | **{int(tot[2])}** |")
    return "\n".join(lines)


# ---------- команды ----------

def cmd_new(a):
    if not re.fullmatch(r"[a-z0-9][a-z0-9\-]*", a.slug):
        sys.exit("slug: только латиница в нижнем регистре, цифры и дефис")
    dst = PROJECTS / a.slug
    if (dst / "state.md").exists():
        sys.exit(f"Проект уже существует: {dst}. Чтобы продолжить: python tools/strategist.py status {a.slug}")
    # Папка может уже быть: человек заранее положил материалы в projects/<slug>/materials/.
    # Тогда шаблоны дописываются рядом, а существующие файлы не трогаются.
    added = []
    for src in sorted(TEMPLATES.rglob("*")):
        rel = src.relative_to(TEMPLATES)
        target = dst / rel
        if src.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
            added.append(target)
    title = a.title or a.slug
    for f in added:
        if f.suffix == ".md":
            f.write_text(read(f).replace("{slug}", a.slug).replace("{title}", title)
                         .replace("{date}", now()), encoding="utf-8")
    append_log(dst, "Проект создан из шаблонов", 1)
    existing = [f for f in (dst / "materials").rglob("*") if f.is_file() and f.name != "README.md"]
    print(f"Создан проект: projects/{a.slug}")
    if existing:
        print(f"В materials/ уже лежит файлов: {len(existing)} — начните с них (шаг 2).")
    else:
        print(f"Материалы кладите в projects/{a.slug}/materials/")


def cmd_list(a):
    if not PROJECTS.exists():
        print("Проектов нет.")
        return
    found = False
    for d in sorted(PROJECTS.iterdir()):
        if d.is_dir() and (d / "state.md").exists():
            st = read_state(d)
            found = True
            print(f"{d.name:30} шаг {st.get('step', '?')} — {st.get('step_status', '?')} — {st.get('title', '')}")
    if not found:
        print("Проектов нет.")


def cmd_status(a):
    p = project_dir(a.slug)
    st = read_state(p)
    step = step_of(st)
    print(f"Проект: {st.get('title', a.slug)}  (projects/{a.slug})")
    print(f"Шаг {step} из 6: {STEPS.get(step, '?')} — {st.get('step_status', '?')}")
    print(f"Обновлено: {st.get('updated', '?')}")
    print(f"Следующее действие: {st.get('next_action', '—')}")
    secs = sections(read(p / "state.md"))
    for name in ("Открытые вопросы к человеку", "Помечено к пересмотру"):
        if has_content(secs.get(name, "")):
            print(f"\n{name}:\n{secs[name].strip()}")
    entries = [l for l in read(p / "log.md").splitlines() if l.startswith("- ")]
    if entries:
        print("\nПоследние действия:")
        for l in entries[-5:]:
            print("  " + l)
    print("\nЗатраты:\n" + cost_summary(p))
    ok, results = run_checks(p, step)
    print("\nПроверки до текущего шага:")
    print_checks(results)


def cmd_set(a):
    p = project_dir(a.slug)
    upd = {"updated": now()}
    if a.step is not None:
        upd["step"] = str(a.step)
    if a.status:
        upd["step_status"] = a.status
    if a.next:
        upd["next_action"] = a.next
    write_state(p, upd)
    print("state.md обновлён: " + ", ".join(f"{k}={v}" for k, v in upd.items()))


def cmd_check(a):
    p = project_dir(a.slug)
    step = a.step or step_of(read_state(p))
    ok, results = run_checks(p, step)
    print_checks(results)
    sys.exit(0 if ok else 1)


def append_log(p: Path, text: str, step) -> None:
    path = p / "log.md"
    tag = f"[шаг {step}] " if step else ""
    with path.open("a", encoding="utf-8") as f:
        f.write(f"- {now()} {tag}{text}\n")


def cmd_log(a):
    p = project_dir(a.slug)
    append_log(p, a.text, a.step)
    write_state(p, {"updated": now()})
    print("Записано в log.md")


def cmd_cost(a):
    p = project_dir(a.slug)
    if a.action == "summary":
        print(cost_summary(p))
        return
    if a.step is None or not a.what:
        sys.exit("Для add нужны --step и --what")
    row = (f"| {today()} | {a.step} | {a.what.replace('|', '/')} | {a.model or '—'} | "
           f"{a.tokens if a.tokens is not None else '—'} | "
           f"{a.usd if a.usd is not None else '—'} | {a.human_min if a.human_min is not None else 0} |\n")
    path = p / "cost.md"
    text = read(path)
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text + row, encoding="utf-8")
    print("Записано в cost.md")


REPORT_PARTS = [
    ("summary.md", "Резюме"),
    ("brief.md", "Бриф"),
    ("situation.md", "Картина ситуации"),
    ("options.md", "Варианты"),
    ("product-strategy.md", "Продуктовая стратегия"),
    ("comms-strategy.md", "Коммуникационная стратегия"),
    ("validation-plan.md", "План проверки"),
    ("decisions.md", "Решения"),
    ("unknowns.md", "Чего мы не знаем"),
    ("interviews.md", "Интервью (модельные ответы — синтетика, не доказательство)"),
    ("facts.md", "Реестр утверждений"),
    ("sources.md", "Источники"),
]


def drop_empty_sections(md: str) -> str:
    """Убрать разделы `## …`, в которых нет содержания (необязательные заготовки шаблона)."""
    lines = clean(md).splitlines()
    out, block = [], []
    def flush():
        if block and (not block[0].startswith("## ") or has_content("\n".join(block[1:]))):
            out.extend(block)
    for line in lines:
        if line.startswith("## "):
            flush()
            block = [line]
        else:
            block.append(line)
    flush()
    return "\n".join(out)


def demote(md: str) -> str:
    """Понизить заголовки на уровень, убрать комментарии и первый заголовок файла."""
    lines = drop_empty_sections(md).strip().splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    text = "\n".join(("#" + l) if l.startswith("#") else l for l in lines).strip()
    return re.sub(r"\n{3,}", "\n\n", text)


def cmd_report(a):
    p = project_dir(a.slug)
    st = read_state(p)
    ok, results = run_checks(p, 6)
    done = 0
    for n, r in results:  # шаги готовы подряд, пока проверки проходят; не зависит от того, обновлён ли state.md
        if r.errors:
            break
        done = n
    out = [f"# {st.get('title', a.slug)}: итоговый отчёт", "",
           f"Собран: {now()}. Готово шагов: {done} из 6 (по проверкам `check`).", ""]
    if not ok:
        out += ["> ⚠️ Не все проверки пройдены — отчёт черновой. Незакрытые пункты:", ""]
        for n, r in results:
            out += [f"> - шаг {n}: {e}" for e in r.errors]
        out.append("")
    out += ["Обозначения: [F…] — утверждение из реестра (тип указан в нём), [S…] — источник.", ""]
    parts = list(REPORT_PARTS)
    deliverable = read(p / "deliverable.md")
    if deliverable and has_content(deliverable):
        # Документ в структуре заказчика — сразу после резюме; рабочие файлы — приложениями.
        parts = [parts[0], ("deliverable.md", "Документ по структуре заказчика"),
                 ("", "Приложения: рабочие материалы")] + parts[1:]
    for fname, title in parts:
        if not fname:
            out += [f"## {title}", "", "Ниже — рабочие файлы, из которых собран документ: бриф, картина ситуации, "
                    "варианты, стратегии, план проверки, решения, пробелы и реестры.", ""]
            continue
        body = demote(read(p / fname))
        if body and has_content(body):  # пустые заготовки шаблона (например, «Интервью») не попадают в отчёт
            out += [f"## {title}", "", body, ""]
    out += ["## Затраты", "", cost_summary(p), ""]
    (p / "report.md").write_text("\n".join(out), encoding="utf-8")
    append_log(p, "Собран report.md", st.get("step"))
    print(f"Собран projects/{a.slug}/report.md" + ("" if ok else " (черновой: есть непройденные проверки)"))


SKIP_FILES = {"log.md", "cost.md", "report.md", "state.md"}


def work_files(p: Path) -> list[Path]:
    return sorted(f for f in p.glob("*.md") if f.name not in SKIP_FILES)


def add_to_section(p: Path, section: str, line: str) -> None:
    path = p / "state.md"
    lines = read(path).splitlines()
    for i, l in enumerate(lines):
        if l.strip() == f"## {section}":
            j = i + 1
            while j < len(lines) and not lines[j].startswith("## "):
                j += 1
            while j > i + 1 and not lines[j - 1].strip():
                j -= 1
            lines.insert(j, line)
            break
    else:
        lines += ["", f"## {section}", line]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def dependents(p: Path, root: str) -> list[str]:
    """ID и все утверждения, которые на него опираются (транзитивно)."""
    facts = facts_index(p)
    found = [root]
    changed = True
    while changed:
        changed = False
        for fid, row in facts.items():
            basis = set(re.findall(r"\b[FS]\d+\b", expand_ranges(row.get("Основание", ""))))
            if fid not in found and basis & set(found):
                found.append(fid)
                changed = True
    return found


def set_fact_status(p: Path, fid: str, status: str, reason: str) -> None:
    """Записать статус в колонку «Статус» facts.md (добавить колонку, если её нет). Строка не удаляется."""
    path = p / "facts.md"
    lines = read(path).splitlines()
    head = next((i for i, l in enumerate(lines) if l.strip().startswith("|") and i + 1 < len(lines)
                 and is_sep(lines[i + 1])), None)
    if head is None:
        sys.exit("facts.md: таблица утверждений не найдена")
    cols = split_row(lines[head])
    if "Статус" not in cols:
        lines[head] = lines[head].rstrip().rstrip("|") + "| Статус |"
        lines[head + 1] = lines[head + 1].rstrip().rstrip("|") + "|---|"
        cols.append("Статус")
    idx = cols.index("Статус")
    value = status + (f": {reason}" if reason and status != "действует" else "")
    value = value.replace("|", "/")
    for i in range(head + 2, len(lines)):
        if not lines[i].strip().startswith("|"):
            break
        cells = split_row(lines[i])
        if cells and cells[0] == fid:
            cells += [""] * (len(cols) - len(cells))
            cells[idx] = value
            lines[i] = "| " + " | ".join(cells) + " |"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return
    sys.exit(f"facts.md: утверждение {fid} не найдено")


def cmd_impact(a):
    p = project_dir(a.slug)
    ident = a.id.upper()
    if not re.fullmatch(r"[SFOUQD]\d+", ident):
        sys.exit("ID: S…, F…, O…, U…, Q… или D…")
    if a.status:
        if ident[0] != "F":
            sys.exit("--status меняется только у утверждений F…")
        set_fact_status(p, ident, a.status, a.mark or "")
        append_log(p, f"Статус {ident}: {a.status}" + (f" ({a.mark})" if a.mark else ""), read_state(p).get("step"))
        print(f"facts.md: {ident} → {a.status}")
    ids = dependents(p, ident) if ident[0] in "SF" else [ident]
    pat = re.compile(r"\b(" + "|".join(map(re.escape, ids)) + r")\b")
    hits: dict[str, list[str]] = {}
    for f in work_files(p):
        h2 = h3 = ""
        for line in clean(read(f)).splitlines():
            if line.startswith("## "):
                h2, h3 = line[3:].strip(), ""
            elif line.startswith("### "):
                h3 = line[4:].strip()
            heading = " › ".join(x for x in (h2, h3) if x)
            m = pat.findall(expand_ranges(line))
            if not m or line.startswith("#"):
                continue
            if f.name == "facts.md" and split_row(line)[0] == ident:
                continue
            where = heading or "—"
            hits.setdefault(f.name, []).append(f"[{where}] {line.strip()[:140]}")
    print(f"Что зависит от {ident}:")
    if len(ids) > 1:
        print(f"  Утверждения, которые опираются на {ident} (цепочкой): {', '.join(ids[1:])}")
    if not hits:
        print("  Ссылок не найдено.")
    for fname, lines in hits.items():
        print(f"\n{fname}:")
        for l in lines:
            print(f"  - {l}")
    decisions = [r for r in first_table(read(p / "decisions.md"))
                 if set(F_REF.findall(r.get("Основания", ""))) & set(ids)]
    if decisions:
        print("\nРешения, основания которых затронуты:")
        for r in decisions:
            print(f"  - {r.get('ID')}: {r.get('Решение')} — пересмотр: {r.get('Условие пересмотра')}")
    if a.mark:
        files = ", ".join(hits) or "нет ссылок"
        add_to_section(p, "Помечено к пересмотру",
                       f"- {now()} {ident} изменён ({a.mark}); затронуто: {', '.join(ids[1:]) or '—'}; файлы: {files}"
                       + (f"; решения: {', '.join(r.get('ID', '') for r in decisions)}" if decisions else ""))
        append_log(p, f"Пересмотр: {ident} — {a.mark}", read_state(p).get("step"))
        print("\nПомечено в state.md → «Помечено к пересмотру».")


def project_metrics(p: Path) -> dict:
    facts = facts_index(p)
    sources = sources_index(p)
    by_type: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for row in facts.values():
        t = row.get("Тип", "?").lower()
        by_type[t] = by_type.get(t, 0) + 1
        st = fact_status(row)
        by_status[st] = by_status.get(st, 0) + 1
    fact_rows = [r for r in facts.values() if r.get("Тип", "").lower() == "факт"]
    sourced = sum(1 for r in fact_rows if any(s in sources for s in S_REF.findall(r.get("Основание", ""))))
    secs = sections(read(p / "options.md"))
    opts = {k: v for k, v in secs.items() if re.match(r"O\d+\b", k) and has_content(v)}
    objections = sum(len(re.findall(r"^\s*[-*]\s+\S", sections(v, 3).get("Возражения", ""), re.M))
                     for v in opts.values())
    promises = first_table(sections(read(p / "comms-strategy.md")).get("Проверка обещаний", ""))
    ok_prom = sum(1 for r in promises if r.get("Чем выполняется в продукте", "").strip("—-– ")
                  and "не подтвержд" not in r.get("Статус", "").lower())
    rel: dict[str, int] = {}
    for r in sources.values():
        k = r.get("Надёжность", "?").lower() or "?"
        rel[k] = rel.get(k, 0) + 1
    usd = sum(num(r.get("USD")) for r in cost_rows(p))
    mins = sum(num(r.get("Минуты человека")) for r in cost_rows(p))
    return {
        "Источников": len(sources), "Источники по надёжности": rel,
        "Утверждений": len(facts), "Утверждения по типам": by_type, "Утверждения по статусу": by_status,
        "Фактов с проверяемым источником": f"{sourced}/{len(fact_rows)}",
        "Названных пробелов": len(first_table(read(p / "unknowns.md"))),
        "Вариантов": len(opts), "Возражений": objections,
        "Обещаний подтверждено продуктом": f"{ok_prom}/{len(promises)}",
        "Решений в журнале": len(first_table(read(p / "decisions.md"))),
        "Затраты, USD": round(usd, 2), "Минуты человека": int(mins),
        "Записей в журнале": sum(1 for l in read(p / "log.md").splitlines() if l.startswith("- ")),
    }


def cmd_metrics(a):
    p = project_dir(a.slug)
    print("| Показатель | Значение |\n|---|---|")
    for k, v in project_metrics(p).items():
        if isinstance(v, dict):
            v = ", ".join(f"{t}: {n}" for t, n in v.items()) or "—"
        print(f"| {k} | {v} |")


def main():
    ap = argparse.ArgumentParser(description="Стратег продуктов — CLI проектов")
    sp = ap.add_subparsers(dest="cmd", required=True)

    s = sp.add_parser("new"); s.add_argument("slug"); s.add_argument("--title"); s.set_defaults(f=cmd_new)
    s = sp.add_parser("list"); s.set_defaults(f=cmd_list)
    s = sp.add_parser("status"); s.add_argument("slug"); s.set_defaults(f=cmd_status)
    s = sp.add_parser("set"); s.add_argument("slug"); s.add_argument("--step", type=int, choices=range(1, 7))
    s.add_argument("--status"); s.add_argument("--next"); s.set_defaults(f=cmd_set)
    s = sp.add_parser("check"); s.add_argument("slug"); s.add_argument("--step", type=int, choices=range(1, 7))
    s.set_defaults(f=cmd_check)
    s = sp.add_parser("log"); s.add_argument("slug"); s.add_argument("text"); s.add_argument("--step", type=int)
    s.set_defaults(f=cmd_log)
    s = sp.add_parser("cost"); s.add_argument("slug"); s.add_argument("action", choices=["add", "summary"])
    s.add_argument("--step", type=int); s.add_argument("--what"); s.add_argument("--model")
    s.add_argument("--tokens", type=int); s.add_argument("--usd", type=float)
    s.add_argument("--human-min", type=int, dest="human_min"); s.set_defaults(f=cmd_cost)
    s = sp.add_parser("report"); s.add_argument("slug"); s.set_defaults(f=cmd_report)
    s = sp.add_parser("impact"); s.add_argument("slug"); s.add_argument("id")
    s.add_argument("--mark", metavar="ПРИЧИНА", help="пометить затронутое к пересмотру в state.md")
    s.add_argument("--status", choices=["устарел", "отозван", "действует"],
                   help="сменить статус утверждения F… в facts.md (причина — из --mark)")
    s.set_defaults(f=cmd_impact)
    s = sp.add_parser("metrics"); s.add_argument("slug"); s.set_defaults(f=cmd_metrics)

    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
