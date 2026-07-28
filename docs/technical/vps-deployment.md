# Развёртывание на Ubuntu VPS

## Docker Compose

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-v2
cp config/env.example .env
nano .env
docker compose -f config/compose.yaml up -d --build
docker compose -f config/compose.yaml ps
docker compose -f config/compose.yaml logs -f bot
```

Контейнер работает от непривилегированного пользователя, имеет healthcheck,
graceful shutdown и именованные volumes для БД, логов и резервных копий.

## Автоматическая установка и запуск через systemd

```bash
git clone https://github.com/aske312/pyHelperITA.git
cd pyHelperITA
chmod +x deploy.sh
./deploy.sh
nano .env
./deploy.sh --run
```

Скрипт работает из фактического каталога проекта, поэтому размещение в
`/opt/corporate-assistant` не обязательно. Секреты в `.env` автоматически получают
режим `600`. Также следует настроить firewall, обновления ОС и внешнее резервное
копирование базы.

`deploy.sh` самостоятельно проверяет совместимый Python 3.11–3.14, `venv`,
`ensurepip`, Git и CA-сертификаты. На Ubuntu отсутствующие пакеты устанавливаются
через `apt-get`, затем проверяются Python-пакеты и конфигурация. Повторный запуск
пропускает уже установленные компоненты. Для самой простой установки рекомендуется
Ubuntu 24.04 LTS; если репозитории ОС не содержат Python 3.11+, скрипт завершится с
понятной диагностикой и не станет подключать сторонний PPA.
Незавершённое `.venv` после ошибки `ensurepip is not available` автоматически
пересоздаётся.

Обычный `./deploy.sh` устанавливает приложение и создаёт `.env`. Если секреты ещё
не заполнены, установка успешно сохраняется, но бот не запускается. Для
постоянного запуска через systemd используется:

```bash
./deploy.sh --run
```

Скрипт создаёт systemd unit для фактического пути проекта, включает автозапуск
после перезагрузки VPS и проверяет, что процесс действительно активен.

Остановка и отключение автозапуска:

```bash
./deploy.sh --off
```

Логи работающего бота:

```bash
journalctl -u corporate-assistant -f
```
