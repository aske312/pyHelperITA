from __future__ import annotations

from datetime import datetime
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.config import Settings
from core.db import format_display_name
from core.models import Employee, Team
from core.service import VacationService


class TeamForm(StatesGroup):
    waiting_name = State()


class TeamNotificationForm(StatesGroup):
    waiting_datetime = State()
    waiting_text = State()


def _buttons(items: list[tuple[str, str]], width: int = 1):
    builder = InlineKeyboardBuilder()
    for text, data in items:
        builder.button(text=text, callback_data=data)
    builder.button(text="✖️ Закрыть", callback_data="ui_close")
    builder.adjust(width)
    return builder.as_markup()


def _team_card(
    team: Team, members: list[Employee], statuses: dict[int, str] | None = None
) -> str:
    statuses = statuses or {}
    member_lines = [
        f"{index}. <b>{escape(format_display_name(member.full_name))}</b>"
        f" — {escape(member.grade or 'грейд не указан')}"
        f" · {escape(statuses[member.id])}"
        if member.id in statuses
        else f"{index}. <b>{escape(format_display_name(member.full_name))}</b> — {escape(member.grade or 'грейд не указан')}"
        for index, member in enumerate(members, 1)
    ]
    composition = (
        "\n".join(member_lines) if member_lines else "<i>Состав пока пуст.</i>"
    )
    return (
        f"👥 <b>{escape(team.name)}</b>\n"
        f"⭐ <b>Руководитель:</b> {escape(format_display_name(team.lead_name))}\n"
        f"👤 <b>Участников:</b> <code>{len(members)}</code>\n\n"
        f"<b>Состав команды</b>\n{composition}"
    )


