"""Snapshots must preserve receipts/state and never silently erase an existing runtime."""
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import time
from functools import partial
from unittest.mock import patch

import pytest
import httpx

import app
from scripts import local_demo
from scripts.anvil_state import AnvilManager, AnvilOwnershipError, atomic_json
from scripts.local_demo import validate_saved_deployment

ROOT = Path(__file__).resolve().parents[2]
BINARY = ROOT / '.cache/tools/foundry' / ('anvil.exe' if os.name == 'nt' else 'anvil')
if not BINARY.exists() and shutil.which('anvil'):
    BINARY=Path(shutil.which('anvil'))


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def state(number=0):
    return {'accounts': {}, 'block': {}, 'best_block_number':number,
            'blocks':[{}] if number else [], 'transactions':[]}


def test_invalid_primary_uses_newest_valid_snapshot(tmp_path):
    manager = AnvilManager(tmp_path)
    manager.state.write_text('{broken')
    atomic_json(manager.previous, state(2))
    atomic_json(manager.autosave, state(3))
    assert manager._load_path() == manager.autosave


def test_all_invalid_snapshots_refuse_to_start_empty_chain(tmp_path):
    manager = AnvilManager(tmp_path)
    manager.state.write_text('{broken')
    with pytest.raises(RuntimeError, match='refusing to start an empty chain'):
        manager._load_path()
    assert manager.state.read_text() == '{broken'


def test_recycled_pid_is_not_stopped(tmp_path):
    manager = AnvilManager(tmp_path, executable=BINARY)
    atomic_json(manager.record, {'pid':1234,'port':8545,'creation_id':'old'})
    with patch('scripts.anvil_state.process_identity', return_value=(str(BINARY.resolve()),'new')):
        with pytest.raises(RuntimeError, match='PID has been reused'):
            manager._owned()


@pytest.mark.parametrize('rpc', [None, 'http://127.0.0.1:18545'])
def test_missing_contracts_preserve_old_deployment(tmp_path, rpc):
    deployment={'rpc':'http://127.0.0.1:8545','chainId':31337,
                'token':'old-token','escrow':'old-escrow','tokenCodeHash':'old','escrowCodeHash':'old'}
    atomic_json(tmp_path/'deployment.json',deployment)
    with patch('scripts.local_demo.local_web3') as connect:
        connect.return_value.eth.chain_id=31337
        connect.return_value.eth.get_code.return_value=b''
        with pytest.raises(RuntimeError, match='cannot be reconstructed'):
            validate_saved_deployment({'directory':tmp_path}, rpc=rpc)
        connect.assert_called_once_with(rpc or deployment['rpc'])
    assert json.loads((tmp_path/'deployment.json').read_text())==deployment


def local_launcher(monkeypatch, root):
    monkeypatch.setattr(local_demo, 'ROOT', root)
    monkeypatch.setattr(local_demo, 'CACHE', root / '.cache')
    for name, filename in [('ENV', 'local-environment.json'), ('CONFIG', 'local-project.json'),
                           ('CHAIN_PORT', 'local-chain-port.json')]:
        monkeypatch.setattr(local_demo, name, root / '.cache' / filename)
    monkeypatch.setattr(local_demo, 'AnvilManager', partial(AnvilManager, executable=BINARY))


