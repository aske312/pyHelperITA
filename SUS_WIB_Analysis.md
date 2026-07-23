# Логика работы SUS с reason WIB — полный технический разбор

> **Источник данных:** `SUS(WIB).xlsx` (выгрузка `DEV_COMMANDS` + `GRP_TO_DEV@prod`), SWT-файлы из `dat\`, C-ядро сплиттера (`scspl_split.c`).
> **Дата анализа:** 23 июля 2026 г.

---

## Содержание

1. [Общий поток исполнения](#1-общий-поток-исполнения)
2. [Ключевые переменные и фильтры](#2-ключевые-переменные-и-фильтры)
3. [Команды по каждому оборудованию](#3-команды-по-каждому-оборудованию)
4. [Вариативность — от чего зависит разница между кейсами](#4-вариативность--от-чего-зависит-разница-между-кейсами)
5. [Сводная таблица всех команд](#5-сводная-таблица-всех-команд-для-wib)
6. [Аномалии и зависимости](#6-аномалии-и-зависимости)
7. [Структурная схема](#7-структурная-схема--ключевые-зависимости)

---

## 1. Общий поток исполнения

```
 ┌─────────────────────────────────────────────────────────┐
 │  BSS/CRM инициирует SUS-транзакцию                       │
 │  Q0: srv_trx_tp_cd=SUS, reason_code=WIB                 │
 └──────────────────────┬──────────────────────────────────┘
                        │
 ┌──────────────────────▼──────────────────────────────────┐
 │  generic.swt — вычисление действующих переменных:        │
 │  • EFFECTIVE_REASON_CODE = WIB (для SUS = reason_code)  │
 │  • EFFECTIVE_REASON_MODE = "B" (3-й символ WIB)         │
 │  • EFFECTIVE_FREE_TEXT_REASON = из free_text "RSN=WIB@" │
 └──────────────────────┬──────────────────────────────────┘
                        │
 ┌──────────────────────▼──────────────────────────────────┐
 │  Сплиттер (scspl_split.c) обходит DEV_COMMANDS           │
 │  Для каждой строки вычисляет GENERATE_CHECK              │
 │  (стековая машина infix→postfix, && / ||)               │
 │  Если true → генерирует device-команду для этого NE      │
 └──────────────────────┬──────────────────────────────────┘
                        │
 ┌──────────────────────▼──────────────────────────────────┐
 │  Для каждого NE свой .swt файл:                          │
 │  Entry: SuspendSub_<NE> → цепочка F: проверок → S: блоки │
 │  F: = логические ветвления (CompareSub, StrExists...)   │
 │  S: = шаблоны команд (генерируют финальный протокол)    │
 └─────────────────────────────────────────────────────────┘
```

### Описание компонентов

| Компонент | Файл/таблица | Назначение |
|---|---|---|
| **Q0** | таблица БД | Service transaction record — содержит `srv_trx_tp_cd`, `reason_code`, `free_text` |
| **DEV_COMMANDS** | таблица БД | Каталог device-команд: одна строка = одна команда на один NE. Колонка `GENERATE_CHECK` — булев предикат |
| **GRP_TO_DEV** | таблица БД | Маппинг `SRV_TRX_TP_CD` → список device-транзакций |
| **generic.swt** | `dat\generic.swt` | Общие правила вычисления `%EFFECTIVE_REASON_CODE`, `%EFFECTIVE_REASON_MODE`, `%EFFECTIVE_FREE_TEXT_REASON` |
| **scspl_split.c** | C-ядро сплиттера | Вычисляет `GENERATE_CHECK` через infix→postfix стековую машину (`Scspf_CalcGenerateCheck`) |
| **\<NE\>.swt** | `dat\<NE>.swt` | Per-NE правила: `C:` (entry point), `F:` (function checks), `S:` (substitution/command templates) |

---

## 2. Ключевые переменные и фильтры

### 2.1. Действующие переменные reason-кода (`dat\generic.swt:1-28`)

| Переменная | Источник | Назначение |
|---|---|---|
| `%reason_code` | колонка Q0 | Сырой код причины (WIB, STB, FTB...) |
| `%REASON_CODE` | `TrimString(%reason_code)` | Обрезанный reason-код |
| `%EFFECTIVE_REASON_CODE` | Для SUS/RSM = `%REASON_CODE`; для Change-tx (CCD/CHD/CCN) = первый reason из `free_text` | «Действующий» код, управляющий логикой |
| `%EFFECTIVE_FREE_TEXT_REASON` | Reason из `free_text` в формате `RSN=COD@` | Используется для проверки **старого** (предыдущего) barring-состояния |
| `%EFFECTIVE_REASON_MODE` | `GetSubString(%EFFECTIVE_REASON_CODE, 2, 1)` — 3-й символ | Направление барринга: `B` = Both, `O` = Outgoing, `I` = Incoming |
| `%IS_EFFECTIVE_OLD_REASON_EMPTY` | Проверка `EFFECTIVE_FREE_TEXT_REASON` на пустоту | `Y` = первая блокировка (нет предыдущего reason) |

### 2.2. Структура reason-кода

Каждый код = **2-символьный TYPE + 1-символьный MODE**:

```
WIB = WI + B  →  Wireline-Initiated, Both (входящий + исходящий барринг)
WIO = WI + O  →  Wireline-Initiated, Outgoing (только исходящий)
STB = ST + B  →  Standard, Both
STO = ST + O  →  Standard, Outgoing
```

### 2.3. Общие фильтры (проходит большинство NE перед suspend)

| Проверка | Что пропускает | WIB проходит? |
|---|---|---|
| `CHK_SUS_IBR_DUMMY` | IBR (внутренний reason) → пропустить (DUMMY_CMD) | Да |
| `CHK_SUS_VNW_DUMMY` | VNW (виртуальный номер) → пропустить | Да |
| `CHK_DACBLOCK` | DAC@DBL → удалить абонента (не suspend) | Да |
| `SUS_FZ_CHK` | FTB (freeze-to-bar) → другой путь (FZ_LOCK) | Да |

**WIB проходит все фильтры** → suspend-команда отправляется на оборудование.

---

## 3. Команды по каждому оборудованию

### 3.1. HLR (ZTE) — `zhlr.swt`

**Что это:** Home Location Register — главная база данных мобильных абонентов.

**Entry point:** `zhlr:C:SuspendHLR:%CHK_SUS_IBR_DUMMY` (строка 919)

**Цепочка для WIB:**
```
CHK_SUS_IBR_DUMMY  →  WIB != IBR  →  CHK_DACBLOCK
CHK_DACBLOCK       →  SUS@WIB != DAC@DBL  →  SUSPENDSUB_COMMAND
SUSPENDSUB_COMMAND →  CHK_KIND_OF_SUS
CHK_KIND_OF_SUS    →  WIB не FZB/FTB, не 533/ORD/NRB/IMB
                   →  CHK_KIND_OF_SUS0 → CHK_KP_SUS_REASON
