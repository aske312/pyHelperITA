#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv"
ACTION=setup
REMOTE_URL="${DEPLOY_REMOTE_URL:-https://github.com/aske312/pyHelperITA.git}"
REMOTE_BRANCH="${DEPLOY_BRANCH:-main}"
SERVICE_NAME=corporate-assistant
SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME.service"
PYTHON_BIN=""
APT_UPDATED=0

# Цвета включаются только в интерактивном терминале.
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
  C_RESET=$'\033[0m'
  C_BOLD=$'\033[1m'
  C_DIM=$'\033[2m'
  C_BLUE=$'\033[34m'
  C_CYAN=$'\033[36m'
  C_GREEN=$'\033[32m'
  C_YELLOW=$'\033[33m'
  C_RED=$'\033[31m'
else
  C_RESET="" C_BOLD="" C_DIM="" C_BLUE="" C_CYAN=""
  C_GREEN="" C_YELLOW="" C_RED=""
fi

section() {
  printf '\n%s%s[%s]%s %s\n' "$C_BOLD" "$C_BLUE" "$1" "$C_RESET" "$2"
}

info() {
  printf '  %s›%s %s\n' "$C_CYAN" "$C_RESET" "$*"
}

ok() {
  printf '  %s✓%s %s\n' "$C_GREEN" "$C_RESET" "$*"
}

warn() {
  printf '  %s!%s %s\n' "$C_YELLOW" "$C_RESET" "$*"
}

fail() {
  printf '  %s✗%s %s\n' "$C_RED" "$C_RESET" "$*" >&2
}

status_row() {
  local label="$1" state="$2" details="${3:-}"
  local color="$C_GREEN"
  local padding=$((22 - ${#label}))
  ((padding < 1)) && padding=1
  [[ "$state" == "УСТАНОВИТЬ" || "$state" == "НЕ ГОТОВО" ]] && color="$C_YELLOW"
  [[ "$state" == "НЕДОСТУПНО" ]] && color="$C_RED"
  printf '  %s%*s %s[%s]%s %s%s%s\n' \
    "$label" "$padding" "" "$color" "$state" "$C_RESET" "$C_DIM" "$details" "$C_RESET"
}

show_python_dependencies() {
  "$VENV/bin/python" - <<'PY'
from importlib.metadata import version

packages = (
    ("aiogram", "aiogram"),
    ("APScheduler", "APScheduler"),
    ("pydantic-settings", "pydantic-settings"),
    ("cryptography", "cryptography"),
    ("psutil", "psutil"),
    ("typer", "typer"),
)
print("  Установленные Python-зависимости:")
for label, distribution in packages:
    print(f"    {label:<20} {version(distribution)}")
PY
}

banner() {
  local action_label os_name
  case "$ACTION" in
    run) action_label="установка и запуск" ;;
    off) action_label="остановка сервиса" ;;
    *) action_label="установка и проверка" ;;
  esac
  os_name="$(
    if [[ -r /etc/os-release ]]; then
      . /etc/os-release
      printf '%s' "${PRETTY_NAME:-Linux}"
    else
      uname -s
    fi
  )"

  printf '\n%s%s' "$C_BOLD" "$C_BLUE"
  printf '  ┌──────────────────────────────────────────────────────────┐\n'
  printf '  │              CORPORATE ASSISTANT DEPLOY                 │\n'
  printf '  └──────────────────────────────────────────────────────────┘'
  printf '%s\n' "$C_RESET"
  printf '  Режим:    %s%s%s\n' "$C_BOLD" "$action_label" "$C_RESET"
  printf '  Система:  %s\n' "$os_name"
  printf '  Проект:   %s\n' "$ROOT"
  printf '  Лог:      %s\n' "$ROOT/logs/installer.log"
  printf '\n  %sСкрипт запущен. Выполняется предварительная проверка…%s\n' \
    "$C_CYAN" "$C_RESET"
}