@pytest.mark.parametrize('kind', ['same', 'foreign', 'unauthorized', 'different-chain', 'different-token', 'offline'])
def test_running_demo_requires_matching_deployment_and_authentication(tmp_path, monkeypatch, kind):
    local_launcher(monkeypatch, tmp_path)
    settings = {'directory':tmp_path / '.cache/runtime', 'api':'http://127.0.0.1:12345', 'apiTokenEnv':'TEST_TOKEN'}
    deployment = {'chainId':31337, 'escrow':'escrow', 'token':'token', 'owner':'owner'}
    atomic_json(local_demo.CONFIG, {'runtime':{}})
    atomic_json(local_demo.ENV, {'TEST_TOKEN':'test-token'})
    atomic_json(settings['directory'] / 'deployment.json', deployment)
    before = {p:p.read_bytes() for p in (local_demo.CONFIG, local_demo.ENV, settings['directory'] / 'deployment.json')}
    protocol = {'profile':'test-protocol'}
    def response(request):
        if kind == 'offline':
            raise httpx.ConnectError('offline', request=request)
        if request.url.path == '/health':
            assert 'Authorization' not in request.headers
            return httpx.Response(200, json={'ok':True, 'protocol':{} if kind == 'foreign' else protocol})
        assert request.headers['Authorization'] == 'Bearer test-token'
        if kind == 'unauthorized':
            return httpx.Response(401)
        public = {**deployment, 'chainId':'31337'}
        if kind == 'different-chain': public['chainId'] = '1'
        if kind == 'different-token': public['token'] = 'other-token'
        return httpx.Response(200, json=public)
    client = httpx.Client
    with patch.object(local_demo, 'settings_file', return_value=settings), \
         patch.object(local_demo, 'service_protocol', return_value=protocol), \
         patch.object(local_demo.httpx, 'Client', side_effect=lambda **kwargs:client(transport=httpx.MockTransport(response), **kwargs)), \
         patch.object(local_demo, 'check_services') as check:
        assert local_demo.running_demo() == (settings['api'] if kind == 'same' else None)
        assert check.call_count == (1 if kind == 'same' else 0)
    assert all(p.read_bytes() == data for p,data in before.items())


def test_repeated_launch_opens_existing_app_without_starting_or_stopping_services(tmp_path, monkeypatch):
    local_launcher(monkeypatch, tmp_path)
    monkeypatch.setenv('AGENTFLOW_OPEN_BROWSER', '1')
    with patch.object(local_demo, 'running_demo', return_value='http://127.0.0.1:12345'), \
         patch.object(local_demo, 'chain_up') as chain, \
         patch.object(local_demo, 'available_service_ports') as ports, \
         patch.object(local_demo, 'ServiceCluster') as cluster, \
         patch.object(local_demo.webbrowser, 'open') as browser, \
         patch.object(AnvilManager, 'checkpoint') as checkpoint:
        local_demo.run_demo()
        local_demo.run_demo()
        assert browser.call_count == 2
        chain.assert_not_called()
        ports.assert_not_called()
        cluster.assert_not_called()
        checkpoint.assert_not_called()


@pytest.mark.skipif(not BINARY.exists(), reason='Run manage.py setup to install Anvil')
def test_launcher_recovers_stale_pid_on_foreign_port_without_losing_history(tmp_path, monkeypatch):
    root = tmp_path / 'project'
    root.mkdir()
    local_launcher(monkeypatch, root)
    original = AnvilManager(root, port=port(), executable=BINARY)
    foreign = AnvilManager(tmp_path / 'other-project', port=original.port, executable=BINARY)
    restored = None
    try:
        w3 = original.ensure_running()
        owner, recipient = w3.eth.accounts[:2]
        receipt = w3.eth.wait_for_transaction_receipt(w3.eth.send_transaction(
            {'from': owner, 'to': recipient, 'value': 34567}))
        w3.provider.make_request('anvil_setCode', [recipient, '0x60006000f3'])
        code_hash = w3.keccak(w3.eth.get_code(recipient)).to_0x_hex()
        deployment = {'rpc': w3.provider.endpoint_uri, 'chainId':31337,
                      'token':recipient, 'escrow':recipient,
                      'tokenCodeHash':code_hash, 'escrowCodeHash':code_hash}
        runtime = root / '.cache/runtime'
        atomic_json(runtime / 'deployment.json', deployment)
        atomic_json(local_demo.CONFIG, {'runtime': {'directory': str(runtime)}})
        atomic_json(local_demo.CHAIN_PORT, {'port':original.port})
        original.stop()
        foreign.ensure_running()
        foreign_pid = foreign.process.pid
        foreign_head = foreign.w3.eth.get_block('latest').hash

        rpc = local_demo.chain_up()
        restored = local_demo.chain_manager()
        assert restored.port != original.port
        assert restored.directory == original.directory
        assert restored.w3.eth.get_transaction_receipt(receipt.transactionHash).blockHash == receipt.blockHash
        assert restored.w3.eth.get_code(recipient) == bytes.fromhex('60006000f3')
        validate_saved_deployment({'directory':runtime}, rpc=rpc)
        assert json.loads((runtime / 'deployment.json').read_text()) == {**deployment, 'rpc':rpc}
        saved = json.loads(local_demo.CHAIN_PORT.read_text())
        assert not Path(saved['stateDirectory']).is_absolute()
        recorded_pid = json.loads(restored.record.read_text())['pid']
        assert local_demo.chain_up() == rpc
        assert json.loads(restored.record.read_text())['pid'] == recorded_pid

        restored.stop()
        assert local_demo.chain_up() == rpc
        restored = local_demo.chain_manager()
        assert restored.w3.eth.get_transaction_receipt(receipt.transactionHash).blockHash == receipt.blockHash
        assert foreign.process.pid == foreign_pid and foreign.process.poll() is None
        assert foreign.w3.eth.get_block('latest').hash == foreign_head
    finally:
        if restored is not None and restored.w3.is_connected():
            restored.stop()
        if foreign.process is not None and foreign.process.poll() is None:
            foreign.stop()
        if original.process is not None and original.process.poll() is None:
            original.stop()


