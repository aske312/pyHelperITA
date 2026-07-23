#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv"

run_as_root() {
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    printf '  ✗ Для установки системных пакетов нужен root или sudo\n'
    exit 1
  fi
}

install_system_dependencies() {
  local need_packages=()

  command -v python3 >/dev/null 2>&1 || need_packages+=(python3)
  command -v git >/dev/null 2>&1 || need_packages+=(git)

  # На Debian/Ubuntu модуль venv/ensurepip поставляется отдельно.
  if command -v python3 >/dev/null 2>&1; then
    python3 -c 'import ensurepip, venv' >/dev/null 2>&1 || need_packages+=(python3-venv)
  else
    need_packages+=(python3-venv)
  fi

  if ((${#need_packages[@]} == 0)); then
    printf '  ✓ Системные зависимости уже установлены\n'
    return
  fi

  if ! command -v apt-get >/dev/null 2>&1; then
    printf '  ✗ Автоустановка поддерживает Debian/Ubuntu (apt-get)\n'
    printf '    Установите вручную: %s\n' "${need_packages[*]}"
    exit 1
  fi

  printf '  • Устанавливаю системные пакеты: %s\n' "${need_packages[*]}"
  run_as_root apt-get update -qq
  run_as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y \
    ca-certificates "${need_packages[@]}"
}

printf '\n  Corporate Assistant\n'
printf '  ─────────────────────────────\n'

install_system_dependencies

mkdir -p "$ROOT/.tmp" "$ROOT/data" "$ROOT/logs" "$ROOT/backups"

if [[ ! -x "$VENV/bin/python" ]]; then
  if [[ -e "$VENV" ]]; then
    case "$VENV" in
      "$ROOT/.venv") rm -rf -- "$VENV" ;;
      *) printf '  ✗ Небезопасный путь venv: %s\n' "$VENV"; exit 1 ;;
    esac
    printf '  • Удалено незавершённое виртуальное окружение\n'
  fi
  printf '  • Создаю виртуальное окружение\n'
  python3 -m venv "$VENV"
else
  printf '  ✓ Виртуальное окружение уже существует\n'
fi

printf '  • Устанавливаю приложение\n'
"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade pip
"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check "$ROOT/config"

if [[ ! -f "$ROOT/.env" ]]; then
  cp "$ROOT/config/env.example" "$ROOT/.env"
  chmod 600 "$ROOT/.env"
  printf '  ! Создан .env — заполните секреты\n'
fi

printf '  • Проверяю конфигурацию\n'
"$VENV/bin/assistant-bot" doctor
"$VENV/bin/assistant-bot" init

printf '  ✓ Готово\n'
printf '  Запуск: .venv/bin/assistant-bot bot\n\n'
