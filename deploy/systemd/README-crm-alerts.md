# Уведомления Averon CRM

Таймер проверяет пять служб CRM и фоновую обработку примерно раз в минуту. Он
считает ошибки и критические события в системном журнале. Для локального
HTTP-процесса проверяет, что `/api/me` отвечает кодом 401 в JSON без сессии.
Недоступность подтверждается двумя последовательными проверками.

Сообщение содержит только название компонента, тип сбоя и число обнаружений.
Тело запроса, сообщение из журнала, параметры клиента и ключи остаются на
сервере. Повторные сбои одного типа объединяются на 15 минут. При отказе
Telegram неподтверждённое уведомление сохраняется и повторяется через пять
минут. Журнал проверяется с последней сохранённой отметки, максимум за сутки
и до 3000 строк за проход. Срабатывание лимита входит в уведомление.

## Установка

Для получателя нужен положительный числовой ID личного чата. Получатель
должен открыть существующего бота и нажать «Старт».

На сервере создайте файл `/etc/averon-crm-alerts.env` с владельцем root и
правами `0600`. Перенесите в него только `BOT_TOKEN` существующего бота и
запишите `CRM_ALERT_CHAT_ID=<числовой ID>`. При смене токена обновите и этот
файл. Если CRM слушает другой порт, укажите `CRM_ALERT_HTTP_PORT=<порт>`.
Значение по умолчанию — 8095.

Запустите команды из каталога рабочей копии Averon.

```sh
sudo systemd-sysusers deploy/systemd/averon-crm-alerts.sysusers.conf
sudo install -m 0644 deploy/systemd/averon-crm-alerts.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/averon-crm-alerts.timer /etc/systemd/system/
sudo systemd-analyze verify /etc/systemd/system/averon-crm-alerts.{service,timer}
sudo systemctl daemon-reload
sudo systemctl start averon-crm-alerts.service
sudo systemctl enable --now averon-crm-alerts.timer
systemctl list-timers averon-crm-alerts.timer
sudo journalctl -u averon-crm-alerts.service -n 20 --no-pager
```

Состояние хранится в `/var/lib/averon-crm-alerts`. Статус `inactive (dead)`
после успешного прохода ожидаем для службы `oneshot`. Успешный запуск сам по
себе подтверждает работу проверки. Доставку подтверждает получатель после
уведомления о реальном сбое или отдельной контролируемой проверки.

Обычные ошибки ввода, ошибки браузера, внешний голосовой сервис и недоступность
самого сервера не входят в этот мониторинг. Для остановки уведомлений выполните
`sudo systemctl disable --now averon-crm-alerts.timer`.
