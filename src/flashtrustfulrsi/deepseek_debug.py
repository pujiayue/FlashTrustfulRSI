"""Official DeepSeek Flash transport for development runs with a private ledger."""

import argparse
import asyncio
from contextlib import closing
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import time
import urllib.error
import urllib.request

from .transport import AmbiguousRequest, BudgetExceeded, ReplayConflict, TransportError

MODEL = "deepseek-flash"
BASE_URL = "https://api.deepseek.com"
CHAT_URL = BASE_URL + "/chat/completions"
MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class DebugBudget:
    requests: int = 4
    input_tokens: int = 64_000
    output_tokens: int = 1024
    per_call_output: int = 256

    def __post_init__(self):
        if any(type(value) is not int or value < 1 for value in asdict(self).values()):
            raise ValueError("Budget values must be positive integers")
        if self.per_call_output > min(self.output_tokens, 32768):
            raise ValueError("Per-call output cap exceeds the development limit")


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class DeepSeekProvider:
    """One development job per private run directory, with explicit call IDs."""

    endpoint = CHAT_URL

    def __init__(self, run_dir, job_id, budget=DebugBudget(), timeout=60):
        if not isinstance(job_id, str) or not job_id.strip() or len(job_id) > 200:
            raise ValueError("A stable job ID of at most 200 characters is required")
        if type(timeout) is not int or not 1 <= timeout <= 300:
            raise ValueError("Timeout must be an integer from 1 to 300 seconds")
        self.job_id = job_id
        self.budget = budget
        self.timeout = timeout
        self.model = MODEL
        root = Path(run_dir)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._require_private(root)
        self.db = root / "deepseek-debug.sqlite3"
        try:
            fd = os.open(self.db, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        self._require_private(self.db)
        settings = _json({"job_id": job_id, "model": MODEL, "endpoint": self.endpoint,
                          "budget": asdict(budget), "timeout": timeout, "schema": 1, "scope": "dev-dsflash"})
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("""CREATE TABLE IF NOT EXISTS calls (
                call_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, request TEXT NOT NULL,
                status TEXT NOT NULL, input_reserved INTEGER NOT NULL, output_reserved INTEGER NOT NULL,
                input_reported INTEGER, output_reported INTEGER, response TEXT, error TEXT,
                started REAL NOT NULL, finished REAL)""")
            row = db.execute("SELECT value FROM settings WHERE id=1").fetchone()
            if row is not None and row[0] != settings:
                raise ReplayConflict("Run identity or budget changed; choose a new run directory")
            db.execute("INSERT OR IGNORE INTO settings VALUES (1, ?)", (settings,))

    @staticmethod
    def _require_private(path):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("The ledger and run directory must be private and owned by the current user")

    def _connect(self):
        db = sqlite3.connect(self.db, timeout=30)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
        except BaseException:
            db.close()
            raise
        return db

    def complete_sync(self, messages, *, max_tokens=256, request_id=None, **options):
        body = {**options, "model": MODEL, "messages": messages, "max_tokens": max_tokens}
        return self.invoke("chat", body, request_id=request_id)

    async def complete(self, messages, **kwargs):
        return await asyncio.to_thread(self.complete_sync, messages, **kwargs)

    def invoke(self, protocol, body, request_id=None):
        if protocol != "chat" or body.get("model") != MODEL or body.get("stream", False) is not False:
            raise ValueError("Development transport requires non-streaming deepseek-flash Chat Completions")
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 240:
            raise ValueError("A stable call ID of at most 240 characters is required")
        if {"max_output_tokens", "max_completion_tokens"} & body.keys():
            raise ValueError("Use max_tokens for Chat Completions")
        cap = body.get("max_tokens")
        if type(cap) is not int or not 1 <= cap <= self.budget.per_call_output:
            raise BudgetExceeded("Explicit max_tokens exceeds the per-call output budget")
        if type(body.get("n", 1)) is not int or body.get("n", 1) != 1:
            raise ValueError("One completion per request is required")
        if not isinstance(body.get("messages"), list) or not body["messages"]:
            raise ValueError("At least one chat message is required")
        if any(not isinstance(tool, dict) or tool.get("type") != "function" for tool in body.get("tools", [])):
            raise ValueError("Only function tools are supported")
        for message in body["messages"]:
            if not isinstance(message, dict) or not isinstance(message.get("content"), (str, type(None))):
                raise ValueError("Development transport accepts text and function tool messages only")
        return self._invoke_body(body, request_id)

    def _invoke_body(self, body, request_id):
        cap = body['max_tokens']
        payload = _json({"stream": False, "thinking": {"type": "disabled"}, **body})
        data = payload.encode()
        if len(data) > MAX_REQUEST_BYTES:
            raise ValueError("Request exceeds the payload size limit")
        digest = hashlib.sha256(self.endpoint.encode() + b"\0" + data).hexdigest()
        # UTF-8 bytes plus framing allowance. Actual vendor usage is recorded separately.
        reserve = len(data) + 4096
        with closing(self._connect()) as db:
            with db:
                db.execute("BEGIN IMMEDIATE")
                old = db.execute("SELECT request_hash,status,response FROM calls WHERE call_id=?",
                                 (request_id,)).fetchone()
                if old is not None:
                    if old[0] != digest:
                        raise ReplayConflict("Call ID refers to a different request")
                    if old[1] == "success":
                        return json.loads(old[2])
                    if old[1] in {"pending", "transport_unknown"}:
                        raise AmbiguousRequest("Reconcile the original attempt before retrying")
                    raise TransportError("Call already finished with a failure; preserve the original attempt")
                usage = _totals(db)
                if (usage["requests"] + 1 > self.budget.requests
                        or usage["accounted_input"] + reserve > self.budget.input_tokens
                        or usage["accounted_output"] + cap > self.budget.output_tokens):
                    raise BudgetExceeded("Insufficient remaining budget for this request reservation")
                key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
                if not key:
                    raise TransportError("DEEPSEEK_API_KEY is missing in the host environment")
                db.execute("""INSERT INTO calls
                    (call_id,request_hash,request,status,input_reserved,output_reserved,started)
                    VALUES (?,?,?,'pending',?,?,?)""", (request_id, digest, payload, reserve, cap, time.time()))
            response_json = None
            reported = (None, None)
            status = "transport_unknown"
            error = None
            try:
                response = self._send(data, key)
                response_json = _json(response)
                reported = self._usage(response)
                self._validate(response)
                self._validate_tools(body,response)
                status = "success"
                return response
            except urllib.error.HTTPError as exc:
                status = "http_error"
                error = f"HTTP {exc.code}"
                exc.close()
                raise TransportError(error) from None
            except (OSError, urllib.error.URLError) as exc:
                error = type(exc).__name__
                raise AmbiguousRequest("Transport outcome unknown; no automatic retry") from None
            except (ValueError, KeyError, TypeError, IndexError) as exc:
                status = "invalid_response"
                error = type(exc).__name__+': '+str(exc)[:300]
                raise TransportError("Invalid or incomplete DeepSeek response") from None
            finally:
                with db:
                    db.execute("""UPDATE calls SET status=?,input_reported=?,output_reported=?,
                        response=?,error=?,finished=? WHERE call_id=?""",
                               (status, *reported, response_json,
                                error, time.time(), request_id))

    def _send(self, data, key):
        request = urllib.request.Request(self.endpoint, data=data, headers={
            "Authorization": "Bearer " + key, "Content-Type": "application/json",
        }, method="POST")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        with opener.open(request, timeout=self.timeout) as response:
            payload = response.read(MAX_RESPONSE_BYTES + 1)
        if len(payload) > MAX_RESPONSE_BYTES:
            raise ValueError("Response exceeds the development payload limit")
        return json.loads(payload)

    def summary(self):
        return ledger_status(self.db.parent)

    def _usage(self, response):
        return _reported_usage(response)

    def _validate(self, response):
        _validate_response(response)

    def _validate_tools(self,body,response):
        allowed={tool['function']['name'] for tool in body.get('tools',[])}
        calls=response['choices'][0]['message'].get('tool_calls') or []
        choice=body.get('tool_choice')
        forced=choice.get('function',{}).get('name') if isinstance(choice,dict) else None
        for call in calls:
            name=call['function']['name']
            if name not in allowed or (forced and name!=forced) or choice=='none':
                raise ValueError('Provider returned a tool outside the declared call contract')
            if not isinstance(json.loads(call['function']['arguments']),dict):
                raise ValueError('Function arguments must be a JSON object')


def _reported_usage(response):
    usage = response.get("usage") if isinstance(response, dict) else None
    if not isinstance(usage, dict):
        return None, None
    values = tuple(usage.get(name) for name in ("prompt_tokens", "completion_tokens"))
    if all(type(value) is int and value >= 0 for value in values):
        return values
    return None, None


def _validate_response(response):
    if not isinstance(response, dict) or response.get("model") != MODEL or not response.get("id"):
        raise ValueError("Unexpected response identity")
    choice = response["choices"][0]
    message = choice["message"]
    if choice["finish_reason"] == "stop" and isinstance(message.get("content"), str) and message["content"].strip():
        return
    if choice["finish_reason"] == "tool_calls" and isinstance(message.get("tool_calls"), list) and message["tool_calls"]:
        return
    raise ValueError("Empty or truncated completion")


def _totals(db):
    row = db.execute("""SELECT count(*),coalesce(sum(input_reported),0),coalesce(sum(output_reported),0),
        coalesce(sum(coalesce(input_reported,input_reserved)),0),
        coalesce(sum(coalesce(output_reported,output_reserved)),0),
        coalesce(sum(input_reported IS NULL),0) FROM calls""").fetchone()
    keys = ("requests", "reported_input", "reported_output", "accounted_input", "accounted_output", "usage_unknown_calls")
    return dict(zip(keys, row))


def _response_id(encoded):
    response = json.loads(encoded) if encoded else None
    return response.get("id") if isinstance(response, dict) else None


def ledger_status(run_dir):
    path = Path(run_dir) / "deepseek-debug.sqlite3"
    DeepSeekProvider._require_private(path.parent)
    DeepSeekProvider._require_private(path)
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        settings = json.loads(db.execute("SELECT value FROM settings WHERE id=1").fetchone()[0])
        calls = [{"call_id": call_id, "state": status, "response_id": _response_id(response),
                  "error": error} for call_id, status, response, error in db.execute(
                      "SELECT call_id,status,response,error FROM calls ORDER BY started")]
        return {**settings, **_totals(db), "calls": calls}


def preflight(provider):
    plain = provider.complete_sync([{"role": "user", "content": "Reply with exactly PROBE_OK."}],
                                   max_tokens=64, request_id="plain")
    if plain["choices"][0]["message"]["content"].strip() != "PROBE_OK":
        raise ValueError("Plain response failed the sentinel check")
    structured = provider.complete_sync(
        [{"role": "system", "content": "Return a JSON object only."},
         {"role": "user", "content": 'Return exactly this JSON object: {"ok":true}'}],
        response_format={"type": "json_object"}, max_tokens=128, request_id="json")
    parsed = json.loads(structured["choices"][0]["message"]["content"])
    if not isinstance(parsed, dict) or set(parsed) != {"ok"} or parsed["ok"] is not True:
        raise ValueError("JSON response failed the contract")
    tools = [{"type": "function", "function": {"name": "echo_probe", "description": "Echo a probe value",
              "parameters": {"type": "object", "properties": {"value": {"type": "string"}},
                             "required": ["value"]}}}]
    messages = [{"role": "user", "content": "Call echo_probe with value PROBE_OK. After the tool replies, output only its value."}]
    response = provider.complete_sync(messages, tools=tools, tool_choice={"type": "function", "function": {"name": "echo_probe"}},
                                      max_tokens=128, request_id="tool")
    message = response["choices"][0]["message"]
    calls = message["tool_calls"]
    if (len(calls) != 1 or not calls[0].get("id") or calls[0]["function"]["name"] != "echo_probe"
            or json.loads(calls[0]["function"]["arguments"]) != {"value": "PROBE_OK"}):
        raise ValueError("Tool call failed the contract")
    messages.extend([{"role": "assistant", "content": message.get("content"), "tool_calls": calls},
                     {"role": "tool", "tool_call_id": calls[0]["id"], "content": "PROBE_OK"}])
    final = provider.complete_sync(messages, tools=tools, tool_choice="none", max_tokens=64,
                                   request_id="tool-followup")
    if final["choices"][0]["message"]["content"].strip() != "PROBE_OK":
        raise ValueError("Tool result round trip failed the sentinel check")
    report = provider.summary()
    if report["usage_unknown_calls"]:
        raise ValueError("Preflight responses did not include complete usage")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("check", help="Offline configuration and credential-presence check")
    live = sub.add_parser("preflight", help="Check plain, JSON, and tool round-trip responses")
    live.add_argument("--live", action="store_true", required=True, help="Enable up to four real requests")
    live.add_argument("--run-dir", required=True, type=Path)
    live.add_argument("--job-id", required=True)
    status = sub.add_parser("status", help="Read only the ledger summary and request status")
    status.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == "check":
            report = {"mode": "offline", "endpoint": CHAT_URL, "model": MODEL,
                      "api_key_present": bool(os.environ.get("DEEPSEEK_API_KEY", "").strip()),
                      "budget": asdict(DebugBudget()), "network_requests": 0}
        elif args.action == "status":
            report = ledger_status(args.run_dir)
        else:
            report = preflight(DeepSeekProvider(args.run_dir, args.job_id))
            report["preflight"] = "passed"
    except (TransportError, ValueError, OSError, sqlite3.Error, KeyError, TypeError) as exc:
        parser.exit(1, _json({"status": "failed", "error_type": type(exc).__name__}) + "\n")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
