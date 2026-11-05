"""Build animated CRM fragments with a normal pointer and typed fictional data.

Requires system ffmpeg and libcairo. No DB/.env/network/models.
Creates GIF/poster pairs and a manifest. These drawings follow current controls and form layouts; they are not browser recordings.
"""
from __future__ import annotations
import json
from pathlib import Path
import shutil
import subprocess
from learning_example_renderer import W,H,render

ROOT=Path(__file__).resolve().parents[1]

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


EXAMPLES.extend([
 ('set_manager_summary','Борис Учебный','В переключателе выберите менеджера','Показатели Бориса Учебного'),
 ('set_period_summary','7 дней','Выберите период сводки','Показатели сводки за 7 дней'),
 ('select_report_weekly','Сводка за неделю','Выберите отчёт нужного периода','Открыта недельная сводка'),
 ('select_call_staff','Учебный разбор','Откройте пример из карточки сотрудника','Открыт подтверждающий разбор')])
DETAILS={
 'save_outcome_callback':{'seconds':14,'poster_time':9.3,'source':['crm/static/work.js:workCard','crm/static/work.js:saveOutcome'],
  'sequence':['Перезвонить','Дата','Время','Комментарий','Сохранить','История клиента'],
  'fields':[{'selector':'[data-f="date"]','label':'Дата','value':'06.11.2026','api_value':'2026-11-06','start':2.7,'end':4.1},
            {'selector':'[data-f="time"]','label':'Время','value':'15:00','start':4.8,'end':5.7},
            {'selector':'[data-f="comment"]','label':'Комментарий','value':'Обсудить предложение','start':6.2,'end':8.8}]},
 'move_task':{'seconds':8.5,'poster_time':5.5,'source':['crm/static/calls.js:callbackMove'],'sequence':['Перенести','Новая дата','Время','Сохранить'],
  'fields':[{'selector':'.move input[type="date"]','label':'Новая дата','value':'07.11.2026','api_value':'2026-11-07','start':1.5,'end':2.8},
            {'selector':'.move input[type="time"]','label':'Время','value':'11:30','start':3.5,'end':4.7}]},
 'save_note':{'seconds':8.5,'poster_time':5.5,'source':['crm/static/deals.js:dealForm'],'sequence':['Заметка','Сохранить','История'],
  'fields':[{'selector':'[data-v="note"]','label':'Заметка','value':'Обсудить предложение 6 ноября в 15:00','start':1.3,'end':5.2}]},
 'prepare_contract':{'seconds':8.5,'poster_time':5.2,'source':['crm/static/contracts/generator.js:mount'],'sequence':['Наименование','ИНН','Реквизиты учебного клиента сверены','Сформировать учебный договор','Журнал договоров'],
  'fields':[{'selector':'#dg-name','label':'Наименование','value':'Учебный Кедр','start':.7,'end':2},
            {'selector':'#dg-inn','label':'ИНН','value':'0000000000','start':2.6,'end':3.8}]}}


def build():
 ffmpeg=shutil.which('ffmpeg')
 if not ffmpeg:raise SystemExit('System ffmpeg required; no dependencies are installed by this script.')
 dest=ROOT/'crm/static/learning/examples';dest.mkdir(exist_ok=True)
 manifest={}
 for key,button,before,after in EXAMPLES:
  detail=DETAILS.get(key,{});seconds=detail.get('seconds',4.25);fps=8
  command=[ffmpeg,'-hide_banner','-loglevel','error','-y','-filter_complex_threads','1','-f','rawvideo','-pixel_format','bgra','-video_size',f'{W}x{H}','-framerate',str(fps),'-i','pipe:0','-filter_complex','split[s0][s1];[s0]palettegen=max_colors=128:stats_mode=full[p];[s1][p]paletteuse=dither=none','-loop','0',str(dest/f'{key}.gif')]
  process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
  try:
   for number in range(round(seconds*fps)):
    frame=render(key,number/fps,button)
    try:process.stdin.write(frame.bytes())
    finally:frame.close_surface()
   process.stdin.close()
   if process.wait(timeout=60):raise RuntimeError(process.stderr.read().decode())
  except BaseException:
   process.kill();process.wait();raise
  finally:process.stderr.close()
  poster=render(key,detail.get('poster_time',0),button)
  try:poster.png(dest/f'{key}.png')
  finally:poster.close_surface()
  manifest[key]={'alt':before+'. '+button+' → '+after+'. Схематический пример по форме CRM, вымышленные данные.',
    'button':button,'result':after,'seconds':seconds,'gif':key+'.gif','poster':key+'.png','width':W,'height':H,
    'cursor':'white-arrow-black-outline','layout':'crm-fragment','revision':'forms-20261105',
    **({'fields':detail['fields'],'sequence':detail['sequence'],'source':detail['source'],'poster_time':detail['poster_time']} if detail else {})}
 (ROOT/'crm/learning_examples.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'examples':len(manifest),'bytes':sum(p.stat().st_size for p in dest.iterdir()),'source':'drawings of current CRM fragments, fictional data; not browser recordings'}))


if __name__=='__main__':build()
