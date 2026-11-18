import unittest
from crm.robot_reply_learning import extract, normal


class LearningTest(unittest.TestCase):
    def test_russian_normalization(self):
        self.assertEqual(normal('  Ёлка, КОРОБ! '), 'елка короб')

    def test_no_speaker_fabrication(self):
        status, reason, evidence = extract('00:00 Алло\n00:03 Что вам нужно?')
        self.assertEqual((status, reason, evidence), ('unattributed', 'speaker_roles_unavailable', []))

    def test_observed_pairs_repeat_and_outcome(self):
        transcript = [('robot', 'Здравствуйте'), ('client', 'Сколько стоит?'), ('robot', 'Цена уточняется.'),
                      ('client', 'Сколько стоит?'), ('robot', 'Стоимость уточнит специалист.')]
        status, _, items = extract(transcript, 'callback')
        self.assertEqual(status, 'extracted')
        self.assertEqual(len(items), 2)
        self.assertFalse(items[0]['repeat_question'])
        self.assertTrue(items[1]['repeat_question'])
        self.assertEqual(items[0]['outcome'], 'callback')
        self.assertIn('unreviewed', items[0]['labels'])

    def test_unknown_speaker_not_used(self):
        self.assertEqual(extract([('unknown', 'Вопрос?'), ('robot', 'Ответ')])[2], [])


if __name__ == '__main__':
    unittest.main()

class RealFormatTest(unittest.TestCase):
    def test_introduction_attribution_timestamps_and_redaction(self):
        from crm.robot_reply_learning import extract
        text = ('[00:01 Спикер 0] Алло.\n[00:03 Спикер 1] Здравствуйте, меня зовут Оксана.\n'
                '[00:07 Спикер 0] Какая цена? Мой номер +7 999 123-45-67.\n'
                '[00:12 Спикер 1] Уточнит специалист.\n[00:15 Спикер 0] А сколько стоит?\n'
                '[00:19 Спикер 1] Нужно уточнить.\n')
        status, _, items = extract(text)
        self.assertEqual(status, 'extracted')
        self.assertEqual(items[0]['intent'], 'price')
        self.assertEqual(items[0]['at'], 7)
        self.assertEqual(items[0]['reaction_at'], 15)
        self.assertNotIn('999', str(items))
        self.assertIn('inferred_intro', items[0]['labels'])
        self.assertTrue(items[1]['repeat_question'])

    def test_ambiguous_introductions_do_not_assign_roles(self):
        from crm.robot_reply_learning import extract
        text = '[00:01 Спикер 0] Меня зовут Оксана.\n[00:04 Спикер 1] Меня зовут Ольга.'
        self.assertEqual(extract(text)[0], 'unattributed')

    def test_intent_clustering_distinct_sources(self):
        from crm.robot_reply_learning import hypotheses
        sources = [{'source_type': 'human', 'source_id': str(i), 'evidence': [{'question': q, 'intent': 'price'}]} for i, q in enumerate(['Какая цена?', 'Сколько стоит?'])]
        group = hypotheses(sources)[0]
        self.assertEqual(len(group['source_ids']), 2)
        self.assertEqual(len(group['variants']), 2)

