"""Cairo drawings of the current CRM fragments, with fictional data only.

Uses the installed libcairo through ctypes; no browser capture is claimed.
The animation never loads production HTML, a database, or an external provider.
"""
from __future__ import annotations
import ctypes as C
import ctypes.util
import math

W,H=480,360
lib=C.CDLL(ctypes.util.find_library('cairo'))
for name,args,ret in [
 ('cairo_image_surface_create',[C.c_int,C.c_int,C.c_int],C.c_void_p),('cairo_create',[C.c_void_p],C.c_void_p),
 ('cairo_image_surface_get_data',[C.c_void_p],C.c_void_p),('cairo_image_surface_get_stride',[C.c_void_p],C.c_int),
 ('cairo_surface_write_to_png',[C.c_void_p,C.c_char_p],C.c_int),
]:
 f=getattr(lib,name);f.argtypes=args;f.restype=ret
for name,args in {
 'cairo_set_source_rgba':[C.c_void_p,C.c_double,C.c_double,C.c_double,C.c_double],
 'cairo_move_to':[C.c_void_p,C.c_double,C.c_double], 'cairo_line_to':[C.c_void_p,C.c_double,C.c_double],
 'cairo_arc':[C.c_void_p,C.c_double,C.c_double,C.c_double,C.c_double,C.c_double],
 'cairo_rectangle':[C.c_void_p,C.c_double,C.c_double,C.c_double,C.c_double],
 'cairo_set_line_width':[C.c_void_p,C.c_double], 'cairo_set_font_size':[C.c_void_p,C.c_double],
 'cairo_select_font_face':[C.c_void_p,C.c_char_p,C.c_int,C.c_int], 'cairo_show_text':[C.c_void_p,C.c_char_p],
 'cairo_translate':[C.c_void_p,C.c_double,C.c_double],
 'cairo_paint':[C.c_void_p], 'cairo_fill':[C.c_void_p], 'cairo_fill_preserve':[C.c_void_p],
 'cairo_stroke':[C.c_void_p], 'cairo_new_path':[C.c_void_p], 'cairo_close_path':[C.c_void_p],
 'cairo_save':[C.c_void_p], 'cairo_restore':[C.c_void_p], 'cairo_clip':[C.c_void_p],
 'cairo_surface_flush':[C.c_void_p], 'cairo_destroy':[C.c_void_p], 'cairo_surface_destroy':[C.c_void_p],
}.items():getattr(lib,name).argtypes=args

PALETTE={'bg':'0e1513','surface':'151e1b','surface2':'1b2622','line':'2a3732','fg':'e6eeea','muted':'93a39d','accent':'3cc2a0','soft':'163a31','ink':'06261e'}

def tween(t,start,end,a,b):return a+(b-a)*min(1,max(0,(t-start)/(end-start)))
def typed(t,start,end,value):return value[:int(len(value)*min(1,max(0,(t-start)/(end-start))))]

def pointer_at(t,points):
 if t<=points[0][0]:return points[0][1:]
 for (t0,x0,y0),(t1,x1,y1) in zip(points,points[1:]):
  if t<=t1:return tween(t,t0,t1,x0,x1),tween(t,t0,t1,y0,y1)
 return points[-1][1:]

