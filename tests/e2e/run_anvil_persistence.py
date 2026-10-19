"""Real service/chain restart: retain settled Jobs and resume an unfinished Job."""
import hashlib
import json
import sys
import time
import uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
import httpx
import app
from scripts.anvil_state import AnvilManager
from tests.e2e import run_main_scenario as fixture


def run():
    run_id=str(uuid.uuid4())
    directory=ROOT/'.cache/persistence-e2e'/run_id
    output=ROOT/'experiments/raw/persistence'/run_id
    output.mkdir(parents=True,exist_ok=True)
    manager=AnvilManager(directory,port=fixture.free_port(),executable=fixture.foundry('anvil'),interval=1)
    evidence={'case_id':'ANVIL-PERSISTENCE-E2E','run_id':run_id,'status':'RUNNING','transactions':[]}
    try:
        w3=manager.ensure_running()
        keyset=fixture.accounts()
        token,escrow=fixture.deploy(w3,keyset[0],keyset[2],evidence)
        _,settings=fixture.build_settings(directory/'application',manager.w3.provider.endpoint_uri,
                                         token,escrow,keyset,delay=False)
        fixture.configure(settings,manager.w3.provider.endpoint_uri,token,escrow,keyset[0])
        with app.ServiceCluster(settings),httpx.Client(trust_env=False,timeout=15) as client:
            first=fixture.create_job(client,settings['api'],run_id+'-settled',120)
            job=fixture.wait_job(client,settings['api'],first)
            first_proof=fixture.validate_settlement(client,settings['api'],job,recovery=False)
            second=fixture.create_job(client,settings['api'],run_id+'-unfinished',120)
            progress=client.get(f"{settings['api']}/api/jobs/{second}",headers=fixture.request_headers()).json()
            assert progress['status'] not in ('SUCCEEDED','FAILED','CANCELLED'),'Job finished before restart test'
        head=w3.eth.get_block('latest')
        nonce=w3.eth.get_transaction_count(keyset[0].address)
        token_contract=w3.eth.contract(address=token,abi=app.ERC20_ABI)
        balances={a.address:token_contract.functions.balanceOf(a.address).call() for a in keyset}
        code=w3.eth.get_code(escrow)
        manager.stop()
        w3=manager.ensure_running()
        assert w3.eth.get_block(head.number).hash==head.hash
        assert w3.eth.block_number==head.number
        assert w3.eth.get_transaction_count(keyset[0].address)==nonce
        assert w3.eth.get_code(escrow)==code
        assert all(token_contract.functions.balanceOf(a).call()==v for a,v in balances.items())
        for event in first_proof['settledEvents']:
            assert w3.eth.get_transaction_receipt(event['transactionHash']).blockHash.to_0x_hex()==event['blockHash']
        with app.ServiceCluster(settings),httpx.Client(trust_env=False,timeout=15) as client:
            restored=client.get(f"{settings['api']}/api/jobs/{first}",headers=fixture.request_headers())
            restored.raise_for_status()
            assert restored.json()['status']=='SUCCEEDED'
            repeated=fixture.validate_settlement(client,settings['api'],restored.json(),recovery=False)
            for name in ('withdrawn','refunded'):
                assert repeated['claims']['chain'][name]==first_proof['claims']['chain'][name]
            recovered=fixture.wait_job(client,settings['api'],second)
            second_proof=fixture.validate_settlement(client,settings['api'],recovered,recovery=False)
            assert len(second_proof['settledEvents'])==4
        evidence.update(status='PASS',completed_job=first,resumed_job=second,
                        restarted_at_block=head.number,block_hash=head.hash.to_0x_hex(),
                        completed_claims={k:first_proof['claims']['chain'][k] for k in ('withdrawn','refunded')},
                        repeated_claims_unchanged=True,artifacts_verified=True,
                        resumed_task_settlements=len(second_proof['settledEvents']))
        manager.checkpoint()
        print(json.dumps(evidence,ensure_ascii=False),flush=True)
        return evidence
    except Exception as exc:
        evidence.update(status='FAIL',failure=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        manager.stop()
        fixture.write_json(output/'result.json',evidence)


if __name__=='__main__':run()
