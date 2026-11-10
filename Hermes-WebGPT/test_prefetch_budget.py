import datetime as dt
from threading import BoundedSemaphore, Event, Lock
import time
import unittest
from unittest.mock import patch
import model_news_prefetch as prefetch


DAY=dt.date(2026,11,10)


class PrefetchBudgetTests(unittest.TestCase):
    def setUp(self):
        prefetch._collect_date.cache_clear()
        self.gate=Event()
        self.done=Event()
        self.count=0
        self.lock=Lock()
        self.slots=BoundedSemaphore(4)
        self.patchers=[patch.object(prefetch,'_FEED_SLOTS',self.slots),
                       patch.object(prefetch,'COLLECT_BUDGET_SECONDS',.05)]
        for patcher in self.patchers:patcher.start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.gate.set()
        deadline=time.monotonic()+1
        while self.count and time.monotonic()<deadline:time.sleep(.005)
        for patcher in reversed(self.patchers):patcher.stop()
        prefetch._collect_date.cache_clear()

    def slow(self,name,url,day):
        with self.lock:self.count+=1
        self.gate.wait(1)
        with self.lock:self.count-=1
        return name,[],{'source':name,'status':'ok','matched':0}

    def test_total_budget_does_not_wait_for_all_slow_sources(self):
        with patch.object(prefetch,'_fetch_one',side_effect=self.slow):
            started=time.monotonic()
            result=prefetch._collect_date(DAY)
            elapsed=time.monotonic()-started
        self.assertLess(elapsed,.5)
        self.assertEqual(len(result['feed_statuses']),4)
        self.assertTrue(all(x['status']=='deadline_exceeded' for x in result['feed_statuses']))

    def test_stalled_sources_do_not_create_an_unbounded_worker_pool(self):
        with patch.object(prefetch,'_fetch_one',side_effect=self.slow):
            prefetch._collect_date(DAY,0)
            result=prefetch._collect_date(DAY,1)
            self.assertLessEqual(self.count,4)
            self.assertTrue(all(x['status']=='busy' for x in result['feed_statuses']))

    def test_fast_sources_are_retained_when_others_exceed_deadline(self):
        article={'title':'OpenAI model fixture','published':'2026-11-10T10:00+00:00','publisher':'fixture','url':'https://example.test/news'}
        def fetch(name,url,day):
            if name=='google_news':return name,[article],{'source':name,'status':'ok','matched':1}
            return self.slow(name,url,day)
        with patch.object(prefetch,'_fetch_one',side_effect=fetch):
            result=prefetch._collect_date(DAY)
        self.assertIn(article,result['articles'])
        self.assertTrue(any(x['status']=='deadline_exceeded' for x in result['feed_statuses']))

    def test_fetch_exception_returns_a_source_status(self):
        with patch.object(prefetch,'_fetch_one',side_effect=RuntimeError('synthetic')):
            result=prefetch._collect_date(DAY)
        self.assertEqual(len(result['feed_statuses']),4)
        self.assertTrue(all(x['status']=='unavailable' for x in result['feed_statuses']))

    def test_request_cache_key_rotates_with_time(self):
        today=prefetch.dt.datetime.now(prefetch.LOCAL_TZ).date().isoformat()
        req={'messages':[{'role':'user','content':'Новости LLM за '+today}]}
        with patch.object(prefetch,'_collect_date',return_value={'articles':[]}) as collect:
            with patch.object(prefetch.time,'time',return_value=600):prefetch.maybe_prefetch_news(req)
            with patch.object(prefetch.time,'time',return_value=660):prefetch.maybe_prefetch_news(req)
        self.assertEqual(collect.call_args_list[0].args[1],10)
        self.assertEqual(collect.call_args_list[1].args[1],11)

    def test_unrelated_request_never_starts_prefetch(self):
        with patch.object(prefetch,'_collect_date') as collect:
            self.assertIsNone(prefetch.maybe_prefetch_news({'messages':[{'role':'user','content':'Привет'}]}))
        collect.assert_not_called()

    def test_following_tool_result_never_restarts_news_fetch(self):
        req={'messages':[{'role':'user','content':'Новости LLM за 2026-11-10'},
                         {'role':'tool','content':'synthetic'}]}
        with patch.object(prefetch,'_collect_date') as collect:
            self.assertIsNone(prefetch.maybe_prefetch_news(req))
        collect.assert_not_called()

if __name__=='__main__':unittest.main()
