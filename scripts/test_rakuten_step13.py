"""Offline tests, synthetic responses; run beside rakuten_integration.py and product_matching.py."""
import io
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.parse
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
import rakuten_integration as r


def item(**kw):
    return dict({'itemCode':'shop:100','shopCode':'shop','shopName':'Shop','itemPrice':9000,
                 'itemName':'Crucial 1TB CT1000P3PSSD8','itemUrl':'https://item.rakuten.co.jp/shop/100/',
                 'affiliateUrl':'https://hb.afl.rakuten.co.jp/hgc/abc/', 'availability':1},**kw)


def yahoo(**kw):
    return dict({'id':'yahoo:shop_100','title':'Crucial 1TB CT1000P3PSSD8','price_jpy':10000,
                 'merchant':'Yahoo!ショッピング','shop_name':'Y','jan_code':'',
                 'url':'https://store.shopping.yahoo.co.jp/shop/100.html','observed_at':r.utcnow().isoformat()},**kw)


class Tests(unittest.TestCase):
    def test_new_endpoint_header_and_throttle(self):
        class Fake:
            def __init__(self): self.reqs=[]
            def open(self,req,timeout):
                self.reqs.append(req); return io.BytesIO(json.dumps({'Items':[item()]}).encode())
        fake=Fake(); t=[0.0]
        c=r.Client('app','key','aff',opener=fake,clock=lambda:t[0],sleep=lambda s:t.__setitem__(0,t[0]+s))
        c.search('SSD');c.search('Anker')
        self.assertGreaterEqual(t[0],1.25)
        self.assertTrue(fake.reqs[0].full_url.startswith(r.ENDPOINT+'?'))
        self.assertEqual(fake.reqs[0].get_header('Accesskey'),'key')
        self.assertNotIn('accessKey=',fake.reqs[0].full_url)
        self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlsplit(fake.reqs[0].full_url).query)['availability'],['1'])
    def test_wrappers(self):
        for o in [{'Items':[item()]},{'Items':[{'Item':item()}]},{'items':[{'item':item()}]}]:
            self.assertEqual(r.parse_items(o,'SSD')[0]['id'],'rakuten:shop:100')
    def test_out_of_stock_and_invalid_price(self):
        self.assertEqual(r.parse_items({'Items':[item(availability=0),item(itemPrice=True),item(itemPrice=0)]},'SSD'),[])
    def test_invalid_url_and_affiliate_required(self):
        self.assertEqual(r.parse_items({'Items':[item(affiliateUrl=''),item(itemUrl='https://rakuten.co.jp.bad.test/a')]},'SSD'),[])
    def test_ambiguous_sku(self):
        self.assertEqual(r.parse_items({'Items':[item(itemPriceMin=9000,itemPriceMax=12000)]},'SSD'),[])
    def test_model_match(self):
        pairs=r.compare([yahoo()],r.parse_items({'Items':[item()]},'SSD'))
        self.assertEqual(len(pairs),1);self.assertEqual(pairs[0]['difference_jpy'],1000)
        self.assertFalse(pairs[0]['eligible_for_auto_deal'])
    def test_model_capacity_bundle_conflicts(self):
        for title in ['Crucial 2TB CT1000P3PSSD8','Crucial 1TB CT2000P3PSSD8',
                      'Crucial 1TB CT1000P3PSSD8 中古','Crucial 1TB CT1000P3PSSD8 2個セット',
                      'Crucial 1TB CT1000P3PSSD8 バルク','Crucial 1TB CT1000P3PSSD8 選べる']:
            self.assertEqual(r.compare([yahoo()],r.parse_items({'Items':[item(itemName=title)]},'SSD')),[],title)
    def test_no_generic_match(self):
        self.assertEqual(r.compare([yahoo(title='加湿器 10L')],r.parse_items({'Items':[item(itemName='加湿器 10L')]},'SSD')),[])
    def test_explicit_valid_jan(self):
        code='4901234567894'; self.assertEqual(r.valid_jan(code),code)
        self.assertEqual(r.title_jan('商品 '+code),'')
        a=yahoo(title='商品',jan_code=code)
        b=r.parse_items({'Items':[item(itemName='商品 JANコード:'+code)]},'SSD')
        self.assertEqual(len(r.compare([a],b)),1)
        self.assertFalse(r.valid_jan('4901234567895'))
    def test_stop_on_429_no_leak(self):
        class Fake:
            def open(self,req,timeout): raise urllib.error.HTTPError(req.full_url,429,'secret payload',{},None)
        c=r.Client('secret-app','secret-key','secret-aff',opener=Fake())
        rows,ok,errors=r.collect(c,['one','two'])
        self.assertEqual((rows,ok,errors,c.requests),([],0,['http_429'],1))
    def test_dedup_and_partial(self):
        class Fake:
            def search(self,q):
                if q=='bad':raise r.ApiFailure('http_500')
                return r.parse_items({'Items':[item()]},q)
        rows,ok,errors=r.collect(Fake(),['one','bad','two'])
        self.assertEqual((len(rows),ok,errors),(1,2,['http_500']))
    def test_freshness(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'price_history').mkdir(); t=r.utcnow(); ts=t.isoformat()
            for file,obj in [('yahoo_candidates.json',{'generated_at':ts,'items':[yahoo()]}),
                             ('price_history/analysis_latest.json',{'generated_at':ts,'items':[{'id':'yahoo:shop_100'}]}),
                             ('deal_candidates.json',{'generated_at':ts,'snapshot_fresh':True})]:
                (p/file).write_text(json.dumps(obj))
            self.assertEqual(len(r.load_yahoo(p,t)[0]),1)
            self.assertEqual(r.load_yahoo(p,t+timedelta(hours=9)),([],None))
            (p/'deal_candidates.json').write_text(json.dumps({'generated_at':ts,'snapshot_fresh':False}))
            self.assertEqual(r.load_yahoo(p,t),([],None))
    def test_query_budget(self):
        self.assertLessEqual(len(r.search_terms([yahoo(title=f'Anker A{i:04d}') for i in range(10)])),18)
    def test_report_safe(self):
        rows=r.parse_items({'Items':[item(itemName='<script>evil</script>')]},'SSD')
        out=r.report(rows,[],'OK')
        self.assertNotIn('<script>evil</script>',out);self.assertIn('Supported by Rakuten Developers',out)
        self.assertIn('https://hb.afl.rakuten.co.jp/',out)
    def test_missing_credentials(self):
        with patch.dict(os.environ,{},clear=True),patch('sys.argv',['rakuten_integration.py']),patch.object(r,'summary') as s:
            r.main(); s.assert_called_once_with('SKIPPED_MISSING_CREDENTIALS',requests=0)

if __name__=='__main__':unittest.main(verbosity=2)
