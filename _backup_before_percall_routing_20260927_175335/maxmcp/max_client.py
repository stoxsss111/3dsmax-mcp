import ctypes
import ctypes.wintypes as wintypes
import json
import os
import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_TIMEOUT = 120.0
DEFAULT_PIPE_NAME = r"\\.\pipe\3dsmax-mcp"
MCP_PIPE_ENV = "MCP_MAX_PIPE"
# Pin this MCP server process to one 3ds Max: pid (12345), instance id (pid-12345),
# pipe name, or part of the open scene file name. Each chat/session runs its own
# MCP server process, so different sessions can drive different Max instances.
MCP_INSTANCE_ENV = "MCP_MAX_INSTANCE"

# Win32 constants for named pipe
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_OPEN_EXISTING = 3
_ERROR_FILE_NOT_FOUND = 2
_ERROR_PATH_NOT_FOUND = 3
_ERROR_ACCESS_DENIED = 5
_ERROR_BROKEN_PIPE = 109
_ERROR_SEM_TIMEOUT = 121
_ERROR_PIPE_BUSY = 231

# CreateFileW returns HANDLE; set proper return type for correct comparison
_kernel32.CreateFileW.restype = wintypes.HANDLE
_kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
]
_kernel32.WaitNamedPipeW.restype = wintypes.BOOL
_kernel32.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
_kernel32.WriteFile.restype = wintypes.BOOL
_kernel32.WriteFile.argtypes = [
    wintypes.HANDLE,
    wintypes.LPCVOID,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    wintypes.LPVOID,
]
_kernel32.ReadFile.restype = wintypes.BOOL
_kernel32.ReadFile.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    wintypes.LPVOID,
]
_kernel32.PeekNamedPipe.restype = wintypes.BOOL
_kernel32.PeekNamedPipe.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
]
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_INVALID_HANDLE = wintypes.HANDLE(-1).value


class AmbiguousMaxInstanceError(ConnectionError):
    """Raised when multiple live Max native bridges exist and none is claimed."""


class MaxBridgeError(Exception):
    """Raised when the native/TCP bridge returns a structured error response."""

    def __init__(self, message: str, response: dict[str, Any]) -> None:
        self.bridge_message = message
        self.bridge_response = response
        super().__init__(f"MAXScript error: {message}")


