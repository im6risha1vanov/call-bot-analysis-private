"""Build small illustrative GIFs from fictional UI labels, never record live CRM.

Requires system ffmpeg with drawtext and DejaVu Sans. No DB/.env/network/models.
Creates GIF/poster pairs and a manifest. These are schematic examples of the
current controls and results, not screenshots or recordings of the real app.
"""
from __future__ import annotations
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
FONT='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
# Key, actual control, before, after. No real accounts, numbers or conversations.
EXAMPLES=[
 ('select_client','Учебный Кедр · УЧ-001','Очередь: выберите клиента','Открыта карточка УЧ-001'),
 ('save_outcome_no_answer','Не взял трубку','Клиент не ответил','В истории: «Не взял трубку»'),
 ('save_outcome_callback','Сохранить','Перезвонить: дата, время, комментарий','Создана задача перезвона'),
 ('verify_outcome','Не взял трубку','После сохранения итога','Итог появился в истории клиента'),
 ('select_task','Перенести','Найдите созданный перезвон','Открыта форма переноса'),
 ('select_task_context','Позвонить','Найдите просроченный перезвон','Открыта карточка с историей'),
 ('move_task','Сохранить','Укажите новое время в той же задаче','Срок изменён, задача осталась одна'),
 ('verify_task','Сохранить','После переноса задачи','Новый срок и комментарий в списке'),
 ('select_deal','Учебный Кедр · УЧ-001','Список сделок','Открыта история нужной сделки'),
 ('save_note','Заметка → Сохранить','Запишите действие, дату и время','Следующий шаг появился в истории'),
 ('verify_history','Учебный Кедр · УЧ-001','Откройте сделку после сохранения','Заметка видна в истории'),
 ('prepare_contract','Сформировать учебный договор','Сверьте название и учебный ИНН','Документ появился в журнале'),
 ('open_section','Договоры','Откройте вкладку договоров','Виден журнал учебных документов'),
 ('select_document','Учебный договор','Выберите строку журнала','Открыта связанная сделка'),
 ('close_deal','×','Прочитайте этап и историю сделки','Карточка закрыта, список доступен'),
 ('set_period','7 дней','Выберите период','Показатели за выбранные 7 дней'),
 ('set_manager','Борис Учебный','Все менеджеры','Показаны данные Бориса Учебного'),
 ('select_call','Учебный Кедр · разбор','Выберите нужный разговор','Открыт разбор и учебная расшифровка'),
 ('close_review','×','Прочитайте выводы разбора','Разбор закрыт, можно продолжать'),
 ('close_card','×','Посмотрите историю клиента','Карточка закрыта, задача сохранена'),
 ('select_report','Отчёт нужного периода','Сверьте заголовок и даты','Открыто содержание отчёта'),
 ('select_person','Борис Учебный','Список сотрудников','Открыта карточка сотрудника'),
 ('filter_deals','Зависшие','Активные сделки','В списке остались зависшие сделки'),
 ('compose','Составить договор','Карточка нужной сделки','Открыта форма связанного договора'),
]
TABS={'queue':'Очередь','callbacks':'Перезвоны','deals':'Сделки','contracts':'Договоры','calls':'Звонки','reports':'Отчёты','summary':'Сводка','staff':'Сотрудники','settings':'Настройки','auto':'Автоматизации','robot':'Робот','learning':'Обучение'}
for key,title in TABS.items():EXAMPLES.append(('visit_'+key,title,'Выберите раздел в меню','Открыт раздел «'+title+'»'))
EXAMPLES.append(('visit_access','Доступы','Настройки: выберите вкладку','Открыт список учётных записей'))


def build():
 ffmpeg=shutil.which('ffmpeg')
 if not ffmpeg or not Path(FONT).is_file():raise SystemExit('Need system ffmpeg + DejaVuSans (no installation attempted).')
 dest=ROOT/'crm/static/learning/examples';dest.mkdir(exist_ok=True)
 manifest={}
 with tempfile.TemporaryDirectory(prefix='averon-learning-frames-') as tmp:
  for key,button,before,after in EXAMPLES:
   texts=['Учебный пример',before,button,after,'Нажатие → результат','Вымышленные данные','↖']
   paths=[]
   for i,text in enumerate(texts):
    path=Path(tmp)/f'{key}-{i}.txt';path.write_text(text,encoding='utf-8');paths.append(path)
   def text(i,x,y,size=17,color='13201c',enable=None):
    tail=f":enable='{enable}'" if enable else ''
    return f"drawtext=fontfile={FONT}:textfile={paths[i]}:expansion=none:x='{x}':y='{y}':fontsize={size}:fontcolor=0x{color}"+tail
   filters=['drawbox=x=8:y=8:w=464:h=264:color=0xdde3de:t=1',text(0,24,20,15,'5d6b66'),text(1,24,57,18),
    'drawbox=x=24:y=99:w=432:h=52:color=0xeef1ee:t=fill',text(2,40,115,19),
    "drawbox=x=24:y=99:w=432:h=52:color=0x0d7a63:t=3:enable='between(t,1,2)'",
    text(6,"360-80*min(t,1)","200-60*min(t,1)",24,'0d7a63','lt(t,2)'),
    "drawbox=x=24:y=166:w=432:h=58:color=0xd9efe8:t=fill:enable='gte(t,2)'",text(3,36,187,17,'0d7a63','gte(t,2)'),text(4,24,239,13,'5d6b66'),text(5,290,239,12,'5d6b66')]
   graph=','.join(filters)
   command=[ffmpeg,'-hide_banner','-loglevel','error','-y','-filter_threads','1','-f','lavfi','-i','color=c=0xffffff:s=480x280:r=8:d=4.25','-filter_complex_threads','1','-filter_complex',graph+',split[s0][s1];[s0]palettegen=max_colors=64:stats_mode=full[p];[s1][p]paletteuse=dither=none','-loop','0',str(dest/f'{key}.gif')]
   subprocess.run(command,check=True,capture_output=True,timeout=30)
   # The exact same first frame is the reduced-motion/paused poster.
   subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-y','-threads','1','-i',str(dest/f'{key}.gif'),'-frames:v','1',str(dest/f'{key}.png')],check=True,capture_output=True,timeout=30)
   manifest[key]={'alt':before+'. '+button+' → '+after+'. Схематический пример на вымышленных данных.','button':button,'result':after,'seconds':4.25,'gif':key+'.gif','poster':key+'.png'}
 (ROOT/'crm/learning_examples.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'examples':len(manifest),'bytes':sum(p.stat().st_size for p in dest.iterdir()),'source':'fictional diagrams; not browser recordings'}))


if __name__=='__main__':build()
