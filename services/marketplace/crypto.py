"""Small, dependency-free EIP-712/secp256k1 implementation.

The module is intentionally self contained so the demo can be reproduced on a
clean Python installation.  Signatures are standard Ethereum ``r || s || v``
signatures and addresses are derived in the same way as Solidity's ``ECDSA``.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from typing import Iterable

# Keccak-f[1600], with the rotation and round constants from FIPS 202.
_RC = [
    1, 0x8082, 0x800000000000808A, 0x8000000080008000,
    0x808B, 0x80000001, 0x8000000080008081, 0x8000000000008009,
    0x8A, 0x88, 0x80008009, 0x8000000A, 0x8000808B,
    0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x800A,
    0x800000008000000A, 0x8000000080008081, 0x8000000000008080,
    0x80000001, 0x8000000080008008,
]
_ROT = [[0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61],
        [28, 55, 25, 21, 56], [27, 20, 39, 8, 14]]
_MASK = (1 << 64) - 1


def _rol(x: int, n: int) -> int:
    return ((x << n) | (x >> (64 - n))) & _MASK if n else x


def _keccak_f(a: list[int]) -> None:
    for rc in _RC:
        c = [a[x] ^ a[x + 5] ^ a[x + 10] ^ a[x + 15] ^ a[x + 20] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                a[x + 5 * y] ^= d[x]
        b = [0] * 25
        for x in range(5):
            for y in range(5):
                b[y + 5 * ((2 * x + 3 * y) % 5)] = _rol(a[x + 5 * y], _ROT[x][y])
        for x in range(5):
            for y in range(5):
                a[x + 5 * y] = b[x + 5 * y] ^ ((~b[(x + 1) % 5 + 5 * y]) & b[(x + 2) % 5 + 5 * y])
        a[0] ^= rc


def keccak256(data: bytes) -> bytes:
    """Ethereum Keccak-256 (the legacy padding differs from SHA3-256)."""
    rate = 136
    padded = bytearray(data)
    padded.append(0x01)
    while len(padded) % rate != rate - 1:
        padded.append(0)
    padded.append(0x80)
    state = [0] * 25
    for offset in range(0, len(padded), rate):
        block = padded[offset: offset + rate]
        for i in range(rate // 8):
            state[i] ^= int.from_bytes(block[i * 8:(i + 1) * 8], "little")
        _keccak_f(state)
    out = bytearray()
    while len(out) < 32:
        out.extend(b"".join(x.to_bytes(8, "little") for x in state[: rate // 8]))
        if len(out) < 32:
            _keccak_f(state)
    return bytes(out[:32])


# secp256k1 domain parameters
_P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_G = (55066263022277343669578718895168534326250603453777594175500187360389116729240,
      32670510020758816978083085130507043184471273380659243275938904335757337482424)
Point = tuple[int, int] | None


def _inv(x: int, m: int) -> int:
    return pow(x % m, -1, m)


def _add(p: Point, q: Point) -> Point:
    if p is None: return q
    if q is None: return p
    if p[0] == q[0] and (p[1] + q[1]) % _P == 0: return None
    if p == q:
        lam = (3 * p[0] * p[0]) * _inv(2 * p[1], _P) % _P
    else:
        lam = (q[1] - p[1]) * _inv(q[0] - p[0], _P) % _P
    x = (lam * lam - p[0] - q[0]) % _P
    return x, (lam * (p[0] - x) - p[1]) % _P


def _mul(k: int, p: Point = _G) -> Point:
    out: Point = None
    while k:
        if k & 1: out = _add(out, p)
        p = _add(p, p)
        k >>= 1
    return out


def _bytes32(x: int) -> bytes:
    return x.to_bytes(32, "big")


def normalize_address(value: str) -> str:
    value = value.lower()
    if not re.fullmatch(r"0x[0-9a-f]{40}", value):
        raise ValueError(f"invalid Ethereum address: {value}")
    return value


def address_from_private_key(private_key: int) -> str:
    pub = _mul(private_key)
    assert pub is not None
    return "0x" + keccak256(_bytes32(pub[0]) + _bytes32(pub[1]))[-20:].hex()


def private_key_from_seed(seed: str) -> int:
    key = int.from_bytes(hashlib.sha256(seed.encode()).digest(), "big") % (_N - 1) + 1
    return key


def _encode_word(value: int | str | bytes) -> bytes:
    if isinstance(value, int): return _bytes32(value)
    if isinstance(value, str) and value.startswith("0x"):
        raw = bytes.fromhex(value[2:])
    elif isinstance(value, str):
        raw = bytes.fromhex(value)
    else: raw = value
    if len(raw) > 32: raise ValueError("word is longer than 32 bytes")
    return raw.rjust(32, b"\0")


DOMAIN_TYPEHASH = keccak256(b"EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)")
QUOTE_TYPEHASH = keccak256(b"Quote(bytes32 jobId,uint256 taskId,uint256 attempt,address agentWallet,uint256 price,bytes32 requirementHash,address paymentToken,uint256 validUntil,uint256 bidNonce)")
RESULT_TYPEHASH = keccak256(b"Result(bytes32 jobId,uint256 taskId,uint256 attempt,address agentWallet,bytes32 resultHash,bytes32 artifactURIHash,uint256 submittedAt)")


def domain_separator(name: str, version: str, chain_id: int, verifying_contract: str) -> bytes:
    return keccak256(DOMAIN_TYPEHASH + keccak256(name.encode()) + keccak256(version.encode()) + _encode_word(chain_id) + _encode_word(normalize_address(verifying_contract)))


def _struct_hash(typehash: bytes, values: Iterable[bytes]) -> bytes:
    return keccak256(typehash + b"".join(values))


def quote_digest(q: dict, *, name: str, version: str, chain_id: int, verifying_contract: str) -> bytes:
    body = _struct_hash(QUOTE_TYPEHASH, [_encode_word(q["job_id"]), _encode_word(q["task_id"]), _encode_word(q["attempt"]), _encode_word(q["agent_wallet"]), _encode_word(q["price"]), _encode_word(q["requirement_hash"]), _encode_word(q["payment_token"]), _encode_word(q["valid_until"]), _encode_word(q["bid_nonce"])])
    return keccak256(b"\x19\x01" + domain_separator(name, version, chain_id, verifying_contract) + body)


def result_digest(r: dict, *, name: str, version: str, chain_id: int, verifying_contract: str) -> bytes:
    body = _struct_hash(RESULT_TYPEHASH, [_encode_word(r["job_id"]), _encode_word(r["task_id"]), _encode_word(r["attempt"]), _encode_word(r["agent_wallet"]), _encode_word(r["result_hash"]), _encode_word(r["artifact_uri_hash"]), _encode_word(r["submitted_at"])])
    return keccak256(b"\x19\x01" + domain_separator(name, version, chain_id, verifying_contract) + body)


def sign_digest(digest: bytes, private_key: int) -> str:
    # RFC6979 deterministic nonce, which makes fixtures stable.
    x = _bytes32(private_key); z = int.from_bytes(digest, "big")
    v = b"\1" * 32; k = b"\0" * 32
    k = hmac.new(k, v + b"\0" + x + digest, hashlib.sha256).digest(); v = hmac.new(k, v, hashlib.sha256).digest()
    k = hmac.new(k, v + b"\1" + x + digest, hashlib.sha256).digest(); v = hmac.new(k, v, hashlib.sha256).digest()
    while True:
        v = hmac.new(k, v, hashlib.sha256).digest(); nonce = int.from_bytes(v, "big")
        if 1 <= nonce < _N:
            r_point = _mul(nonce)
            assert r_point is not None
            r = r_point[0] % _N
            if r:
                s = (_inv(nonce, _N) * (int.from_bytes(digest, "big") + r * private_key)) % _N
                if s:
                    recid = (r_point[1] & 1) | (2 if r_point[0] >= _N else 0)
                    if s > _N // 2: s = _N - s; recid ^= 1
                    return "0x" + _bytes32(r).hex() + _bytes32(s).hex() + format(27 + recid, "02x")
        k = hmac.new(k, v + b"\0", hashlib.sha256).digest(); v = hmac.new(k, v, hashlib.sha256).digest()


def recover_address(digest: bytes, signature: str) -> str:
    raw = bytes.fromhex(signature[2:] if signature.startswith("0x") else signature)
    if len(raw) != 65: raise ValueError("signature must be 65 bytes")
    r, s, v = int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:64], "big"), raw[64]
    recid = v - 27 if v >= 27 else v
    if recid not in (0, 1, 2, 3) or not (0 < r < _N and 0 < s <= _N // 2): raise ValueError("invalid signature")
    x = r + (recid // 2) * _N
    if x >= _P: raise ValueError("invalid recovery point")
    alpha = (pow(x, 3, _P) + 7) % _P
    y = pow(alpha, (_P + 1) // 4, _P)
    if (y & 1) != (recid & 1): y = _P - y
    r_point = (x, y)
    z = int.from_bytes(digest, "big")
    # Q = r^-1 (sR - zG), where R is the recovered ephemeral point.
    pub = _mul(_inv(r, _N), _add(_mul(s, r_point), _mul((-z) % _N, _G)))
    if pub is None: raise ValueError("invalid recovered public key")
    return "0x" + keccak256(_bytes32(pub[0]) + _bytes32(pub[1]))[-20:].hex()


def verify_digest(digest: bytes, signature: str, expected_address: str) -> bool:
    try: return recover_address(digest, signature) == normalize_address(expected_address)
    except (ValueError, IndexError): return False
