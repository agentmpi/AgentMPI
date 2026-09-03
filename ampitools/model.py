"""Model executors: a rank whose executor is a raw model API call.

Every earlier executor in this runtime assumed that an *agent host* --- a coding
assistant session, a headless CLI --- would eventually claim the task.  That
assumption is where every production run so far failed: the host capped
concurrent sessions at ten, the population was sixty-four, and the protocol spent
its time waiting for an executor to exist.  This module removes the host.  A rank
is an operating-system process; its executor is a chat-completions endpoint; its
only concurrency limit is the provider's rate limit, which is orders of magnitude
above anything a session host offers.

What a raw endpoint lacks, compared with an agent session, is a body: it cannot
read a file, search the web, or run a command.  So the executor carries a *minimal
agent harness* --- a tool loop in which the model may call a few host-side
functions before answering --- and that is the whole of it.  The tools are
supplied by the harness author, are ordinary Python callables, and are described
to the model in the OpenAI function-calling schema, which every provider behind an
OpenAI-compatible endpoint accepts.

Two properties of this executor matter to the protocol and are recorded in the
trace.

**Every invocation begins with an empty context.**  A session-hosted agent carries
its earlier tasks in its context window; a raw call carries only the prompt the
harness composed.  The context budget the harness manages is therefore *the
prompt*, and the provider reports its exact size in tokens, so for the first time
the runtime's context ledger can be checked against a measurement rather than an
estimate.

**Contract violations are repaired in-conversation.**  A result that fails its
contract is not thrown away and re-requested from scratch: the violation is
returned to the model as the next user turn, so the repair costs one more call
against a cached prefix rather than a fresh attempt.  The trace records every
repair, because a contract the model cannot satisfy on the first try is evidence
about the contract as much as about the model.

The executor emits four trace events, and the analysis package reads them exactly
as it reads the broker's ``claim``/``submit`` pair:

    task.start   rank, aid, label, worker, model
    task.tool    rank, aid, tool, seconds, chars
    task.retry   rank, aid, label, attempt, violations
    task.done    rank, aid, label, worker, model, attempts, calls, tool_calls,
                 prompt_tokens, completion_tokens, reasoning_tokens, cached_tokens,
                 cost_usd, seconds, result_tokens
    task.fail    rank, aid, label, error, attempts
"""

from __future__ import annotations

import json
import os
import random
import re
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ampi.core.payload import canonical, check_contract
from ampi.errors import AmpiError, err
from ampi.tokens import count_tokens

from .executor import Task

__all__ = [
    "ChatModel",
    "ChatResponse",
    "ModelError",
    "ModelExecutor",
    "Tool",
    "Usage",
    "extract_json",
    "worker_identity",
]

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
ENV_API_KEY = "OPENROUTER_API_KEY"
ENV_BASE_URL = "AMPI_MODEL_BASE_URL"
ENV_WORKER_ID = "AMPI_WORKER_ID"

#: HTTP statuses worth retrying.  A 4xx other than these is a defect in the
#: request and retrying it is a way of paying for the same error many times.
RETRY_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 520, 521, 522, 524})


def worker_identity() -> str:
    """Who is executing: the launcher's label, else host and pid.

    Provenance is the difference between a scale claim and an assertion: a run
    that says sixty-four ranks were served must be able to say by *what*, and a
    process id on a named machine is the cheapest fact that cannot be faked after
    the fact.
    """
    given = os.environ.get(ENV_WORKER_ID)
    if given:
        return given
    return f"proc:{socket.gethostname()}:{os.getpid()}"