@pytest.mark.parametrize('snapshot', [None, '{broken'])
def test_launcher_never_replaces_missing_or_corrupt_history_with_empty_chain(tmp_path, monkeypatch, snapshot):
    local_launcher(monkeypatch, tmp_path)
    atomic_json(local_demo.CHAIN_PORT, {'port':8545})
    atomic_json(local_demo.CONFIG, {'existing':True})
    if snapshot is not None:
        directory = tmp_path / '.cache/anvil'
        directory.mkdir()
        (directory / 'state.json').write_text(snapshot)
    with patch.object(AnvilManager, 'ensure_running', side_effect=AnvilOwnershipError('foreign port')) as start:
        with pytest.raises(RuntimeError, match='no saved checkpoint|All saved Anvil snapshots are invalid'):
            local_demo.chain_up()
        assert start.call_count == 1
    assert json.loads(local_demo.CHAIN_PORT.read_text()) == {'port':8545}
    assert json.loads(local_demo.CONFIG.read_text()) == {'existing':True}


@pytest.mark.parametrize('assigned', [False, True])
def test_new_merchant_port_collision_preserves_existing_assignments(tmp_path, monkeypatch, assigned):
    local_launcher(monkeypatch, tmp_path)
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1', 0))
        occupied.listen()
        endpoint = f'http://127.0.0.1:{occupied.getsockname()[1]}'
        runtime = {'directory': str(tmp_path / '.cache/runtime'),
                   **{key:f'http://127.0.0.1:{port()}' for key in ('api','market','validator')},
                   'agents':[{'id':'new-merchant','endpoint':endpoint}],
                   'allowedAgentOrigins':[endpoint, 'http://127.0.0.1:1']}
        document = {'runtime':runtime}
        atomic_json(local_demo.CONFIG, document)
        jobs = [{'status':'SUCCEEDED', 'selected': {'agentId':'new-merchant' if assigned else 'old-merchant'}}]
        with patch.object(local_demo, 'Database') as database:
            database.return_value.values.return_value = jobs
            if assigned:
                with pytest.raises(RuntimeError, match='Existing project history has been preserved'):
                    local_demo.available_service_ports()
                assert json.loads(local_demo.CONFIG.read_text()) == document
            else:
                local_demo.available_service_ports()
                saved = json.loads(local_demo.CONFIG.read_text())['runtime']
                assert saved['agents'][0]['endpoint'] != endpoint
                assert saved['allowedAgentOrigins'] == [saved['agents'][0]['endpoint'], 'http://127.0.0.1:1']
                assert all(saved[key] == runtime[key] for key in ('api','market','validator'))


