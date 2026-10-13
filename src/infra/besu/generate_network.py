"""Generate QBFT genesis and private node keys locally. Generated secrets are gitignored."""

import json

import rlp
from eth_account import Account
from eth_keys import keys as eth_keys
from web3 import Web3

from experiments.scripts.common import ROOT, keys


def generate():
    directory = ROOT / "infra" / "besu" / "generated"
    directory.mkdir(parents=True, exist_ok=True)
    secret = keys(create=True)
    node_keys = []
    for i in range(1, 6):
        path = directory / f"node{i}.key"
        if not path.exists():
            path.write_text(Account.create().key.hex(), encoding="ascii")
        node_keys.append(eth_keys.PrivateKey(bytes.fromhex(path.read_text().strip())))
    enodes = [
        f"enode://{key.public_key.to_bytes().hex()}@172.29.37.{i + 10}:30303"
        for i, key in enumerate(node_keys, start=1)
    ]
    validators = [key.public_key.to_canonical_address() for key in node_keys[:4]]
    genesis = {
        "config": {
            "chainId": 1337,
            "homesteadBlock": 0,
            "eip150Block": 0,
            "eip155Block": 0,
            "eip158Block": 0,
            "byzantiumBlock": 0,
            "constantinopleBlock": 0,
            "petersburgBlock": 0,
            "istanbulBlock": 0,
            "berlinBlock": 0,
            "londonBlock": 0,
            "zeroBaseFee": True,
            "qbft": {"blockperiodseconds": 2, "epochlength": 30000, "requesttimeoutseconds": 4},
        },
        "nonce": "0x0",
        "timestamp": "0x0",
        "extraData": Web3.to_hex(rlp.encode([bytes(32), validators, [], 0, []])),
        "gasLimit": "0x1c9c380",
        "difficulty": "0x1",
        "mixHash": "0x" + "00" * 32,
        "coinbase": "0x" + "00" * 20,
        "baseFeePerGas": "0x0",
        "alloc": {
            Account.from_key(key).address[2:]: {"balance": hex(1000 * 10**18)} for key in secret.values()
        },
    }
    (directory / "genesis.json").write_text(json.dumps(genesis, indent=2), encoding="utf-8")
    (directory / "static-nodes.json").write_text(json.dumps(enodes, indent=2), encoding="utf-8")
    (directory / "permissions.toml").write_text(
        "nodes-allowlist=" + json.dumps(enodes) + "\n", encoding="utf-8"
    )
    for i in range(1, 6):
        config = f"""data-path="/data"
genesis-file="/config/genesis.json"
node-private-key-file="/config/node{i}.key"
p2p-host="172.29.37.{i + 10}"
p2p-port=30303
discovery-enabled=false
min-gas-price=0
host-allowlist=["localhost","127.0.0.1","rpcnode"]
rpc-http-enabled={str(i == 5).lower()}
rpc-http-host="0.0.0.0"
rpc-http-port=8545
rpc-http-api=["ETH","NET","WEB3","QBFT"]
permissions-nodes-config-file-enabled=true
permissions-nodes-config-file="/config/permissions.toml"
"""
        (directory / f"node{i}.toml").write_text(config, encoding="utf-8")
    print(f"Generated 4 QBFT validators + RPC node in {directory}; chainId=1337")


if __name__ == "__main__":
    generate()
