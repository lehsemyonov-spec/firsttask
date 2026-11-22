# Проекты

Каждая папка — одна задача руководителя продукта, пройденная агентом. Создаётся командой:

```
python tools/strategist.py new <slug> --title "Название"
```

Внутри: `materials/` (исходники), рабочие файлы шагов, `state.md` (где остановились), `log.md` (что сделано), `cost.md` (сколько стоило), `report.md` (итог).

## Что здесь лежит

| Папка | Что это | Среда и модель |
|---|---|---|
| [`svyaz-blizkih/`](svyaz-blizkih/README.md) | **Демонстрационный проект** по брифу МегаФона «Связь для близких и собственных устройств» — основной результат | Claude Code, старшая модель Claude |
| [`svyaz-blizkih-opencode/`](svyaz-blizkih-opencode/README.md) | Тот же бриф, **независимый прогон в другой среде** — проверка переносимости | OpenCode, DeepSeek V4.1 Flash и Nemotron 3.5 Super VL |
| [`vozvratim/`](vozvratim/report.md) | Вторая задача (сервис возврата потерянных вещей) — пробный прогон с реальными материалами владельца | Claude Code |
| [`vozvratim-opencode/`](vozvratim-opencode/README.md) | Проверочная копия «Возвратим»: продолжение готового проекта в OpenCode; новые данные вымышлены для теста | OpenCode |

Проверить любой проект: `python tools/strategist.py check <папка> --step 6`; показатели — `python tools/strategist.py metrics <папка>`.
