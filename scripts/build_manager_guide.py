#!/usr/bin/env python3
"""Create a standalone, three-page OOXML manager guide with the standard library."""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

NAME = 'Инструкция_менеджеру_Averon.docx'
PAGES = [
    [
        ('Title', 'Averon: помощник менеджера'),
        ('Subtitle', 'Как пользоваться разбором звонков и тренажёром • 4 октября 2026'),
        ('Heading1', 'Для чего нужен бот'),
        ('Normal', 'Бот помогает увидеть сильные действия и упущения в рабочих разговорах, выбрать один навык и потренировать его перед следующим звонком. Это подсказка для развития, а не обещание продажи и не замена вашего руководителя.'),
        ('Normal', 'ГЦК — генерация целевых контактов; ОПС — определение посетителей сайта. Коммерческое предложение в названиях ситуаций может сокращаться до «КП».'),
        ('Normal', 'Есть два личных чата в Telegram: основной бот @codex_bot_example_bot — разборы звонков; @averon_training_bot — учебные разговоры. Записи из телефонии поступают автоматически. Присылать аудиофайл звонка в основной бот не нужно.'),
        ('Heading1', 'Подключение: первые три шага'),
        ('Normal', '1. Найдите @codex_bot_example_bot в Telegram. Откройте личный чат и нажмите «Старт» или отправьте /start.'),
        ('Normal', '2. Если бот не узнаёт вас, попросите своего руководителя выдать доступ и проверить ваш Telegram-аккаунт. После подключения снова отправьте /start в основном боте.'),
        ('Normal', '3. Откройте @averon_training_bot и отправьте /start. Внизу появятся кнопки тренировок. Работайте в личном чате: чужие кнопки и групповые чаты не дают доступа к вашим занятиям.'),
        ('Heading1', 'Как выглядит разбор звонка'),
        ('Normal', 'Обычный разбор приходит одним сообщением. В заголовке — менеджер, дата, длительность, полный номер клиента. Далее — 🎯 Итог звонка, 📊 Оценка звонка, ✅ Что получилось, 📌 Главный упущенный момент, 💬 Что изменить, 🎯 Что сделать дальше и 👤 Руководителю. При недостатке данных отдельные разделы могут отсутствовать.'),
        ('Normal', 'Менеджеру автоматически приходят звонки, требующие внимания. В вечерней сводке видна общая статистика. Отсутствие отдельного сообщения после каждого звонка само по себе не означает ошибку: короткие разговоры, отсутствие записи и правила отбора могут исключать звонок из обычной выдачи.'),
        ('Normal', 'Балл 0–100 описывает выполнение применимых критериев действующей шкалы. Знаки ✅, ⚠️ и ❌ учитывают достигнутый результат и ключевые упущения; они не являются диапазонами балла. Галочка не означает отсутствие ошибок. Балл не равен вероятности продажи. Отказ клиента и плохая работа менеджера — разные вещи. При обрывочной записи бот сообщает, что данных мало. Дополнительная проверка учитывает этап продажи, но не прибавляет свой результат к основному баллу.'),
        ('Heading1', 'Кнопка «Расширенный обзор»'),
        ('Normal', 'Нажмите кнопку под нужным разбором. Откроются сохранённые критерии, подтверждающие реплики, договорённости, возражения, рекомендации и доступные объяснения именно этого разбора. Разные проверки обозначены отдельно. Отсутствующие цитаты и таймкоды не придумываются.'),
        ('Normal', 'Длинный обзор приходит пронумерованными частями или текстовым файлом. «К краткому разбору» снова показывает основной итог; «Закрыть обзор» закрывает сообщение обзора. Повторное открытие использует сохранённые подробности.'),
    ],
    [
        ('Title', 'Тренируйте один навык за раз'),
        ('Subtitle', 'Меню в личном чате @averon_training_bot'),
        ('Heading1', 'Шесть основных кнопок'),
        ('Normal', '🎯 Начать тренировку — открыть ситуации для полного разговора. Выберите ситуацию кнопкой: тренировка начнётся сразу.'),
        ('Normal', '📋 Ситуации — тот же каталог. На странице до восьми ситуаций. «⬅️ Назад» и «Вперёд ➡️» листают страницы; «🏠 Меню» возвращает к подсказке. Полное название и задача видны над кнопками.'),
        ('Normal', '⚡ Короткая отработка — сразу начать пять разных ситуаций в случайном порядке. Выбирать сценарий не нужно. Обстоятельства показаны текстом; голосом звучит только реплика клиента.'),
        ('Normal', '🔁 Повторить — повторить последнюю завершённую тренировку с изменёнными обстоятельствами. Если завершённых занятий ещё нет, сначала начните и завершите одно.'),
        ('Normal', '⏹ Завершить — закончить активную сессию и сохранить ответы для разбора. Если активной сессии нет, бот спокойно предложит начать тренировку.'),
        ('Normal', '❓ Помощь — объяснить действия и снова показать меню. Запасной способ открыть меню — /help.'),
        ('Heading1', 'Разговор целиком: простой порядок'),
        ('Normal', '1. Нажмите «🎯 Начать тренировку». Например, выберите «Секретарь и общая почта» или «Сопровождение ГЦК». Названия ситуаций могут дополняться.'),
        ('Normal', '2. Прочитайте задачу и реплику клиента. Отвечайте короткими текстовыми или голосовыми сообщениями, как на настоящем звонке. Голос будет распознан; при проблеме с записью используйте текст.'),
        ('Normal', '3. Продолжайте до завершения разговора либо нажмите «⏹ Завершить». Получите доступный разбор. Если бот пишет, что ответы отправлены на обработку, итог придёт после очереди; точное время не гарантируется.'),
        ('Normal', '«Общая тренировка» в каталоге запускает разговор без выбора конкретной ситуации. Одновременно идёт только одна сессия: повторный запуск не заменяет её. Продолжите текущую или завершите её кнопкой.'),
        ('Heading1', 'Короткая отработка: без выбора ситуации'),
        ('Normal', 'Нажмите «⚡ Короткая отработка», прочитайте обстоятельства и ответьте на возражение. Затем переходите к следующему упражнению. После ответа бот даёт доступную обратную связь. Итоговый разбор учитывает именно отвеченные упражнения; при досрочном завершении пропущенные ответы не сочиняются.'),
        ('Normal', 'В полной тренировке отрабатывайте весь разговор; в короткой — одну реакцию на возражение за упражнение. Если звук недоступен, реплика остаётся текстом. Можно тренироваться неоднократно в течение дня, но ограничения одной сессии сохраняются.'),
        ('Normal', 'Запасные команды: /train — начать; /scenarios — каталог; /stop — завершить; /repeat — повторить. Для обычной работы достаточно кнопок.'),
    ],
    [
        ('Title', 'Применяйте вывод в следующем звонке'),
        ('Subtitle', 'Рабочий пример, помощь и памятка новичку'),
        ('Heading1', 'Вымышленный пример рабочего дня'),
        ('Normal', 'Все реплики ниже придуманы для обучения. Это не запись разговора коллеги.'),
        ('Normal', '1. Звонок. Клиент просит прислать предложение. Менеджер отвечает: «Отправлю сегодня», но не согласовывает продолжение.'),
        ('Normal', '2. Разбор. Бот показывает итог отдельно от качества разговора: интерес к материалам есть, а следующий шаг остался неполным. Менеджер открывает «Расширенный обзор» и проверяет реплики.'),
        ('Normal', '3. Один навык. Выберите конкретную задачу: уточнить, когда клиент посмотрит предложение и когда удобно его обсудить. Не пытайтесь исправить всё за одну тренировку.'),
        ('Normal', '4. Тренировка. В тренажёре откройте ситуации и выберите «Согласованное КП» (коммерческое предложение). Пример учебной реплики: «Отправлю предложение сегодня. Когда вам удобно его посмотреть и обсудить?» Дождитесь ответа клиента и согласуйте подходящее продолжение.'),
        ('Normal', '5. Следующий звонок. Примените навык по ситуации, затем проверьте новый разбор. Сравнивайте конкретные действия, а не только балл.'),
        ('Heading1', 'Если что-то не получилось'),
        ('Normal', 'Разбор не пришёл. Убедитесь, что начали основной бот и доступ выдан. Запись появляется не мгновенно, а разбор зависит от обработки. Дайте системе время. Если знаете номер звонка из заголовка прежнего сообщения, /review 123 открывает сохранённый разбор: замените 123 на нужный номер. Если проблема сохраняется, обратитесь к своему руководителю с датой и временем звонка.'),
        ('Normal', 'Кнопка обзора выдала ошибку. Основной разбор остаётся доступным. Повторите открытие позже или откройте сохранённый разбор через /review и его номер. Кнопка старого сообщения может относиться к отсутствующей записи; попросите руководителя проверить её.'),
        ('Normal', 'Не хватает данных или цитата кажется неверной. Не воспринимайте предположение как факт. Сверьтесь со своей записью и обсудите вывод с руководителем. Короткая запись не доказывает, что вы пропустили все этапы разговора.'),
        ('Normal', 'Тренировка уже идёт. Продолжите её или нажмите «⏹ Завершить». Если нажали старую кнопку, откройте /help и пользуйтесь текущим меню. Не отправляйте один и тот же ответ много раз, пока бот его обрабатывает.'),
        ('Normal', 'После завершения разбор ещё не готов. Ответы сохранены; подождите обработку. При длительном ожидании сообщите руководителю, в каком боте, когда и какое действие выполняли. Не запускайте новую сессию только ради повторного получения старого ответа.'),
        ('Heading1', 'Памятка первых действий'),
        ('Normal', '□ Открыть оба бота и нажать /start.\n□ Проверить доступ со своим руководителем.\n□ Прочитать итог и отделить его от оценки качества.\n□ Открыть «Расширенный обзор» для спорного момента.\n□ Выбрать один навык и пройти тренировку.\n□ Завершить сессию, изучить обратную связь и применить навык.'),
    ],
]

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
ET.register_namespace('w', W); ET.register_namespace('r', R)