class Frame:
 def __init__(self):
  self.s=lib.cairo_image_surface_create(0,W,H);self.c=lib.cairo_create(self.s)
  self.colour('bg');lib.cairo_paint(self.c)
 def colour(self,name,alpha=1):
  value=PALETTE.get(name,name).lstrip('#');lib.cairo_set_source_rgba(self.c,*(int(value[i:i+2],16)/255 for i in (0,2,4)),alpha)
 def rect(self,x,y,w,h,fill='surface',border=None,r=9):
  c=self.c;r=min(r,w/2,h/2);lib.cairo_new_path(c)
  for cx,cy,a in [(x+w-r,y+r,-math.pi/2),(x+w-r,y+h-r,0),(x+r,y+h-r,math.pi/2),(x+r,y+r,math.pi)]:lib.cairo_arc(c,cx,cy,r,a,a+math.pi/2)
  lib.cairo_close_path(c);self.colour(fill);lib.cairo_fill_preserve(c)
  if border:self.colour(border);lib.cairo_set_line_width(c,1.5);lib.cairo_stroke(c)
  else:lib.cairo_new_path(c)
 def text(self,text,x,y,size=19,colour='fg',bold=False):
  lib.cairo_select_font_face(self.c,b'DejaVu Sans',0,int(bold));lib.cairo_set_font_size(self.c,size);self.colour(colour);lib.cairo_move_to(self.c,x,y);lib.cairo_show_text(self.c,str(text).encode())
 def line(self,x,y,x2,y2,colour='line',width=1):
  self.colour(colour);lib.cairo_set_line_width(self.c,width);lib.cairo_move_to(self.c,x,y);lib.cairo_line_to(self.c,x2,y2);lib.cairo_stroke(self.c)
 def caption(self,title):
  self.text('Учебный пример · фрагмент CRM',14,21,13,'muted');self.text(title,20,55,22,bold=True)
 def button(self,text,x,y,w=150,active=False,primary=False,size=17):
  self.rect(x,y,w,38,'accent' if primary else 'soft' if active else 'surface','accent' if active or primary else 'line')
  self.text(text,x+10,y+25,size,'ink' if primary else 'accent' if active else 'fg',primary)
 def field(self,label,value,x,y,w=210,focus=False,placeholder=''):
  if label:self.text(label,x,y,18,'muted')
  self.rect(x,y+10,w,40,'surface','accent' if focus else 'line')
  self.text(value or placeholder,x+10,y+37,21,'fg' if value else 'muted')
 def cursor(self,x,y,click=False):
  # Standard white arrow, black outline, top-left hotspot. Not a text glyph.
  c=self.c;lib.cairo_new_path(c)
  points=[(0,0),(0,27),(7,20),(13,33),(18,30),(12,18),(24,18)]
  for i,(dx,dy) in enumerate(points):getattr(lib,'cairo_move_to' if i==0 else 'cairo_line_to')(c,x+dx,y+dy)
  lib.cairo_close_path(c);self.colour('ffffff');lib.cairo_fill_preserve(c);self.colour('111111');lib.cairo_set_line_width(c,1.7);lib.cairo_stroke(c)
  if click:self.colour('accent',.7);lib.cairo_set_line_width(c,2);lib.cairo_arc(c,x,y,12,0,2*math.pi);lib.cairo_stroke(c)
 def table(self,title,rows,chosen=-1):
  self.caption(title);self.rect(14,74,452,230,'surface','line');self.text('Клиент / запись',26,102,17,'muted');self.text('Итог / этап',273,102,17,'muted');self.line(26,113,454,113)
  for i,(a,b) in enumerate(rows):
   y=124+i*62
   if i==chosen:self.rect(20,y-3,440,56,'soft',r=6)
   self.text(a,28,y+21,18);self.text(b,273,y+21,16,'muted');self.line(26,y+49,454,y+49)
 def history(self,kind='callback'):
  self.caption('История клиента' if kind!='note' else 'История сделки');self.rect(14,76,452,242,'surface','line')
  if kind=='callback':
   self.text('Открытый перезвон',28,108,20,bold=True);self.text('6 ноября, 15:00',28,142,23,'accent',True);self.text('«Обсудить предложение»',28,184,19);self.line(28,204,452,204);self.text('Перезвонить',28,235,19);self.text('Отметка в CRM',28,267,16,'muted')
  elif kind=='no_answer':self.text('Отметка в CRM',28,111,16,'muted');self.text('Не взял трубку',28,153,23,'accent',True);self.text('Учебный Кедр · УЧ-001',28,202,19)
  else:
   self.text('Заметка',28,110,20,bold=True);self.text('Обсудить предложение',28,151,20);self.text('6 ноября в 15:00',28,183,20);self.line(28,210,452,210);self.text('Отправлены материалы',28,248,18,'muted')
 def outcomes(self,t,callback=True):
  saved=t>=10.3 if callback else t>=2
  if saved:self.history('callback' if callback else 'no_answer');return
  scroll=tween(t,1.4,2.2,0,210) if callback else 0
  lib.cairo_save(self.c);lib.cairo_rectangle(self.c,0,32,W,H-32);lib.cairo_clip(self.c);lib.cairo_translate(self.c,0,-scroll)
  self.rect(10,38,460,516,'surface','line');self.text('Итог звонка',24,64,21,bold=True)
  labels=['Не взял трубку','Перезвонить','Назначил встречу','Отправил КП','Интерес, думает','Отказ','Неверный номер']
  for i,label in enumerate(labels):self.button(label,24+(i%2)*226,78+(i//2)*44,208,active=(i==(1 if callback else 0) and t>=1),size=17)
  if callback and t>=1:
   for text,x,w in [('Завтра',24,93),('Через 2 дня',123,140),('Через неделю',269,165)]:self.button(text,x,268,w,size=16)
   self.field('Дата',typed(t,2.7,4.1,'06.11.2026'),24,327,208,2.5<=t<4.6,'дд.мм.гггг')
   self.field('Время',typed(t,4.8,5.7,'15:00'),250,327,208,4.6<=t<6,'--:--')
   self.text('Комментарий',24,403,18,'muted');self.rect(24,415,434,70,'surface','accent' if 6<=t<9.5 else 'line')
   comment=typed(t,6.2,8.8,'Обсудить предложение');self.text(comment,35,447,21,'fg' if comment else 'muted')
   self.button('Сохранить',24,498,155,primary=True);self.button('Отмена',190,498,112)
  lib.cairo_restore(self.c);self.rect(0,0,W,31,'bg',r=0);self.text('Учебный пример · вымышленные данные',14,21,13,'muted')
  points=[(0,430,320),(.8,280 if callback else 70,98),(1.2,280 if callback else 70,98),(2.4,65,355-scroll),(4.3,65,355-scroll),(4.6,295,355-scroll),(5.8,295,355-scroll),(6,65,439-scroll),(9.4,65,439-scroll),(9.9,82,518-scroll)]
  x,y=pointer_at(t,points);self.cursor(x,y,any(abs(t-c)<.12 for c in ([1,2.5,4.6,6,10] if callback else [1])))
 def callback(self,t,move=False,opening=False):
  self.caption('Перезвоны');self.rect(14,76,452,256,'surface','line')
  done=move and not opening and t>=6.5;self.text('7 ноября, 11:30' if done else '6 ноября, 15:00',26,105,21,'accent',True);self.text('Учебный Кедр · УЧ-001',26,138,19);self.text('Обсудить предложение',26,165,17,'muted')
  self.button('Позвонить',26,181,137,primary=True);self.button('Перенести',170,181,143,active=move and t>=1);self.button('Выполнено',320,181,132)
  if move and 1<=t and (opening or t<6.5):
   self.field('','06.11.2026' if opening else typed(t,1.5,2.8,'07.11.2026'),26,227,175,1.3<=t<3.3,'дд.мм.гггг');self.field('','15:00' if opening else typed(t,3.5,4.7,'11:30'),210,227,105,3.3<=t<5.7,'--:--');self.button('Сохранить',323,237,130,primary=True)
   self.text('Отменить перезвон',26,312,17,'muted')
  elif done:self.text('Срок изменён. Задача та же.',26,282,20,'accent')
  elif t>=2 and not move:self.text('Нужная задача выбрана',26,282,19,'muted')
  points=[(0,420,324),(.8,205,198),(2,205,198)] if opening else [(0,420,324),(.8,205,198),(1.3,65,254),(2.9,65,254),(3.3,248,254),(5.3,248,254),(6.1,360,254)] if move else [(0,420,324),(1,210,198),(2,210,198)]
  self.cursor(*pointer_at(t,points),click=any(abs(t-c)<.12 for c in ([1,1.3,3.3,6.1] if move else [1])))
 def note(self,t):
  if t>=6.4:self.history('note');return
  self.caption('Учебный Кедр');self.button('Заметка',24,78,137,active=t>=.9);self.button('Составить договор',172,78,282)
  if t>=.9:
   self.text('Заметка',24,147,19,'muted');self.rect(24,159,432,95,'surface','accent');text=typed(t,1.3,5.2,'Обсудить предложение 6 ноября в 15:00');self.text(text[:22],35,191,21);self.text(text[22:],35,222,21);self.button('Сохранить',24,269,155,primary=True)
  else:self.text('История',24,165,19,bold=True);self.text('Отправлены материалы',24,204,19,'muted')
  self.cursor(*pointer_at(t,[(0,423,330),(.7,75,98),(1.2,55,189),(5.6,55,189),(6,80,287)]),click=abs(t-.9)<.12 or abs(t-6)<.12)
 def contract(self,t):
  if t>=6.6:self.table('Журнал договоров',[('УЧ-001 · Кедр','Учебный договор')],0);return
  self.caption('Реквизиты клиента');self.field('Наименование',typed(t,.7,2,'Учебный Кедр'),24,90,432,.5<=t<2.5)
  self.field('ИНН',typed(t,2.6,3.8,'0000000000'),24,165,210,2.5<=t<4.4)
  self.rect(24,238,23,23,'soft' if t>=4.5 else 'surface','accent' if t>=4.5 else 'line',r=4)
  if t>=4.5:self.line(29,249,34,255,'accent',2.5);self.line(34,255,43,242,'accent',2.5)
  self.text('Реквизиты учебного клиента',57,248,17);self.text('сверены',57,271,17)
  self.button('Сформировать учебный договор',24,292,432,primary=True,size=19)
  self.cursor(*pointer_at(t,[(0,425,325),(.5,58,119),(2.5,58,194),(4.4,34,246),(5.8,159,309)]),click=any(abs(t-c)<.13 for c in [.5,2.5,4.5,6]))
 def segments(self,key,t):
  if key=='set_manager_summary':
   self.caption('Сводка · менеджер')
   for i,label in enumerate(['Весь отдел','Анна Учебная','Борис Учебный']):self.button(label,18+i*150,81,146,active=i==(2 if t>=1.4 else 0),size=15)
   self.text('Борис Учебный' if t>=1.4 else 'Весь отдел',24,177,22,bold=True);self.text('С результатом',24,221,18,'muted');self.text('34' if t>=1.4 else '61',24,267,28,'accent',True)
   self.cursor(*pointer_at(t,[(0,430,325),(1,361,102),(2,361,102)]),click=abs(t-1.4)<.15);return
  if key=='set_manager':
   self.caption('Звонки · менеджер');self.rect(24,79,300,44,'surface','accent');self.text('Борис Учебный' if t>=2.3 else 'Все менеджеры',37,107,20);self.text('⌄',295,106,20)
   if .7<=t<2.3:
    self.rect(24,128,300,135,'surface2','line');
    for i,text in enumerate(['Все менеджеры','Анна Учебная','Борис Учебный']):self.text(text,37,159+i*43,20,'accent' if i==2 else 'fg')
   if t>=2.3:self.text('Звонки Бориса Учебного',24,174,21);self.text('Учебный Кедр',24,219,19)
   self.cursor(*pointer_at(t,[(0,432,327),(.6,125,100),(1.1,125,100),(2,132,244),(2.5,132,244)]),click=abs(t-.7)<.12 or abs(t-2.2)<.12);return
  self.caption('Сводка' if key=='set_period_summary' else 'Звонки' if key=='set_period' else 'Сделки');key='set_period' if key=='set_period_summary' else key;labels=['Сегодня','7 дней','30 дней','Месяц'] if key=='set_period' else ['В работе','Зависшие','Отказ','Оплачено','Все']
  x=22
  for i,label in enumerate(labels):
   w=107 if key=='set_period' else [93,106,69,107,57][i];self.button(label,x,81,w-4,active=i==(1 if t>=1.4 else 0),size=16 if key=='set_period' else 14);x+=w
  self.text('Учебный Кедр',26,182,21);self.text('За 7 дней' if key=='set_period' else 'Telegram или почта',26,220,18,'muted')
  self.cursor(*pointer_at(t,[(0,420,325),(1,165,100),(2,165,100)]),click=abs(t-1.4)<.15)
 def select(self,key,t):
  selected=t>=1.7
  if key=='select_client':
   self.caption('Дальше по очереди')
   for i,text in enumerate(['Другой клиент · УЧ-002','Учебный Кедр · УЧ-001','Учебный Лён · УЧ-003']):
    self.rect(16,83+i*64,448,57,'soft' if selected and i==1 else 'surface','line');self.text(text,29,116+i*64,20)
   if selected:
    self.rect(0,32,W,H-32,'bg',r=0);self.text('Учебный Кедр · УЧ-001',24,70,23,bold=True);self.text('Итог звонка',24,123,20,bold=True);self.button('Не взял трубку',24,148,208);self.button('Перезвонить',250,148,208);self.text('История клиента',24,234,20,bold=True);self.text('Карточка нужного клиента',24,275,18,'muted')
  elif key.startswith('select_report'):
   title='Сводка за неделю' if key.endswith('weekly') else 'Итог дня менеджера';self.caption('Отчёты');self.rect(14,83,452,210,'surface','accent' if selected else 'line');self.text(title,26,118,21,bold=True)
   self.text('Борис Учебный' if key.endswith('weekly') else 'Анна Учебная',26,153,18,'muted')
   if selected:
    for i,line in enumerate(['Борису стоит обсудить','согласование следующего','разговора.'] if key.endswith('weekly') else ['Отправка предложения','согласована. Согласуйте','дату продолжения.']):self.text(line,26,199+i*30,20)
  elif key=='select_person' or key=='select_call_staff':
   self.caption('Сотрудники' if key=='select_person' else 'Борис Учебный')
   if not selected and key=='select_person':
    self.table('Сотрудники',[('Анна Учебная','Менеджер'),('Борис Учебный','Менеджер')],-1)
   else:
    self.text('Дата продолжения не согласована',24,109,19);self.button('Учебный разбор',24,137,225)
    if selected and key=='select_call_staff':self.review()
  elif selected:
   if key=='select_call':self.review()
   else:self.deal()
  else:
   title={'select_deal':'Сделки','select_call':'Звонки','select_document':'Журнал договоров'}[key]
   self.table(title,[('Другой клиент','Другая запись'),('Учебный Кедр','Разбор' if key=='select_call' else 'Договор' if key=='select_document' else 'Telegram / почта')])
  point=(120,165) if key=='select_call_staff' else (125,117) if key.startswith('select_report') else (120,210) if key!='select_client' else (130,180)
  self.cursor(*pointer_at(t,[(0,430,330),(1.2,*point),(2,*point)]),click=abs(t-1.7)<.13)
 def deal(self):
  self.rect(12,38,456,310,'surface','line');self.text('Учебный Кедр',26,74,23,bold=True);self.text('×',430,73,27,'muted');self.text('Telegram или почта',26,111,18,'muted');self.button('Составить договор',24,134,274);self.button('Заметка',309,134,139);self.text('История',26,211,20,bold=True);self.text('Отправлены материалы',26,251,20);self.text('Следующий шаг не зафиксирован',26,286,18,'muted')
 def review(self):
  self.rect(12,38,456,310,'surface','line');self.text('Учебный разбор',26,76,23,bold=True);self.text('×',430,74,27,'muted');self.text('Итог',26,126,20,bold=True)
  for i,line in enumerate(['Клиент согласился получить','предложение, но дата','следующего разговора','не согласована.']):self.text(line,26,163+i*32,21)
 def close(self,key,t):
  if t>=1.9:
   if key=='close_card':self.callback(0)
   else:self.table('Сделки' if key=='close_deal' else 'Звонки',[('Учебный Кедр','В работе')])
   return
  if key=='close_deal':self.deal()
  elif key=='close_review':self.review()
  else:
   self.caption('Карточка клиента');self.text('×',430,53,27,'muted');self.text('Учебный Кедр · УЧ-001',24,112,21);self.text('История клиента',24,168,20,bold=True);self.text('Клиент ждёт обсуждения',24,211,20);self.text('предложения.',24,241,20)
  self.cursor(*pointer_at(t,[(0,330,320),(1.3,435,58 if key=='close_card' else 62),(1.8,435,58 if key=='close_card' else 62)]),click=abs(t-1.6)<.15)
 def navigation(self,key,button,t):
  if key in ['open_section','visit_access']:
   title='Настройки' if key=='visit_access' else 'Сделки';self.caption(title)
   self.button('Промты' if key=='visit_access' else 'Сделки',24,83,150,active=t<1.3);self.button('Доступы' if key=='visit_access' else 'Договоры',184,83,160,active=t>=1.3)
   if t>=1.3:self.text('Учётные записи' if key=='visit_access' else 'Журнал договоров',24,183,23)
   pt=(230,102)
  elif key=='compose':
   if t<1.8:self.deal()
   else:self.caption('Договор по сделке:');self.text('Учебный Кедр',24,103,23);self.field('Наименование','',24,155,420,placeholder='Название клиента');self.field('ИНН','',24,236,210,placeholder='ИНН клиента')
   pt=(95,153)
  else:
   self.caption('Разделы CRM');self.button(button,24,88,270,active=t>=1.3,size=22)
   if t>=1.3:self.text(button,24,195,25,bold=True);self.text('Раздел открыт',24,232,18,'muted')
   pt=(120,107)
  self.cursor(*pointer_at(t,[(0,430,325),(1,*pt),(2,*pt)]),click=abs(t-1.3)<.15)
 def bytes(self):lib.cairo_surface_flush(self.s);return C.string_at(lib.cairo_image_surface_get_data(self.s),lib.cairo_image_surface_get_stride(self.s)*H)
 def png(self,path):
  if lib.cairo_surface_write_to_png(self.s,str(path).encode()):raise RuntimeError('Could not write preview poster')
 def close_surface(self):lib.cairo_destroy(self.c);lib.cairo_surface_destroy(self.s)


def render(key,t,button):
 f=Frame()
 if key=='save_outcome_callback':f.outcomes(t)
 elif key=='save_outcome_no_answer':f.outcomes(t,False)
 elif key=='save_note':f.note(t)
 elif key=='prepare_contract':f.contract(t)
 elif key=='move_task':f.callback(t,True)
 elif key=='select_task':f.callback(t,True,True)
 elif key=='verify_task':f.callback(7,True)
 elif key=='select_task_context':
  if t<1.7:f.callback(0);f.cursor(*pointer_at(t,[(0,430,326),(1,80,200)]),click=abs(t-1.2)<.15)
  else:f.caption('Карточка клиента');f.text('История клиента',24,119,23);f.text('Клиент ждёт обсуждения',24,170,21);f.text('предложения.',24,202,21)
 elif key in ['set_manager','set_period','set_manager_summary','set_period_summary','filter_deals']:f.segments(key,t)
 elif key.startswith('select_'):f.select(key,t)
 elif key.startswith('close_'):f.close(key,t)
 elif key=='verify_outcome':f.history('no_answer');f.cursor(350,160)
 elif key=='verify_history':f.history('note');f.cursor(350,180)
 else:f.navigation(key,button,t)
 return f
