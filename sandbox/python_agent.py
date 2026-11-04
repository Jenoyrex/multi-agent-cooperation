"""PythonFileAgent: runs the user's own .py file in a separate process, one process per negotiation.

The server never imports the file. The child gets an allowlisted environment, so no API key is inherited, and each turn
has a timeout, after which the process is killed. It is not a security sandbox: the file runs with the user's own
permissions and could read their files. That is why it needs the explicit "Run this file on my computer" consent.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from pydantic import ValidationError

from sandbox import policy
from src.agents.base import Agent, AgentView, CallRecord
from src.agents.schema import InvalidAgentOutputError, NegotiationAction, TransportError

ROOT = Path(__file__).resolve().parents[1]
RUNNER = Path(__file__).with_name("python_runner.py")
START_TIMEOUT_S = 20.0
MAX_REPLY_BYTES = 256 * 1024
# What Python itself needs to start on Windows, macOS and Linux. Nothing else is passed on, so no key can be.
ENV_ALLOW = ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE",
             "LANG", "LC_ALL", "LC_CTYPE", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "VIRTUAL_ENV")


def child_env() -> dict:
    env = {k: os.environ[k] for k in ENV_ALLOW if k in os.environ}
    # This process's import path: the app's packages, and pydantic wherever it was installed (venv, user site).
    env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys([str(ROOT), *(p for p in sys.path if p)]))
    env["PYTHONIOENCODING"], env["PYTHONDONTWRITEBYTECODE"] = "utf-8", "1"
    return env


def view_message(view: AgentView) -> dict:
    return {"role": view.role, "resource_pool": view.resource_pool, "own_valuation": view.own_valuation,
            "round_number": view.round_number, "rounds_remaining": view.rounds_remaining, "max_rounds": view.max_rounds,
            "notices": list(getattr(view, "notices", ())),
            "transcript": [{"turn": t.turn_number, "actor": t.actor, "action": t.action.model_dump()} for t in view.transcript_so_far]}


class PythonFileAgent(Agent):
    def __init__(self, name: str, source: str, class_name: str, timeout_s: float = 10.0):
        policy.require_local("Running a Python file")
        self.name, self.source, self.class_name, self.timeout_s = name, source, class_name, timeout_s
        self._proc, self._dir = None, None

    async def _start(self) -> None:
        self._dir = tempfile.mkdtemp(prefix="cooperation-lab-agent-")
        path = Path(self._dir) / "user_agent.py"
        path.write_text(self.source, encoding="utf-8")
        self._proc = await asyncio.create_subprocess_exec(
            sys.executable, str(RUNNER), str(path), self.class_name, env=child_env(), cwd=self._dir,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, limit=MAX_REPLY_BYTES)
        await self._read(START_TIMEOUT_S)

    async def _read(self, timeout: float) -> dict:
        try:
            line = await asyncio.wait_for(self._proc.stdout.readline(), timeout)
        except asyncio.TimeoutError:
            await self.close()
            raise TransportError(f"the Python file did not answer within {timeout:g} s; it was stopped") from None
        except ValueError:  # one line longer than the limit
            await self.close()
            raise InvalidAgentOutputError("the Python file's reply is too long") from None
        if not line:
            await self.close()
            raise TransportError("the Python file stopped running")
        try:
            return json.loads(line)
        except ValueError:
            raise InvalidAgentOutputError("the Python file's reply could not be read") from None

    async def generate_response(self, view: AgentView) -> NegotiationAction:
        policy.require_local("Running a Python file")
        start = time.monotonic()
        self.last_call = None
        try:
            if self._proc is None:
                await self._start()
            self._proc.stdin.write((json.dumps(view_message(view)) + "\n").encode())
            await self._proc.stdin.drain()
            reply = await self._read(self.timeout_s)
            if "invalid" in reply:
                raise InvalidAgentOutputError(reply["invalid"], reply.get("reason", "malformed_output"))
            try:
                action = NegotiationAction(**reply["action"])
            except (KeyError, TypeError, ValidationError):
                raise InvalidAgentOutputError("the Python file's action does not match the action format") from None
        except (TransportError, InvalidAgentOutputError) as exc:
            self.last_call = CallRecord(model=self.name, latency_s=time.monotonic() - start, error=str(exc) if isinstance(exc, TransportError) else None)
            raise
        except (OSError, ConnectionError) as exc:
            self.last_call = CallRecord(model=self.name, latency_s=time.monotonic() - start, error=type(exc).__name__)
            raise TransportError(f"the Python file could not be reached ({type(exc).__name__})") from None
        self.last_call = CallRecord(model=self.name, latency_s=time.monotonic() - start)
        return action

    async def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                if proc.stdin:
                    proc.stdin.close()
                if proc.returncode is None:
                    proc.kill()
                await asyncio.wait_for(proc.wait(), 5)
            except (OSError, ProcessLookupError, asyncio.TimeoutError):
                pass
        if self._dir:
            shutil.rmtree(self._dir, ignore_errors=True)
            self._dir = None