CHK_KP_SUS_REASON  →  WIB не в KP-списке  →  SUS_CMD + CHK_DEL_BC_SUS + SUSPEND_BPOS
```

**Команды для WIB (mode=B → двусторонний барринг):**

| # | Команда на оборудование | Что делает |
|---|---|---|
| 1 | `Mod ODB:MSISDN=7XXXXXXXXXX,BOC=1,BIC=1;` | **ODB (Operator Determined Barring):** BOC=1 (Bar Outgoing Calls — запрет исходящих), BIC=1 (Bar Incoming Calls — запрет входящих). Абонент не может звонить и принимать звонки. |
| 2 | `Mod ODB:MSISDN=7XXXXXXXXXX,BPOS=1;` | **BPOS=1** (Bearer Position) — переключение позиционирования на GSM. |
| 3 | `Mod BscEx:MSISDN=7XXXXXXXXXX,RoamSch=CS_VC,PsRoamSch=PS_VC;` | **Roaming schedule** — схемы роуминга для CS (voice) и PS (data) в HPLMN. Только для postpaid. |
| 4 | IMS ODB XML (см. ниже) | **Синхронизация IMS barring** для VoLTE — только если `VOLTE3_IND=Y` |

**Команда #4 — IMS ODB XML (протокол):**
```xml
SET REPOSDATA: PUI=tel:+7XXXXXXXXXX,REPOSOPTYPE=1,SRVIND=4,
REPOSITORYDATA=<![CDATA[<OdbForImsOrientedServices ...>
  <OdbForImsMultimediaTelephonyServices>
    <OutgoingBarring>0</OutgoingBarring>
    <IncomingBarring>0</IncomingBarring>
  </OdbForImsMultimediaTelephonyServices>
</OdbForImsOrientedServices>]]>;
```

Оба барринга (outgoing + incoming) = `0` (полный запрет). Отключает VoLTE-звонки в обоих направлениях.

**Разница WIB vs. другие reason-коды на ZTE HLR:**

| Reason | Mode | BOC | BIC | IMS BOC | IMS BIC | KP (keep priority) | Доп. команды |
|---|---|---|---|---|---|---|---|
| **WIB** | B | 1 | 1 | 0 | 0 | Нет | BPOS, RoamSch |
| WIO | O | 1 | — | 0 | — | Нет | BPOS, RoamSch |
| STB | B | 1 | 1 | 0 | 0 | Нет | BPOS, RoamSch |
| STO | O | 1 | — | 0 | — | Нет | BPOS, RoamSch |
| USO/F1B/F2B | B/O | 1 | 1 | 0 | 0 | **Да** | + CAMEL/SMS deletion |
| FZB/FTB | — | — | — | — | — | — | FZ_LOCK (отдельный путь) |
| 533/ORD/NRB | — | — | — | — | — | — | Camel barring |

**Источники:** `zhlr.swt:919-999` (SUS chain), `zhlr.swt:1108-1125` (SUS_CMD), `zhlr.swt:1110-1117` (ODB commands), `zhlr.swt:2951-2999` (IMS ODB XML), `zhlr.swt:1384-1392` (BPOS).

---

### 3.2. HLR (Ericsson) — `ghlr.swt`

**Что это:** Shared HLR-логика для Ericsson (потребляется vendor-файлами).

**Особенность:** `ghlr.swt` не содержит entry point `SuspendHLR` — это **разделяемая библиотека** F:/S: блоков, используемая vendor-специфичными файлами (`eric.swt` и др.).

**WIB в ghlr.swt используется в трёх цепочках проверки** (все возвращают B или Y для WIB):

| Цепочка | Переменная | Результат для WIB | Назначение |
|---|---|---|---|
| `CHK_OLD_SUS_TYPE_FT` (line 1776-1794) | `%EFFECTIVE_FREE_TEXT_REASON` | **B** (Both) | Определение направления блокировки по старому reason |
| `CHK_OLD_SUS_TYPE_RC` (line 1797-1815) | `%EFFECTIVE_REASON_CODE` | **B** (Both) | То же, но по reason_code (exact match) |
| `CHK_OLD_SUS_FT` (line 1817-1835) | `%EFFECTIVE_FREE_TEXT_REASON` | **Y** (валидный) | Проверка, является ли старый reason suspend-кодом |

**Мульти-Lock логика** (line 1664-1674):
```
SUSPEND_CMD_NEW → SUSPEND_CMD_BOTH_CHK → SUS_HLR_CHK_BOTH → SUS_ONLY_BOTH
```
Для WIB: `EFFECTIVE_REASON_MODE = "B"` → `SUSPEND_CMD_BOTH_CHK` → `SUS_HLR_CHK_BOTH`. Если абонент ещё не B-заблокирован → `SUS_ONLY_BOTH` (vendor-specific команда).

**WIB НЕ в KP-списке** (line 1870-1873): KP-коды = `USO, F1O, F2O, CLB, BDB, BUD, USB, F1B, F2B`. WIB → стандартный suspend без keep-priority.

---

### 3.3. HSS (Huawei) — `hua_v600.swt` (префикс `hua:`)

**Что это:** Home Subscriber Server для LTE/EPS — управление профайлом абонента в 4G.

**Entry point:** `ghss:C:SuspendEPS:%CHK_SUSPENDEPS` (строка 256)

**Цепочка для WIB:**
```
CHK_SUSPENDEPS → CHK_SUS_IBR_DUMMY → CHK_SUS_VNW_DUMMY
WIB != IBR, != VNW → SET_SMSO_SMST_SUS + SUSPEND_EPS_CMD_CHK_SIM
SUSPEND_EPS_CMD_CHK_SIM → CHK_SUSPEND_FZ
WIB не FZB/FTB, не 533/ORD/NRB/IMB → CHK_SUSPEND_NEW
CHK_SUSPEND_NEW → SUSPEND_EPS_CMD_CHK_KP
WIB не в KP-списке → SUSPEND_EPS_CMD
```

**Команда для WIB:**
```
MOD ODBPOS: IMSI="<IMSI>", ODBPOS=BAPOS;
```

**Что делает:** `BAPOS` = **B**ar **A**ll **P**acket-domain **O**riginated **S**essions — полностью запрещает все PS (packet switched) сессии. Абонент не может использовать мобильный интернет.

**WIB в `CHK_BLOCKED_NOT_KP`** (line 313): WIB классифицируется как "not keep-priority" — абонент **теряет** приоритет/KP статус при блокировке (в отличие от USO, F1O, F2O и т.д.).

**Источники:** `hua_v600.swt:256-331` (SUS chain), `hua_v600.swt:1539-1541` (SUSPEND_EPS_CMD), `hua_v600.swt:296-326` (CHK_BLOCKED_NOT_KP).

---

### 3.4. HSS (ZTE) — `hua_v600.swt` (префикс `zte:`)

**Команда для WIB:**
```
Mod ODB:IMSI=<IMSI>,BPOS=1;
```

**Что делает:** `BPOS=1` — Bar all Packet-domain Originated Sessions. Аналогично Huawei HSS, но синтаксис ZTE.

Плюс — IMS ODB XML (идентично ZTE HLR, см. п.3.1): оба barring (BOC+BIC) = `0`.

**Источники:** `hua_v600.swt:3201-3205` (ZTE HSS command), `hua_v600.swt:3363-3411` (IMS ODB XML).

---

### 3.5. HSS (Ericsson) — `erhss.swt`

**Что это:** Ericsson HSS для управления VoLTE/IMS профилями.

**Два entry point:**

| Entry point | Поведение для WIB |
|---|---|
| `SusSub_HSS` (line 450) | **Ничего** — WIB не входит в {533, ORD, NRB} |
| `SuspendSub_HSS` (line 227) | `hssSuspendResume` с кодом `0` (unblock) — это DAC/cancel flow (пред-шаг перед удалением) |

**Вывод:** Ericsson HSS не выполняет suspend-команду для WIB через стандартный entry point. Только причины 533/ORD/NRB триггерят фактический suspend (`hssSuspendResume` с `BLOCK_CODE=3`).

---

### 3.6. AAA (Ericsson) — `esaaa.swt`

**Что это:** AAA-сервер для широкополосного доступа (FTTB/FTTG).

**Entry point:** `aaa:C:SuspendSub_AAA:%CHK_DAC_AAA%CHK_SUS_SUPPLSRV%CHK_DACBLOCK` (строка 298)

**Команда для WIB:**
```xml
""
<chat><jc>
<dm name="InvStrMthd"><string>SCCMDLSTBEAN</string><string>setAccountLock</string></dm>
<string>7{subscriber_no}</string>
<string>LCK_USER</string>
<string>true</string>
</jc></chat>
```

**Что делает:** Вызывает `setAccountLock` — блокирует аккаунт на AAA-сервере с типом блокировки `LCK_USER` (пользовательская/добровольная). Параметр `true` = применить блокировку (при resume — `false`).

**Трёхуровневая система блокировок:**

| Уровень | Rule | Reason-коды | Lock type | Смысл |
|---|---|---|---|---|
| 1 | `SUS_AAA_REASONS` | BLB, BIB, SUB, LOB, OTB, PPB, RMB, S1B, STB, STO, USB, USO | `LCK_ADMIN` | Административная блокировка |
| 2 | `SUS_AAA_REASONS_2` | BDB, CLB, F1B, F1O, F2B, F2O, FAB, FIB | `LCK_FINANCE` | Финансовая блокировка |
| **3** | **`SUS_AAA_REASONS_3`** | **WIB, WIO** | **`LCK_USER`** | **Пользовательская (self-care) блокировка** |

**Разница:** WIB — единственный нетипичный reason, дающий user-level блокировку вместо admin/finance. Означает, что блокировка инициирована самим пользователем (через self-care портал/приложение).

**Источники:** `esaaa.swt:298-323` (SUS chain + reason mapping), `esaaa.swt:31-34` (lock type definitions).

---

### 3.7. IPTV — `ciptv.swt`

**Что это:** IPTV middleware (Cisco/IPTV платформа).

**Entry point:** `iptv:C:SuspendSub_IPTV:%SUSPEND_IPTV%CHK_DACBLOCK` (строка 216)

**Команда для WIB:**
```xml
""
<chat><jc>
<dm name="InvStrMthd"><string>SCIPTVSOAP</string><string>setAccountBlock</string></dm>
<string>{subscriber_no}</string>
<string>self</string>
<string>1</string>
</jc></chat>
```

**Что делает:** Вызывает `setAccountBlock` — блокирует IPTV-подписку. Параметр `self` = тип блокировки "пользовательская". Параметр `1` = заблокировать (при resume — `0`).

| Reason-коды | block_type | Смысл |
|---|---|---|
| **WIB, WIO** | **`self`** | Пользовательская блокировка |
| Все остальные (non-DAC) | `admin` | Административная блокировка |
| DAC транзакция | `admin` | Деактивация |

**Источники:** `ciptv.swt:146-174` (SUS chain), `ciptv.swt:60-66` (block type definitions).

---

### 3.8. HPSA — `hpsa.swt`

**Что это:** HSPA (HSS-PS-Application) — платформа для управления MSO365/GPRS-сервисами.

**Entry point:** `hpsa:C:SuspendSub_HPSA:%CHK_SUS_IBR_DUMMY` (строка 315)

**Команда для WIB:**
```xml
""
<chat><jc>
<dm name="InvStrMthd"><string>HPSASOAP</string><string>blockUnblockCtn</string></dm>
<string>{subscriber_no}</string>
<string>block</string>
<string>1</string>
[<string>BNDLID={mso365_bndlid}</string>]   ← только если есть MSO365 bundle
</jc></chat>
```

**Что делает:** Вызывает `blockUnblockCtn` (Block/Unblock CTN) — блокирует абонента на HPSA платформе.

| block_reason | Reason-коды | Смысл |
|---|---|---|
| **`1`** | **WIB, WIO, STB, STO** | **Добровольная (subscriber-initiated)** блокировка |
| `2` | Все остальные | Системная/административная |
| — | IBR, VNW | Пропускается (DUMMY_CMD) |

**Условие генерации (GENERATE_CHECK):** команда отправляется только если `mso365_ind=Y` OR `gprsvc_ind=Y` OR `vcpedf_ind=Y`.

**Источники:** `hpsa.swt:315-330` (SUS chain + block reason), `hpsa.swt:324-328` (command template).

---

### 3.9. MSQ (JSON v2) — `gfmsq.swt`

**Что это:** Конвергентная платформа управления (FMSq / JSON2 API).

**Entry point:** `msq_json2:C:SuspendSub_MSQ:%CHK_SUS_JSON2_IBR_DUMMY` (строка 2121)

**Команда для WIB** (уникальная операция!):
```json
{
  "operation": "wiblockSubscriber",
  "ctn": "{subscriber_no}",
  "imsi": "{imsi}",
  "simType": "{network2}",
  "reason_code": "WIB"
}
```

**Что делает:** Вызывает `wiblockSubscriber` — специализированная операция блокировки для WIB/WIO. Отличается от стандартного suspend тем, что передаёт IMSI и simType (тип сети), а не convergence-параметры.

**Сравнение операций:**

| Причина | operation | Параметры | Описание |
|---|---|---|---|
| **WIB/WIO** | **`wiblockSubscriber`** | ctn, imsi, simType, reason_code | **Спец. блокировка (Widget Initiated)** |
| STO, STB, BLB | `suspendSubscriber` | ctn, reason_code + convergence params | Стандартная блокировка |
| Нет предыдущей причины | `firstblockSubscriber` | ctn, imsi, simType, reason_code | Первая блокировка |

**Условие генерации:** `network=G||S||cncnet_ind=G` + не MGR + не SMO/SMB, **или** `network=T` + `gfntft_ind=Y`.

**Источники:** `gfmsq.swt:2121-2141` (SUS chain), `dat\inc\INC_WI_SUS_JSON2.msq.inc` (JSON body).

---

### 3.10. BBI (Corbina FTTB) — `bbi.swt`

**Что это:** Широкополосный доступ Corbina (FTTB).

**Entry point:** `bbi:C:SuspendSub_BBI:%SUS_SUB_BBI` (строка 15)

**Команда для WIB:**
```xml
""
<chat><jc>
<dm name="InvStrMthd"><string>SCCOBBISOAP</string><string>setLocAccess</string></dm>
<string>{subscriber_no}</string>
<int>0</int>
</jc></chat>
```

**Что делает:** Вызывает `setLocAccess` — отключает локальный доступ (internet) на платформе Corbina. `0` = заблокировать, `1` = разблокировать.

**WIB не отличается** от других reason-кодов (BLB, OTB, STB, STO, WIO) — все дают одну и ту же команду `setLocAccess(state=0)`. Reason-код работает только как gate: если он в списке `|BLB|OTB|STB|STO|WIB|WIO|` → блокируем, если нет → ничего не делаем.

**Источники:** `bbi.swt:7-46` (полный SUS chain + command).

---

### 3.11. DVB-H (Mobile TV) — `acdvbh.swt`

**Что это:** Мобильное ТВ (DVB-H / On-Demand Broadband).

**Entry point:** `dvbh:C:SuspendSub_DVBH:%CHK_SUS_DVBH` (строка 157)

**Команда для WIB:**
```xml
""
<chat><jc>
<dm name="InvStrMthd"><string>SCCMDLSTBEAN</string><string>suspendODBENS</string></dm>
<string>7{subscriber_no}</string>
<string>SUS{call_id}</string>
</jc></chat>
```

**Что делает:** Вызывает `suspendODBENS` (Suspend On-Demand Broadband Enterprise Subscription) — блокирует ENS-подписку на мобильном ТВ.

| Reason-коды | Метод | Тип подписки |
|---|---|---|
| **WIB, BLB, OTB, STB, STO** | **`suspendODBENS`** | **Enterprise** |
| Остальные | `suspendODBPPS` | Pre-Paid |

**Источники:** `acdvbh.swt:148-161` (SUS chain), `acdvbh.swt:97-98` (method definitions).

---

### 3.12. CMD — `cmd.swt`

**Что это:** CMD-интерфейс (SCCMDP) — платформа управления дополнительными сервисами (SDB, DTM3, GVISAC и т.д.).

**Entry point:** `cmd:C:SuspendSub_CMD:%CHK_SUS_IBR_DUMMY` (строка 475)

**Поведение для WIB:**
- WIB **не входит** в список freeze-причин (FZB, FTB, OFB, 533, ORD, NRB, IMB)
- → `BLK_FZB_CMD` **не вызывается**
- → Никакая специальная suspend-команда на CMD не отправляется

**Единственное влияние WIB:** через `CHK_OLD_SUS_TYPE_FT5` (возвращает "B") — используется **только при resume** (RSM) для определения, нужно ли отправить `changeSuspendFull` для снятия баррингов.

**Источники:** `cmd.swt:475-510` (SUS chain), `cmd.swt:3463-3480` (CHK_OLD_SUS_TYPE_FT), `cmd.swt:3501-3510` (SET_SUS_FULL_ZERO/ONE).

---

### 3.13. INNC — `innc.swt`

**Что это:** IN/Charging платформа (Nokia Converged) — управление периодическими списаниями (Periodic Charges) и аккумуляторами.

**Entry point:** `innc:C:SuspendSub_INNC:%CHK_DACBLOCK` (строка 5634)

**Команды для WIB:**

| # | Команда | Что делает |
|---|---|---|
| 1 | Множество вызовов `INN_DeletePC` (SOAP) | Удаление всех активных Periodic Charges |
| 2 | Вызовы `INN_SetAccumulator` | Обнуление аккумуляторов (балансов) |
| 3 | **DVBHL_SUS_CHK** → удаление DVBH periodic charge | **WIB-специфично** — т.к. WIB входит в `SUS_RSN_CODE` |

**WIB в `SUS_RSN_CODE`** (line 110): `BLB,OTB,STB,STO,WIB` — только эти 5 кодов триггерят DVBH-блок. Для reason-кодов **не входящих** в список, DVBH-блок пропускается.

**Маршрутизация по customer_type** (line 5645):
- **Prepaid (P):** `CHK_SUS_PC` — удаление PC по старой схеме
- **Postpaid:** `CHK_SUS_AC` (accumulator cleanup) + `CHK_SUS_ALCO` + `CHK_SUS_INNC_DVBH` + `CHK_BALANCE_SUS`

**Источники:** `innc.swt:5634-5694` (SUS chain), `innc.swt:110` (SUS_RSN_CODE).

---

### 3.14. PCRF (Huawei) — `hupcrf.swt`

**Что это:** Policy and Charging Rules Function — управление политиками и сессиями данных.

**Entry point:** `hupcr:C:SuspendSub_PCRF:%SUS_REASON_CODE_CHK` (строка 1483)

**Команды для WIB (две команды):**

| # | Метод | Параметры | Что делает |
|---|---|---|---|
| 1 | `updateSubWithDeaSession` | subscriber_no, BLOCK_NRB | Устанавливает extension-атрибут "BLOCK_NRB" — помечает абонента как заблокированного в policy-системе |
| 2 | `deactivateSession` | subscriber_no | **Принудительно разрывает все активные data-сессии** абонента — мгновенно отключает интернет |

**Источники:** `hupcrf.swt:1483-1498` (SUS chain + commands).

---

### 3.15. BSBW (BroadSoft) — `bsbw.swt`

**Что это:** BroadSoft BroadWorks — платформа VoIP/SIP телефонии.

**Команда для WIB** (условие: `ipcprv_ind=Y`):
```xml
<dm name="InvStrMthd"><string>suspendSubscriber</string></dm>
<string>{subscriber_no}</string>
<string>@x.sip.beeline.ru</string>
<string>Intercept User</string>
```

**Что делает:** Блокирует VoIP-подписчика. "Intercept User" — сервис-имя означает, что звонки блокируются/перехватываются (маршрутизируются на announcement/voicemail).

---

### 3.16. ECC — `ecc.swt`

**Команда:** `blockAccount` на платформе SCECCP. Блокирует аккаунт на E-Commerce Center.

---

### 3.17. FMTN (PBX/FMC) — `laosp.swt`

**Команда:** `blockAccount` на платформе SCFMTNSOAP. Блокирует PBX/FMC (Fixed-Mobile Convergence) аккаунт.

Варианты: `fmn`, `ldfmn`, `fmnmk`, `fmna` — одинаковая команда, разные условия срабатывания по индикаторам.

---

### 3.18. MTP (mTopUp) — `ismtp.swt`

**Команда:** `suspendService` с параметрами: subscriber_no, BAN, reason_code="WIB". Блокирует услугу мобильного пополнения счёта.

Условие: `network=G` + `mtpplt_ind=Y` (или `network=J` + `CHK_OTTVE_GAME_IND=JY`, или `cncnet_ind+mtpplt_ind=GY`).

---

### 3.19. TVE (KATVE) — `katve.swt`

**Команда:** `HouseholdSuspend` — приостановка домохозяйства (семейного аккаунта). Блокирует всю IPTV-подписку домохозяйства. `role_id=16` для SUS (vs `17` для DAC).

---

### 3.20. ICLGC (Intellicom) — `iclgc.swt`

**Команда:** `ctnBlock` — блокирует CTN (Call Telephone Number) на шлюзе Intellicom. Останавливает SMS-обработку.

Условие: `stpsms_ind=Y` + нет предыдущего free-text reason (первая блокировка).

---

### 3.21. SMSNE — `smsne.swt`

**Поведение для WIB:** Проверяет mapping-таблицу (`FindValByKey`) для комбинации SUS+WIB. Если найден periodic charge ID → добавляет periodic charge командой:
```
C {subscriber}, SERVICE=PPS, OBJECT=PERIODIC_CHARGE, CHARGE_ID={id}, START_DATE={date}, END_DATE={date}
```
Удаление подписчика (`D {subscriber}`) выполняется **только** для DAC@DBL, не для WIB.

---

### 3.22. FunBox / FunTS — `smsgw.swt`

**Команда:** `blockCTN` — блокирует CTN на A2P SMS-шлюзе.

| Платформа | Условие | Особенность |
|---|---|---|
| FunBox | `network=J` + `a2psms_ind=Y` | Только для Java-региона |
| FunTS | `tgsms_ind=Y` + первая блокировка | Только при `IS_EFFECTIVE_OLD_REASON_EMPTY=Y` |

---

### 3.23. OFD — `mtofd.swt`

**Команда:** `blockCtn` (Block CTN) на OFD-платформе с параметрами: resource_name, subscriber_no, reason_code=WIB.

Варианты: `mtofd` (MicroTech, несколько подтипов: OFDPAC, OFDX5, OFDFIX) и `llofd` (LL OFD, условие: `ardofd_ind=Y`).

---

### 3.24. TF (GFPR) — `gfprt.swt`

**Команда:** XML-сообщение через Sun MQ:
```xml
<ns0:message xmlns:ns0='urn:sc-tf:messaging'>
    <ns0:subscriber-data>
        <ns0:msisdn>{subscriber_no}</ns0:msisdn>
    </ns0:subscriber-data>
    <ns0:operation>
        <ns0:name>suspendSubscriber</ns0:name>
    </ns0:operation>