class ProposalRulesTest(unittest.TestCase):
    def sources(self, phrases):
        from crm.robot_reply_learning import intent_of
        return [{'source_type':'human', 'source_id':str(n), 'evidence':[{'question':q,'intent':intent_of(q),'answer':'наблюдение'}]} for n,q in enumerate(phrases)]

    def test_packaging_requires_explicit_profile_and_two_sources(self):
        from crm.robot_reply_learning import build_candidates
        src = self.sources(['Сколько стоит?', 'Какая цена?'])
        self.assertEqual(build_candidates(src), [])
        proposals = build_candidates(src, 'packaging')
        self.assertEqual(len(proposals), 1)
        self.assertIn('размера', proposals[0]['answer'])
        self.assertEqual(len(proposals[0]['triggers']), 2)
        self.assertEqual(build_candidates(src[:1], 'packaging'), [])

    def test_packaging_facts_and_excluded_or_compound(self):
        from crm.robot_reply_learning import build_candidates
        good = self.sources(['Какие коробки производите?', 'Что вы производите?'])
        self.assertIn('гофрокартона', build_candidates(good, 'packaging')[0]['answer'])
        for text in ['Не звоните', 'Сколько стоит и когда перезвоните?', 'Какая цена 60x40x40?', 'Алло', 'Это автоответчик?']:
            self.assertEqual(build_candidates(self.sources([text,text]), 'packaging'), [], text)

    def test_fingerprint_stable_when_duplicate_source_added(self):
        from crm.robot_reply_learning import build_candidates
        src = self.sources(['Сколько стоит?', 'Какая цена?'])
        first = build_candidates(src, 'packaging')[0]['fingerprint']
        self.assertEqual(first, build_candidates(src + self.sources(['Какая цена?']), 'packaging')[0]['fingerprint'])
    def test_improvement_requires_repeated_observed_reasks(self):
        from crm.robot_reply_learning import build_candidates
        src = self.sources(['Сколько стоит?', 'Какая цена?'])
        first = build_candidates(src, 'packaging')[0]
        for item in src:
            item['evidence'][0]['repeat_question'] = True
        improved = build_candidates(src, 'packaging')[0]
        self.assertNotEqual(first['answer'], improved['answer'])
        self.assertNotEqual(first['fingerprint'], improved['fingerprint'])
        self.assertIn('размера', improved['answer'])

    def test_generic_repeated_acknowledgements_and_busy(self):
        from crm.robot_reply_learning import build_candidates
        src = self.sources(['Сейчас не интересует', 'Мне не интересно'])
        for source in src: source['evidence'][0]['answer'] = 'Понял вас, спасибо.'
        self.assertEqual(len(build_candidates(src, 'generic')), 1)
        for source in src: source['evidence'][0]['answer'] = 'Мы обещаем бесплатную доставку.'
        self.assertEqual(build_candidates(src, 'generic'), [])
        busy = self.sources(['Сейчас некогда', 'Перезвоните позже'])
        self.assertNotIn('когда', build_candidates(busy, 'packaging')[0]['answer'].lower())
    def test_single_repeated_print_question_is_actionable(self):
        from crm.robot_reply_learning import build_candidates, extract
        transcript=[('robot','Здравствуйте'),('client','У вас есть офсетная печать?'),('robot','Печать?'),
                    ('client','Офсетная печать есть?'),('robot','Уточню.')]
        status, _, pairs=extract(transcript)
        src=[{'source_type':'human','source_id':'one','evidence':pairs}]
        drafts=build_candidates(src,'packaging')
        self.assertEqual(len(drafts),1)
        self.assertEqual(drafts[0]['answer'], 'Мы делаем простую флексопечать в один или два цвета.')
        self.assertEqual(len(drafts[0]['group']['source_ids']), 1)

    def test_customer_spec_does_not_make_print_question(self):
        from crm.robot_reply_learning import build_candidates
        self.assertEqual(build_candidates(self.sources(['Мне нужна печать', 'Нам нужна печать']), 'packaging'), [])

    def test_new_variant_alone_does_not_change_fingerprint(self):
        from crm.robot_reply_learning import build_candidates
        src=self.sources(['Сколько стоит?', 'Какая цена?'])
        first=build_candidates(src, 'packaging')[0]
        src += self.sources(['Какая стоимость?'])
        self.assertEqual(first['fingerprint'], build_candidates(src, 'packaging')[0]['fingerprint'])

    def test_polarity_and_mixed_speaker_price_are_rejected(self):
        from crm.robot_reply_learning import intent_of, build_candidates
        positive = 'Goodspack? Не знаю, да, из Москвы. Мне нужно очень много разных размеров'
        self.assertNotEqual(intent_of(positive), 'no_need')
        noise = 'есть мы стоимость коробки? - Смотрите через предложения.'
        self.assertEqual(build_candidates(self.sources([noise, noise]), 'packaging'), [])

class IncrementalLearningTest(unittest.IsolatedAsyncioTestCase):
    async def test_unchanged_source_is_not_reparsed(self):
        import hashlib, json
        from unittest.mock import patch
        from crm.robot_reply_learning import ingest_source
        raw = [('client','Вопрос')]
        canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str)
        digest = hashlib.sha256(('extract-v2:' + canonical).encode('utf-8')).hexdigest()
        class Pool:
            async def fetchrow(self, *args): return {'transcript_hash':digest,'outcome':'','metadata':{}}
            async def execute(self, sql, *args): self.sql = sql
        pool=Pool()
        with patch('crm.robot_reply_learning.extract', side_effect=AssertionError('reparsed')):
            self.assertEqual(await ingest_source(pool,'owner',1,'human','1',raw), 'unchanged')
        self.assertIn('checked_at',pool.sql)

    async def test_refresh_skips_unchanged_projects(self):
        from unittest.mock import patch
        from crm.robot_reply_learning import refresh_all
        class Pool:
            async def fetch(self, sql):
                self.sql=sql
                return []
        pool=Pool()
        with patch('crm.robot_reply_learning.propose', side_effect=AssertionError('unchanged refresh')):
            await refresh_all(pool)
        self.assertIn('updated_at',pool.sql)

    def test_location_draft_does_not_guess_and_color_needs_subject(self):
        from crm.robot_reply_learning import build_candidates
        sources = ProposalRulesTest().sources(['А вы где находитесь?', 'Где у вас производство?'])
        answer = build_candidates(sources, 'packaging')[0]['answer']
        self.assertEqual(answer, 'Точный адрес производства нужно уточнить.')
        self.assertEqual(build_candidates(ProposalRulesTest().sources(['Какие цвета есть?', 'А цвета какие есть?']), 'packaging'), [])

