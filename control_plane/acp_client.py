"""JSON-RPC ACP client over stdio for `agent acp`."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
from pathlib import Path
from collections.abc import Awaitable, Callable
from typing import Any

from control_plane.agent_resolve import resolve_agent_executable
from control_plane.model_cli import cli_argv_model_for_agent, cli_model_id_for_argv

logger = logging.getLogger(__name__)


def _powershell_exe() -> str:
    w = shutil.which("powershell.exe")
    if w:
        return w
    root = os.environ.get("SystemRoot", r"C:\Windows")
    cand = Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if cand.is_file():
        return str(cand)
    return "powershell.exe"


def _cmd_exe() -> str:
    w = shutil.which("cmd.exe")
    if w:
        return w
    cand = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "cmd.exe"
    if cand.is_file():
        return str(cand)
    return "cmd.exe"


def _wrap_argv_for_windows_shims(argv: list[str]) -> list[str]:
    """
    Windows Cursor install often has agent.cmd + agent.ps1 (no .exe). Subprocess cannot
    execute .ps1/.cmd as argv[0] directly; use PowerShell or cmd.exe /c.
    """
    if sys.platform != "win32" or not argv:
        return argv
    prog = argv[0]
    pl = str(prog).lower()
    rest = argv[1:]
    if pl.endswith(".ps1"):
        return [
            _powershell_exe(),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            prog,
            *rest,
        ]
    if pl.endswith((".cmd", ".bat")):
        return [_cmd_exe(), "/c", prog, *rest]
    return argv


OnUpdate = Callable[[dict[str, Any]], Awaitable[None]]
OnPermission = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
OnQuestion = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class AcpClient:
    """
    Spawns `agent acp` and speaks JSON-RPC 2.0 over newline-delimited stdin/stdout.
    """

    def __init__(
        self,
        workspace: str,
        agent_executable: str,
        extra_args: list[str],
        api_key: str | None,
        on_update: OnUpdate | None = None,
        on_permission: OnPermission | None = None,
        on_question: OnQuestion | None = None,
        model: str | None = None,
    ) -> None:
        self.workspace = workspace
        self.agent_executable = agent_executable
        self.extra_args = list(extra_args)
        self.api_key = api_key
        self.on_update = on_update
        self.on_permission = on_permission
        self.on_question = on_question
        m = cli_model_id_for_argv(model.strip()) if isinstance(model, str) and model.strip() else None
        # Effective `--model` at spawn (None = omit flag). spawn_model matches SessionManager recycle checks.
        self.model: str | None = m
        self.spawn_model: str | None = self.model

        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._closed = asyncio.Event()
        self._send_lock = asyncio.Lock()
        self.session_id: str | None = None
        self.session_config_options: list[dict[str, Any]] = []
        self._stderr_lines: list[str] = []
        self._stderr_lines_cap = 48

    def _resolve_agent_executable(self) -> str:
        return resolve_agent_executable(self.agent_executable)

    def _build_argv(self, agent_exe: str) -> list[str]:
        argv = [agent_exe]
        if self.api_key:
            argv.extend(["--api-key", self.api_key])
        argv.extend(["--trust", "--force", "--workspace", self.workspace])
        argv_model = cli_argv_model_for_agent(self.model)
        if argv_model:
            argv.extend(["--model", argv_model])
        argv.extend(self.extra_args)
        argv.append("acp")
        return argv

    @staticmethod
    def _argv_for_log(argv: list[str]) -> list[str]:
        out: list[str] = []
        skip_next = False
        for a in argv:
            if skip_next:
                out.append("(redacted)")
                skip_next = False
                continue
            if a == "--api-key":
                out.append(a)
                skip_next = True
            else:
                out.append(a)
        return out

    async def start(self) -> None:
        if self._proc:
            return
        env = os.environ.copy()
        if self.api_key:
            env["CURSOR_API_KEY"] = self.api_key
        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(asyncio.subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

        agent_exe = self._resolve_agent_executable()
        argv = self._build_argv(agent_exe)
        argv = _wrap_argv_for_windows_shims(argv)
        logger.info("Starting ACP: %s", self._argv_for_log(argv))
        self._proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=self.workspace,
            creationflags=creationflags,
        )
        self._closed.clear()
        self._reader_task = asyncio.create_task(self._read_stdout_loop())
        self._stderr_task = asyncio.create_task(self._read_stderr_loop())

    async def _read_stderr_loop(self) -> None:
        if not self._proc or not self._proc.stderr:
            return
        while True:
            line = await self._proc.stderr.readline()
            if not line:
                break
            try:
                text = line.decode("utf-8", errors="replace").rstrip()
            except Exception:
                text = str(line)
            if text:
                self._stderr_lines.append(text)
                if len(self._stderr_lines) > self._stderr_lines_cap:
                    self._stderr_lines = self._stderr_lines[-self._stderr_lines_cap :]
                lvl = logging.WARNING if any(
                    x in text.lower() for x in ("error", "fatal", "panic", "invalid", "unknown model")
                ) else logging.DEBUG
                logger.log(lvl, "agent stderr: %s", text)

    def _log_stderr_tail(self) -> None:
        if not self._stderr_lines:
            return
        tail = "\n".join(self._stderr_lines[-24:])
        logger.warning("Recent agent stderr before ACP exit:\n%s", tail)

    async def _read_stdout_loop(self) -> None:
        if not self._proc or not self._proc.stdout:
            return
        assert self._proc.stdout is not None
        while True:
            line = await self._proc.stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line.decode("utf-8"))
            except json.JSONDecodeError:
                logger.warning("Non-JSON ACP line: %s", line[:200])
                continue
            await self._dispatch_incoming(msg)

        # Wake pending futures on unexpected EOF
        self._log_stderr_tail()
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(RuntimeError("ACP process ended unexpectedly"))
        self._pending.clear()
        self._closed.set()

    async def _dispatch_incoming(self, msg: dict[str, Any]) -> None:
        if "id" in msg and msg["id"] is not None and ("result" in msg or "error" in msg):
            rid = msg["id"]
            if rid in self._pending:
                fut = self._pending.pop(rid)
                if "error" in msg:
                    fut.set_exception(RuntimeError(str(msg["error"])))
                else:
                    fut.set_result(msg.get("result"))
            return

        method = msg.get("method")
        if not method:
            return

        if method == "session/update" and self.on_update:
            await self.on_update(msg.get("params") or {})
            return

        req_id = msg.get("id")
        if req_id is None:
            logger.debug("ACP notification: %s", method)
            return

        result: dict[str, Any] = {}
        try:
            if method == "session/request_permission" and self.on_permission:
                result = await self.on_permission(str(req_id), msg.get("params") or {})
            elif method.startswith("cursor/") and self.on_question:
                result = await self.on_question(str(req_id), msg)
            else:
                # Default: allow-once style permission if shape matches
                if method == "session/request_permission":
                    result = {"outcome": {"outcome": "selected", "optionId": "allow-once"}}
                else:
                    result = {}
        except Exception as e:
            logger.exception("Handler error for %s: %s", method, e)
            await self._send_raw({"jsonrpc": "2.0", "id": req_id, "error": {"message": str(e)}})
            return

        await self._send_raw({"jsonrpc": "2.0", "id": req_id, "result": result})

    async def _send_raw(self, obj: dict[str, Any]) -> None:
        if not self._proc or not self._proc.stdin:
            raise RuntimeError("ACP not started")
        line = json.dumps(obj, ensure_ascii=False) + "\n"
        async with self._send_lock:
            self._proc.stdin.write(line.encode("utf-8"))
            await self._proc.stdin.drain()

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if not self._proc:
            await self.start()
        assert self._proc is not None
        req_id = self._next_id
        self._next_id += 1
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        await self._send_raw({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}})
        return await fut

    async def initialize(self) -> None:
        await self.request(
            "initialize",
            {
                "protocolVersion": 1,
                "clientCapabilities": {
                    "fs": {"readTextFile": False, "writeTextFile": False},
                    "terminal": False,
                },
                "clientInfo": {"name": "cursor-cli-control-plane", "version": "0.3.0"},
            },
        )

    async def authenticate(self) -> None:
        await self.request("authenticate", {"methodId": "cursor_login"})

    async def session_new(self) -> str:
        result = await self.request(
            "session/new",
            {"cwd": self.workspace, "mcpServers": []},
        )
        if isinstance(result, dict):
            sid = result.get("sessionId")
            self.session_id = str(sid) if sid is not None else str(result)
            self.session_config_options = result.get("configOptions") or []
        elif isinstance(result, str):
            self.session_id = result
            self.session_config_options = []
        else:
            self.session_id = str(result)
            self.session_config_options = []
        return self.session_id or ""

    async def session_set_config_option(self, config_id: str, value: str) -> list[dict[str, Any]]:
        """Call session/set_config_option and store the updated configOptions returned by the agent."""
        result = await self.request(
            "session/set_config_option",
            {
                "sessionId": self.session_id,
                "configId": config_id,
                "value": value,
            },
        )
        if isinstance(result, dict):
            updated = result.get("configOptions") or []
            self.session_config_options = updated
            return updated
        return []

    async def session_load(self, session_id: str) -> None:
        await self.request(
            "session/load",
            {
                "sessionId": session_id,
                "cwd": self.workspace,
                "mcpServers": [],
            },
        )
        self.session_id = session_id

    async def session_prompt(self, text: str) -> Any:
        if not self.session_id:
            raise RuntimeError("No ACP session")
        return await self.request(
            "session/prompt",
            {
                "sessionId": self.session_id,
                "prompt": [{"type": "text", "text": text}],
            },
        )

    async def cancel_and_kill(self, grace_seconds: float = 3.0) -> None:
        await self.kill(grace_seconds=grace_seconds)

    async def kill(self, grace_seconds: float = 3.0) -> None:
        proc = self._proc
        if not proc:
            return
        if proc.returncode is not None:
            self._proc = None
            return
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=grace_seconds)
        except asyncio.TimeoutError:
            logger.warning("ACP process did not exit; killing")
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            if sys.platform == "win32":
                # Best-effort tree kill on Windows
                try:
                    os.system(f"taskkill /F /T /PID {proc.pid} >nul 2>&1")
                except Exception:
                    pass
            await proc.wait()
        self._proc = None
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None
        if self._stderr_task:
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except asyncio.CancelledError:
                pass
            self._stderr_task = None
        self._closed.set()
