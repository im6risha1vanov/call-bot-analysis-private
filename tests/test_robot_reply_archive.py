import unittest
from crm import robot_reply_archive as arc

SPEECH = ('[00:01 Спикер 0] Добрый день, слушаю вас.\n'
          '[00:03 Спикер 1] Здравствуйте, производство картонной упаковки, расскажите, нужна ли вам коробка?\n'
          '[00:09 Спикер 0] Подскажите, какая печать возможна?\n'
          '[00:13 Спикер 1] Мы делаем простую флексопечать в один или два цвета.\n'
          '[00:19 Спикер 0] Хорошо, а какой тираж необходим?\n'
          '[00:23 Спикер 1] Для расчёта уточним ваши параметры.')
OPERATORS={'olga':'14','oksana':'15','robot':'13'}


def row(**changes):
    obj={'id':1,'extension':'14','tab':None,'duration_seconds':30,'transcript':SPEECH}
    obj.update(changes)
    return obj

class ArchiveTests(unittest.TestCase):
    def test_intro_proves_older_packaging_operator(self):
        self.assertEqual(arc.classify(row(),OPERATORS),('eligible','verified_operator_intro','olga'))
        status,reason,pairs,role=arc.materialize(SPEECH)
        self.assertEqual(status,'extracted')
        self.assertGreaterEqual(len(pairs),1)
        self.assertIn('inferred_intro',pairs[0]['labels'])
        self.assertEqual(pairs[0]['at'],9)

    def test_verified_sheet_oksana_and_excluded_other_sheet(self):
        self.assertEqual(arc.classify(row(extension='15',tab=arc.SHEET),OPERATORS)[:2],('eligible','linked_sheet'))
        self.assertEqual(arc.classify(row(tab='Найм'),OPERATORS)[0],'other_verified_sheet')
        self.assertEqual(arc.classify(row(extension='15'),OPERATORS),('eligible','verified_operator_intro','oksana'))

    def test_ambiguous_machine_short_and_robot_are_not_training(self):
        short=row(tab=arc.SHEET,duration_seconds=5)
        self.assertEqual(arc.classify(short,OPERATORS)[0],'too_short')
        self.assertEqual(arc.classify(row(extension='13',tab=arc.SHEET),OPERATORS)[0],'robot_or_unverified_operator')
        machine=SPEECH.replace('Подскажите, какая печать возможна?','Абонент недоступен, оставьте голосовое сообщение.')
        self.assertEqual(arc.classify(row(transcript=machine,tab=arc.SHEET),OPERATORS)[0],'answering_machine')
        ambiguous=SPEECH.replace('Спикер 1','Спикер 0')
        self.assertEqual(arc.classify(row(transcript=ambiguous,tab=arc.SHEET),OPERATORS)[0],'ambiguous_speakers')

    def test_other_service_intro_blocks_even_verified_sheet(self):
        text=SPEECH.replace('Здравствуйте, производство картонной упаковки, расскажите, нужна ли вам коробка?',
                            'Здравствуйте, звоню по поводу бухгалтерского сопровождения и коробок.')
        self.assertIn(arc.classify(row(transcript=text,tab=arc.SHEET),OPERATORS)[0],('contradictory_topic','ambiguous_speakers'))

    def test_customer_product_question_and_business_description_do_not_assign_roles(self):
        lines=arc.call_analysis.split_lines('[00:00 Спикер 0] Здравствуйте, слушаю.\n[00:03 Спикер 1] Вы производство картонной упаковки?\n[00:08 Спикер 0] Да, у нас производство картонной упаковки.')
        self.assertIsNone(arc.role_map(lines)[0])
        lines=arc.call_analysis.split_lines('[00:00 Спикер 0] Алло, да.\n[00:03 Спикер 1] Мы производим картонную упаковку для клиентов.')
        self.assertIsNone(arc.role_map(lines)[0])

    def test_refusal_stops_followup_but_prior_price_or_supplier_preserved(self):
        text=('[00:00 Оператор] Добрый день, меня зовут Оксана, производство картонной упаковки.\n'
              '[00:05 Клиент] Мы уже работаем с поставщиком, расскажите про стоимость.\n'
              '[00:10 Оператор] Расскажите, пожалуйста, какой нужен размер.\n'
              '[00:15 Клиент] Не звоните мне больше.\n'
              '[00:20 Оператор] А когда перезвонить?\n'
              '[00:25 Клиент] Завтра утром.\n'
              '[00:30 Оператор] Хорошо, позвоню завтра.')
        status,_,pairs,_=arc.materialize(text)
        self.assertEqual(status,'extracted')
        self.assertEqual(len(pairs),1)
        self.assertIn('поставщиком',pairs[0]['question'])
        self.assertNotIn('Завтра',str(pairs))

    def test_operator_configuration_mismatch_fails_closed(self):
        specs=[{'id':'olga','extension':'14','configured':True},{'id':'oksana','extension':'15','configured':True},{'id':'robot','extension':'13','configured':True}]
        self.assertEqual(arc.resolve_operators(specs,'14'),OPERATORS)
        with self.assertRaises(ValueError): arc.resolve_operators(specs,'99')
        with self.assertRaises(ValueError): arc.resolve_operators(specs,'15')

if __name__=='__main__': unittest.main()
