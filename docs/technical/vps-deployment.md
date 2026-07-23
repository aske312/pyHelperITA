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

## Systemd без Docker

```bash
chmod +x deploy.sh
./deploy.sh
sudo useradd --system --home /opt/corporate-assistant assistant
sudo chown -R assistant:assistant /opt/corporate-assistant
sudo cp config/systemd/corporate-assistant.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now corporate-assistant
sudo systemctl status corporate-assistant
```

Репозиторий должен находиться в `/opt/corporate-assistant`. Секреты в `.env`
должны иметь режим `600`. Также следует настроить firewall, обновления ОС и
внешнее резервное копирование volume с базой.

`deploy.sh` самостоятельно проверяет `python3`, `python3-venv`, `ensurepip`,
Git и CA-сертификаты. На Debian/Ubuntu отсутствующие пакеты устанавливаются
через `apt-get`; повторный запуск пропускает уже установленные компоненты.
Незавершённое `.venv` после ошибки `ensurepip is not available` автоматически
пересоздаётся.

Обычный `./deploy.sh` только устанавливает и проверяет приложение. Для
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