def node(parent, name, text=None, **attrs):
    child = ET.SubElement(parent, '{' + W + '}' + name, {'{' + W + '}' + k: str(v) for k, v in attrs.items()})
    child.text = text
    return child


def xml(root):
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def create(path, *, pages=None, footer_label='памятка менеджеру'):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        shutil.copy2(path, path.with_name(path.stem + '-' + stamp + path.suffix))
    doc = ET.Element('{' + W + '}document'); body = node(doc, 'body')
    for number, page in enumerate(PAGES if pages is None else pages):
        if number:
            node(node(node(body, 'p'), 'r'), 'br', type='page')
        for style, text in page:
            paragraph = node(body, 'p'); props = node(paragraph, 'pPr'); node(props, 'pStyle', val=style)
            for i, line in enumerate(text.splitlines()):
                run = node(paragraph, 'r')
                if i:
                    node(run, 'br')
                node(run, 't', line)
    section = node(body, 'sectPr')
    node(section, 'footerReference', type='default').set('{' + R + '}id', 'rId2')
    node(section, 'pgSz', w=11906, h=16838)
    node(section, 'pgMar', top=850, bottom=850, left=1000, right=1000, header=400, footer=400)
    styles = ET.Element('{' + W + '}styles')
    for name, size, color in (('Normal', 21, '263444'), ('Title', 38, '203B65'),
                              ('Subtitle', 19, '536678'), ('Heading1', 25, '203B65')):
        style = node(styles, 'style', type='paragraph', styleId=name)
        node(style, 'name', val=name)
        if name != 'Normal': node(style, 'basedOn', val='Normal')
        ppr = node(style, 'pPr')
        if name != 'Normal': node(ppr, 'keepNext')
        node(ppr, 'spacing', after=100 if name == 'Normal' else 130, line=246, lineRule='auto')
        rpr = node(style, 'rPr'); node(rpr, 'rFonts', ascii='Calibri', hAnsi='Calibri', cs='Calibri')
        if name in {'Title', 'Heading1'}: node(rpr, 'b')
        node(rpr, 'color', val=color); node(rpr, 'sz', val=size)
        node(rpr, 'lang', val='ru-RU')
    footer = ET.Element('{' + W + '}ftr'); p = node(footer, 'p'); ppr = node(p, 'pPr'); node(ppr, 'jc', val='right')
    node(node(p, 'r'), 't', 'Averon • ' + footer_label + ' • ')
    node(node(p, 'r'), 'fldChar', fldCharType='begin')
    node(node(p, 'r'), 'instrText', ' PAGE ')
    node(node(p, 'r'), 'fldChar', fldCharType='end')
    content_types = '''<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/><Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/></Types>'''
    root_rels = '''<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'''
    doc_rels = '''<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/></Relationships>'''
    with ZipFile(path, 'w', ZIP_DEFLATED) as archive:
        for name, value in {'[Content_Types].xml': content_types, '_rels/.rels': root_rels,
                            'word/document.xml': xml(doc), 'word/styles.xml': xml(styles),
                            'word/footer1.xml': xml(footer), 'word/_rels/document.xml.rels': doc_rels}.items():
            archive.writestr(name, value)
    with ZipFile(path) as archive:
        assert archive.testzip() is None
        for name in archive.namelist(): ET.fromstring(archive.read(name))
    return path


if __name__ == '__main__':
    print(create(Path(__file__).resolve().parents[1] / 'docs' / NAME))
