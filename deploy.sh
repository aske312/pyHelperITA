#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv"

printf '\n  Corporate Assistant\n'
printf '  ─────────────────────────────\n'

command -v python3 >/dev/null || { printf '  ✗ Python 3 не найден\n'; exit 1; }

if [[ ! -d "$VENV" ]]; then
  printf '  • Создаю виртуальное окружение\n'
  python3 -m venv "$VENV"
fi

printf '  • Устанавливаю приложение\n'
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet "$ROOT/config"

if [[ ! -f "$ROOT/.env" ]]; then
  cp "$ROOT/config/env.example" "$ROOT/.env"
  chmod 600 "$ROOT/.env"
  printf '  ! Создан .env — заполните секреты\n'
fi

mkdir -p "$ROOT/data" "$ROOT/logs" "$ROOT/backups"

printf '  • Проверяю конфигурацию\n'
"$VENV/bin/assistant-bot" doctor
"$VENV/bin/assistant-bot" init

printf '  ✓ Готово\n'
printf '  Запуск: .venv/bin/assistant-bot bot\n\n'
