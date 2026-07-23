# Функциональность и связи

## Основные области

| Область | Сервис/модель ядра | Telegram-адаптер | Хранилище |
|---|---|---|---|
| Профили и роли | `service.py`, `access.py`, `models.py` | `profile.py`, `onboarding.py` | `employees`, профильные таблицы |
| Команды | `config.py`, `features.py` | `application.py` | `config/features.config` |
| Отпуска и отсутствия | `service.py`, `events.py` | `calendar.py`, `absence.py` | `vacations`, `sick_leaves`, `day_offs` |
| Команды сотрудников | `service.py` | `team.py`, `owner.py` | `teams`, `team_members` |
| Уведомления | `reminders.py` | доставка через Telegram | notification-таблицы |
| Интеграции | `core/integrations/` | `integrations.py` | `employee_integrations`, `integration_secrets` |

## Поток запроса

1. Интерфейс преобразует внешний идентификатор и ввод пользователя.
2. Прикладной сервис проверяет данные, права и фичтоглы.
3. `Database` выполняет транзакцию и возвращает доменную модель.
4. Интерфейс форматирует результат. Бизнес-сервис не формирует Telegram-кнопки
   и не должен зависеть от `aiogram`.

## Доступ

Роли и permissions описаны в `config/permissions.json`. Роль тимлида вычисляется
из `is_team_lead`; наследование разрешений выполняет `PermissionPolicy`.
Доступность модуля и команды дополнительно ограничивается фичтоглами.