show_preflight() {
  local python_details="требуется версия 3.11–3.14"
  local candidate

  section "1/5" "Предварительная проверка"
  if command -v apt-get >/dev/null 2>&1; then
    status_row "Менеджер пакетов" "ГОТОВО" "apt-get"
  else
    status_row "Менеджер пакетов" "НЕДОСТУПНО" "нужен Ubuntu/Debian"
  fi

  if command -v git >/dev/null 2>&1; then
    status_row "Git" "ГОТОВО" "$(git --version | awk '{print $3}')"
  else
    status_row "Git" "УСТАНОВИТЬ" "будет загружен автоматически"
  fi

  if dpkg-query -W -f='${Status}' ca-certificates 2>/dev/null |
      grep -q "install ok installed"; then
    status_row "CA-сертификаты" "ГОТОВО"
  else
    status_row "CA-сертификаты" "УСТАНОВИТЬ" "будут загружены автоматически"
  fi

  for candidate in python3 python3.14 python3.13 python3.12 python3.11; do
    if command -v "$candidate" >/dev/null 2>&1 && python_is_compatible "$candidate"; then
      python_details="$("$candidate" --version 2>&1)"
      status_row "Python" "ГОТОВО" "$python_details"
      candidate=""
      break
    fi
  done
  [[ -n "$candidate" ]] &&
    status_row "Python" "УСТАНОВИТЬ" "$python_details"

  if [[ -x "$VENV/bin/python" ]] && python_is_compatible "$VENV/bin/python"; then
    status_row "Окружение .venv" "ГОТОВО"
  else
    status_row "Окружение .venv" "УСТАНОВИТЬ" "будет создано автоматически"
  fi

  if [[ -f "$ROOT/.env" ]]; then
    status_row "Конфигурация .env" "ГОТОВО" "содержимое проверим позже"
  else
    status_row "Конфигурация .env" "УСТАНОВИТЬ" "будет создан шаблон"
  fi
}

on_error() {
  local exit_code=$?
  fail "Развёртывание прервано на строке ${BASH_LINENO[0]} (код $exit_code)."
  [[ -n "${INSTALL_LOG:-}" ]] && printf '  Подробности: %s\n' "$INSTALL_LOG" >&2
  exit "$exit_code"
}

trap on_error ERR

usage() {
  printf 'Использование: ./deploy.sh [--run|--off]\n'
  printf '  без параметров  установить и проверить приложение\n'
  printf '  --run           установить и постоянно запустить бота\n'
  printf '  --off           остановить бота и отключить автозапуск\n'
}

case "${1:-}" in
  "") ;;
  --run) ACTION=run ;;
  --off) ACTION=off ;;
  --help|-h) usage; exit 0 ;;
  *) printf 'Неизвестный параметр: %s\n' "$1"; usage; exit 2 ;;
esac

run_as_root() {
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    fail "Для установки системных пакетов нужен root или sudo"
    exit 1
  fi
}

