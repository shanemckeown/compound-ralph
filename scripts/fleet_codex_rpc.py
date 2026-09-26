#!/usr/bin/env python3
"""Small local transports for existing Codex threads; never launches/resumes a server.

App-server JSONL is public (https://learn.chatgpt.com/docs/app-server). Desktop
IPC is an explicitly version-pinned local adapter for ChatGPT.app build8109;
unknown app builds fail closed. Neither probe starts a model turn.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import struct
import time
import uuid

MAX_FRAME = 8 * 1024 * 1024
DESKTOP_ASAR = Path('/Applications/ChatGPT.app/Contents/Resources/app.asar')
DESKTOP_ASAR_SHA256 = '64fc2f27d2dddfa968acfacbe5e4e0328071bdc406351ff4a7d18f0b4692c83d'
DESKTOP_METHODS = {'initialize': 0, 'thread-owner-discovery': 1,
                   'thread-follower-start-turn': 2, 'thread-follower-steer-turn': 1}

class TransportError(Exception):
    """No reliable acknowledgement. A submitted mutation must not be retried."""

class RPCError(Exception):
    """Explicit server rejection (not a transport timeout)."""
    def __init__(self, message, code=None):
        super().__init__(str(message))
        self.code = code


def _validate_socket(path):
    p = Path(path).expanduser()
    if not p.is_absolute():
        raise TransportError('Codex socket path must be absolute')
    try:
        info = p.lstat()
        parent = p.parent.stat()
    except OSError as exc:
        raise TransportError(f'Codex socket unavailable: {exc.strerror}') from exc
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        raise TransportError('Codex endpoint must be a socket owned by the current user')
    if parent.st_uid != os.getuid() or parent.st_mode & 0o022 or info.st_mode & 0o022:
        raise TransportError('Codex endpoint or its parent is writable by another user')
    return str(p)


def _check_desktop_build():
    try:
        with DESKTOP_ASAR.open('rb') as file:
            h = hashlib.sha256()
            for chunk in iter(lambda: file.read(1024 * 1024), b''):
                h.update(chunk)
            digest = h.hexdigest()
    except OSError as exc:
        raise TransportError('Reviewed ChatGPT desktop adapter is unavailable') from exc
    if digest != DESKTOP_ASAR_SHA256:
        raise TransportError('ChatGPT app build changed; desktop relay adapter needs compatibility review')


class _SocketBase:
    def __init__(self, socket_path, timeout=15):
        self.socket_path = socket_path
        self.timeout = timeout
        self.sock = None
        self.deadline = None

    def _connect(self):
        path = _validate_socket(self.socket_path)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        try:
            self.sock.connect(path)
        except OSError as exc:
            self.close()
            raise TransportError(f'Cannot connect to Codex socket: {exc.strerror}') from exc

    def close(self):
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def __exit__(self, *args):
        self.close()

    def _send(self, data):
        try:
            self.sock.sendall(data)
        except OSError as exc:
            raise TransportError('Codex socket write failed; delivery outcome may be unknown') from exc

    def _recv(self, length):
        if self.deadline is not None:
            left = self.deadline - time.monotonic()
            if left <= 0:
                raise TransportError('Codex request acknowledgement timed out')
            self.sock.settimeout(left)
        try:
            data = self.sock.recv(length)
        except OSError as exc:
            raise TransportError('Codex request acknowledgement unavailable') from exc
        if not data:
            raise TransportError('Codex connection closed before acknowledgement')
        return data


class CodexRPC(_SocketBase):
    """One initialized app-server JSONL socket connection."""
    def __init__(self, socket_path, timeout=15):
        super().__init__(socket_path, timeout)
        self.sequence = 0
        self.buffer = b''

    def __enter__(self):
        self._connect()
        try:
            self.request('initialize', {'clientInfo': {'name': 'aestheticc-fleet',
                         'version': '1.0'}, 'capabilities': {'experimentalApi': False}})
            self._send_json({'method': 'initialized', 'params': {}})
            return self
        except Exception:
            self.close()
            raise

    def _send_json(self, obj):
        self._send(json.dumps(obj, ensure_ascii=False).encode('utf-8') + b'\n')

    def request(self, method, params):
        self.sequence += 1
        request_id = self.sequence
        self.deadline = time.monotonic() + self.timeout
        self._send_json({'id': request_id, 'method': method, 'params': params})
        while True:
            while b'\n' not in self.buffer:
                self.buffer += self._recv(65536)
                if len(self.buffer) > MAX_FRAME:
                    raise TransportError('Codex response exceeded the bounded frame size')
            line, self.buffer = self.buffer.split(b'\n', 1)
            try:
                response = json.loads(line)
            except (ValueError, UnicodeDecodeError) as exc:
                raise TransportError('Malformed Codex response') from exc
            if not isinstance(response, dict):
                raise TransportError('Malformed Codex response shape')
            # This integration never answers approval/tool requests. The existing
            # owner keeps responsibility for those requests and permissions.
            if response.get('id') != request_id or 'method' in response:
                continue
            if 'error' in response:
                error = response['error']
                if not isinstance(error, dict):
                    raise TransportError('Malformed Codex error acknowledgement')
                raise RPCError(error.get('message', 'Codex request rejected'), error.get('code'))
            result = response.get('result')
            if not isinstance(result, dict):
                raise TransportError('Codex response did not contain a result object')
            return result


class DesktopRPC(_SocketBase):
    """Version-pinned desktop follower protocol, addressed by owner client UUID."""
    def __init__(self, socket_path, timeout=15):
        super().__init__(socket_path, timeout)
        self.client_id = None

    def __enter__(self):
        _check_desktop_build()
        self._connect()
        try:
            response = self.request('initialize', {'clientType': 'aestheticc-fleet'})
            self.client_id = response['result'].get('clientId')
            if not isinstance(self.client_id, str) or not self.client_id:
                raise TransportError('Desktop IPC initialization did not identify this client')
            return self
        except Exception:
            self.close()
            raise

    def _send_json(self, message):
        body = json.dumps(message, ensure_ascii=False).encode('utf-8')
        if not 0 < len(body) <= MAX_FRAME:
            raise TransportError('Desktop IPC request too large')
        self._send(struct.pack('<I', len(body)) + body)

    def _exact(self, length):
        chunks = bytearray()
        while len(chunks) < length:
            chunks.extend(self._recv(length - len(chunks)))
        return bytes(chunks)

    def request(self, method, params, target_client_id=None):
        if method not in DESKTOP_METHODS:
            raise TransportError('Desktop IPC method is outside the reviewed relay adapter')
        request_id = str(uuid.uuid4())
        message = {'type': 'request', 'requestId': request_id, 'method': method,
                   'params': params, 'version': DESKTOP_METHODS[method],
                   'timeoutMs': max(1000, int((self.timeout - 1) * 1000))}
        if self.client_id:
            message['sourceClientId'] = self.client_id
        if target_client_id:
            message['targetClientId'] = target_client_id
        self.deadline = time.monotonic() + self.timeout
        self._send_json(message)
        while True:
            size = struct.unpack('<I', self._exact(4))[0]
            if not 0 < size <= MAX_FRAME:
                raise TransportError('Desktop IPC response exceeded the bounded frame size')
            try:
                response = json.loads(self._exact(size))
            except (ValueError, UnicodeDecodeError) as exc:
                raise TransportError('Malformed desktop IPC response') from exc
            if not isinstance(response, dict):
                raise TransportError('Malformed desktop IPC response shape')
            if response.get('type') == 'client-discovery-request':
                self._send_json({'type': 'client-discovery-response',
                                 'requestId': response.get('requestId'),
                                 'response': {'canHandle': False}})
                continue
            # Discard unsolicited thread broadcasts without persisting or logging.
            if response.get('requestId') != request_id or response.get('type') != 'response':
                continue
            if response.get('resultType') == 'error':
                raise RPCError(response.get('error', 'Desktop request rejected'))
            if response.get('resultType') != 'success' or response.get('method') != method:
                raise TransportError('Desktop IPC acknowledgement shape/method mismatch')
            if not isinstance(response.get('result'), dict):
                raise TransportError('Malformed desktop IPC result acknowledgement')
            if target_client_id and response.get('handledByClientId') != target_client_id:
                raise TransportError('Desktop IPC acknowledgement came from the wrong owner')
            return response

    def discover_owner(self, session_id):
        response = self.request('thread-owner-discovery',
                                {'hostId': 'local', 'conversationId': session_id})
        owner = response.get('handledByClientId')
        if not isinstance(owner, str) or not owner or owner == self.client_id:
            raise TransportError('Desktop did not identify a distinct thread owner')
        if response['result'].get('supportsUntrustedAppInput') is not True:
            raise TransportError('Desktop thread owner lacks the reviewed protocol capability')
        return owner


def is_desktop_socket(socket_path):
    return Path(socket_path).name == 'ipc.sock' and Path(socket_path).parent.name == 'ipc'


def probe_thread(session_id, socket_path):
    """LIVE / NOT_LOADED / UNKNOWN, with no transcript reads or model calls."""
    result = {'status': 'UNKNOWN', 'name': 'codex', 'reason': '', 'thread': None}
    if not isinstance(session_id, str) or not session_id or not isinstance(socket_path, str) or not socket_path:
        result['reason'] = 'Codex thread ID and socket are required'
        return result
    result['name'] = f'codex-{session_id[:8]}'
    try:
        if is_desktop_socket(socket_path):
            with DesktopRPC(socket_path) as rpc:
                owner = rpc.discover_owner(session_id)
            result.update(status='LIVE', reason='Desktop verified exact thread owner',
                          owner_client_id=owner, transport='desktop-ipc')
            return result
        with CodexRPC(socket_path) as rpc:
            response = rpc.request('thread/read', {'threadId': session_id, 'includeTurns': False})
        thread = response.get('thread')
        if not isinstance(thread, dict) or thread.get('id') != session_id:
            raise TransportError('Codex returned a different or malformed thread')
        runtime_status = thread.get('status')
        if not isinstance(runtime_status, dict):
            raise TransportError('Codex returned a malformed thread status')
        status = runtime_status.get('type')
        result['name'] = thread.get('name') or result['name']
        result['thread'] = {'id': session_id, 'status': thread.get('status')}
        if status in ('active', 'idle', 'systemError'):
            result.update(status='LIVE', reason=f'App server reports {status}', transport='app-server')
        elif status == 'notLoaded':
            result.update(status='NOT_LOADED', reason='App server reports thread not loaded')
        else:
            result['reason'] = 'App server returned an unknown thread state'
    except (TransportError, RPCError, OSError, ValueError) as exc:
        result['reason'] = str(exc)
    return result
