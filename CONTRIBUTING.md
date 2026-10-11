# Як долучитися / Contributing

Дякую за інтерес до MonoBudget! Пропозиції та виправлення вітаються.

## Правила безпеки (головне)

1. **Жодних справжніх даних.** Ні токенів, ні виписок, ні IBAN, ні номерів карток, ні Telegram ID, ні імен. Фікстури в `tests/fixtures` синтетичні (IBAN з контрольними цифрами `00`, замасковані картки), фейкові токени збираються в `tests/conftest.py` під час виконання.
2. **Мінімум залежностей.** Нову залежність додавайте лише з обґрунтуванням у PR; усе фіксується з хешами в `uv.lock`.
3. **Мережа — лише Monobank і Telegram**, без аналітики й телеметрії. Нічого не логувати з токенами чи повними IBAN.
4. **Усе, що йде в повідомлення Telegram, — екранувати.**

## Робочий процес

```bash
uv sync                                        # середовище розробки (точні версії)
git config core.hooksPath .githooks            # один раз: сканер у pre-commit, commit-msg, pre-push
uv run pytest                                  # тести
uv run ruff check . && uv run ruff format --check .
python3 scripts/secret_scan.py --all           # сканер секретів (і --history --all)
docker build -t monobudget-telegram:dev .      # образ
```

- Гілка `main` захищена: зміни лише через pull request із зеленим CI (`test`, `audit`, `secret-scan`, `docker`).
- Бізнес-логіку (періоди, бюджет, категорії, синхронізацію, тексти) пишіть як чисті функції з тестами; Telegram і Monobank у тестах — лише фейки з `tests/conftest.py`.
- Рядки інтерфейсу — в обох мовах (`i18n.py`, тест перевіряє однаковість ключів).
- Хибне спрацювання сканера дозволяється лише для конкретного правила на рядку: `secret-scan:allow <rule-id>`, з поясненням у PR.

---

**English:** synthetic test data only, minimal locked dependencies, network only to Monobank/Telegram, escape everything sent to Telegram, never log secrets. Run `uv run pytest`, `ruff`, `python3 scripts/secret_scan.py --all`; enable hooks with `git config core.hooksPath .githooks`. `main` accepts only PRs with green CI.
