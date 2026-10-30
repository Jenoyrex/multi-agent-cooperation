"""Static inspection of an uploaded Python agent file.

The source is parsed with `ast` and read as a syntax tree. It is never imported,
compiled to bytecode or executed: this server has no isolated sandbox for untrusted
Python, so an uploaded agent can be registered and reviewed but cannot run here.
The report tells the user whether the file matches the engine's interface
(`src/agents/base.py`):

    class MyAgent(Agent):
        async def generate_response(self, view: AgentView) -> NegotiationAction: ...

and flags imports and calls that would need network, files, processes or providers.
"""
from __future__ import annotations

import ast
import hashlib
import re

from workbench.agents import WorkbenchError

MAX_SOURCE_BYTES = 48_000
FILENAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_. -]{0,80}\.py$")

# Imports an offline negotiation agent never needs. Network, processes, files, providers, dynamic code.
BLOCKED = {"os", "sys", "subprocess", "socket", "shutil", "ctypes", "multiprocessing", "threading", "signal",
           "http", "urllib", "urllib3", "requests", "httpx", "aiohttp", "websocket", "websockets", "ftplib", "smtplib",
           "telnetlib", "webbrowser", "anthropic", "openai", "google", "cohere", "mistralai", "ollama", "transformers",
           "pickle", "marshal", "shelve", "importlib", "builtins", "pathlib", "glob", "tempfile", "io", "code", "pty"}
SAFE = {"math", "random", "statistics", "itertools", "functools", "collections", "dataclasses", "typing", "json",
        "re", "enum", "heapq", "bisect", "fractions", "decimal", "operator", "string", "abc", "copy", "__future__"}
ENGINE = ("src.agents", "src.environment")  # the project's own interface modules
BLOCKED_CALLS = {"eval", "exec", "compile", "open", "__import__", "input", "breakpoint", "globals", "locals", "vars",
                 "setattr", "delattr", "getattr"}
ACTIONS = {"OFFER", "ACCEPT", "WALK_AWAY"}


def _issue(level: str, text: str, line: int | None = None) -> dict:
    return {"level": level, "text": text, "line": line}


def _doc(node) -> str:
    d = ast.get_docstring(node) or ""
    return d.strip().split("\n\n")[0].replace("\n", " ")[:300]


def inspect_source(filename, source) -> dict:
    """A compatibility report for one uploaded file. Raises WorkbenchError only for an unusable request."""
    if not isinstance(filename, str) or not FILENAME_RE.match(filename.strip()):
        raise WorkbenchError("Upload a Python file whose name ends in .py (letters, digits, '_', '-', '.', spaces).", "upload.filename")
    if not isinstance(source, str):
        raise WorkbenchError("The file contents must be text.", "upload.source")
    raw = source.encode("utf-8")
    if not raw.strip():
        raise WorkbenchError("The file is empty.", "upload.source")
    if len(raw) > MAX_SOURCE_BYTES:
        raise WorkbenchError(f"The file is {len(raw) // 1000} KB; the limit is {MAX_SOURCE_BYTES // 1000} KB. An agent is usually a few hundred lines.", "upload.source")
    base = {"filename": filename.strip(), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
            "lines": source.count("\n") + 1, "executed": False}
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        return {**base, "status": "invalid", "classes": [], "imports": [], "compatible_classes": [],
                "issues": [_issue("error", f"Python could not read this file: {exc.msg}.", exc.lineno)]}

    issues, imports = [], []
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [("." * node.level) + (node.module or "")]
        for full in names:
            top = full.lstrip(".").split(".")[0]
            verdict = ("engine" if full.startswith(ENGINE) else "relative" if full.startswith(".")
                       else "blocked" if top in BLOCKED else "standard" if top in SAFE else "unknown")
            imports.append({"module": full, "line": node.lineno, "verdict": verdict})
            if verdict == "blocked":
                issues.append(_issue("error", f"Imports “{full}”, which reaches the network, files, processes or a paid provider. "
                                               "Agents here negotiate offline from what the engine gives them.", node.lineno))
            elif verdict == "relative":
                issues.append(_issue("warning", f"Relative import “{full}”: only this one file is uploaded.", node.lineno))
            elif verdict == "unknown":
                issues.append(_issue("warning", f"Imports “{full}”, which is not part of the standard set this review recognises.", node.lineno))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in BLOCKED_CALLS:
            issues.append(_issue("error", f"Calls {node.func.id}(), which can run or reach arbitrary code.", node.lineno))

    classes = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        methods = {f.name: f for f in node.body if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))}
        gen = methods.get("generate_response")
        bases = [ast.unparse(b) for b in node.bases]
        actions = sorted({c.value for c in ast.walk(node) if isinstance(c, ast.Constant) and c.value in ACTIONS})
        problems = []
        if gen is None:
            problems.append("has no generate_response method")
        else:
            if not isinstance(gen, ast.AsyncFunctionDef):
                problems.append("generate_response must be async (async def)")
            if len(gen.args.args) != 2:
                problems.append("generate_response must take exactly (self, view)")
        classes.append({"name": node.name, "line": node.lineno, "bases": bases, "doc": _doc(node),
                        "methods": sorted(methods), "actions": actions, "subclasses_agent": "Agent" in bases,
                        "compatible": not problems, "problems": problems})

    compatible = [c["name"] for c in classes if c["compatible"]]
    for c in classes:
        if c["compatible"] and not c["subclasses_agent"]:
            issues.append(_issue("info", f"{c['name']} does not subclass Agent; it still matches the interface by shape.", c["line"]))
        if c["compatible"] and set(c["actions"]) != ACTIONS:
            missing = ", ".join(sorted(ACTIONS - set(c["actions"])))
            issues.append(_issue("info", f"{c['name']} never mentions {missing}; it may never use those actions.", c["line"]))
        if c["problems"] and "generate_response" in c["methods"]:
            issues.append(_issue("error", f"{c['name']}: {'; '.join(c['problems'])}.", c["line"]))
    if not compatible:
        issues.insert(0, _issue("error", "No class matches the agent interface: a class with "
                                         "“async def generate_response(self, view)” that returns a NegotiationAction."))
    errors = any(i["level"] == "error" for i in issues)
    status = "invalid" if not compatible else "needs-changes" if errors else "compatible"
    return {**base, "status": status, "classes": classes, "imports": imports, "compatible_classes": compatible, "issues": issues}
