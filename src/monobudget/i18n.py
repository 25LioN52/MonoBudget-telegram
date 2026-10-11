"""User-facing strings (Ukrainian and English). Both languages must have the same keys."""

from __future__ import annotations

STRINGS: dict[str, dict[str, str]] = {
    "uk": {
        "week": "Тиждень",
        "month": "Місяць",
        "last_week": "Минулий тиждень",
        "last_month": "Минулий місяць",
        "of": "з",
        "left": "залишилось",
        "over": "понад ліміт",
        "spent": "Витрачено",
        "limit": "ліміт",
        "remaining": "залишок",
        "overspent": "перевищено на",
        "today_track": "Сьогодні: ~{x}, щоб вкластися в тиждень",
        "today_over": "Тижневий ліміт вичерпано",
        "updated": "Оновлено {t}",
        "stale": "⚠️ Дані від {t}",
        "loading": "⏳ Завантажую операції з Monobank (ліміт API — 1 запит на хвилину)…",
        "no_eur": "Не знайдено EUR-рахунків Monobank (або вони не входять в ACCOUNT_IDS).",
        "err_rate_limit": "Monobank обмежив запити — повторю пізніше",
        "err_network": "Немає зв'язку з Monobank — повторю пізніше",
        "err_http": "Помилка Monobank — повторю пізніше",
        "err_parse": "Неочікувана відповідь Monobank — дані збережено",
        "err_auth": "Monobank не приймає токен. Створіть новий на api.monobank.ua і оновіть MONO_TOKEN.",
        "btn_week": "📅 Тиждень",
        "btn_month": "🗓 Місяць",
        "btn_refresh": "🔄 Оновити",
        "btn_all": "📋 Усі операції",
        "btn_back": "⬅️ До звіту",
        "btn_to_week": "⇄ Тиждень",
        "btn_to_month": "⇄ Місяць",
        "refresh_now": "Оновлюю…",
        "refresh_wait": "Наступний запит до Monobank через {s} с",
        "no_spending": "Витрат за цей період немає.",
        "tx_count": "{n} оп.",
        "vs_prev_week": "{x} до мин. тижня",
        "vs_prev_month": "{x} до мин. місяця",
        "vs_prev_none": "мин. період ще завантажується",
        "page": "стор. {n}/{total}",
        "all_transactions": "Усі операції",
        "no_transactions": "Операцій немає.",
        "hold": "⏳",
        "refund": "повернення",
        "notify_expense": "{amount} · {desc} · {category} · тиждень: залишилось {left}",
        "notify_expense_over": "{amount} · {desc} · {category} · тиждень: перевищено на {left}",
        "alert_warn": "⚠️ {period}: витрачено {pct}% ліміту ({spent} з {limit})",
        "alert_over": "🔴 {period}: ліміт перевищено ({spent} з {limit})",
        "period_report_title": "Звіт: {period}",
        "setlimit_usage": "Використання: /setlimit 200 — тижневий ліміт у євро; /setlimit reset — повернути значення з налаштувань ({env}).",
        "setlimit_bad": "Ліміт має бути числом від 1 до 1000000.",
        "setlimit_done": "Тижневий ліміт: {x}. Місячний: {m}.",
        "setlimit_reset": "Ліміт скинуто до значення з налаштувань: {x}.",
        "category_usage": "Використання: /category <фрагмент опису> = <категорія>\nНаприклад: /category mercadona = Продукти\nКатегорії: {names}. Можна вказати і свою назву.",
        "category_bad": "Фрагмент опису — від 2 до 60 символів, назва категорії — до 40.",
        "category_done": "«{pattern}» → {category}",
        "categories_title": "Ваші правила категорій:",
        "categories_empty": "Правил ще немає. Додайте: /category mercadona = Продукти",
        "category_deleted": "Правило видалено.",
        "settings": (
            "<b>Налаштування</b>\n"
            "Тижневий ліміт: {limit}{limit_src}\nМісячний ліміт зараз: {month_limit}\n"
            "Початок тижня: {week_start} · Часовий пояс: {tz}\n"
            "Рахунки: {accounts}\nВиключати власні перекази: {own}\nПовернення зменшують витрати: {refunds}\n"
            "Виключені MCC: {mcc}\nВиключені описи: {descs}\n"
            "Попередження: {warn}% · Сповіщення про кожну витрату: {notify}\n"
            "Опитування Monobank: кожні {poll} хв · Звіт наприкінці періоду: {report}\n"
            "Правил категорій: {overrides}"
        ),
        "limit_src_override": " (змінено командою /setlimit)",
        "all_eur": "усі EUR",
        "yes": "так",
        "no": "ні",
        "none": "—",
        "help": (
            "<b>MonoBudget</b> — бюджет витрат з Monobank.\n\n"
            "/status — закріплене повідомлення зі станом бюджету\n"
            "/week, /month — звіт за категоріями\n"
            "/setlimit 200 — тижневий ліміт (місячний рахується автоматично)\n"
            "/category mercadona = Продукти — правило категорії за описом\n"
            "/categories — ваші правила (з видаленням)\n"
            "/settings — поточні налаштування\n\n"
            "Дані оновлюються кожні кілька хвилин. Рахуються лише EUR-рахунки."
        ),
        "start": "Привіт! Я стежитиму за вашим бюджетом. Стан бюджету — у закріпленому повідомленні.",
        "cmd_start": "Запуск і закріплене повідомлення",
        "cmd_status": "Стан бюджету",
        "cmd_week": "Звіт за тиждень",
        "cmd_month": "Звіт за місяць",
        "cmd_setlimit": "Тижневий ліміт",
        "cmd_category": "Правило категорії",
        "cmd_categories": "Правила категорій",
        "cmd_settings": "Налаштування",
        "cmd_help": "Довідка",
        "weekdays": "пн,вт,ср,чт,пт,сб,нд",
        "months": "січ.,лют.,бер.,квіт.,трав.,черв.,лип.,серп.,вер.,жовт.,лист.,груд.",
    },
    "en": {
        "week": "Week",
        "month": "Month",
        "last_week": "Last week",
        "last_month": "Last month",
        "of": "of",
        "left": "left",
        "over": "over",
        "spent": "Spent",
        "limit": "limit",
        "remaining": "remaining",
        "overspent": "over by",
        "today_track": "Today: ~{x} to stay on track",
        "today_over": "Weekly limit used up",
        "updated": "Updated {t}",
        "stale": "⚠️ Data from {t}",
        "loading": "⏳ Loading transactions from Monobank (API limit: 1 request per minute)…",
        "no_eur": "No Monobank EUR accounts found (or none match ACCOUNT_IDS).",
        "err_rate_limit": "Monobank rate limit — will retry",
        "err_network": "Can't reach Monobank — will retry",
        "err_http": "Monobank error — will retry",
        "err_parse": "Unexpected Monobank response — data kept",
        "err_auth": "Monobank rejected the token. Create a new one at api.monobank.ua and update MONO_TOKEN.",
        "btn_week": "📅 Week",
        "btn_month": "🗓 Month",
        "btn_refresh": "🔄 Refresh",
        "btn_all": "📋 All transactions",
        "btn_back": "⬅️ Back to report",
        "btn_to_week": "⇄ Week",
        "btn_to_month": "⇄ Month",
        "refresh_now": "Refreshing…",
        "refresh_wait": "Next Monobank request in {s} s",
        "no_spending": "No spending in this period.",
        "tx_count": "{n} tx",
        "vs_prev_week": "{x} vs last week",
        "vs_prev_month": "{x} vs last month",
        "vs_prev_none": "previous period still loading",
        "page": "page {n}/{total}",
        "all_transactions": "All transactions",
        "no_transactions": "No transactions.",
        "hold": "⏳",
        "refund": "refund",
        "notify_expense": "{amount} · {desc} · {category} · week left {left}",
        "notify_expense_over": "{amount} · {desc} · {category} · week over by {left}",
        "alert_warn": "⚠️ {period}: {pct}% of the limit spent ({spent} of {limit})",
        "alert_over": "🔴 {period}: limit exceeded ({spent} of {limit})",
        "period_report_title": "Report: {period}",
        "setlimit_usage": "Usage: /setlimit 200 — weekly limit in euros; /setlimit reset — back to the configured value ({env}).",
        "setlimit_bad": "The limit must be a number from 1 to 1000000.",
        "setlimit_done": "Weekly limit: {x}. Monthly: {m}.",
        "setlimit_reset": "Limit reset to the configured value: {x}.",
        "category_usage": "Usage: /category <description part> = <category>\nExample: /category mercadona = Groceries\nCategories: {names}. A custom name works too.",
        "category_bad": "The description part must be 2–60 characters and the category up to 40.",
        "category_done": "“{pattern}” → {category}",
        "categories_title": "Your category rules:",
        "categories_empty": "No rules yet. Add one: /category mercadona = Groceries",
        "category_deleted": "Rule deleted.",
        "settings": (
            "<b>Settings</b>\n"
            "Weekly limit: {limit}{limit_src}\nMonthly limit now: {month_limit}\n"
            "Week starts: {week_start} · Time zone: {tz}\n"
            "Accounts: {accounts}\nExclude own transfers: {own}\nRefunds reduce spending: {refunds}\n"
            "Excluded MCC: {mcc}\nExcluded descriptions: {descs}\n"
            "Warning at: {warn}% · Notify each expense: {notify}\n"
            "Monobank polling: every {poll} min · Period-end report: {report}\n"
            "Category rules: {overrides}"
        ),
        "limit_src_override": " (set with /setlimit)",
        "all_eur": "all EUR",
        "yes": "yes",
        "no": "no",
        "none": "—",
        "help": (
            "<b>MonoBudget</b> — your Monobank spending budget.\n\n"
            "/status — pinned budget status message\n"
            "/week, /month — category report\n"
            "/setlimit 200 — weekly limit (monthly is derived)\n"
            "/category mercadona = Groceries — category rule by description\n"
            "/categories — your rules (with delete)\n"
            "/settings — current settings\n\n"
            "Data refreshes every few minutes. Only EUR accounts are counted."
        ),
        "start": "Hi! I'll keep track of your budget. The status is in the pinned message.",
        "cmd_start": "Start and pin the status",
        "cmd_status": "Budget status",
        "cmd_week": "Week report",
        "cmd_month": "Month report",
        "cmd_setlimit": "Weekly limit",
        "cmd_category": "Category rule",
        "cmd_categories": "Category rules",
        "cmd_settings": "Settings",
        "cmd_help": "Help",
        "weekdays": "Mon,Tue,Wed,Thu,Fri,Sat,Sun",
        "months": "Jan,Feb,Mar,Apr,May,Jun,Jul,Aug,Sep,Oct,Nov,Dec",
    },
}


def t(lang: str, key: str, **values: object) -> str:
    text = STRINGS.get(lang, STRINGS["en"])[key]
    return text.format(**values) if values else text
