# Політика безпеки / Security Policy

## Як повідомити про вразливість

**Не створюйте публічний issue.** Скористайтеся приватним каналом GitHub:
[Security → Report a vulnerability](https://github.com/25LioN52/MonoBudget-telegram/security/advisories/new).

Опишіть проблему, як її відтворити і можливий вплив. **Не надсилайте токени, свій Telegram ID чи справжні виписки** — для демонстрації достатньо синтетичних даних. Я намагаюся відповідати протягом 7 днів. Підтримується лише остання версія образу.

## Якщо токен міг потрапити до сторонніх

- **Monobank:** відкрийте [api.monobank.ua](https://api.monobank.ua/) і створіть новий токен (старий перестане працювати). Він дає доступ лише на читання, але розкриває баланси, операції та IBAN.
- **Telegram:** у [@BotFather](https://t.me/BotFather) → ваш бот → *API Token* → *Revoke current token*. З токеном бота можна читати повідомлення, які йому надсилають, і писати від його імені.
- Оновіть файли в `secrets/` і перезапустіть контейнер.

## Модель загроз (коротко)

| Ризик | Захист |
|---|---|
| Витік токенів із репозиторію | Токени лише в `secrets/*.txt` (Docker secrets) або `.env`, обидва в `.gitignore`. Сканер секретів у хуках git, у хуку Claude Code і в CI (плюс TruffleHog і GitHub push protection) |
| Витік токенів через логи | Форматер логів маскує токени, URL Bot API та IBAN (у т.ч. у трасуваннях помилок); логи сторонніх бібліотек приглушені |
| Чужі люди пишуть боту | Відповідь лише `ALLOWED_USER_IDS` і лише в приватному чаті; кнопки перевіряються так само; з груп бот виходить |
| Доступ до NAS з інтернету | Немає вхідних портів: Telegram — long polling, Monobank — опитування (webhook свідомо не реалізовано) |
| Підміна тексту в повідомленнях | Усі описи операцій і назви екрануються перед HTML; попередній перегляд посилань вимкнено |
| Перенаправлення токена Monobank | Лише `https://api.monobank.ua`, redirect не виконуються, обмежений розмір відповіді |
| Компрометація контейнера | Користувач без прав (не root), файлова система лише для читання, `cap_drop: ALL`, `no-new-privileges`, ліміти пам'яті/процесів, без pip у рантаймі |
| Ланцюг постачання | Мінімум залежностей, точні версії з хешами (`uv.lock`), pip-audit у CI, базові образи й дії GitHub Actions закріплені за digest/SHA, Dependabot |
| Підміна образу | Образ публікується лише з тегу `vX.Y.Z` на `main` після всіх перевірок CI; теги захищені від зміни й видалення; підписана атестація походження: `gh attestation verify oci://ghcr.io/25lion52/monobudget-telegram:<тег> --owner 25LioN52` |
| Дані на NAS | Лише потрібні поля операцій; IBAN власних рахунків — лише як ключовані хеші (HMAC); база й резервні копії з правами лише для власника |
| Збої під час запуску | Помилки (наприклад, мережа ще не готова після ввімкнення NAS) логуються через маскувальний форматер; бот повторює підключення з паузами |
| Дані в Telegram | ⚠️ Чати з ботами не мають наскрізного шифрування: суми й описи, які надсилає бот, зберігаються на серверах Telegram |

---

## Reporting a vulnerability (English)

**Do not open a public issue.** Use GitHub's private
[Report a vulnerability](https://github.com/25LioN52/MonoBudget-telegram/security/advisories/new) form. Never send tokens, your Telegram id or real statements. I aim to reply within 7 days; only the latest image is supported.

If a token may have leaked: create a new Monobank token at api.monobank.ua; revoke the bot token in @BotFather (*API Token → Revoke*); update `secrets/` and restart the container.