</ns0:message>
```
Приостанавливает подписчика на Tele2 Flexible Platform.

---

### 3.25. ENUM (henum) — `ctenu.swt`

**Команда:**
```
RMV DNAPTRREC: E164NUM="7{subscriber}", ZONENAME="enum.arpa", ENUMFLAG=ENS_TYPE;
```

**Что делает:** Удаляет DNS NAPTR-запись для номера абонента в зоне `enum.arpa`. Абонент становится недоступным через SIP/VoLTE — входящие VoLTE-звонки не могут разрешить SIP URI.

До 3 записей может быть удалено (в зависимости от индикаторов):
- **RECVC** (Receive Callback) — если `RECVC_NO_VOLTE3_VAL=Y`
- **VOLTE3** — если `VOLTE3_IND=Y` + `vrtnm1_ind=N`
- **LABBF1** — если `labbf1_ind=Y` + `VOLTE3_IND=N`

---

### 3.26. eSIM — `hhlr.swt` (hesim) / `nok.swt` (nesim)

**hesim (Huawei eSIM):** Для WIB выполняется стандартная HLR-последовательность suspend (ODB barring, CAMEL модификация) для вторичного IMSI eSIM-профиля. Условие: `mimsi_ind=Y` + `E_SIM_PARAM` exists.

**nesim (Nokia eSIM):** Полная HLR-последовательность suspend для Nokia: SMS barring, ODB-IC/OC, roaming restrictions, GPRS barring, supplementary services barring, CAMEL, BSxx flags. Условие: `mimsi_ind=Y` + `E_SIM_PARAM` + `MARKET_VIP`.

---

### 3.27. B2B TAS — `b2btas.swt`

**Команды для WIB:**
- `vnBlocked` (REST) — блокирует виртуальные и базовые номера. Параметры: number_type (main/virtual), blocked=true, direction (incoming/outgoing/both — **WIB=both**)
- `vnProfileCamel` (REST) — обновляет CAMEL-профиль для заблокированного состояния

---

### 3.28. SCIM — `b2btas.swt`

**Команды для WIB** (условие: первая блокировка, `IS_OLD_REASON_EMPTY=Y`):
- `blockService` (REST) — блокирует VoLTE (WCVLTE) сервис, если `wcvlte_ind=Y`
- `blockService` (REST) — блокирует Lab KSS сервис, если `labkss_ind=Y`

---

### 3.29. PBE — `pbe.swt`

**Команда:** `Suspend_PBE` (SOAP) — блокирует контент/партнёрские биллинг-сервисы (PRTUBB, PRTCNV).

Параметры: operation="BLOCKS", operation_type="BLOCK", reason_code="WIB", subscriber_no, SOC name.

Пропускается только для reason NRB.

---

### 3.30. SMS Router — `smsr.swt`

**Поведение для WIB:** **Ничего не делает.** WIB не входит в распознанные reason-коды для SMS Router ({533, ORD, NRB, IMB}).

---

### 3.31. CTENU — `ctenu.swt`

**Команда:** `RMV DNAPTRREC` (аналогично henum, п.3.25). Удаляет ENUM DNS-записи.

---

### 3.32. HSS (Nokia) — `SusSub_HSS` в `erhss.swt`

**Поведение для WIB:** Аналогично Ericsson HSS — WIB не триггерит suspend-команду. Только 533/ORD/NRB.

---

## 4. Вариативность — от чего зависит разница между кейсами

### 4.1. Зависимость от MODE (B vs O)

Самый фундаментальный фактор. `EFFECTIVE_REASON_MODE` (3-й символ кода):
- **B** (Both) → барринг входящих + исходящих
- **O** (Outgoing) → барринг только исходящих

| Аспект | WIB (B) | WIO (O) |
|---|---|---|
| HLR ODB | BOC=1, **BIC=1** | BOC=1, BIC не ставится |
| IMS BIC XML | `<IncomingBarring>0</IncomingBarring>` | **не генерируется** |
| INNC SUS_RSN_CODE | **WIB в списке** | **WIO НЕ в списке** |
| DVB-H SUS_RSN_CODES | **WIB в списке** | **WIO НЕ в списке** |
| B2B TAS direction | **both** | outgoing only |

### 4.2. Зависимость от типа блокировки (User vs Admin vs Finance)

Трёхуровневая система на AAA/IPTV/HPSA:

| Платформа | WIB → | Другие → | Параметр |
|---|---|---|---|
| AAA (esaaa) | `LCK_USER` | `LCK_ADMIN` / `LCK_FINANCE` | lock type |
| IPTV (ciptv) | `self` | `admin` | block type |
| HPSA | `1` (voluntary) | `2` (system) | block reason |

**Смысл:** WIB = блокировка инициирована пользователем (self-care), а не системой/администрацией/финансами.

### 4.3. Зависимость от Keep-Priority (KP)

| Группа | Reason-коды | Поведение |
|---|---|---|
| **KP (keep-priority)** | USO, F1O, F2O, CLB, BDB, BUD, USB, F1B, F2B | Сохраняют приоритет + доп. CAMEL/SMS команды |
| **Not-KP** | **WIB**, WIO, STB, STO, BLB, OTB, PPB, FIB, BIB, S1B, FAB, ZTB, GSB, LOB, DSB, RMB, SUB | **Теряют приоритет**, нет доп. команд |

WIB — **не** keep-priority. Абонент теряет CAMEL-подписки, SMS-настройки и приоритеты (в отличие от USO).

### 4.4. Зависимость от предыдущего reason-кода (multi-lock)

Несколько NE проверяют `IS_EFFECTIVE_OLD_REASON_EMPTY`:

| Сценарий | Поведение |
|---|---|
| **Первая блокировка** (old reason пуст) | `firstblockSubscriber` (MSQ), полный блок, ICLGC/FunTS срабатывают |
| **Повторная блокировка** (old reason есть) | `wiblockSubscriber` (MSQ), ICLGC → DUMMY_CMD (пропуск), FunTS → пропуск |

### 4.5. Зависимость от customer_type (Prepaid vs Postpaid)

| Аспект | Prepaid (P) | Postpaid (B) |
|---|---|---|
| INNC | `CHK_SUS_PC` (удаление PC по старой схеме) | `CHK_SUS_AC` (accumulator cleanup) + `CHK_SUS_ALCO` + DVBH |
| HLR RoamSch | Не отправляется | `RoamSch=CS_VC, PsRoamSch=PS_VC` |
| IMS ODB RSM | Доп. проверка для prepaid (`CUST_SUB_TYPE:P`) | Отдельная проверка для keep-priority кодов |

### 4.6. Зависимость от network type

| Network | Что меняется |
|---|---|
| **G** | Большинство команд отправляется (стандартный GSM) |
| **J** | FunBox (A2P SMS), TVE (OTT), ICLGC — работают только для J |
| **T** | MSQ: отдельный путь через `CHK_SUS_NETWORK_T` + `gfntft_ind` |
| **F/M** | INNC: входит в GENERATE_CHECK, но команды те же |
| **L** | INNC: CorporateAccount (другой entry-point) |
| **E** | EPS (LTE): `network2=E` → SuspendEPS на HSS |

### 4.7. Зависимость от GENERATE_CHECK (per-device булево)

Каждая строка в `DEV_COMMANDS` имеет свой `GENERATE_CHECK` — предикат, определяющий, дойдёт ли команда до оборудования:

| Строка (xlsx) | NE | GENERATE_CHECK (упрощённо) | Смысл |
|---|---|---|---|
| 1 | HLR | `network:G || cncnet_ind:G` + `net_fix:G` | Только GSM-сеть с фиксированной сетью |
| 5 | INNC | `(G||F||M||cncnet_ind:G)` + `customer_type:P` | Только prepaid в GSM/F/M сетях |
| 17 | EPS/LTE | `(G||cncnet_ind:G)` + `network2:E` | Только LTE-абоненты |
| 22 | MSQ JSON2 | `(G||S||cncnet_ind:G)` + not MGR/SMO/SMB, **или** `T`+`gfntft_ind` | GSM/S сеть, не системная блокировка |
| 25 | HPSA | `mso365_ind:Y || gprsvc_ind:Y || vcpedf_ind:Y` | Только если есть соответствующий сервис |
| 27 | VRZ (Veraz) | пустой → always true | Безусловно для всех |

**Движок вычисления:** `scspl_split.c`, функция `Scspf_CalcGenerateCheck` (line ~2596). Преобразует infix-выражение (`Equal:%field:value` объединённые `&&`/`||`) в postfix, вычисляет через стек. Возвращает `SUCCESS` → команда генерируется, `FAILURE` → пропускается.

---

## 5. Сводная таблица всех команд для WIB

| # | Оборудование | Файл | Метод/API | Что делает (простыми словами) | WIB-специфика |
|---|---|---|---|---|---|
| 1 | ZTE HLR | zhlr.swt | `Mod ODB:...,BOC=1,BIC=1` | Запрет всех звонков (вх+исх) | **Both** barring |
| 2 | ZTE HLR | zhlr.swt | `Mod ODB:...,BPOS=1` | Установка GSM-позиционирования | — |
| 3 | ZTE HLR (postpaid) | zhlr.swt | `Mod BscEx:...,RoamSch=CS_VC,PsRoamSch=PS_VC` | Установка roaming schedule в HPLMN | — |
| 4 | ZTE HLR (VoLTE) | zhlr.swt | `SET REPOSDATA:...` + IMS XML | Запрет VoLTE-звонков (вх+исх) | **Both** BOC+BIC XML |
| 5 | Huawei HSS | hua_v600.swt | `MOD ODBPOS:...,ODBPOS=BAPOS` | Полный запрет PS-сессий (интернета) | Not keep-priority |
| 6 | ZTE HSS | hua_v600.swt | `Mod ODB:IMSI=...,BPOS=1` | Полный запрет PS-сессий | — |
| 7 | ZTE HSS (VoLTE) | hua_v600.swt | IMS XML (BOC+BIC=0) | Запрет VoLTE через HSS | **Both** |
| 8 | AAA | esaaa.swt | `setAccountLock(...,LCK_USER,true)` | Блокировка аккаунта (пользовательская) | **LCK_USER** (self-care) |
| 9 | IPTV | ciptv.swt | `setAccountBlock(...,self,1)` | Блокировка IPTV (пользовательская) | **self** block type |
| 10 | HPSA | hpsa.swt | `blockUnblockCtn(...,block,1)` | Блокировка CTN (добровольная) | **reason=1** (voluntary) |
| 11 | MSQ JSON2 | gfmsq.swt | JSON `wiblockSubscriber` | **Спец. блокировка** (WI-тип) | **Уникальная операция** |
| 12 | BBI | bbi.swt | `setLocAccess(...,0)` | Отключение широкополосного доступа | Нет различий |
| 13 | DVB-H | acdvbh.swt | `suspendODBENS` | Блокировка ENS-подписки (ТВ) | **ENS** (Enterprise) |
| 14 | ECC | ecc.swt | `blockAccount` | Блокировка аккаунта (E-Commerce) | — |
| 15 | FMTN | laosp.swt | `blockAccount` | Блокировка PBX/FMC аккаунта | — |
| 16 | MTP | ismtp.swt | `suspendService(...,WIB)` | Блокировка mTopUp сервиса | reason передаётся |
| 17 | TVE | katve.swt | `HouseholdSuspend` | Приостановка домохозяйства (IPTV) | role_id=16 |
| 18 | PCRF | hupcrf.swt | `updateSubWithDeaSession` + `deactivateSession` | Установка блок-флага + разрыв сессий | — |
| 19 | BSBW | bsbw.swt | `suspendSubscriber(...,Intercept User)` | Блокировка VoIP с перехватом | — |
| 20 | ICLGC | iclgc.swt | `ctnBlock` | Блокировка CTN (SMS-шлюз) | Только при первой блокировке |
| 21 | FunBox | smsgw.swt | `blockCTN` | Блокировка A2P SMS | Только network=J |
| 22 | FunTS | smsgw.swt | `blockCTN` | Блокировка SMS (Tele2) | Только первая блокировка |
| 23 | OFD | mtofd.swt | `blockCtn` | Блокировка CTN (OFD) | — |
| 24 | TF (GFPR) | gfprt.swt | `suspendSubscriber` (XML/MQ) | Suspend на Tele2 Flexible Platform | — |
| 25 | ENUM | ctenu.swt | `RMV DNAPTRREC` | Удаление DNS-записи (отключение VoLTE) | — |
| 26 | eSIM (Huawei) | hhlr.swt | HLR ODB/CAMEL cmds | Suspend для eSIM-профиля | — |
| 27 | eSIM (Nokia) | nok.swt | Полный HLR suspend | Комплексный suspend для eSIM | — |
| 28 | B2B TAS | b2btas.swt | `vnBlocked` (REST) | Блокировка виртуальных/базовых номеров | **Both** direction |
| 29 | SCIM | b2btas.swt | `blockService` (REST) | Блокировка VoLTE/KSS сервиса | Только первая блокировка |
| 30 | PBE | pbe.swt | `Suspend_PBE` | Блокировка партнёрских сервисов | — |
| 31 | INNC | innc.swt | `INN_DeletePC` x N | Удаление всех periodic charges | + DVBH PC deletion |
| 32 | CMD | cmd.swt | *(ничего для WIB)* | Нет suspend-команды | WIB не в FZ-списке |
| 33 | SMS Router | smsr.swt | *(ничего для WIB)* | Нет команды | WIB не распознан |
| 34 | Ericsson HSS | erhss.swt | *(ничего для WIB)* | Нет команды | Только 533/ORD/NRB |

---

## 6. Аномалии и зависимости

### 6.1. Аномалии

1. **WIB без WIO в innc.swt и acdvbh.swt** — только 5 кодов `BLB,OTB,STB,STO,WIB`. WIO не распознан. Если придёт suspend с WIO для этих NE, команда не сгенерируется (reason не в списке → suspend пропускается).

2. **Дублирующий GENERATE_CHECK в строке 22 (msq_json2)** — `network:G` дублируется дважды в составе `(G||cncnet_ind:G||G||M)`. Вероятно, copy-paste артефакт.

3. **hpsa.swt: WIB в suspend-списке, но НЕ в resume-списке** — при suspend WIB даёт block-reason `1`, но при resume (line 345) WIB отсутствует в списке `RSOT,RSBO,NR,WIO` → unblock-reason всегда `2`. WIO есть в обоих, WIB — только в suspend.

4. **nfc.swt vs hpsa.swt: рассинхрон** — nfc использует `%REASON_TR` и `%reason_code` (lowercase), hpsa использует `%EFFECTIVE_REASON_CODE` и `%EFFECTIVE_FREE_TEXT_REASON`. При change-транзакциях (CCD/CHD) они могут давать разные результаты.

5. **cmd.swt не распознаёт S2B** — цепочка `CHK_OLD_SUS_TYPE_FT` заканчивается на `WIO:O` без фоллбэка на S2B (в отличие от ghlr, где есть `S2B:B`). Если reason=S2B, cmd вернёт пусто вместо `B`.

6. **Строки с пустым GENERATE_CHECK** — SuspendSub_VRZ (строка 27), SuspendSub_OFD (35,39), SuspendSub_BSBW (37), Suspend_CTENU (40,55), SuspendEnum (41-43,46,57-58), Block_B2B (48), SusSub_HSS (49), Suspend_SMS (50-52), Suspend_PBE (53), Block_SCIM (54), SuspendSub_PCRF (56) — срабатывают безусловно для любого reason, включая WIB. Нет reason-специфичной логики.

7. **Строка 53 (Suspend_PBE, pbe)** — `KEEP_CONVRS_IND` пуст (не `Y`), в отличие от всех остальных строк. Может быть пропущена конверсия.

8. **Строки 5 и 6 (innc/innc2)** — два разных NE-суффикса (5 и 7) указывают на один и тот же `DVC_TRX_NM_CD` = `SuspendSub_INNC`, но разные `DVC_TP` (`innc` vs `innc2`). innc2 — новое подключение того же NE-типа (дата: 09.10.2023).

9. **Ericsson HSS и SMS Router игнорируют WIB** — WIB не входит в их распознанные reason-коды ({533, ORD, NRB, IMB}).

### 6.2. Ключевые зависимости

```
WIB (reason_code)
  │
  ├── MODE = "B" (Both)
  │     └── HLR: BOC+BIC barring; IMS: BOC+BIC XML; B2B: both directions
  │
  ├── CATEGORY = "User/Self-care" (не Admin, не Finance)
  │     └── AAA: LCK_USER; IPTV: "self"; HPSA: reason=1
  │
  ├── KEEP-PRIORITY = No (теряет приоритет)
  │     └── Huawei HSS: BAPOS (bar all); нет доп. CAMEL/SMS команд
  │
  ├── WI-TYPE (Widget Initiated)
  │     └── MSQ: уникальная операция "wiblockSubscriber"
  │
  ├── В SUS_RSN_CODE (BLB,OTB,STB,STO,WIB)
  │     └── INNC: DVBH PC deletion; DVB-H: ENS suspend
  │
  ├── НЕ в {533,ORD,NRB,IMB,FZB,FTB}
  │     └── HSS/PCRF/SMSR: стандартный путь (не camel/freeze)
  │
  └── GENERATE_CHECK (per-device предикат)
        └── Определяет, дойдёт ли команда вообще
            (network, customer_type, indicators, trx_src, reason_code exclusions)
