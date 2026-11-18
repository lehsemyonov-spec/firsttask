#!/usr/bin/env python3
"""Хуки для Claude Code (подключены в .claude/settings.json). Другие среды могут вызывать вручную.

  hook.py session-start   — в начале сессии: список проектов и где остановились (попадает в контекст)
  hook.py post-edit       — после правки файла проекта: целостность ссылок, синтетика, секреты, ПДн.
                            Код выхода 2 = агенту возвращается сообщение об ошибке для исправления.
Незаконченный шаг (пустые разделы) хук НЕ блокирует — это задача `strategist.py check` при переходе.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import strategist as st  # noqa: E402

INTEGRITY = ("несуществующ", "синтетик", "без источника", "без ссылок", "неверный ID",
             "неизвестный тип", "неизвестный статус", "отозванн", "рабочий файл агента", "не подкреплено", "не подтверждено —")
SECRET = re.compile(r"(sk-[A-Za-z0-9_\-]{16,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,}|"
                    r"(api[_-]?key|token|password|пароль)\s*[:=]\s*\S{8,})", re.I)
EMAIL = re.compile(r"(?<![\w/.])[\w.+-]+@[\w-]+\.[\w.]+")
PHONE = re.compile(r"(?:\+7|\b8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}\b")


def session_start() -> int:
    if not st.PROJECTS.exists():
        return 0
    rows = []
    for d in sorted(st.PROJECTS.iterdir()):
        if not (d / "state.md").exists():
            continue
        s = st.read_state(d)
        marked = st.sections(st.read(d / "state.md")).get("Помечено к пересмотру", "")
        n_marked = sum(1 for l in marked.splitlines() if l.strip().startswith("- "))
        rows.append(f"- {d.name}: шаг {s.get('step', '?')} ({s.get('step_status', '?')}), "
                    f"далее: {s.get('next_action', '—')}" + (f"; к пересмотру: {n_marked}" if n_marked else ""))
    if rows:
        print("Проекты «Стратега продуктов» (перед продолжением прочитай state.md и log.md проекта):")
        print("\n".join(rows))
    return 0


def post_edit() -> int:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    path = Path((data.get("tool_input") or {}).get("file_path") or "")
    try:
        rel = path.resolve().relative_to(st.PROJECTS.resolve())
    except (ValueError, OSError):
        return 0
    if len(rel.parts) < 2 or path.suffix != ".md":
        return 0
    project = st.PROJECTS / rel.parts[0]
    problems = []

    text = st.read(path)
    if SECRET.search(text):
        problems.append(f"{rel}: похоже на секрет/ключ — удалите, ключи хранятся только в .env")
    if rel.parts[1] != "materials":
        if EMAIL.search(st.clean(text)) or PHONE.search(st.clean(text)):
            problems.append(f"{rel}: похоже на персональные данные (e-mail/телефон) — обезличьте (152-ФЗ)")

    if (project / "state.md").exists() and rel.parts[1] != "materials":
        _, results = st.run_checks(project, 6)
        for n, r in results:
            problems += [f"шаг {n}: {e}" for e in r.errors if any(k in e for k in INTEGRITY)]

    if problems:
        print("Проверка после правки нашла проблемы — исправь их сейчас:\n" +
              "\n".join(f"- {p}" for p in dict.fromkeys(problems)), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    sys.exit({"session-start": session_start, "post-edit": post_edit}.get(cmd, lambda: 0)())
