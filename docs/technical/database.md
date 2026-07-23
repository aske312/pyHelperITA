# База данных

SQLite является отдельным адаптером в
`core/integrations/database/sqlite.py`. Соединения включают foreign keys, WAL,
busy timeout и commit/rollback.

Единая настройка подключения:

```dotenv
DATABASE_URL=sqlite:///data/base.sqlite3
```

Фабрика `create_database()` выбирает драйвер по URL-схеме. Дополнительный
драйвер регистрируется через `register_database_driver()`. Старый
`DATABASE_PATH` преобразуется в SQLite URL для обратной совместимости.

## Группы таблиц

- сотрудники: `employees`, `employee_contacts`, `employee_work_profiles`;
- команды: `teams`, `team_members`;
- отсутствия: `vacations`, `sick_leaves`, `day_offs`;
- напоминания: `reminder_settings`, `sent_reminders`;
- уведомления: `scheduled_notifications`, `system_notification_log`;
- интеграции: `employee_integrations`, `integration_secrets`.

Основной владелец данных — `employees`. Связанные записи удаляются каскадно,
кроме ссылок руководителя и наставника, которые обнуляются. Секреты интеграций
лежат отдельно от открытых метаданных и содержат только Fernet ciphertext.
Мастер-ключ хранится вне БД в `INTEGRATION_SECRET_KEY`.

Изменения схемы должны добавляться идемпотентно в `Database.initialize()` и
проверяться тестом как для новой, так и для существующей базы.