def create_team_router(service: VacationService, settings: Settings) -> Router:
    router = Router(name="team")

    def get_actor(telegram_id: int) -> Employee | None:
        return service.database.get_employee_by_telegram(telegram_id)

    def available_teams(actor: Employee) -> list[Team]:
        return service.database.list_teams(actor.id)

    def employee_label(employee: Employee) -> str:
        marks = []
        if employee.role == "owner":
            marks.append("владелец")
        if employee.is_team_lead:
            marks.append("⭐ руководитель")
        suffix = f" · {', '.join(marks)}" if marks else ""
        status = service.database.employee_presence_status(employee.id)
        if status:
            suffix += f" · {status}"
        return f"{format_display_name(employee.full_name)}{suffix}"

    async def show_employees_panel(message: Message, actor: Employee) -> None:
        teams = available_teams(actor)
        if not teams:
            text = "👥 <b>За вами пока не закреплена команда</b>"
            await message.answer(text, parse_mode="HTML")
            return
        for team in teams:
            members = service.database.list_team_members(team.id)
            actions = [
                ("👤 Действия с сотрудником", f"team_members:{team.id}"),
                ("➕ Пригласить", f"invite_team:{team.id}"),
                ("➖ Исключить", f"dismiss_team:{team.id}"),
                ("🔔 Оповестить команду", f"team_notification:{team.id}"),
            ]
            if actor.role == "owner":
                actions.append(("🗑 Удалить команду", f"delete_team:{team.id}"))
            await message.answer(
                _team_card(
                    team,
                    members,
                    {
                        item.id: status
                        for item in members
                        if (
                            status := service.database.employee_presence_status(item.id)
                        )
                    },
                ),
                parse_mode="HTML",
                reply_markup=_buttons(actions),
            )

    async def show_all_teams(message: Message) -> None:
        teams = service.database.list_teams()
        items = [
            (f"👥 {team.name} · {team.lead_name}", f"manage_team:{team.id}")
            for team in teams
        ]
        items.append(("➕ Создать команду", "teams_create"))
        text = (
            "👥 <b>Все команды</b>\n\nВыберите команду для управления:"
            if teams
            else "👥 <b>Команд пока нет</b>\n\nСоздайте первую команду."
        )
        await message.answer(text, parse_mode="HTML", reply_markup=_buttons(items))

    @router.message(Command("teams"))
    async def teams_command(message: Message) -> None:
        if message.from_user is None:
            return
        current = get_actor(message.from_user.id)
        if current is None or current.role != "owner":
            await message.answer(
                "Управление всеми командами доступно только владельцу."
            )
            return
        await show_all_teams(message)

    @router.callback_query(F.data.startswith("manage_team:"))
    async def manage_team(query: CallbackQuery) -> None:
        current = get_actor(query.from_user.id)
        if current is None or current.role != "owner":
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        team = service.database.get_team(int((query.data or "").split(":")[1]))
        members = service.database.list_team_members(team.id)
        await query.message.edit_text(
            _team_card(
                team,
                members,
                {
                    item.id: status
                    for item in members
                    if (status := service.database.employee_presence_status(item.id))
                },
            ),
            parse_mode="HTML",
            reply_markup=_buttons(
                [
                    ("👤 Сотрудники", f"team_members:{team.id}"),
                    ("➕ Пригласить", f"invite_team:{team.id}"),
                    ("➖ Исключить", f"dismiss_team:{team.id}"),
                    ("🔔 Оповестить", f"team_notification:{team.id}"),
                    ("🗑 Удалить команду", f"delete_team:{team.id}"),
                    ("← Все команды", "teams_back"),
                ]
            ),
        )
        await query.answer()

    @router.callback_query(F.data == "teams_back")
    async def teams_back(query: CallbackQuery) -> None:
        current = get_actor(query.from_user.id)
        if current is None or current.role != "owner":
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        teams = service.database.list_teams()
        await query.message.edit_text(
            "👥 <b>Все команды</b>\n\nВыберите команду для управления:",
            parse_mode="HTML",
            reply_markup=_buttons(
                [
                    (f"👥 {team.name} · {team.lead_name}", f"manage_team:{team.id}")
                    for team in teams
                ]
                + [("➕ Создать команду", "teams_create")]
            ),
        )
        await query.answer()

    @router.message(Command("employees"))
    async def employees(message: Message) -> None:
        if message.from_user is None:
            return
        actor = get_actor(message.from_user.id)
        if actor is None or not actor.is_team_lead:
            await message.answer(
                "⛔ Управление командой доступно только её руководителю."
            )
            return
        await show_employees_panel(message, actor)

    @router.callback_query(F.data.startswith("team_members:"))
    async def team_members(query: CallbackQuery) -> None:
        actor = get_actor(query.from_user.id)
        team = service.database.get_team(int((query.data or "").split(":")[1]))
        if actor is None or (actor.role != "owner" and team.lead_id != actor.id):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        members = service.database.list_team_members(team.id)
        if not members:
            await query.answer("В команде пока нет сотрудников.", show_alert=True)
            return
        await query.message.edit_text(
            f"👥 <b>{escape(team.name)}</b>\n\n"
            "Выберите сотрудника, чтобы открыть доступные действия:",
            parse_mode="HTML",
            reply_markup=_buttons(
                [(employee_label(item), f"employee:{item.id}") for item in members]
            ),
        )
        await query.answer()

    @router.callback_query(F.data.startswith("team_notification:"))
    async def team_notification_start(query: CallbackQuery, state: FSMContext) -> None:
        current = get_actor(query.from_user.id)
        team = service.database.get_team(int((query.data or "").split(":")[1]))
        if current is None or (current.role != "owner" and team.lead_id != current.id):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        await state.set_state(TeamNotificationForm.waiting_datetime)
        await state.set_data({"team_notification_id": team.id})
        await query.message.edit_text(
            f"🔔 <b>Оповещение команды {escape(team.name)}</b>\n\n"
            "Введите дату и время отправки: <code>ДД.ММ.ГГГГ ЧЧ:ММ</code>",
            parse_mode="HTML",
        )
        await query.answer()

    @router.message(
        TeamNotificationForm.waiting_datetime, F.text & ~F.text.startswith("/")
    )
    async def team_notification_datetime(message: Message, state: FSMContext) -> None:
        try:
            value = datetime.strptime((message.text or "").strip(), "%d.%m.%Y %H:%M")
            if value <= datetime.now():
                raise ValueError
        except ValueError:
            await message.answer("Введите будущую дату в формате ДД.ММ.ГГГГ ЧЧ:ММ.")
            return
        await state.update_data(
            team_notification_at=value.isoformat(timespec="minutes")
        )
        await state.set_state(TeamNotificationForm.waiting_text)
        await message.answer("Введите текст оповещения для всей команды:")

    @router.message(TeamNotificationForm.waiting_text, F.text & ~F.text.startswith("/"))
    async def team_notification_text(message: Message, state: FSMContext) -> None:
        if message.from_user is None:
            return
        current = get_actor(message.from_user.id)
        data = await state.get_data()
        team = service.database.get_team(int(data["team_notification_id"]))
        text = (message.text or "").strip()
        if current is None or (current.role != "owner" and team.lead_id != current.id):
            await state.clear()
            return
        if not text:
            await message.answer("Текст не может быть пустым.")
            return
        notification = service.database.add_scheduled_notification(
            datetime.fromisoformat(str(data["team_notification_at"])),
            text,
            current.id,
            (),
            target_team_id=team.id,
        )
        await state.clear()
        await message.answer(
            f"✅ Оповещение #{notification.id} для команды "
            f"<b>{escape(team.name)}</b> запланировано.",
            parse_mode="HTML",
        )

    @router.message(Command("team_create"))
    async def create_team_start(message: Message) -> None:
        if message.from_user is None:
            return
        actor = get_actor(message.from_user.id)
        if actor is None or actor.role != "owner":
            await message.answer("⛔ Создавать команды может только владелец продукта.")
            return
        leaders = [
            item
            for item in service.database.list_employees()
            if item.is_team_lead and item.role != "guest"
        ]
        if not leaders:
            await message.answer(
                "Сначала назначьте сотруднику свойство руководителя через /employees."
            )
            return
        await message.answer(
            "⭐ <b>Новая команда</b>\n\nВыберите руководителя:",
            parse_mode="HTML",
            reply_markup=_buttons(
                [
                    (employee_label(item), f"create_team_lead:{item.id}")
                    for item in leaders
                ]
            ),
        )

    @router.callback_query(F.data == "teams_create")
    async def teams_create(query: CallbackQuery) -> None:
        current = get_actor(query.from_user.id)
        if current is None or current.role != "owner":
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        leaders = [
            item
            for item in service.database.list_employees()
            if item.is_team_lead and item.role != "guest"
        ]
        if not leaders:
            await query.answer(
                "Сначала назначьте руководителя через /staff.", show_alert=True
            )
            return
        await query.message.edit_text(
            "⭐ <b>Новая команда</b>\n\nВыберите руководителя:",
            parse_mode="HTML",
            reply_markup=_buttons(
                [
                    (employee_label(item), f"create_team_lead:{item.id}")
                    for item in leaders
                ]
            ),
        )
        await query.answer()

    @router.callback_query(F.data.startswith("create_team_lead:"))
    async def create_team_lead(query: CallbackQuery, state: FSMContext) -> None:
        actor = get_actor(query.from_user.id)
        if actor is None or actor.role != "owner":
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        lead_id = int((query.data or "").split(":")[1])
        lead = service.database.get_employee(lead_id)
        if not lead.is_team_lead:
            await query.answer(
                "Сотрудник больше не является руководителем.", show_alert=True
            )
            return
        await state.set_state(TeamForm.waiting_name)
        await state.set_data({"team_lead_id": lead_id})
        await query.message.edit_text(
            f"⭐ Руководитель: <b>{escape(format_display_name(lead.full_name))}</b>\n\n"
            "Введите название новой команды:",
            parse_mode="HTML",
        )
        await query.answer()

    @router.message(TeamForm.waiting_name, F.text & ~F.text.startswith("/"))
    async def create_team_finish(message: Message, state: FSMContext) -> None:
        if message.from_user is None:
            return
        actor = get_actor(message.from_user.id)
        if actor is None or actor.role != "owner":
            await state.clear()
            return
        name = (message.text or "").strip()
        data = await state.get_data()
        try:
            team = service.database.create_team(name, int(data["team_lead_id"]))
        except (ValueError, LookupError) as error:
            await message.answer(f"Не удалось создать команду: {escape(str(error))}")
            return
        await state.clear()
        await message.answer(
            f"✅ <b>Команда создана</b>\n\n{_team_card(team, [])}",
            parse_mode="HTML",
        )

    async def start_invite(message: Message, actor: Employee) -> None:
        teams = available_teams(actor)
        if not teams:
            await message.answer("Доступных команд нет.")
            return
        if len(teams) == 1:
            await show_invite_candidates(message, actor, teams[0])
            return
        await message.answer(
            "Выберите команду:",
            reply_markup=_buttons(
                [(team.name, f"invite_team:{team.id}") for team in teams]
            ),
        )

    async def show_invite_candidates(
        message: Message, actor: Employee, team: Team
    ) -> None:
        current_ids = {item.id for item in service.database.list_team_members(team.id)}
        candidates = [
            item
            for item in service.database.list_employees()
            if item.id not in current_ids
            and item.id != team.lead_id
            and item.role != "owner"
            and item.role != "guest"
        ]
        if not candidates:
            await message.answer("Нет сотрудников, которых можно добавить.")
            return
        await message.answer(
            f"➕ <b>Добавление в {escape(team.name)}</b>\n\nВыберите сотрудника:",
            parse_mode="HTML",
            reply_markup=_buttons(
                [
                    (employee_label(item), f"invite_member:{team.id}:{item.id}")
                    for item in candidates
                ]
            ),
        )

    @router.message(Command("invite_team"))
    async def invite_command(message: Message) -> None:
        if message.from_user is None:
            return
        actor = get_actor(message.from_user.id)
        if actor is None or (actor.role != "owner" and not actor.is_team_lead):
            await message.answer("Недостаточно прав.")
            return
        await start_invite(message, actor)

    @router.callback_query(F.data.startswith("invite_team:"))
    async def invite_team(query: CallbackQuery) -> None:
        actor = get_actor(query.from_user.id)
        team = service.database.get_team(int((query.data or "").split(":")[1]))
        if actor is None or (actor.role != "owner" and team.lead_id != actor.id):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        await show_invite_candidates(query.message, actor, team)
        await query.answer()

    @router.callback_query(F.data.startswith("invite_member:"))
    async def invite_member(query: CallbackQuery) -> None:
        actor = get_actor(query.from_user.id)
        _, raw_team, raw_employee = (query.data or "").split(":")
        team = service.database.get_team(int(raw_team))
        employee = service.database.get_employee(int(raw_employee))
        if actor is None or (actor.role != "owner" and team.lead_id != actor.id):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        previous_lead_id = employee.team_lead_id
        try:
            employee = service.database.add_team_member(team.id, employee.id)
        except (ValueError, LookupError) as error:
            await query.answer(str(error), show_alert=True)
            return
        await query.message.edit_text(
            f"✅ <b>{escape(employee_label(employee))}</b> "
            f"добавлен в команду <b>{escape(team.name)}</b>.",
            parse_mode="HTML",
        )
        if (
            actor.role != "owner"
            and previous_lead_id is not None
            and previous_lead_id != actor.id
            and settings.owner_telegram_id
        ):
            await query.bot.send_message(
                settings.owner_telegram_id,
                "⚠️ <b>Сотрудник переведён между командами</b>\n\n"
                f"Руководитель: {escape(format_display_name(actor.full_name))}\n"
                f"Сотрудник: {escape(employee_label(employee))}\n"
                f"Новая команда: {escape(team.name)}",
                parse_mode="HTML",
            )
        await query.answer("Сотрудник добавлен")

    async def start_dismiss(message: Message, actor: Employee) -> None:
        teams = available_teams(actor)
        if not teams:
            await message.answer("Доступных команд нет.")
            return
        if len(teams) == 1:
            await show_dismiss_candidates(message, teams[0])
            return
        await message.answer(
            "Выберите команду:",
            reply_markup=_buttons(
                [(team.name, f"dismiss_team:{team.id}") for team in teams]
            ),
        )

    async def show_dismiss_candidates(message: Message, team: Team) -> None:
        members = service.database.list_team_members(team.id)
        if not members:
            await message.answer("В команде нет сотрудников.")
            return
        await message.answer(
            f"➖ <b>Исключение из {escape(team.name)}</b>\n\nВыберите сотрудника:",
            parse_mode="HTML",
            reply_markup=_buttons(
                [
                    (employee_label(item), f"dismiss_member:{team.id}:{item.id}")
                    for item in members
                ]
            ),
        )

    @router.message(Command("dismiss_team"))
    async def dismiss_command(message: Message) -> None:
        if message.from_user is None:
            return
        actor = get_actor(message.from_user.id)
        if actor is None or (actor.role != "owner" and not actor.is_team_lead):
            await message.answer("Недостаточно прав.")
            return
        await start_dismiss(message, actor)

    @router.callback_query(F.data.startswith("dismiss_team:"))
    async def dismiss_team(query: CallbackQuery) -> None:
        actor = get_actor(query.from_user.id)
        team = service.database.get_team(int((query.data or "").split(":")[1]))
        if actor is None or (actor.role != "owner" and team.lead_id != actor.id):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        await show_dismiss_candidates(query.message, team)
        await query.answer()

    @router.callback_query(F.data.startswith("dismiss_member:"))
    async def dismiss_member(query: CallbackQuery) -> None:
        actor = get_actor(query.from_user.id)
        _, raw_team, raw_employee = (query.data or "").split(":")
        team = service.database.get_team(int(raw_team))
        if actor is None or (actor.role != "owner" and team.lead_id != actor.id):
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        employee = service.database.get_employee(int(raw_employee))
        removed = service.database.remove_team_member(team.id, employee.id)
        if not removed:
            await query.answer(
                "Сотрудник уже не состоит в этой команде.", show_alert=True
            )
            return
        await query.message.edit_text(
            f"✅ <b>{escape(employee_label(employee))}</b> "
            f"исключён из команды <b>{escape(team.name)}</b>.",
            parse_mode="HTML",
        )
        await query.answer("Сотрудник исключён")

    @router.message(Command("delete_team"))
    async def delete_team_command(message: Message) -> None:
        if message.from_user is None:
            return
        actor = get_actor(message.from_user.id)
        if actor is None or actor.role != "owner":
            await message.answer("⛔ Удалять команды может только владелец продукта.")
            return
        teams = service.database.list_teams()
        if not teams:
            await message.answer("Команд для удаления нет.")
            return
        await message.answer(
            "🗑 <b>Удаление команды</b>\n\nВыберите команду:",
            parse_mode="HTML",
            reply_markup=_buttons(
                [(team.name, f"delete_team:{team.id}") for team in teams]
            ),
        )

    @router.callback_query(F.data.startswith("delete_team:"))
    async def delete_team_request(query: CallbackQuery) -> None:
        actor = get_actor(query.from_user.id)
        team = service.database.get_team(int((query.data or "").split(":")[1]))
        if actor is None or actor.role != "owner":
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        await query.message.edit_text(
            f"🗑 Удалить команду <b>{escape(team.name)}</b>?\n\n"
            "У сотрудников будет очищена привязка к команде.",
            parse_mode="HTML",
            reply_markup=_buttons(
                [
                    ("Удалить безвозвратно", f"confirm_delete_team:{team.id}"),
                    ("Отмена", "team_cancel"),
                ]
            ),
        )
        await query.answer()

    @router.callback_query(F.data.startswith("confirm_delete_team:"))
    async def delete_team_confirm(query: CallbackQuery) -> None:
        actor = get_actor(query.from_user.id)
        if actor is None or actor.role != "owner":
            await query.answer("Недостаточно прав.", show_alert=True)
            return
        team_id = int((query.data or "").split(":")[1])
        team = service.database.get_team(team_id)
        service.database.delete_team(team_id)
        await query.message.edit_text(
            f"✅ Команда <b>{escape(team.name)}</b> удалена. "
            "Привязки сотрудников очищены.",
            parse_mode="HTML",
        )
        await query.answer("Команда удалена")

    @router.callback_query(F.data == "team_cancel")
    async def team_cancel(query: CallbackQuery) -> None:
        await query.message.edit_text("Удаление команды отменено.")
        await query.answer()

    return router