@pytest.mark.skipif(not BINARY.exists(), reason='Run manage.py setup to install Anvil')
def test_clean_restart_restores_contracts_balances_receipts_and_history(tmp_path):
    manager=AnvilManager(tmp_path,port=port(),executable=BINARY,interval=1)
    try:
        w3=manager.ensure_running()
        owner,recipient=w3.eth.accounts[:2]
        artifact=app.artifact('MockUSDC')
        factory=w3.eth.contract(abi=artifact['abi'],bytecode=artifact['bytecode']['object'])
        receipt=w3.eth.wait_for_transaction_receipt(factory.constructor(owner).transact({'from':owner}))
        token=w3.eth.contract(address=receipt.contractAddress,abi=artifact['abi'])
        mint=w3.eth.wait_for_transaction_receipt(token.functions.mint(recipient,123456).transact({'from':owner}))
        code=w3.eth.get_code(token.address)
        before={'head':w3.eth.block_number,'hash':w3.eth.get_block('latest').hash,
                'nonce':w3.eth.get_transaction_count(owner),'balance':w3.eth.get_balance(owner),
                'historical_balance':w3.eth.get_balance(owner,receipt.blockNumber),
                'logs':w3.eth.get_logs({'address':token.address,'fromBlock':0,'toBlock':'latest'})}
        manager.stop()
        w3=manager.ensure_running()
        token=w3.eth.contract(address=receipt.contractAddress,abi=artifact['abi'])
        assert w3.eth.block_number==before['head']
        assert w3.eth.get_block('latest').hash==before['hash']
        assert w3.eth.get_code(token.address)==code
        assert token.functions.balanceOf(recipient).call()==123456
        assert w3.eth.get_balance(owner)==before['balance']
        assert w3.eth.get_transaction_count(owner)==before['nonce']
        assert w3.eth.get_balance(owner,receipt.blockNumber)==before['historical_balance']
        assert w3.eth.get_transaction_receipt(mint.transactionHash).blockHash==mint.blockHash
        assert w3.eth.get_transaction(mint.transactionHash).blockNumber==mint.blockNumber
        assert w3.eth.get_logs({'address':token.address,'fromBlock':0,'toBlock':'latest'})==before['logs']
    finally:
        manager.stop()


@pytest.mark.skipif(not BINARY.exists(), reason='Run manage.py setup to install Anvil')
def test_abrupt_exit_recovers_native_periodic_checkpoint(tmp_path):
    manager=AnvilManager(tmp_path,port=port(),executable=BINARY,interval=1)
    try:
        w3=manager.ensure_running()
        tx=w3.eth.send_transaction({'from':w3.eth.accounts[0],'to':w3.eth.accounts[1],'value':12345})
        receipt=w3.eth.wait_for_transaction_receipt(tx)
        deadline=time.monotonic()+15
        while True:
            try:
                value=json.loads(manager.autosave.read_text())
                if value['best_block_number']>=receipt.blockNumber:break
            except (FileNotFoundError,ValueError):pass
            assert time.monotonic()<deadline,'periodic autosave did not occur'
            time.sleep(.1)
        manager.process.kill();manager.process.wait(timeout=10)
        w3=manager.ensure_running()
        assert w3.eth.get_transaction_receipt(tx).blockHash==receipt.blockHash
        assert w3.eth.block_number==receipt.blockNumber
    finally:
        manager.stop()


@pytest.mark.skipif(not BINARY.exists(), reason='Run manage.py setup to install Anvil')
def test_unmanaged_chain_is_rejected_and_owned_legacy_chain_is_migrated(tmp_path):
    manager=AnvilManager(tmp_path,port=port(),executable=BINARY,interval=1)
    legacy=subprocess.Popen([str(BINARY),'--host','127.0.0.1','--port',str(manager.port),'--silent'],
                            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        deadline=time.monotonic()+15
        while not manager.w3.is_connected():
            assert time.monotonic()<deadline
            time.sleep(.1)
        tx=manager.w3.eth.send_transaction({'from':manager.w3.eth.accounts[0],
                                           'to':manager.w3.eth.accounts[1],'value':55})
        receipt=manager.w3.eth.wait_for_transaction_receipt(tx)
        with pytest.raises(RuntimeError,match='not owned by this checkout'):
            manager.ensure_running()
        assert legacy.poll() is None
        atomic_json(manager.record,{'pid':legacy.pid,'port':manager.port})
        manager.process=legacy
        w3=manager.ensure_running()
        assert w3.eth.get_transaction_receipt(tx).blockHash==receipt.blockHash
        assert json.loads(manager.record.read_text())['persistence_version']==1
    finally:
        if legacy.poll() is None:
            legacy.terminate();legacy.wait(timeout=10)
        if manager.record.exists() and manager.w3.is_connected():manager.stop()