install_system_dependencies() {
  local need_packages=()

  command -v git >/dev/null 2>&1 || need_packages+=(git)
  dpkg-query -W -f='${Status}' ca-certificates 2>/dev/null |
    grep -q "install ok installed" || need_packages+=(ca-certificates)

  if ! command -v apt-get >/dev/null 2>&1; then
    fail "Автоустановка поддерживает Debian/Ubuntu (apt-get)"
    exit 1
  fi

  if ((${#need_packages[@]} > 0)); then
    info "Устанавливаю системные пакеты: ${need_packages[*]}"
    apt_update
    apt_install "${need_packages[@]}"
  fi

  select_compatible_python
  ensure_python_venv
  ok "Системные зависимости готовы (Python $("$PYTHON_BIN" -c \
    'import platform; print(platform.python_version())'))"
}

apt_update() {
  if ((APT_UPDATED == 0)); then
    run_as_root apt-get update -qq >>"$INSTALL_LOG" 2>&1
    APT_UPDATED=1
  fi
}

apt_install() {
  run_as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y \
    "$@" >>"$INSTALL_LOG" 2>&1
}

python_is_compatible() {
  "$1" -c 'import sys; raise SystemExit(not ((3, 11) <= sys.version_info[:2] < (3, 15)))' \
    >/dev/null 2>&1
}

select_compatible_python() {
  local candidate package

  for candidate in python3 python3.14 python3.13 python3.12 python3.11; do
    if command -v "$candidate" >/dev/null 2>&1 && python_is_compatible "$candidate"; then
      PYTHON_BIN="$(command -v "$candidate")"
      return
    fi
  done

  apt_update
  for package in python3.14 python3.13 python3.12 python3.11; do
    if apt-cache show "$package" >/dev/null 2>&1; then
      info "Устанавливаю совместимый $package"
      apt_install "$package" "$package-venv"
      PYTHON_BIN="$(command -v "$package")"
      return
    fi
  done

  fail "Нужен Python 3.11–3.14, но подходящий пакет не найден."
  printf '    Рекомендуется Ubuntu 24.04 LTS или заранее установленный Python 3.11+.\n'
  exit 1
}

ensure_python_venv() {
  local versioned_python venv_package

  if "$PYTHON_BIN" -c 'import ensurepip, venv' >/dev/null 2>&1; then
    return
  fi

  versioned_python="$("$PYTHON_BIN" -c \
    'import sys; print(f"python{sys.version_info.major}.{sys.version_info.minor}")')"
  venv_package="$versioned_python-venv"
  info "Устанавливаю $venv_package"
  apt_update
  apt_install "$venv_package"
  "$PYTHON_BIN" -c 'import ensurepip, venv' >/dev/null 2>&1 || {
    printf '  ✗ Модуль venv недоступен для %s\n' "$PYTHON_BIN"
    exit 1
  }
}

update_repository() {
  if [[ ! -d "$ROOT/.git" ]]; then
    printf '  ! Каталог не является git-репозиторием — обновление пропущено\n'
    return
  fi
  if [[ -n "$(git -C "$ROOT" status --porcelain --untracked-files=no)" ]]; then
    printf '  ✗ Есть локальные изменения. Обновление остановлено, данные не затронуты.\n'
    exit 1
  fi
  printf '  • Получаю новую версию из %s (%s)\n' "$REMOTE_URL" "$REMOTE_BRANCH"
  git -C "$ROOT" remote set-url origin "$REMOTE_URL"
  git -C "$ROOT" fetch --prune origin "$REMOTE_BRANCH"
  git -C "$ROOT" merge --ff-only "origin/$REMOTE_BRANCH"
}

stop_service() {
  section "1/1" "Остановка сервиса"
  if ! command -v systemctl >/dev/null 2>&1; then
    fail "systemd не найден; постоянный сервис недоступен"
    exit 1
  fi
  if run_as_root systemctl list-unit-files "$SERVICE_NAME.service" \
      --no-legend 2>/dev/null | grep -q "$SERVICE_NAME.service"; then
    run_as_root systemctl disable --now "$SERVICE_NAME.service"
    ok "Бот остановлен, автозапуск отключён"
  else
    info "Сервис не установлен — бот уже выключен"
  fi
  printf '\n'
}

install_and_start_service() {
  if ! command -v systemctl >/dev/null 2>&1 || [[ ! -d /run/systemd/system ]]; then
    fail "systemd не запущен. Используйте Docker Compose для фоновой работы."
    exit 1
  fi

  local service_user
  local unit_tmp
  service_user="${SUDO_USER:-$(id -un)}"
  unit_tmp="$(mktemp "$ROOT/.temp/systemd-unit.XXXXXX")"

  cat >"$unit_tmp" <<EOF
[Unit]
Description=Corporate Assistant Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$service_user
WorkingDirectory=$ROOT
EnvironmentFile=$ROOT/.env
ExecStart=$VENV/bin/assistant-bot bot
ExecStartPre=$VENV/bin/assistant-bot doctor --quiet
Restart=always
RestartSec=5
TimeoutStopSec=30
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

  run_as_root install -m 0644 "$unit_tmp" "$SERVICE_FILE"
  rm -f -- "$unit_tmp"
  run_as_root systemctl daemon-reload
  run_as_root systemctl enable "$SERVICE_NAME.service"
  run_as_root systemctl restart "$SERVICE_NAME.service"
  if ! run_as_root systemctl is-active --quiet "$SERVICE_NAME.service"; then
    fail "Сервис не запустился. Последние сообщения:"
    run_as_root journalctl -u "$SERVICE_NAME.service" -n 30 --no-pager
    exit 1
  fi
  ok "systemd-сервис активен и добавлен в автозапуск"
}

banner

if [[ "$ACTION" == off ]]; then
  stop_service
  exit 0
fi

mkdir -p "$ROOT/.temp" "$ROOT/data" "$ROOT/logs" "$ROOT/backups"
export TMPDIR="$ROOT/.temp"
export PIP_CACHE_DIR="$ROOT/.temp/pip-cache"
INSTALL_LOG="$ROOT/logs/installer.log"
touch "$INSTALL_LOG"

show_preflight

section "2/5" "Системные зависимости"
install_system_dependencies

section "3/5" "Обновление проекта"
update_repository

if [[ -x "$VENV/bin/python" ]] && ! python_is_compatible "$VENV/bin/python"; then
  info "Пересоздаю окружение с совместимой версией Python"
  rm -rf -- "$VENV"
fi

section "4/5" "Python-окружение и приложение"
if [[ ! -x "$VENV/bin/python" ]]; then
  if [[ -e "$VENV" ]]; then
    case "$VENV" in
      "$ROOT/.venv") rm -rf -- "$VENV" ;;
      *) printf '  ✗ Небезопасный путь venv: %s\n' "$VENV"; exit 1 ;;
    esac
    info "Удалено незавершённое виртуальное окружение"
  fi
  info "Создаю виртуальное окружение"
  "$PYTHON_BIN" -m venv "$VENV"
else
  ok "Виртуальное окружение уже существует"
fi

info "Устанавливаю приложение и зависимости"
"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check \
  --log "$INSTALL_LOG" --upgrade pip
"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check \
  --log "$INSTALL_LOG" "$ROOT/config"
"$VENV/bin/python" -m pip check >>"$INSTALL_LOG" 2>&1
"$VENV/bin/python" -c 'import aiogram, apscheduler, cryptography, pydantic_settings, typer'
ok "Python-зависимости установлены и совместимы"

if [[ ! -f "$ROOT/.env" ]]; then
  cp "$ROOT/config/env.example" "$ROOT/.env"
  warn "Создан .env — заполните секреты"
fi
chmod 600 "$ROOT/.env"

"$VENV/bin/assistant-bot" init

section "5/5" "Проверка готовности"
info "Проверяю конфигурацию и доступность компонентов"
CONFIG_READY=1
if ! "$VENV/bin/assistant-bot" doctor; then
  CONFIG_READY=0
  if [[ "$ACTION" == run ]]; then
    fail "Запуск отменён: заполните .env и повторите ./deploy.sh --run"
    exit 1
  fi
  warn "Приложение установлено. Заполните .env перед запуском."
fi

if [[ "$ACTION" == run ]]; then
  info "Настраиваю постоянный сервис"
  install_and_start_service
fi

printf '\n%s%s' "$C_BOLD" "$C_GREEN"
printf '  ┌──────────────────────────────────────────────────────────┐\n'
if [[ "$ACTION" == run ]]; then
  printf '  │  ГОТОВО: БОТ ЗАПУЩЕН И РАБОТАЕТ                        │\n'
elif ((CONFIG_READY == 1)); then
  printf '  │  ГОТОВО: ПРИЛОЖЕНИЕ ПРОВЕРЕНО, НО ЕЩЁ НЕ ЗАПУЩЕНО      │\n'
else
  printf '  │  УСТАНОВЛЕНО: ТРЕБУЕТСЯ ЗАПОЛНИТЬ .env                │\n'
fi
printf '  └──────────────────────────────────────────────────────────┘'
printf '%s\n' "$C_RESET"

status_row "Python" "ГОТОВО" "$("$VENV/bin/python" --version 2>&1)"
status_row "Зависимости" "ГОТОВО" "pip check успешно"
show_python_dependencies
if ((CONFIG_READY == 1)); then
  status_row "Конфигурация" "ГОТОВО" ".env проверен"
else
  status_row "Конфигурация" "НЕ ГОТОВО" "заполните .env"
fi
if [[ "$ACTION" == run ]]; then
  status_row "Сервис" "ГОТОВО" "active + enabled"
  printf '\n  Логи:       %sjournalctl -u %s -f%s\n' \
    "$C_CYAN" "$SERVICE_NAME" "$C_RESET"
  printf '  Состояние:  %ssystemctl status %s%s\n' \
    "$C_CYAN" "$SERVICE_NAME" "$C_RESET"
  printf '  Остановить: %s./deploy.sh --off%s\n\n' "$C_CYAN" "$C_RESET"
else
  status_row "Сервис" "НЕ ГОТОВО" "не запущен"
  printf '\n  Запустить:  %s./deploy.sh --run%s\n' "$C_CYAN" "$C_RESET"
  printf '  Лог:        %s%s%s\n\n' "$C_CYAN" "$INSTALL_LOG" "$C_RESET"
fi
