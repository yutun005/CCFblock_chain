"""B API compatibility backed by the same audited crypto libraries and C typed data."""
from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_keys import keys
from eth_utils import keccak, to_checksum_address
from services.chain_adapter.signing import TYPES

FIELD_NAMES = {"jobId":"job_id", "taskId":"task_id", "agentWallet":"agent_wallet",
               "requirementHash":"requirement_hash", "paymentToken":"payment_token",
               "validUntil":"valid_until", "bidNonce":"bid_nonce", "resultHash":"result_hash",
               "artifactURIHash":"artifact_uri_hash", "submittedAt":"submitted_at"}


def keccak256(data):
    return bytes(keccak(data))


def normalize_address(value):
    return to_checksum_address(value).lower()


def address_from_private_key(private_key):
    return Account.from_key(private_key.to_bytes(32,"big")).address.lower()


def _digest(kind, payload, *, name, version, chain_id, verifying_contract):
    message = {n: payload[FIELD_NAMES.get(n,n)] for n,_ in TYPES[kind]}
    encoded = encode_typed_data(domain_data={"name":name,"version":version,"chainId":chain_id,
                                            "verifyingContract":verifying_contract},
                               message_types={kind:[{"name":n,"type":t} for n,t in TYPES[kind]]},
                               message_data=message)
    return keccak256(b"\x19"+encoded.version+encoded.header+encoded.body)


def quote_digest(payload, **domain):
    return _digest("Quote", payload, **domain)


def result_digest(payload, **domain):
    return _digest("Result", payload, **domain)


def sign_digest(digest, private_key):
    sig=keys.PrivateKey(private_key.to_bytes(32,"big")).sign_msg_hash(digest)
    return "0x"+(sig.r.to_bytes(32,"big")+sig.s.to_bytes(32,"big")+bytes([sig.v+27])).hex()


def recover_address(digest, signature):
    raw=bytes.fromhex(signature.removeprefix("0x"))
    if len(raw)!=65 or raw[64] not in (27,28):
        raise ValueError("invalid signature")
    sig=keys.Signature(raw[:64]+bytes([raw[64]-27]))
    if sig.s > 0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0:
        raise ValueError("noncanonical signature")
    return sig.recover_public_key_from_msg_hash(digest).to_checksum_address().lower()


def verify_digest(digest, signature, expected_address):
    try:
        return recover_address(digest, signature)==normalize_address(expected_address)
    except (ValueError, TypeError, Exception):
        return False
