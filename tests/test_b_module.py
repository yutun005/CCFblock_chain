import time
import unittest
import json
from pathlib import Path
from services.agents.business import AdversarialAgent, build_demo_agents
from services.marketplace.market import AgentRegistry, QuoteMarket
from services.marketplace.models import Quote, Result, TaskRequest

DOMAIN={"name":"AgentFlow Pay","version":"1","chain_id":31337,"verifying_contract":"0x0000000000000000000000000000000000000001"}
TOKEN="0x0000000000000000000000000000000000000002"

class BModuleTests(unittest.TestCase):
  def test_keccak_signed_quote_and_result(self):
    a=build_demo_agents()[0]; t=TaskRequest("job",0,0,"data_cleaning",{"rows":[{"value":1}]},"req")
    q=a.quote(t,payment_token=TOKEN,budget=100,valid_until=int(time.time())+60)
    self.assertTrue(q.verify(**DOMAIN))
    r=a.handle(t); self.assertTrue(r.verify(**DOMAIN))

  def test_market_has_two_candidates_and_budget_filter(self):
    reg=AgentRegistry()
    for a in build_demo_agents(): reg.register(a)
    m=QuoteMarket(reg,domain=DOMAIN)
    t=TaskRequest("job",0,0,"data_cleaning",{"rows":[]},"req")
    qs=m.request_quotes(t,payment_token=TOKEN,budget=20)
    self.assertEqual([q.agent_id for q in qs], ["cleaner-a","cleaner-b"])
    self.assertEqual(m.choose(qs).agent_id, "cleaner-a")
    reg.unregister("cleaner-b")
    self.assertEqual([c.agent_id for c in reg.discover("data_cleaning")], ["cleaner-a"])

  def test_tamper_and_faults_fail_closed(self):
    a=build_demo_agents()[0]; t=TaskRequest("job",0,0,"data_cleaning",{"rows":[]},"req")
    q=a.quote(t,payment_token=TOKEN,budget=100,valid_until=int(time.time())+60); d=q.as_dict(); d["price"]+=1
    self.assertFalse(Quote(**d).verify(**DOMAIN))
    bad=AdversarialAgent("bad","bad",["data_cleaning"],fault="timeout")
    try: bad.handle(t)
    except TimeoutError: pass
    else: self.fail("timeout fault did not fail closed")

  def test_published_cross_language_fixture(self):
    data=json.loads((Path(__file__).parents[1]/"experiments/fixtures/signature_vectors.json").read_text())
    self.assertTrue(Quote(**data["quote"], agent_id="cleaner-a", skill="data_cleaning").verify(**data["domain"]))
    self.assertTrue(Result(**data["result"]).verify(**data["domain"]))
