#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv"
ACTION=setup
SERVICE_NAME=corporate-assistant
SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME.service"

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

stop_service() {
  if ! command -v systemctl >/dev/null 2>&1; then
    printf '  ✗ systemd не найден; постоянный сервис недоступен\n'
    exit 1
  fi
  if run_as_root systemctl list-unit-files "$SERVICE_NAME.service" \
      --no-legend 2>/dev/null | grep -q "$SERVICE_NAME.service"; then
    run_as_root systemctl disable --now "$SERVICE_NAME.service"
    printf '  ■ Бот остановлен, автозапуск отключён\n\n'
  else
    printf '  • Сервис не установлен — бот уже выключен\n\n'
  fi
}

install_and_start_service() {
  if ! command -v systemctl >/dev/null 2>&1 || [[ ! -d /run/systemd/system ]]; then
    printf '  ✗ systemd не запущен. Используйте Docker Compose для фоновой работы.\n'
    exit 1
  fi

  local service_user
  local unit_tmp
  service_user="${SUDO_USER:-$(id -un)}"
  unit_tmp="$(mktemp)"

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
  run_as_root systemctl enable --now "$SERVICE_NAME.service"
  if ! run_as_root systemctl is-active --quiet "$SERVICE_NAME.service"; then
    printf '  ✗ Сервис не запустился. Последние сообщения:\n'
    run_as_root journalctl -u "$SERVICE_NAME.service" -n 30 --no-pager
    exit 1
  fi
  printf '  ● Бот работает постоянно\n'
  printf '  Логи: journalctl -u %s -f\n\n' "$SERVICE_NAME"
}

printf '\n  Corporate Assistant\n'
printf '  ─────────────────────────────\n'

if [[ "$ACTION" == off ]]; then
  stop_service
  exit 0
fi

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
if [[ "$ACTION" == run ]]; then
  printf '  • Настраиваю постоянный сервис\n'
  install_and_start_service
  exit 0
fi

printf '  Бот установлен, но ещё не запущен.\n'
printf '  Запуск сейчас: ./deploy.sh --run\n'
printf '  Выключение: ./deploy.sh --off\n\n'