```

---

## 7. Структурная схема — ключевые зависимости

### 7.1. Карта reason-кода WIB

```
WIB
 ├── TYPE: WI (Wireline/Widget Initiated)
 ├── MODE: B (Both — входящий + исходящий барринг)
 ├── CATEGORY: User/Self-care (не Admin, не Finance)
 ├── KEEP-PRIORITY: No (теряет KP статус)
 ├── SUS_RSN_CODE: В списке (BLB,OTB,STB,STO,WIB)
 ├── FZ-список: НЕ входит (не FZB,FTB,OFB,533,ORD,NRB,IMB)
 └── KP-список: НЕ входит (не USO,F1O,F2O,CLB,BDB,BUD,USB,F1B,F2B)
```

### 7.2. Сравнение WIB с ключевыми reason-кодами

| Характеристика | WIB | WIO | STB | STO | USO | FZB |
|---|---|---|---|---|---|---|
| Mode | B | O | B | O | O | — |
| HLR BOC | 1 | 1 | 1 | 1 | 1 | — |
| HLR BIC | 1 | — | 1 | — | — | — |
| IMS BOC XML | 0 | 0 | 0 | 0 | 0 | — |
| IMS BIC XML | 0 | — | 0 | — | — | — |
| AAA Lock | LCK_USER | LCK_USER | LCK_ADMIN | LCK_ADMIN | LCK_ADMIN | — |
| IPTV block_type | self | self | admin | admin | admin | — |
| HPSA block_reason | 1 | 1 | 1 | 1 | 2 | — |
| MSQ operation | wiblock | wiblock | suspend | suspend | suspend | — |
| INNC SUS_RSN | да | нет | да | да | нет | нет |
| DVB-H method | ENS | — | ENS | ENS | PPS | — |
| Keep-Priority | No | No | No | No | Yes | — |
| HSS suspend | нет | нет | нет | нет | нет | FZ_LOCK |
| SMS Router | нет | нет | нет | нет | нет | нет |

---

*Документ подготовлен на основе анализа `SUS(WIB).xlsx`, SWT-файлов из `dat\`, и C-ядра сплиттера `scspl_split.c`.*
