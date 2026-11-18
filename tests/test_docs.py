"""Ссылки в инструкциях ведут на существующие файлы: агент не должен искать то, чего нет."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ["AGENTS.md", "GUIDE.md", "README.md", "CLAUDE.md", *map(str, ROOT.glob("workflow/*.md")),
        *map(str, ROOT.glob("knowledge/*.md")), *map(str, ROOT.glob(".claude/skills/*/SKILL.md")),
        *map(str, ROOT.glob(".claude/agents/*.md"))]
PATH = re.compile(r"`((?:knowledge|workflow|tools|templates|docs|benchmark|tests|\.claude)/[^`\s<>…*]+?\.(?:md|py|json))`")


class DocsTest(unittest.TestCase):
    def test_referenced_files_exist(self):
        missing = []
        for doc in DOCS:
            text = (ROOT / doc).read_text(encoding="utf-8")
            for ref in PATH.findall(text):
                if not (ROOT / ref).exists():
                    missing.append(f"{Path(doc).relative_to(ROOT) if Path(doc).is_absolute() else doc}: {ref}")
        self.assertEqual(missing, [], "Битые ссылки в инструкциях:\n" + "\n".join(missing))

    def test_hard_rules_numbered_in_order(self):
        text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        block = text.split("## Жёсткие правила", 1)[1].split("\n## ", 1)[0]
        nums = [int(n) for n in re.findall(r"^(\d+)\. \*\*", block, re.M)]
        self.assertEqual(nums, list(range(1, len(nums) + 1)))


if __name__ == "__main__":
    unittest.main()