class MaxClient:
    """Client that sends commands to 3ds Max via named pipe or TCP."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        timeout: float = DEFAULT_TIMEOUT,
        transport: str = "auto",
        pipe_name: str = DEFAULT_PIPE_NAME,
    ):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.transport = transport
        self.pipe_name = pipe_name
        # One persistent pipe handle + lock per 3ds Max instance, so commands to
        # different Max instances never block each other.
        self._pipe_handles: dict[str, int] = {}
        self._pipe_locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        # Session-level target chosen with select_instance() (per MCP server process).
        self._session_instance: Optional[dict[str, Any]] = None
        self._local = threading.local()
        env_key = os.environ.get(MCP_INSTANCE_ENV, "").strip()
        if env_key:
            self._env_instance_key: Optional[str] = env_key
        else:
            self._env_instance_key = None

    def clear_last_response(self) -> None:
        """Clear thread-local metadata from the previous command."""
        self._local.last_response = None
        self._local.last_error = None

    def get_last_transport(self) -> dict[str, Any] | None:
        """Return compact transport metadata from the last command on this thread."""
        response = getattr(self._local, "last_response", None)
        if isinstance(response, dict):
            meta = response.get("meta") if isinstance(response.get("meta"), dict) else {}
            return {
                "transport": meta.get("transport"),
                "requested_transport": meta.get("requestedTransport"),
                "request_id": response.get("requestId"),
                "protocol_version": meta.get("protocolVersion"),
                "client_round_trip_ms": meta.get("clientRoundTripMs"),
                "fallback_error": meta.get("fallbackError"),
            }
        error = getattr(self._local, "last_error", None)
        if isinstance(error, dict):
            return error
        return None

    @property
    def native_available(self) -> bool:
        """Check whether the native C++ bridge is currently available."""
        if self.transport == "pipe":
            return True
        if self.transport == "tcp":
            return False
        try:
            return self._probe_pipe_available(self._resolve_pipe_name())
        except (ConnectionError, TimeoutError):
            return False

    def _config_dir(self) -> Path:
        root = os.environ.get("LOCALAPPDATA")
        if root:
            return Path(root) / "3dsmax-mcp"
        return Path.home() / "AppData" / "Local" / "3dsmax-mcp"

    def _load_instance(self, path: Path) -> dict[str, Any] | None:
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("pipe"), str):
            return None
        return data

    def _active_instance(self) -> dict[str, Any] | None:
        return self._load_instance(self._config_dir() / "active_instance.json")

    def _live_instances(self) -> list[dict[str, Any]]:
        instances_dir = self._config_dir() / "instances"
        try:
            paths = sorted(instances_dir.glob("*.json"))
        except OSError:
            return []

        live: list[dict[str, Any]] = []
        for path in paths:
            data = self._load_instance(path)
            if data and self._probe_pipe_available(data["pipe"]):
                live.append(data)
        return live

    # ── Multi-instance targeting ─────────────────────────────────
    def _instance_scene(self, pipe_name: str, timeout: float = 3.0) -> str:
        """Open scene path of one instance ('' untitled, '<busy>' if Max does not answer)."""
        previous = getattr(self._local, "override_pipe", None)
        self._local.override_pipe = pipe_name
        try:
            response = self.send_command(
                'maxFilePath + maxFileName', cmd_type="maxscript", timeout=timeout
            )
            result = response.get("result", "")
            return result if isinstance(result, str) else str(result)
        except Exception:  # noqa: BLE001 - informational only
            return "<busy>"
        finally:
            self._local.override_pipe = previous

    def list_instances(self, details: bool = False) -> list[dict[str, Any]]:
        """Live 3ds Max bridges (instance files whose pipe answers)."""
        active = self._active_instance() or {}
        selected = self._session_instance or {}
        out: list[dict[str, Any]] = []
        for index, item in enumerate(self._live_instances(), start=1):
            info = dict(item)
            info["index"] = index
            info["claimed"] = item.get("pipe") == active.get("pipe")
            info["selected"] = item.get("pipe") == selected.get("pipe")
            if details:
                info["scene"] = self._instance_scene(item["pipe"])
            out.append(info)
        return out

    def find_instance(self, key: str) -> dict[str, Any]:
        """Match an instance by list index, pid, instance id, pipe name or scene name part."""
        key = str(key).strip()
        live = self.list_instances()
        if not live:
            raise ConnectionError("No running 3ds Max MCP bridge found.")
        lowered = key.lower()
        exact: list[dict[str, Any]] = []
        for item in live:
            candidates = {
                str(item.get("index")),
                str(item.get("pid", "")),
                str(item.get("instance_id", "")).lower(),
                str(item.get("pipe", "")).lower(),
            }
            if lowered in candidates:
                exact.append(item)
        if len(exact) == 1:
            return exact[0]
        live = self.list_instances(details=True)
        partial = [
            item for item in live
            if lowered and lowered in str(item.get("scene", "")).lower()
        ]
        if len(partial) == 1:
            return partial[0]
        labels = ", ".join(
            f"#{i.get('index')} {i.get('instance_id', '?')} scene={i.get('scene', '?')!r}"
            for i in live
        )
        if not exact and not partial:
            raise ValueError(f"No 3ds Max instance matches {key!r}. Available: {labels}")
        raise ValueError(f"{key!r} matches several 3ds Max instances. Available: {labels}")

    def select_instance(self, key: str | None) -> dict[str, Any] | None:
        """Bind this MCP server process to one Max instance ('' / 'auto' clears)."""
        if key is None or str(key).strip().lower() in ("", "auto", "none", "clear"):
            self._session_instance = None
            return None
        item = self.find_instance(key)
        self._session_instance = {k: item[k] for k in ("instance_id", "pid", "pipe") if k in item}
        return item

    @contextmanager
    def targeting(self, key: str | None):
        """Route commands on this thread to one Max instance for the duration of the block."""
        if not key or not str(key).strip():
            yield None
            return
        item = self.find_instance(key)
        previous = getattr(self._local, "override_pipe", None)
        self._local.override_pipe = item["pipe"]
        try:
            yield item
        finally:
            self._local.override_pipe = previous

    def _is_pinned(self) -> bool:
        return bool(
            getattr(self._local, "override_pipe", None)
            or self._session_instance
            or os.environ.get(MCP_PIPE_ENV)
            or self._env_instance_key
        )

    def _resolve_pipe_name(self) -> str:
        override = getattr(self._local, "override_pipe", None)
        if override:
            return override

        if self._session_instance:
            pipe = self._session_instance["pipe"]
            if self._probe_pipe_available(pipe):
                return pipe
            label = self._session_instance.get("instance_id", pipe)
            raise ConnectionError(
                f"The selected 3ds Max instance ({label}) is not running any more. "
                "Call list_max_instances and select_max_instance again."
            )

        env_pipe = os.environ.get(MCP_PIPE_ENV)
        if env_pipe:
            return env_pipe

        if self._env_instance_key:
            try:
                item = self.select_instance(self._env_instance_key)
            except (ValueError, ConnectionError):
                item = None
            if item:
                return item["pipe"]

        if self.pipe_name != DEFAULT_PIPE_NAME:
            return self.pipe_name

        active = self._active_instance()
        if active and self._probe_pipe_available(active["pipe"]):
            return active["pipe"]

        live = self._live_instances()
        if len(live) == 1:
            return live[0]["pipe"]
        if len(live) > 1:
            labels = ", ".join(
                f"{item.get('instance_id', 'unknown')} pid={item.get('pid', '?')}"
                for item in live
            )
            raise AmbiguousMaxInstanceError(
                "Multiple 3ds Max MCP instances are running. "
                "Call list_max_instances + select_max_instance to pick one for this session, "
                "or in the target 3ds Max window run MCP > MCP Claim This Max. "
                f"Available instances: {labels}"
            )

        return DEFAULT_PIPE_NAME

    def _probe_pipe_available(self, pipe_name: str | None = None) -> bool:
        """Best-effort probe that treats a busy pipe as available."""
        pipe_name = pipe_name or self.pipe_name
        handle = _kernel32.CreateFileW(
            pipe_name,
            _GENERIC_READ | _GENERIC_WRITE,
            0,
            None,
            _OPEN_EXISTING,
            0,
            None,
        )
        if handle != _INVALID_HANDLE:
            _kernel32.CloseHandle(handle)
            return True

        err = ctypes.get_last_error()
        if err in (_ERROR_PIPE_BUSY, _ERROR_ACCESS_DENIED):
            return True
        if err in (_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND):
            return False

        if _kernel32.WaitNamedPipeW(pipe_name, 0):
            return True
        wait_err = ctypes.get_last_error()
        if wait_err in (_ERROR_SEM_TIMEOUT, _ERROR_PIPE_BUSY, _ERROR_ACCESS_DENIED):
            return True
        return False

    def _pipe_lock_for(self, pipe_name: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._pipe_locks.get(pipe_name)
            if lock is None:
                lock = threading.Lock()
                self._pipe_locks[pipe_name] = lock
            return lock

    def _close_pipe_handle(self, pipe_name: str) -> None:
        handle = self._pipe_handles.pop(pipe_name, None)
        if handle not in (None, 0, _INVALID_HANDLE):
            _kernel32.CloseHandle(handle)

    def _ensure_pipe_handle(self, deadline: float, pipe_name: str) -> int:
        handle = self._pipe_handles.get(pipe_name)
        if handle not in (None, 0, _INVALID_HANDLE):
            return handle

        while True:
            handle = _kernel32.CreateFileW(
                pipe_name,
                _GENERIC_READ | _GENERIC_WRITE,
                0,
                None,
                _OPEN_EXISTING,
                0,
                None,
            )
            if handle != _INVALID_HANDLE:
                self._pipe_handles[pipe_name] = handle
                return handle

            err = ctypes.get_last_error()
            if err in (_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND):
                raise ConnectionError(
                    f"Named pipe {pipe_name} not found. "
                    "Is the MCP Bridge plugin loaded in 3ds Max?"
                )
            if err != _ERROR_PIPE_BUSY:
                raise ConnectionError(f"Failed to open pipe: Win32 error {err}")

            remaining_ms = int((deadline - time.perf_counter()) * 1000)
            if remaining_ms <= 0:
                raise TimeoutError(
                    f"Timed out waiting for named pipe {pipe_name} after "
                    f"{self.timeout}s."
                )

            wait_ms = min(remaining_ms, 250)
            if _kernel32.WaitNamedPipeW(pipe_name, wait_ms):
                continue
            wait_err = ctypes.get_last_error()
            if wait_err in (_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND):
                raise ConnectionError(
                    f"Named pipe {pipe_name} disappeared while waiting."
                )
            if wait_err in (_ERROR_SEM_TIMEOUT, _ERROR_PIPE_BUSY):
                continue
            raise ConnectionError(
                f"Failed waiting for named pipe {pipe_name}: "
                f"Win32 error {wait_err}"
            )

    def send_command(
        self,
        command: str,
        cmd_type: str = "maxscript",
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """Send a command to 3ds Max and return the parsed JSON response."""
        effective_timeout = timeout or self.timeout
        request_id = uuid4().hex
        started_at = time.perf_counter()
        transport_used = self.transport
        fallback_error: str | None = None
        self.clear_last_response()

        request = json.dumps({
            "command": command,
            "type": cmd_type,
            "requestId": request_id,
            "protocolVersion": 2,
        }, ensure_ascii=True)

        if self.transport == "pipe":
            transport_used = "namedpipe"
            response_data = self._send_via_pipe(request, effective_timeout)
        elif self.transport == "tcp":
            transport_used = "tcp"
            response_data = self._send_via_tcp(request, effective_timeout)
        else:
            try:
                transport_used = "namedpipe"
                response_data = self._send_via_pipe(request, effective_timeout)
            except AmbiguousMaxInstanceError:
                raise
            except (ConnectionError, TimeoutError) as exc:
                if self._is_pinned():
                    # A specific Max was requested: never fall back to TCP, which
                    # could silently reach a different 3ds Max instance.
                    raise
                fallback_error = str(exc)
                transport_used = "tcp"
                response_data = self._send_via_tcp(request, effective_timeout)

        try:
            response = self._parse_response(response_data, request_id, started_at)
        except Exception as exc:
            self._local.last_error = {
                "transport": transport_used,
                "requested_transport": self.transport,
                "request_id": request_id,
                "error": str(exc),
                "fallback_error": fallback_error,
            }
            raise

        meta = response.setdefault("meta", {})
        meta.setdefault("transport", transport_used)
        meta.setdefault("requestedTransport", self.transport)
        if fallback_error:
            meta.setdefault("fallbackError", fallback_error)
        self._local.last_response = response
        return response

    # ── Named Pipe transport ─────────────────────────────────────
    def _send_via_pipe(self, request: str, timeout: float) -> bytes:
        deadline = time.perf_counter() + timeout
        data = (request + "\n").encode("utf-8")
        pipe_name = self._resolve_pipe_name()

        with self._pipe_lock_for(pipe_name):
            for attempt in range(2):
                handle = self._ensure_pipe_handle(deadline, pipe_name)
                try:
                    total_written = 0
                    while total_written < len(data):
                        written = wintypes.DWORD()
                        ok = _kernel32.WriteFile(
                            handle,
                            data[total_written:],
                            len(data) - total_written,
                            ctypes.byref(written),
                            None,
                        )
                        if not ok:
                            err = ctypes.get_last_error()
                            if err == _ERROR_BROKEN_PIPE:
                                raise BrokenPipeError("Pipe closed while writing request.")
                            raise ConnectionError(
                                f"Failed writing to pipe: Win32 error {err}"
                            )
                        if written.value == 0:
                            raise ConnectionError(
                                "Pipe write returned 0 bytes written."
                            )
                        total_written += written.value

                    response_data = bytearray()
                    buf = ctypes.create_string_buffer(65536)
                    while True:
                        if time.perf_counter() >= deadline:
                            self._close_pipe_handle(pipe_name)
                            raise TimeoutError(
                                f"Timed out waiting for named pipe response after "
                                f"{timeout}s."
                            )

                        bytes_read = wintypes.DWORD()
                        ok = _kernel32.ReadFile(
                            handle, buf, len(buf), ctypes.byref(bytes_read), None
                        )
                        if bytes_read.value > 0:
                            response_data.extend(buf.raw[:bytes_read.value])
                            if b"\n" in response_data:
                                return bytes(response_data)

                        if not ok:
                            err = ctypes.get_last_error()
                            if err == _ERROR_BROKEN_PIPE:
                                raise BrokenPipeError(
                                    "Pipe closed while reading response."
                                )
                            raise ConnectionError(
                                f"Failed reading from pipe: Win32 error {err}"
                            )

                        if bytes_read.value == 0:
                            raise BrokenPipeError(
                                "Pipe closed before response terminator."
                            )
                except BrokenPipeError:
                    self._close_pipe_handle(pipe_name)
                    if attempt == 0 and time.perf_counter() < deadline:
                        continue
                    raise ConnectionError("Named pipe connection closed during request.")
                except ConnectionError:
                    self._close_pipe_handle(pipe_name)
                    if attempt == 0 and time.perf_counter() < deadline:
                        continue
                    raise

    # ── TCP transport (legacy) ───────────────────────────────────
    def _send_via_tcp(self, request: str, timeout: float) -> bytes:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)

        try:
            sock.connect((self.host, self.port))
            sock.sendall((request + "\n").encode("utf-8"))

            response_data = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response_data += chunk
                if b"\n" in response_data:
                    break

            return response_data

        except socket.timeout:
            raise TimeoutError(
                f"3ds Max did not respond within {timeout}s. "
                "Is the MCP TCP listener running in 3ds Max?"
            )
        except ConnectionRefusedError:
            raise ConnectionError(
                f"Could not connect to 3ds Max on {self.host}:{self.port}. "
                "Is the MCP TCP listener running in 3ds Max?"
            )
        finally:
            sock.close()

    # ── Response parsing (shared) ────────────────────────────────
    def _parse_response(
        self, response_data: bytes, request_id: str, started_at: float
    ) -> dict[str, Any]:
        # Strip UTF-8 BOM if present
        if response_data.startswith(b'\xef\xbb\xbf'):
            response_data = response_data[3:]
        response_str = response_data.decode("utf-8", errors="replace").strip()

        if not response_str:
            raise RuntimeError("Empty response from 3ds Max")

        response = json.loads(response_str)
        response_request_id = response.get("requestId")
        if response_request_id not in (None, "", request_id):
            raise RuntimeError(
                f"Mismatched response requestId: expected {request_id}, got {response_request_id}"
            )

        response["requestId"] = request_id
        meta = response.get("meta")
        if not isinstance(meta, dict):
            meta = {}
            response["meta"] = meta
        meta.setdefault(
            "clientRoundTripMs",
            round((time.perf_counter() - started_at) * 1000.0, 3),
        )

        if not response.get("success", False):
            error_msg = response.get("error", "Unknown error")
            raise MaxBridgeError(str(error_msg), response)

        return response
