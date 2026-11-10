import copy
import datetime as dt
import unittest
from unittest.mock import patch
import model_news_prefetch as p

class FollowupEvidenceTests(unittest.TestCase):
    def setUp(self):
        p._TURN_EVIDENCE.clear()
        self.user='Найди три новости LLM за '+dt.datetime.now(p.LOCAL_TZ).date().isoformat()
        self.first={'messages':[{'role':'user','content':self.user}]}
        self.follow={'messages':[{'role':'user','content':self.user},
            {'role':'assistant','content':None,'tool_calls':[{'function':{'name':'process','arguments':'{}'}}]},
            {'role':'tool','content':'REAL_TOOL_RESULT','tool_call_id':'call1'}]}
        self.evidence={'kind':'public_rss_headlines_not_verified_full_articles',
            'articles':[{'title':'A real headline','url':'https://example.test/news'}],
            'feed_statuses':[{'source':'google_news','status':'ok'}]}

    def initial(self):
        with patch.object(p,'_collect_date',return_value=copy.deepcopy(self.evidence)) as fetch:
            value=p.maybe_prefetch_news(self.first)
            self.assertEqual(fetch.call_count,1)
            return value

    def test_followup_uses_initial_evidence_without_fetch(self):
        self.initial()
        with patch.object(p,'_collect_date',side_effect=AssertionError('network forbidden')):
            value=p.maybe_prefetch_news(self.follow)
        self.assertEqual(value['articles'],self.evidence['articles'])
        self.assertEqual(value['retrieval_mode'],'cached_no_network')
        self.assertTrue(value['reused_from_initial_user_turn'])

    def test_no_cached_evidence_means_no_fetch(self):
        with patch.object(p,'_collect_date',side_effect=AssertionError('network forbidden')):
            self.assertIsNone(p.maybe_prefetch_news(self.follow))

    def test_returned_initial_evidence_cannot_corrupt_cache(self):
        self.initial()['articles'][0]['title']='CORRUPTED'
        self.assertEqual(p.maybe_prefetch_news(self.follow)['articles'],self.evidence['articles'])

    def test_returned_followup_cannot_corrupt_cache(self):
        self.initial()
        p.maybe_prefetch_news(self.follow)['articles'].clear()
        self.assertEqual(p.maybe_prefetch_news(self.follow)['articles'],self.evidence['articles'])

    def test_latest_user_is_the_only_key(self):
        self.initial()
        changed=copy.deepcopy(self.follow)
        changed['messages'].insert(2,{'role':'user','content':'Теперь объясни устройство трансформера'})
        self.assertIsNone(p.maybe_prefetch_news(changed))

    def test_different_news_request_does_not_reuse(self):
        self.initial()
        changed=copy.deepcopy(self.follow)
        changed['messages'][0]['content']+=' только первоисточники'
        self.assertIsNone(p.maybe_prefetch_news(changed))

    def test_assistant_tail_does_not_trigger_fetch_or_reuse(self):
        self.initial()
        with patch.object(p,'_collect_date',side_effect=AssertionError('network forbidden')):
            self.assertIsNone(p.maybe_prefetch_news({'messages':self.follow['messages'][:-1]}))

    def test_expiry_does_not_extend_on_reads(self):
        with patch.object(p.time,'monotonic',return_value=1000):self.initial()
        with patch.object(p.time,'monotonic',return_value=1299):
            self.assertEqual(p.maybe_prefetch_news(self.follow)['evidence_age_seconds'],299)
        with patch.object(p.time,'monotonic',return_value=1300),patch.object(p,'_collect_date',side_effect=AssertionError('no fetch')):
            self.assertIsNone(p.maybe_prefetch_news(self.follow))
        self.assertEqual(len(p._TURN_EVIDENCE),0)

    def test_clock_rollback_discards_cache(self):
        with patch.object(p.time,'monotonic',return_value=1000):self.initial()
        with patch.object(p.time,'monotonic',return_value=999):
            self.assertIsNone(p.maybe_prefetch_news(self.follow))

    def test_count_is_bounded(self):
        for i in range(40):p._remember_turn(str(i),self.evidence)
        self.assertEqual(len(p._TURN_EVIDENCE),p.TURN_EVIDENCE_MAX)
        self.assertNotIn('0',p._TURN_EVIDENCE)

    def test_cache_has_no_raw_user_text(self):
        self.initial()
        keys=list(p._TURN_EVIDENCE)
        self.assertEqual(len(keys[0][0]),64)
        self.assertNotIn(self.user,repr(p._TURN_EVIDENCE))

    def test_canonical_tool_messages_remain_exact(self):
        self.initial();before=copy.deepcopy(self.follow)
        p.maybe_prefetch_news(self.follow)
        self.assertEqual(before,self.follow)

    def test_failed_initial_fetch_does_not_invent_evidence(self):
        value={**self.evidence,'articles':[],'feed_statuses':[{'source':'google_news','status':'deadline_exceeded'}]}
        with patch.object(p,'_collect_date',return_value=value):p.maybe_prefetch_news(self.first)
        follow=p.maybe_prefetch_news(self.follow)
        self.assertEqual(follow['articles'],[])
        self.assertEqual(follow['feed_statuses'],value['feed_statuses'])

    def test_non_news_user_does_not_fetch(self):
        with patch.object(p,'_collect_date',side_effect=AssertionError('no fetch')):
            self.assertIsNone(p.maybe_prefetch_news({'messages':[{'role':'user','content':'Привет'}]}))

    def test_model_view_retains_evidence_and_actual_tool_result(self):
        from model_prompt_compaction import model_request_for_web
        self.initial();request=copy.deepcopy(self.follow)
        request['model']='web-chatgpt-high'
        request['tools']=[{'type':'function','function':{'name':'process',
            'parameters':{'type':'object','properties':{'action':{'enum':['list','poll']}}}}}]
        before=copy.deepcopy(request)
        with patch.object(p,'_collect_date',side_effect=AssertionError('no network')):
            value=model_request_for_web(request)
        self.assertEqual(value['messages'][-1],request['messages'][-1])
        self.assertEqual(value['tool_catalog'][0]['name'],'process')
        self.assertEqual(value['prefetched_public_news']['articles'],self.evidence['articles'])
        self.assertEqual(request,before)

    def test_no_user_or_unbounded_user_text_creates_no_cache(self):
        for messages in ([],[{'role':'tool','content':'untrusted'}],
                         [{'role':'user','content':self.user+'x'*2100}]):
            self.assertIsNone(p.maybe_prefetch_news({'messages':messages}))
        self.assertEqual(len(p._TURN_EVIDENCE),0)

if __name__=='__main__':unittest.main()
