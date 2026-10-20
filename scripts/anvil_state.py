"""Owned local Anvil lifecycle with durable checkpoints and native periodic autosave."""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
import gzip
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import time
import uuid

import requests
from web3 import Web3


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def process_identity(pid: int):
    """Return executable and process birth identity; do not trust a recycled PID."""
    if os.name == 'nt':
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            status = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(status)) or status.value != 259:
                return None
            buffer = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(buffer))
            times = [wintypes.FILETIME() for _ in range(4)]
            if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
                raise RuntimeError('Cannot verify Anvil executable')
            if not kernel.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
                raise RuntimeError('Cannot verify Anvil process creation time')
            birth = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
            return str(Path(buffer.value).resolve()), str(birth)
        finally:
            kernel.CloseHandle(handle)
    directory = Path(f'/proc/{pid}')
    try:
        executable = (directory / 'exe').resolve(strict=True)
        fields = (directory / 'stat').read_text().rsplit(')', 1)[1].split()
        return str(executable), fields[19]
    except (FileNotFoundError, ProcessLookupError):
        return None


def owns_port(pid: int, port: int) -> bool:
    if os.name == 'nt':
        result = subprocess.run(['netstat', '-ano', '-p', 'tcp'], capture_output=True,
                                check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        return any(len(parts := line.split()) == 5 and parts[0] == 'TCP'
                   and parts[1].endswith(':' + str(port)) and parts[3] == 'LISTENING'
                   and parts[4] == str(pid) for line in result.stdout.decode('ascii', errors='ignore').splitlines())
    inodes = set()
    for path in (Path('/proc/net/tcp'), Path('/proc/net/tcp6')):
        if path.exists():
            for line in path.read_text().splitlines()[1:]:
                parts = line.split()
                if parts[3] == '0A' and int(parts[1].rsplit(':', 1)[1], 16) == port:
                    inodes.add('socket:[' + parts[9] + ']')
    for fd in Path(f'/proc/{pid}/fd').glob('*'):
        try:
            if os.readlink(fd) in inodes:
                return True
        except FileNotFoundError:
            pass
    return False


def validate_state(value) -> dict:
    if not isinstance(value, dict) or not isinstance(value.get('accounts'), dict):
        raise ValueError('Snapshot has no account state')
    if not all(name in value for name in ('block', 'best_block_number', 'blocks', 'transactions')):
        raise ValueError('Snapshot is missing chain history')
    number = value['best_block_number']
    if not isinstance(number, int) or number < 0:
        raise ValueError('Invalid snapshot block number')
    if number and not value['blocks']:
        raise ValueError('Snapshot is missing persisted blocks')
    return value


class AnvilOwnershipError(RuntimeError):
    """The endpoint belongs to another process; it must never be stopped or adopted."""


class AnvilManager:
    def __init__(self, root: Path, port=8545, directory=None, executable=None, interval=5):
        self.root = Path(root).resolve()
        self.port = int(port)
        profile = 'anvil' if self.port == 8545 else f'anvil-{self.port}'
        self.directory = Path(directory or self.root / '.cache' / profile).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        record_name = 'local-anvil-process.json' if self.port == 8545 else f'local-anvil-{self.port}-process.json'
        self.record = self.root / 'deployments' / record_name
        self.state = self.directory / 'state.json'
        self.previous = self.directory / 'state.previous.json'
        self.autosave = self.directory / 'autosave.json'
        local = self.root / '.cache/tools/foundry' / ('anvil.exe' if os.name == 'nt' else 'anvil')
        self.executable = Path(executable or (local if local.exists() else shutil.which('anvil') or local)).resolve()
        self.interval = max(1, int(interval))
        session = requests.Session()
        session.trust_env = False
        self.w3 = Web3(Web3.HTTPProvider(f'http://127.0.0.1:{self.port}', session=session,
                                       request_kwargs={'timeout':10}))
        self.process = None

    @contextmanager
    def locked(self, directory=None, timeout=20):
        directory = Path(directory) if directory else self.directory
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / 'control.lock').open('a+b') as stream:
            stream.seek(0)
            if not stream.read(1):
                stream.write(b'0'); stream.flush()
            deadline = time.monotonic() + timeout
            while True:
                try:
                    stream.seek(0)
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Another Anvil lifecycle operation is still running')
                    time.sleep(.1)
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_UN)

    def _owned(self):
        if not self.record.exists():
            raise AnvilOwnershipError('Port is not owned by this checkout; refusing to stop or adopt another chain')
        record = json.loads(self.record.read_text(encoding='utf-8'))
        pid = int(record['pid'])
        identity = process_identity(pid)
        if not identity or Path(identity[0]) != self.executable or int(record['port']) != self.port:
            raise AnvilOwnershipError('Recorded PID is not this checkout\'s listening Anvil')
        if record.get('creation_id') and record['creation_id'] != identity[1]:
            raise AnvilOwnershipError('Anvil PID has been reused; refusing to stop it')
        if not owns_port(pid, self.port):
            raise AnvilOwnershipError('Recorded Anvil does not own the configured RPC port')
        if self.w3.eth.chain_id != 31337 or 'anvil' not in self.w3.client_version.lower():
            raise AnvilOwnershipError('Persistence is restricted to owned local Anvil chainId 31337')
        return record

    def _snapshot(self):
        self._owned()
        response = self.w3.provider.make_request('anvil_dumpState', [True])
        if 'error' in response or not isinstance(response.get('result'), str):
            raise RuntimeError('Anvil state export failed; existing snapshots were preserved')
        raw = bytes.fromhex(response['result'].removeprefix('0x'))
        if raw.startswith(b'\x1f\x8b'):
            raw = gzip.decompress(raw)
        state = validate_state(json.loads(raw))
        if self.state.exists():
            try:
                old = validate_state(json.loads(self.state.read_text(encoding='utf-8')))
                atomic_json(self.previous, old)
            except (ValueError, OSError):
                pass
        atomic_json(self.state, state)
        print(f'Anvil checkpoint saved at block {state["best_block_number"]}', flush=True)
        return state

    def checkpoint(self):
        with self.locked():
            return self._snapshot()

    def _load_path(self):
        valid, invalid = [], []
        for path in (self.state, self.autosave, self.previous):
            if path.exists():
                try:
                    value = validate_state(json.loads(path.read_text(encoding='utf-8')))
                    valid.append((value['best_block_number'], path.stat().st_mtime_ns, path))
                except (ValueError, OSError):
                    invalid.append(path.name)
        if not valid and invalid:
            raise RuntimeError('All saved Anvil snapshots are invalid; refusing to start an empty chain. Preserve .cache/anvil and restore a backup.')
        if invalid:
            print('Ignoring invalid snapshot(s): ' + ', '.join(invalid), flush=True)
        return max(valid, key=lambda item:item[:2])[2] if valid else None

    def _stop(self, save=True):
        record = self._owned()
        if save:
            self._snapshot()
        os.kill(int(record['pid']), signal.SIGTERM)
        if self.process and self.process.pid == int(record['pid']):
            self.process.wait(timeout=10)
        deadline = time.monotonic() + 10
        while process_identity(int(record['pid'])):
            if time.monotonic() >= deadline:
                raise RuntimeError('Anvil did not stop; snapshot is saved, no replacement process was started')
            time.sleep(.05)
        print('Project Anvil stopped; chain checkpoints, keys and application data preserved', flush=True)

    def stop(self):
        with self.locked():
            if not self.w3.is_connected():
                if self.record.exists():
                    record = json.loads(self.record.read_text(encoding='utf-8'))
                    if process_identity(int(record['pid'])):
                        raise RuntimeError('Recorded process is alive but RPC is unavailable; refusing an unverified stop')
                print('Project Anvil is already stopped; saved checkpoints preserved', flush=True)
                return
            self._stop()

    def ensure_running(self):
        with self.locked():
            if self.w3.is_connected():
                record = self._owned()
                if record.get('persistence_version') == 1 and record.get('state_directory') == str(self.directory):
                    return self.w3
                print('Upgrading owned memory-only Anvil: snapshot, restart, restore', flush=True)
                saved = self._load_path()
                if not (saved and self.w3.eth.block_number == 0 and json.loads(saved.read_text(encoding='utf-8'))['best_block_number'] > 0):
                    self._snapshot()
                self._stop(save=False)
            with socket.socket() as probe:
                if os.name != 'nt':
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    probe.bind(('127.0.0.1', self.port))
                except OSError as exc:
                    raise AnvilOwnershipError('RPC port is occupied by another service') from exc
            path = self._load_path()
            command = [str(self.executable), '--host', '127.0.0.1', '--port', str(self.port),
                       '--chain-id', '31337', '--silent', '--dump-state', str(self.autosave),
                       '--state-interval', str(self.interval), '--preserve-historical-states']
            if path:
                command.extend(['--load-state', str(path)])
            self.record.parent.mkdir(parents=True, exist_ok=True)
            with (self.directory / 'anvil.log').open('a', encoding='utf-8') as log:
                self.process = subprocess.Popen(command, stdout=log, stderr=log,
                    start_new_session=os.name != 'nt',
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            try:
                deadline = time.monotonic() + 20
                while not self.w3.is_connected():
                    if self.process.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError('Anvil did not start; inspect .cache/anvil/anvil.log')
                    time.sleep(.1)
                identity = process_identity(self.process.pid)
                atomic_json(self.record, {'pid':self.process.pid, 'port':self.port,
                    'creation_id':identity[1], 'persistence_version':1,
                    'state_directory':str(self.directory), 'executable':str(self.executable)})
                self._snapshot()
                print(f'Persistent Anvil ready on 127.0.0.1:{self.port} (block {self.w3.eth.block_number})', flush=True)
                return self.w3
            except BaseException:
                if self.process.poll() is None:
                    self.process.terminate(); self.process.wait(timeout=10)
                raise