# ---------------------------------------------------------------------------
# Usage accounting
# ---------------------------------------------------------------------------


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 0
    tool_calls: int = 0
    seconds: float = 0.0

    def add(self, other: Usage) -> Usage:
        for k in ("prompt_tokens", "completion_tokens", "reasoning_tokens", "cached_tokens",
                  "calls", "tool_calls"):
            setattr(self, k, getattr(self, k) + getattr(other, k))
        self.cost_usd += other.cost_usd
        self.seconds += other.seconds
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "cached_tokens": self.cached_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "calls": self.calls,
            "tool_calls": self.tool_calls,
            "seconds": round(self.seconds, 3),
        }

    @classmethod
    def from_response(cls, raw: dict[str, Any], seconds: float) -> Usage:
        u = raw.get("usage") or {}
        cd = u.get("completion_tokens_details") or {}
        pd = u.get("prompt_tokens_details") or {}
        return cls(
            prompt_tokens=int(u.get("prompt_tokens") or 0),
            completion_tokens=int(u.get("completion_tokens") or 0),
            reasoning_tokens=int(cd.get("reasoning_tokens") or 0),
            cached_tokens=int(pd.get("cached_tokens") or 0),
            cost_usd=float(u.get("cost") or 0.0),
            calls=1,
            seconds=seconds,
        )


@dataclass
class ChatResponse:
    content: str
    tool_calls: list[dict[str, Any]]
    finish_reason: str
    usage: Usage
    model: str
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @property
    def message(self) -> dict[str, Any]:
        """The assistant message, in the shape the next request must echo."""
        m: dict[str, Any] = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            m["tool_calls"] = self.tool_calls
        return m


