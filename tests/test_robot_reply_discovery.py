import unittest
from crm.robot_reply_discovery import build_candidates


def source(id, q, answer, reaction='Да, через сайт', **extra):
    return {'source_type':'human','source_id':str(id),'owner':'a','project_id':1,'evidence':[{'intent':'unknown:example','question':q,'answer':answer,'reaction':reaction,'at':8,'answer_at':12,'reaction_at':15,**extra}]}

class DiscoveryTests(unittest.TestCase):
    def test_unknown_drawing_question_repeated_with_neutral_question(self):
        items=[source(1,'Как отправить чертеж?','Как вам удобнее отправить чертеж?'), source(2,'Как отправить вам чертеж?','Как вам удобнее отправить чертеж?')]
        found=build_candidates(items,'generic')
        self.assertEqual(len(found),1)
        self.assertFalse(found[0]['fact_needed'])
        self.assertEqual(len(found[0]['group']['source_ids']),2)
        self.assertEqual(found[0]['group']['observations'][0]['reaction_at'],15)

    def test_factual_answer_is_blocked_for_review(self):
        items=[source(1,'Как отправить чертеж?','Мы принимаем чертежи через сайт.'),source(2,'Как отправить вам чертеж?','Мы принимаем чертежи через сайт.')]
        candidate=build_candidates(items)[0]
        self.assertTrue(candidate['fact_needed'])
        self.assertIn('проверка',candidate['fact_note'])

    def test_distinct_sources_required_and_no_mixed_projects(self):
        one=source(1,'Как отправить чертеж?','Как удобнее передать чертеж?')
        self.assertEqual(build_candidates([one,one]),[])
        with self.assertRaises(ValueError):
            build_candidates([one,{**source(2,'Как отправить чертеж?','Как удобнее передать чертеж?'),'owner':'b','project_id':2}])

    def test_reject_pii_filler_repeat_and_negative_reaction(self):
        base=source(1,'Как отправить чертеж?','Как удобнее передать чертеж?')
        for other in [source(2,'Как отправить чертеж?','Как удобнее передать чертеж?',reaction='Не надо'),
                      source(2,'Как отправить чертеж?','Как удобнее передать чертеж?',reaction='Спасибо'),
                      source(2,'Как отправить чертеж?','Как удобнее передать чертеж?',reaction='Да, +7 999 123-45-67'),
                      source(2,'Как отправить чертеж?','Как удобнее передать чертеж?',repeat_question=True)]:
            self.assertEqual(build_candidates([base,other]),[])

    def test_known_intent_does_not_compete(self):
        a=source(1,'Сколько стоят коробки?','Как удобнее передать чертеж?',intent='price')
        b=source(2,'Сколько стоят коробки?','Как удобнее передать чертеж?',intent='price')
        self.assertEqual(build_candidates([a,b]),[])

    def test_contradictory_interrogatives_are_not_clustered(self):
        a=source(1,'Как отправить чертеж?', 'Как удобнее передать чертеж?')
        b=source(2,'Когда отправить чертеж?', 'Как удобнее передать чертеж?')
        self.assertEqual(build_candidates([a,b]),[])

    def test_generic_supplier_objection_can_be_discovered(self):
        a=source(1,'Мы работаем с поставщиком','Что важно при выборе поставщика?',reaction='Важна надёжность',intent='supplier')
        b=source(2,'Мы уже работаем с поставщиком','Что важно при выборе поставщика?',reaction='Важны сроки',intent='supplier')
        self.assertEqual(len(build_candidates([a,b],'generic')),1)
        self.assertEqual(build_candidates([a,b],'packaging'),[])

    def test_no_combination_of_unrelated_terms(self):
        a=source(1,'Как отправить чертеж?','Как удобнее передать чертеж?')
        b=source(2,'Как отправить реквизиты?','Как удобнее передать чертеж?')
        self.assertEqual(build_candidates([a,b]),[])

if __name__ == '__main__': unittest.main()
