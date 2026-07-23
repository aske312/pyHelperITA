# Конфигурация

Постоянные конфигурационные файлы собраны в `config/`:

- `features.config` — модули, команды, автоматизации и строгая проверка связей;
- `permissions.json` — роли, наследование и permissions;
- `directories.json` — справочные значения профилей;
- `pyproject.toml`, `requirements.txt` — сборка и зависимости;
- `compose.yaml`, `Dockerfile` — контейнерный запуск.

`.env` остаётся в корне как локальный секретный runtime-файл и не коммитится.
Пути можно переопределить полями `Settings` или переменными окружения.

Все временные артефакты проекта находятся в `.temp/`: кэши pip,
pytest и Ruff, PID-файл и временные экспорты. Рабочие логи приложения находятся
отдельно в `logs/`, включая `logs/installer.log` установочных сценариев.

Проверки из корня проекта запускаются с явным конфигурационным файлом:

```bash
pytest -c config/pyproject.toml
ruff check --config config/pyproject.toml core tests
```
