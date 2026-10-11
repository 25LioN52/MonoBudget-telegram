## Що змінено / What changed

<!-- Коротко: що і навіщо. / Briefly: what and why. -->

## Перевірки / Checks

- [ ] `uv run pytest` проходить / passes
- [ ] `uv run ruff check .` і `uv run ruff format --check .` проходять / pass
- [ ] `python3 scripts/secret_scan.py --all` проходить / passes
- [ ] Нова логіка покрита тестами / New logic is tested

## Безпека / Security (обов'язково / required)

- [ ] Немає токенів, справжніх виписок, IBAN, номерів карток, Telegram ID чи імен — лише синтетичні фікстури / Synthetic data only
- [ ] Токени не логуються й не потрапляють у повідомлення, помилки, базу чи файли / Tokens never logged or stored outside env/secrets
- [ ] Будь-який текст банку чи користувача екранується перед Telegram HTML / Bank and user text is HTML-escaped
- [ ] Перевірка доступу (ALLOWED_USER_IDS, лише приватні чати) не послаблена / Access control not weakened
- [ ] Мережа лише до Monobank і Telegram; ліміти Monobank дотримано / Network only to Monobank/Telegram; Monobank limits respected
- [ ] Нові залежності обґрунтовано, `uv.lock` оновлено, pip-audit чистий / New dependencies justified, lock updated, pip-audit clean
- [ ] Docker/compose: не root, лише читання, без портів — не послаблено / Container hardening not weakened
- [ ] Зображення в `docs/` переглянуто вручну (без реальних даних) / Images in `docs/` reviewed by hand