class ModelError(Exception):
    """The endpoint could not produce a completion after every retry."""

    def __init__(self, message: str, *, status: int | None = None, retryable: bool = False,
                 retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.retryable = retryable
        self.retry_after = retry_after


# ---------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------


class ChatModel:
    """One model behind an OpenAI-compatible chat-completions endpoint.

    Standard library only.  The whole runtime has no third-party dependency and
    an executor that needed one would be the first thing a harness author had to
    install before the protocol worked; ``urllib`` reads the proxy and CA
    environment the way ``curl`` does, which is what a sandbox provides.

    ``transport`` replaces the network for tests: a callable taking the request
    body and returning the response body, so the executor's loop --- tool calls,
    contract repair, retry --- can be exercised without a provider.
    """

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_s: float = 600.0,
        max_retries: int = 6,
        rate_limit_patience_s: float = 1800.0,
        reasoning: dict[str, Any] | None = None,
        plugins: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        provider: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        transport: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.model = model
        self.api_key = api_key or os.environ.get(ENV_API_KEY, "")
        self.base_url = (base_url or os.environ.get(ENV_BASE_URL) or DEFAULT_BASE_URL).rstrip("/")
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        #: How long to keep waiting on a rate limit before giving the task up.
        #: A 429 is not a failure of the task and must not become a failure of
        #: the rank: the first p=16 production run lost six ranks to a
        #: twenty-requests-a-minute cap because rate limits were retried like
        #: transport faults, six times and then never.  A rank blocked on a
        #: rate limit is exactly like a rank blocked in a collective --- alive,
        #: waiting, and not to be convicted --- so the wait is bounded by
        #: patience, not by a retry count.
        self.rate_limit_patience_s = rate_limit_patience_s
        self.rate_limited_s = 0.0
        self.reasoning = reasoning
        self.plugins = plugins
        self.temperature = temperature
        self.provider = provider
        self.extra = extra or {}
        self.headers = headers or {}
        self.transport = transport
        self.usage = Usage()
        self.retries = 0

    def describe(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "base_url": self.base_url,
            "reasoning": self.reasoning,
            "plugins": self.plugins,
            "temperature": self.temperature,
            "provider": self.provider,
        }

    # -- one call ------------------------------------------------------------
    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> ChatResponse:
        body: dict[str, Any] = {"model": self.model, "messages": messages,
                                "usage": {"include": True}}
        if tools:
            body["tools"] = tools
        if tool_choice:
            body["tool_choice"] = tool_choice
        if max_tokens:
            body["max_tokens"] = max_tokens
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if self.reasoning:
            body["reasoning"] = self.reasoning
        if self.plugins:
            body["plugins"] = self.plugins
        if self.provider:
            body["provider"] = self.provider
        body.update(self.extra)

        last: ModelError | None = None
        attempt = 0
        limited_since: float | None = None
        while True:
            started = time.time()
            try:
                raw = self._post(body)
                resp = self._parse(raw, time.time() - started)
                self.usage.add(resp.usage)
                return resp
            except ModelError as exc:
                last = exc
                if not exc.retryable:
                    raise
                if exc.status == 429:
                    # Wait it out, up to the patience budget, and count the wait.
                    limited_since = limited_since or time.time()
                    waited = time.time() - limited_since
                    if waited >= self.rate_limit_patience_s:
                        raise
                    pause = min(60.0, exc.retry_after or (5.0 + waited / 10.0)) * random.uniform(0.8, 1.4)
                    self.rate_limited_s += pause
                    self.retries += 1
                    time.sleep(pause)
                    continue
                if attempt >= self.max_retries:
                    raise
                attempt += 1
                self.retries += 1
                # Exponential backoff with jitter.  Two hundred ranks that all got
                # a 503 at the same instant and all retry at the same instant get
                # the same 503 again; the jitter is what separates them.
                time.sleep(min(90.0, (2 ** attempt) * random.uniform(0.8, 1.6)))
        raise last or ModelError("no attempts were made")  # pragma: no cover

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        if self.transport is not None:
            return self.transport(body)
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=data,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/agentmpi/AgentMPI",
                "X-Title": "AgentMPI",
                **self.headers,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                payload = r.read()
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace")[:500]
            after = exc.headers.get("Retry-After") if exc.headers else None
            try:
                retry_after = float(after) if after else None
            except ValueError:
                retry_after = None
            raise ModelError(f"HTTP {exc.code}: {text}", status=exc.code,
                             retryable=exc.code in RETRY_STATUSES, retry_after=retry_after) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            raise ModelError(f"transport: {exc}", retryable=True) from None
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            raise ModelError("the endpoint returned a body that is not JSON", retryable=True) from None

    @staticmethod
    def _parse(raw: dict[str, Any], seconds: float) -> ChatResponse:
        error = raw.get("error")
        if error:
            code = int(error.get("code") or 0) if isinstance(error, dict) else 0
            msg = error.get("message") if isinstance(error, dict) else str(error)
            raise ModelError(f"endpoint error {code}: {msg}", status=code or None,
                             retryable=code in RETRY_STATUSES or code >= 500)
        choices = raw.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            raise ModelError("the endpoint returned no choices", retryable=True)
        msg = choices[0].get("message") or {}
        content = msg.get("content") or ""
        if isinstance(content, list):  # some providers return content parts
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        calls = [c for c in (msg.get("tool_calls") or []) if isinstance(c, dict)]
        return ChatResponse(
            content=content,
            tool_calls=calls,
            finish_reason=str(choices[0].get("finish_reason") or ""),
            usage=Usage.from_response(raw, seconds),
            model=str(raw.get("model") or ""),
            raw=raw,
        )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@dataclass
class Tool:
    """A host-side function the model may call.

    ``fn`` receives the arguments the model supplied as keywords and returns a
    string --- or anything ``str()`` renders --- which is truncated to
    ``max_chars`` before it goes back into the conversation.  The truncation is a
    context-management decision made by the harness author, not the model: a
    fetched web page is not going to fit, and a tool that returns it whole makes
    the next call's prompt unbounded.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[..., Any]
    max_chars: int = 6000

    def spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def run(self, arguments: Any) -> str:
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError:
                return f"error: the arguments were not valid JSON: {arguments[:200]}"
        if not isinstance(arguments, dict):
            return "error: the arguments must be a JSON object"
        try:
            out = self.fn(**arguments)
        except TypeError as exc:
            return f"error: bad arguments for {self.name}: {exc}"
        except Exception as exc:  # noqa: BLE001 - a tool failure is data for the model
            return f"error: {type(exc).__name__}: {exc}"
        text = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False, default=str)
        if len(text) > self.max_chars:
            text = text[: self.max_chars] + f"\n…[truncated at {self.max_chars} characters]"
        return text


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> Any:
    """Find the JSON object a model wrapped in prose or a fence.

    Models are told to write only the object and often do; when they do not, the
    object is still there, and throwing away a correct translation because it
    arrived inside a markdown fence is the kind of loss a harness should not
    inflict on itself.
    """
    text = text.strip()
    if not text:
        raise ValueError("empty reply")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = _FENCE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    dec = json.JSONDecoder()
    if text[0] in "{[":
        # The reply *is* the object and it did not parse: it is truncated or
        # malformed.  Scanning inward would return the first complete nested
        # object --- one paragraph of a cut-off translation --- which satisfies
        # nothing and misleads every repair that follows.  Found in production.
        raise ValueError("the JSON object is truncated or malformed")
    for opener in ("{", "["):
        start = text.find(opener)
        while start != -1:
            try:
                value, _end = dec.raw_decode(text[start:])
                return value
            except json.JSONDecodeError:
                start = text.find(opener, start + 1)
    raise ValueError("no JSON object found in the reply")


# ---------------------------------------------------------------------------
# The executor
# ---------------------------------------------------------------------------


class ModelExecutor:
    """Invoke a task by calling a model, with a bounded tool loop and contract repair.

    Conforms to :class:`ampi.executor.Executor`: a harness that used the broker or
    a function executor uses this one without changing a line, which is the point
    of keeping the process manager out of the protocol.
    """

    kind = "model"

    def __init__(
        self,
        amp: Any,
        model: ChatModel,
        *,
        tools: list[Tool] | None = None,
        system: str = "",
        max_steps: int = 8,
        max_attempts: int = 3,
        max_tokens: int | None = 24_000,
        max_prompt_tokens: int = 60_000,
        worker_id: str | None = None,
        log_dir: str | Path | None = None,
        models: dict[str, ChatModel] | None = None,
        tools_for: Callable[[Task], list[Tool] | None] | None = None,
        json_mode: bool = True,
        fallback: ChatModel | None = None,
    ) -> None:
        self.amp = amp
        self.model = model
        self.tools = list(tools or [])
        self.system = system
        self.max_steps = max_steps
        self.max_attempts = max_attempts
        self.max_tokens = max_tokens
        #: The tool loop stops offering tools once the conversation has cost this
        #: many prompt tokens in total.  Every round re-sends the whole
        #: conversation, so an executor that keeps searching pays quadratically;
        #: in the first pilot one research task reached nine calls and 81,000
        #: prompt tokens before answering.  This is the budget that bounds it.
        self.max_prompt_tokens = max_prompt_tokens
        self.worker_id = worker_id or worker_identity()
        self.log_dir = Path(log_dir) if log_dir else None
        if self.log_dir:
            self.log_dir.mkdir(parents=True, exist_ok=True)
        #: Per-label-prefix model overrides: ``{"research": cheap_model}``.
        self.models = models or {}
        self.tools_for = tools_for
        self.json_mode = json_mode
        #: A second model to try, from a fresh conversation, when the first has
        #: exhausted its attempts without producing a conforming result.  A
        #: heterogeneous population can afford this and a homogeneous one cannot:
        #: in production one model degenerated into thousands of blank lines
        #: mid-object on the same paragraphs three times running, and repairing
        #: in-conversation only reproduced the degeneration.
        self.fallback = fallback
        self.usage = Usage()
        self.tasks = 0
        self.failures = 0
        self.fallbacks = 0

    def model_for(self, task: Task) -> ChatModel:
        for prefix, m in self.models.items():
            if task.label.startswith(prefix):
                return m
        return self.model

    # -- the loop --------------------------------------------------------------
    def invoke(self, task: Task) -> Any:
        try:
            return self._invoke(task, self.model_for(task))
        except AmpiError:
            if self.fallback is None or self.fallback.model == self.model_for(task).model:
                raise
            self.fallbacks += 1
            self.amp.trace("task.fallback", rank=task.rank, aid=task.aid, label=task.label,
                           model=self.fallback.model)
            return self._invoke(task, self.fallback)

    def _invoke(self, task: Task, model: ChatModel) -> Any:
        tools = self.tools_for(task) if self.tools_for else self.tools
        tools = list(tools or [])
        by_name = {t.name: t for t in tools}
        specs = [t.spec() for t in tools] or None
        json_wanted = self.json_mode and task.contract is not None and task.contract.kind == "json"

        self.amp.trace("task.start", rank=task.rank, aid=task.aid, label=task.label,
                       worker=self.worker_id, model=model.model,
                       prompt_tokens_est=count_tokens(task.prompt))
        if self.log_dir:
            (self.log_dir / f"{task.aid}.prompt.md").write_text(task.prompt, encoding="utf-8")

        messages: list[dict[str, Any]] = []
        if self.system:
            messages.append({"role": "system", "content": self.system})
        messages.append({"role": "user", "content": task.prompt})

        usage = Usage()
        started = time.time()
        attempts = 0
        steps = 0
        last_error = ""
        value: Any = None
        while attempts < self.max_attempts:
            attempts += 1
            try:
                text, used, finish = self._converse(model, messages, specs, by_name, task,
                                                    json_mode=json_wanted and not specs)
            except ModelError as exc:
                last_error = str(exc)
                usage.add(used_from(exc))
                break
            usage.add(used)
            steps += used.calls
            if task.contract is None or task.contract.kind != "json":
                value = text
            else:
                try:
                    value = extract_json(text)
                    if finish == "length":
                        raise ValueError("the reply was cut off at the output limit")
                except ValueError as exc:
                    last_error = f"the reply was not a JSON object ({exc})"
                    messages.append({"role": "assistant", "content": text})
                    fix = ("Your reply was cut off before the JSON object was complete. Reply "
                           "again with the whole object, and write it as compactly as the "
                           "task allows (no extra whitespace, no commentary)."
                           if finish == "length" else
                           f"Your reply could not be parsed: {exc}. Reply again with ONLY the "
                           "JSON object requested, no prose and no markdown fence.")
                    messages.append({"role": "user", "content": fix})
                    self.amp.trace("task.retry", rank=task.rank, aid=task.aid, label=task.label,
                                   attempt=attempts, violations=[last_error])
                    continue
            violations = check_contract(value, task.contract, subs={"rank": task.rank})
            if not violations:
                break
            last_error = "; ".join(violations)
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user", "content": self._repair_message(task, violations)})
            self.amp.trace("task.retry", rank=task.rank, aid=task.aid, label=task.label,
                           attempt=attempts, violations=violations[:6])
            value = None
        else:
            # Exhausted the attempts on a contract the model would not meet.
            value = None

        seconds = time.time() - started
        self.usage.add(usage)
        if self.log_dir:
            self._log_conversation(task, messages)
        if value is None and last_error:
            self.failures += 1
            self.amp.trace("task.fail", rank=task.rank, aid=task.aid, label=task.label,
                           worker=self.worker_id, error=last_error[:300], attempts=attempts,
                           seconds=round(seconds, 3), **_usage_fields(usage))
            raise err(
                "AMPI_ERR_OP_FAILED",
                f"executor could not complete {task.label!r} for rank {task.rank}: {last_error}",
                aid=task.aid, attempts=attempts,
            )
        self.tasks += 1
        result_tokens = count_tokens(canonical(value))
        if self.log_dir:
            (self.log_dir / f"{task.aid}.result.json").write_text(
                json.dumps(value, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        self.amp.trace("task.done", rank=task.rank, aid=task.aid, label=task.label,
                       worker=self.worker_id, model=model.model, attempts=attempts,
                       result_tokens=result_tokens, seconds=round(seconds, 3),
                       **_usage_fields(usage))
        return value

    def _converse(
        self,
        model: ChatModel,
        messages: list[dict[str, Any]],
        specs: list[dict[str, Any]] | None,
        by_name: dict[str, Tool],
        task: Task,
        *,
        json_mode: bool,
    ) -> tuple[str, Usage, str]:
        """Run the tool loop until the model answers in prose; return the answer."""
        used = Usage()
        nudged = False
        for step in range(self.max_steps + 2):
            exhausted = specs is not None and (
                step >= self.max_steps or used.prompt_tokens >= self.max_prompt_tokens)
            if exhausted and not nudged:
                # Withdrawing the tools silently leaves a model that wanted one
                # with nothing to say --- the empty replies of the first pilot.
                # Tell it, in its own turn, that the answer is due now.
                messages.append({"role": "user", "content": (
                    "You have used all the tool calls available for this task. Write "
                    "the JSON object now from what you have found; where something is "
                    "unsettled, say so in the relevant field rather than searching again.")})
                nudged = True
            resp = model.complete(
                messages,
                tools=None if exhausted else specs,
                tool_choice=None,
                max_tokens=self.max_tokens,
                json_mode=json_mode or (exhausted and task.contract is not None
                                        and task.contract.kind == "json"),
            )
            used.add(resp.usage)
            if not resp.tool_calls:
                return resp.content, used, resp.finish_reason
            if exhausted:  # a tool call after the tools were withdrawn: refuse it
                messages.append(resp.message)
                for call in resp.tool_calls:
                    messages.append({"role": "tool", "tool_call_id": call.get("id") or "x",
                                     "name": (call.get("function") or {}).get("name", ""),
                                     "content": "error: no further tool calls are available"})
                continue
            messages.append(resp.message)
            for call in resp.tool_calls:
                fn = call.get("function") or {}
                name = str(fn.get("name") or "")
                tool = by_name.get(name)
                t0 = time.time()
                out = tool.run(fn.get("arguments")) if tool else f"error: no tool named {name!r}"
                used.tool_calls += 1
                self.amp.trace("task.tool", rank=task.rank, aid=task.aid, tool=name,
                               seconds=round(time.time() - t0, 3), chars=len(out))
                messages.append({"role": "tool", "tool_call_id": call.get("id") or name,
                                 "name": name, "content": out})
        raise ModelError("the tool loop did not terminate")

    @staticmethod
    def _repair_message(task: Task, violations: list[str]) -> str:
        lines = "\n".join(f"- {v}" for v in violations[:8])
        hint = ""
        if task.contract and task.contract.max_tokens and any("exceeds" in v for v in violations):
            hint = (f"\nThe result must fit in {task.contract.max_tokens} tokens: shorten the "
                    "entries rather than dropping required fields.")
        return (
            "Your result does not satisfy its contract:\n" + lines + hint +
            "\nReply again with ONLY the corrected JSON object."
        )

    def _log_conversation(self, task: Task, messages: list[dict[str, Any]]) -> None:
        assert self.log_dir is not None
        with open(self.log_dir / f"{task.aid}.messages.jsonl", "w", encoding="utf-8") as fh:
            for m in messages:
                fh.write(json.dumps(m, ensure_ascii=False, default=str) + "\n")

    def stats(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "worker": self.worker_id,
            "model": self.model.describe(),
            "tasks": self.tasks,
            "failures": self.failures,
            "fallbacks": self.fallbacks,
            "retries": self.model.retries,
            "rate_limited_s": round(self.model.rate_limited_s, 1),
            "usage": self.usage.to_dict(),
        }


def used_from(exc: ModelError) -> Usage:
    """A failed exchange still cost the calls it made; keep the count honest."""
    return Usage(calls=0)


def _usage_fields(usage: Usage) -> dict[str, Any]:
    """Usage as trace fields.  ``seconds`` is the task's wall time and belongs to
    the event; the endpoint's own time goes under ``api_seconds``."""
    d = usage.to_dict()
    d["api_seconds"] = d.pop("seconds")
    return d