class DiscoveryIntegrationTest(unittest.TestCase):
    def test_builder_keeps_fact_needed_and_independent_sources(self):
        from crm.robot_reply_learning import build_candidates
        from tests.test_robot_reply_discovery import source
        calls = [source(1, 'Как отправить чертеж?', 'Мы принимаем чертежи через сайт.'),
                 source(2, 'Как отправить вам чертеж?', 'Мы принимаем чертежи через сайт.')]
        out = build_candidates(calls, 'generic')
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0]['fact_needed'])
        self.assertEqual(len(out[0]['group']['source_ids']), 2)

class GuideStarterTest(unittest.TestCase):
    def test_explicit_scope_empty_project_and_fact_guard(self):
        from crm.robot_reply_learning import build_candidates
        self.assertEqual(build_candidates([], 'packaging'), [])
        self.assertEqual(build_candidates([], 'generic', True), [])
        guide=build_candidates([], 'packaging', True)
        by_intent={c['intent']:c for c in guide}
        self.assertTrue({'price','assortment','material','print','quote_requirements','supplier','busy','location'} <= set(by_intent))
        self.assertTrue(all(c['source_kind']=='guide' and not c['group']['source_ids'] for c in guide))
        self.assertTrue(by_intent['location']['fact_needed'])
        self.assertIn('Какая цена?',by_intent['price']['triggers'])
        self.assertIn('Когда лучше перезвонить?',by_intent['busy']['answer'])
        self.assertNotIn('?',by_intent['quote_requirements']['answer'])

    def test_learned_topic_is_not_duplicated(self):
        from crm.robot_reply_learning import build_candidates
        sources=ProposalRulesTest().sources(['Какая цена?', 'Сколько стоит?'])
        out=build_candidates(sources,'packaging',True)
        price=[c for c in out if c['intent']=='price']
        self.assertEqual(len(price),1)
        self.assertEqual(len(price[0]['group']['source_ids']),2)
        self.assertEqual(price[0]['source_kind'],'guide')
    def test_learned_location_is_still_fact_blocked(self):
        from crm.robot_reply_learning import build_candidates
        rows=ProposalRulesTest().sources(['А вы где находитесь?', 'Где у вас производство?'])
        candidate=next(c for c in build_candidates(rows,'packaging',True) if c['intent']=='location')
        self.assertTrue(candidate['fact_needed'])
        self.assertIn('Где находится производство?',candidate['triggers'])

    def test_repeated_new_variant_changes_snapshot_once(self):
        from crm.robot_reply_learning import build_candidates
        rows=ProposalRulesTest().sources(['Какая цена?', 'Сколько стоит?'])
        before=next(c for c in build_candidates(rows,'packaging',True) if c['intent']=='price')
        extra=ProposalRulesTest().sources(['Какая стоимость?', 'Какая стоимость?'])
        for i, source in enumerate(extra): source['source_id']='extra'+str(i)
        after=next(c for c in build_candidates(rows+extra,'packaging',True) if c['intent']=='price')
        self.assertNotEqual(before['fingerprint'],after['fingerprint'])
        self.assertIn('Какая стоимость?',after['triggers'])

class ApprovedPrintGuideTests(unittest.TestCase):
    def test_guide_snapshot_has_separate_explicit_offset_and_logo_variants(self):
        from crm.robot_reply_learning import build_candidates
        items = build_candidates([], 'packaging', guide_enabled=True)
        item = next(x for x in items if x['intent'] == 'print')
        for phrase in ('Какая печать возможна?', 'Можно нанести логотип?', 'У вас есть офсетная печать?', 'Офсетная печать есть?', 'Афсетная печать есть?'):
            self.assertIn(phrase,item['triggers'])
        self.assertEqual(item['answer'],'Мы делаем простую флексопечать в один или два цвета.')
