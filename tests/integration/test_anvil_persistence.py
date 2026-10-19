"""Snapshots must preserve receipts/state and never silently erase an existing runtime."""
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import time
from unittest.mock import patch

import pytest

import app
from scripts.anvil_state import AnvilManager, atomic_json
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


def test_missing_contracts_preserve_old_deployment(tmp_path):
    deployment={'rpc':'http://127.0.0.1:8545','chainId':31337,
                'token':'old-token','escrow':'old-escrow','tokenCodeHash':'old','escrowCodeHash':'old'}
    atomic_json(tmp_path/'deployment.json',deployment)
    with patch('scripts.local_demo.local_web3') as connect:
        connect.return_value.eth.chain_id=31337
        connect.return_value.eth.get_code.return_value=b''
        with pytest.raises(RuntimeError, match='cannot be reconstructed'):
            validate_saved_deployment({'directory':tmp_path})
    assert json.loads((tmp_path/'deployment.json').read_text())==deployment


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
