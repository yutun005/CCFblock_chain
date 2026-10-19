"""Idle Anvil must not create an already expired Job or poison the task viewer."""
import asyncio
from pathlib import Path
from unittest.mock import PropertyMock,patch,MagicMock,AsyncMock

import pytest
from fastapi.testclient import TestClient
import app
from scripts.anvil_state import AnvilManager
from tests.e2e import run_main_scenario as fixture
from tests.integration.test_anvil_persistence import BINARY,port


@pytest.mark.parametrize('client_name,chain_id,latest,wall,expected',[
    ('anvil/v1.5.1',31337,100,10000,10900),
    ('anvil/v1.5.1',31337,20000,10000,20900),
    ('besu/v25',1337,100,10000,1000),
])
def test_funding_deadline_uses_appropriate_chain_clock(client_name,chain_id,latest,wall,expected):
    chain=app.Chain.__new__(app.Chain)
    chain.chain_id=chain_id;chain.w3=MagicMock()
    chain.w3.client_version=client_name
    with patch.object(app.Chain,'now',new_callable=PropertyMock,return_value=latest),patch.object(app.time,'time',return_value=wall):
        assert chain.funding_deadline(900)==expected


@pytest.fixture
def engine(tmp_path):
    if not BINARY.exists():pytest.skip('Run manage.py setup')
    manager=AnvilManager(tmp_path/'node',port=port(),executable=BINARY,interval=1)
    w3=manager.ensure_running()
    keyset=fixture.accounts()
    evidence={'run_id':'creation-clock-test','transactions':[]}
    token,escrow=fixture.deploy(w3,keyset[0],keyset[2],evidence)
    _,settings=fixture.build_settings(tmp_path/'services',w3.provider.endpoint_uri,token,escrow,keyset,delay=False)
    fixture.configure(settings,w3.provider.endpoint_uri,token,escrow,keyset[0])
    config=app.runtime_config(settings,'api')
    instance=app.Engine(config['deployment'],settings['directory'],config['market'],config['validator'],
                        config['token'],config['signers'],config['allowedAgentOrigins'])
    registered = [{"agentId": a["id"], "wallet": keyset[3].address,
                   "status": "ONLINE", "skills": list(a["prices"])} for a in settings["agents"]]
    try:
        # This fixture exercises funding, not discovery; supply the registry boundary explicitly.
        with patch.object(instance, "agents", new=AsyncMock(return_value=registered)):
            yield instance,config,settings,manager
    finally:manager.stop()


def test_idle_head_first_creation_uses_valid_next_block_deadline(engine):
    instance,config,_,manager=engine
    inputs=app.scenario_inputs(config,'cross_org_analysis')
    plan=app.data_plan([x['sha256'] for x in inputs])
    stale=manager.w3.eth.get_block('latest').timestamp-3600
    with patch.object(app.Chain,'now',new_callable=PropertyMock,return_value=stale):
        job=asyncio.run(instance.create(plan,inputs,'idle-first-request'))
    assert instance.snapshot(job)['chain']['deposit']==100000000
    assert instance.db.get('jobs',job)['deadline']>manager.w3.eth.get_block('latest').timestamp


def test_reverted_funding_is_auditable_and_returns_conflict_instead_of_500(engine):
    instance,config,settings,manager=engine
    owner=fixture.accounts()[0]
    instance.chain.tx(instance.chain.token.functions.approve(instance.chain.escrow.address,0),owner.key.hex(),'revoke-for-test')
    inputs=app.scenario_inputs(config,'cross_org_analysis')
    plan=app.data_plan([x['sha256'] for x in inputs])
    with pytest.raises(app.ChainReverted):
        asyncio.run(instance.create(plan,inputs,'failed-funding-request'))
    record=instance.db.values('jobs')[0]
    assert record['status']=='INTERRUPTED' and record['lastFault']['action']=='cancel'
    assert instance.chain.job(record['jobId'])['owner']=='0x'+'00'*20
    with patch.object(app,'Engine',return_value=instance):
        service=app.api_app(config,settings['directory'])
        with TestClient(service) as client:
            headers={'Authorization':'Bearer '+config['token']}
            for suffix in ('','/settlements'):
                response=client.get('/api/jobs/'+record['jobId']+suffix,headers=headers)
                assert response.status_code==409
                assert '托管' in response.json()['detail']
    with pytest.raises(app.JobNotFunded):
        asyncio.run(instance.create(plan,inputs,'failed-funding-request'))
