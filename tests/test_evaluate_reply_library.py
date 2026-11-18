import unittest
from scripts.evaluate_reply_library import evaluate, split

class HeldOutTests(unittest.TestCase):
    def test_train_only_and_unknown_privacy(self):
        train = [str(n) for n in range(100) if split(n) == 'train'][:2]
        test = next(str(n) for n in range(100) if split(n) == 'test')
        def call(cid, question):
            return {'call_id':cid, 'transcript':[('robot','Здравствуйте'),('client',question),('robot','Ответ')]}
        result = evaluate([call(train[0],'Сколько стоит?'), call(train[1],'Какая цена?'),
                           call(test,'Сколько стоит?')], 'packaging')
        self.assertEqual((result['train_calls'],result['test_calls']), (2,1))
        self.assertGreaterEqual(result['candidate_count'], 1)
        self.assertGreaterEqual(result['fact_needed_blocked'], 1)
        self.assertEqual(result['counts']['candidate_selected'], 1)
        unknown = evaluate([call(train[0],'Сколько стоит?'), call(train[1],'Какая цена?'),
                            call(test,'Секретная фраза клиента')], 'packaging')
        self.assertNotIn('Секретная', str(unknown))
        self.assertIn('unknown', unknown['by_intent'])

    def test_duplicate_id_rejected(self):
        with self.assertRaises(ValueError):
            evaluate([{'call_id':'x'}, {'call_id':'x'}])
