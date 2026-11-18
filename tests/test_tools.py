"""Автотесты инструментов. Запуск: python -m unittest discover tests -v

Учебный проект tests/fixture/ содержит вымышленные данные и копируется во временную папку.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "tools" / "strategist.py"
FIXTURE = ROOT / "tests" / "fixture"


class ToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.projects = self.tmp / "projects"
        shutil.copytree(FIXTURE, self.projects / "demo")
        self.env = {**os.environ, "STRATEGIST_PROJECTS": str(self.projects), "PYTHONIOENCODING": "utf-8"}

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_cli(self, *args, script=CLI, stdin=None):
        return subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True,
                              encoding="utf-8", env=self.env, input=stdin)

    def edit(self, name, old, new):
        f = self.projects / "demo" / name
        f.write_text(f.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")

    def test_fixture_passes_all_checks(self):
        r = self.run_cli("check", "demo", "--step", "6")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_new_project_fails_until_filled(self):
        r = self.run_cli("new", "fresh", "--title", "Новый")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_cli("check", "fresh", "--step", "1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("не согласован", r.stdout)

    def test_fact_without_source_is_error(self):
        self.edit("facts.md", "| факт | S2 |", "| факт | S9 |")
        r = self.run_cli("check", "demo", "--step", "3")
        self.assertEqual(r.returncode, 1)
        self.assertIn("несуществующий источник S9", r.stdout)

    def test_synthetic_cannot_be_evidence(self):
        self.edit("comms-strategy.md", "48% жалоб — на цену [F2]", "так сказали модельные респонденты [F5]")
        r = self.run_cli("check", "demo", "--step", "5")
        self.assertEqual(r.returncode, 1)
        self.assertIn("F5 — синтетика", r.stdout)

    def test_unbacked_promise_is_error(self):
        self.edit("comms-strategy.md", "| Основное бесплатно навсегда | бесплатный план | будет по плану |",
                  "| Лучший на рынке | — | не подтверждено |")
        r = self.run_cli("check", "demo", "--step", "5")
        self.assertEqual(r.returncode, 1)
        self.assertIn("не подкреплено продуктом", r.stdout)

    def test_options_need_status_quo_and_choice(self):
        self.edit("options.md", "[статус-кво]", "")
        self.edit("options.md", "Выбрано человеком: O3", "Выбрано человеком:")
        r = self.run_cli("check", "demo", "--step", "4")
        self.assertIn("статус-кво", r.stdout)
        self.assertIn("не выбрал", r.stdout)

    def test_close_option_is_mandatory(self):
        self.edit("options.md", "[закрыть]", "")
        r = self.run_cli("check", "demo", "--step", "4")
        self.assertEqual(r.returncode, 1)
        self.assertIn("[закрыть]", r.stdout)

    def test_synthetic_laundering_through_conclusion(self):
        with (self.projects / "demo" / "facts.md").open("a", encoding="utf-8") as f:
            f.write("| F6 | Реклама — главный барьер | вывод | F5 | средняя | Q2 |\n")
        self.edit("product-strategy.md", "Не переходим на рекламу.", "Не переходим на рекламу [F6].")
        r = self.run_cli("check", "demo", "--step", "5")
        self.assertEqual(r.returncode, 1)
        self.assertIn("F6 (вывод) опирается на синтетику F5", r.stdout)

    def test_synthetic_based_hypothesis_is_allowed(self):
        with (self.projects / "demo" / "facts.md").open("a", encoding="utf-8") as f:
            f.write("| F6 | Реклама может раздражать | гипотеза | F5 | низкая | Q2 |\n")
        self.edit("product-strategy.md", "Не переходим на рекламу.", "Не переходим на рекламу (гипотеза [F6]).")
        r = self.run_cli("check", "demo", "--step", "6")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_ranges_are_expanded(self):
        self.edit("product-strategy.md", "(прогноз [F4])", "(прогноз [F4], см. F3–F8)")
        r = self.run_cli("check", "demo", "--step", "5")
        self.assertIn("ссылка на отозванный F6", r.stdout)  # F6 в учебном проекте отозван
        for missing in ("F7", "F8"):
            self.assertIn(f"несуществующий факт {missing}", r.stdout)
        r = self.run_cli("impact", "demo", "F2")
        self.assertIn("см. F3–F8", r.stdout)

    def test_non_numeric_step_does_not_crash(self):
        self.edit("state.md", "step: 6", "step: шаг 6 из 6")
        r = self.run_cli("status", "demo")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Шаг 6 из 6", r.stdout)
        self.edit("state.md", "step: шаг 6 из 6", "step: ???")
        self.assertEqual(self.run_cli("status", "demo").returncode, 0)

    def test_impact_follows_chain_and_marks_state(self):
        r = self.run_cli("impact", "demo", "S2", "--mark", "обновлены отзывы")
        self.assertEqual(r.returncode, 0, r.stderr)
        for token in ("F2", "F3", "F4", "options.md", "product-strategy.md", "D1"):
            self.assertIn(token, r.stdout)
        state = (self.projects / "demo" / "state.md").read_text(encoding="utf-8")
        self.assertIn("S2 изменён (обновлены отзывы)", state)

    def test_metrics(self):
        r = self.run_cli("metrics", "demo")
        self.assertIn("| Фактов с проверяемым источником | 3/3 |", r.stdout)
        self.assertIn("| Утверждения по статусу | действует: 5, отозван: 1 |", r.stdout)
        self.assertIn("| Вариантов | 4 |", r.stdout)

    def test_report_and_cost(self):
        self.run_cli("cost", "demo", "add", "--step", "4", "--what", "варианты", "--usd", "0.2")
        r = self.run_cli("report", "demo")
        self.assertEqual(r.returncode, 0, r.stderr)
        report = (self.projects / "demo" / "report.md").read_text(encoding="utf-8")
        self.assertIn("## Продуктовая стратегия", report)
        self.assertIn("0.50", report)
        # Резюме — первым разделом, картина ситуации — сразу после брифа.
        order = [report.index(h) for h in ("## Резюме", "## Бриф", "## Картина ситуации", "## Варианты")]
        self.assertEqual(order, sorted(order))

    def test_revoked_and_outdated_facts(self):
        # Отозвать F2 командой: строка остаётся, статус и причина записаны, в журнале запись.
        r = self.run_cli("impact", "demo", "F2", "--status", "отозван", "--mark", "ошибка в выгрузке отзывов")
        self.assertEqual(r.returncode, 0, r.stderr)
        facts = (self.projects / "demo" / "facts.md").read_text(encoding="utf-8")
        self.assertIn("| F2 | 48% негативных отзывов", facts)
        self.assertIn("отозван: ошибка в выгрузке отзывов |", facts)
        r = self.run_cli("check", "demo", "--step", "6")
        self.assertEqual(r.returncode, 1)
        self.assertIn("comms-strategy.md: ссылка на отозванный F2", r.stdout)
        self.assertIn("facts.md: F3 действует, но опирается на неактуальные F2 (отозван)", r.stdout)
        self.assertIn("decisions.md: D1 опирается на отозванные F2", r.stdout)
        # Решение поставлено на пересмотр — это уже не ошибка, а предупреждение.
        self.edit("decisions.md", "| действует |", "| пересмотреть |")
        r = self.run_cli("check", "demo", "--step", "6")
        self.assertNotIn("D1 опирается на отозванные", r.stdout)
        self.assertIn("decisions.md: ссылка на отозванный F2", r.stdout)

    def test_outdated_is_warning_and_old_tables_work(self):
        self.run_cli("impact", "demo", "F2", "--status", "устарел", "--mark", "есть данные за 2026")
        r = self.run_cli("check", "demo", "--step", "5")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("ссылка на устаревший F2", r.stdout)
        # Проект без колонки «Статус» (созданный до её появления) проверяется как раньше,
        # а команда добавляет колонку сама.
        f = self.projects / "demo" / "facts.md"
        old = "\n".join(l.rsplit("|", 2)[0] + "|" if l.startswith("|") else l
                        for l in f.read_text(encoding="utf-8").splitlines() if "F6" not in l)
        f.write_text(old + "\n", encoding="utf-8")
        self.assertNotIn("Статус", f.read_text(encoding="utf-8"))
        self.assertEqual(self.run_cli("check", "demo", "--step", "6").returncode, 0)
        self.run_cli("impact", "demo", "F4", "--status", "отозван", "--mark", "прогноз не подтвердился")
        text = f.read_text(encoding="utf-8")
        self.assertIn("| Статус |", text)
        self.assertIn("| F4 |", text)
        self.assertIn("отозван: прогноз не подтвердился |", text)
        self.edit("facts.md", "отозван: прогноз", "забыт: прогноз")
        r = self.run_cli("check", "demo", "--step", "3")
        self.assertIn("F4 — неизвестный статус «забыт: прогноз не подтвердился»", r.stdout)

    def test_situation_rules(self):
        self.edit("situation.md", "## Что спорно", "## Спорное")
        self.edit("situation.md", "цену подписки [F2]", "цену подписки [F2]; студенты раздражены рекламой [F5]")
        self.edit("situation.md", "[U1]", "[U9]")
        self.edit("situation.md", "за янв–сен 2025 [F1].", "за янв–сен 2025 [F1].\n- Фримиум даст +3–6 п.п. [F4].")
        r = self.run_cli("check", "demo", "--step", "3")
        self.assertEqual(r.returncode, 1)
        self.assertIn("нет раздела «Что спорно»", r.stdout)
        self.assertIn("situation.md: F5 — синтетика", r.stdout)
        self.assertIn("несуществующий пробел U9", r.stdout)
        self.assertIn("F4 — прогноз, а стоит в «Что известно»", r.stdout)
        self.assertIn("критичные пробелы не упомянуты", r.stdout)

    def test_situation_is_required(self):
        (self.projects / "demo" / "situation.md").unlink()
        r = self.run_cli("check", "demo", "--step", "3")
        self.assertIn("situation.md: файл отсутствует", r.stdout)

    def test_summary_rules(self):
        self.edit("summary.md", "девять месяцев [F1].", "девять месяцев.")
        self.edit("summary.md", "тест фримиума (O3)", "тест фримиума")
        self.edit("summary.md", "## Что решить сейчас", "## Потом")
        r = self.run_cli("check", "demo", "--step", "6")
        self.assertEqual(r.returncode, 1)
        self.assertIn("основание без ссылки на факт", r.stdout)
        self.assertIn("не назван выбранный человеком вариант O3", r.stdout)
        self.assertIn("нет раздела «Что решить сейчас»", r.stdout)

    def test_data_check_warns(self):
        csv_file = self.tmp / "m.csv"
        csv_file.write_text("Дата;Удержание, %;Выручка, руб\n01.01.2025;18,5;100\n01.02.2025;150;-5\n"
                            "01.03.2025;;90\n", encoding="utf-8")
        r = self.run_cli(str(csv_file), script=ROOT / "tools" / "data_check.py")
        self.assertIn("значения > 100", r.stdout)
        self.assertIn("пропусков 1", r.stdout)
        self.assertIn("отрицательные суммы", r.stdout)

    def test_data_check_price_list_like_sheet(self):
        # Как реальный прайс: шапка с телефоном над таблицей, пустые столбцы, суммы текстом, блоки.
        rows = ["Контакты: +7 (900) 111 22 33;;;;;", "УПАКОВКА;;;;;",
                "Размер;До 1000 шт;От 1000 шт;;;", "До 20 см;28р.;26р.;;;", "До 40 см;33р.;30р.;;;",
                ";;;;;", "Хранение;68 р./сутки;;;;", "Сборка;5р./шт;;;;", ";;;;;", "Итого;;;;;"]
        f = self.tmp / "price.csv"
        f.write_text("\n".join(rows) + "\n", encoding="utf-8")
        script = ROOT / "tools" / "data_check.py"
        out = self.run_cli(str(f), script=script).stdout
        self.assertIn("заголовки — строка 3", out)
        self.assertIn("Пустых столбцов отброшено: 3", out)
        self.assertIn("[телефон скрыт]", out)
        self.assertNotIn("111 22 33", out)
        self.assertIn("Похоже на документ в виде таблицы", out)
        self.assertIn("сумм записаны текстом с единицами", out)
        dump = self.run_cli(str(f), "--dump", script=script).stdout
        self.assertIn("| Строка | A | B | C |", dump)
        self.assertIn("| 4 | До 20 см | 28р. | 26р. |", dump)
        manual = self.run_cli(str(f), "--header", "4", script=script).stdout
        self.assertIn("заголовки — строка 4", manual)

    def test_hook_flags_broken_reference(self):
        hook = ROOT / "tools" / "hook.py"
        if not hook.exists():
            self.skipTest("hook.py отсутствует")
        self.edit("facts.md", "| факт | S2 |", "| факт | S9 |")
        payload = json.dumps({"tool_input": {"file_path": str(self.projects / "demo" / "facts.md")}})
        r = self.run_cli("post-edit", script=hook, stdin=payload)
        self.assertEqual(r.returncode, 2)
        self.assertIn("S9", r.stderr)
        # незаконченный шаг (пустые разделы) хук не блокирует
        self.edit("facts.md", "| факт | S9 |", "| факт | S2 |")
        self.edit("brief.md", "Выбор модели монетизации на следующий квартал.", "")
        payload = json.dumps({"tool_input": {"file_path": str(self.projects / "demo" / "brief.md")}})
        r = self.run_cli("post-edit", script=hook, stdin=payload)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
