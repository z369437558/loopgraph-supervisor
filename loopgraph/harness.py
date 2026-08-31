"""Harness abstraction (harness-neutral core, DSH-first default).

The supervisor only ever sees `complete(system, user) -> str`, so any LLM
backend plugs in unchanged. DSH (DeepSeek Harness) is the first-class default
when DEEPSEEK_API_KEY is present; the deterministic MockHarness keeps the whole
demo runnable offline with zero keys. Adding a backend = one subclass + one
registry line — no supervisor changes.
"""
import json
import os
import urllib.request


class Harness:
    name = "base"

    def complete(self, system: str, user: str) -> str:
        raise NotImplementedError


class OpenAICompatibleHarness(Harness):
    """Any chat-completions endpoint speaking the OpenAI wire format."""
    name = "openai-compatible"

    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def complete(self, system, user):
        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
        }).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"]


class DeepSeekHarness(OpenAICompatibleHarness):
    """DSH: DeepSeek chat completions (OpenAI-compatible wire format)."""
    name = "deepseek"

    def __init__(self):
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise SystemExit(
                "DEEPSEEK_API_KEY is not set. Export it, or use --harness mock.")
        super().__init__(
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            api_key=api_key,
            model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        )


class MockHarness(Harness):
    """Deterministic offline harness.

    First attempt returns a naive implementation; once the prompt carries
    verifier/human feedback it returns a corrected one. Exists so the full
    loop (fail -> feedback -> retry -> pass -> HITL -> promote) runs with no
    API key and is reproducible in CI.
    """
    name = "mock"

    NAIVE = 'def slugify(text):\n    return text.lower().replace(" ", "-")\n'
    FIXED = (
        "import re\n\n"
        "def slugify(text):\n"
        '    text = re.sub(r"[^a-z0-9]+", "-", text.lower())\n'
        '    return text.strip("-")\n'
    )

    def complete(self, system, user):
        code = self.FIXED if "FEEDBACK" in user else self.NAIVE
        return f"```python\n{code}```"


HARNESSES = {
    "deepseek": DeepSeekHarness,
    "mock": MockHarness,
}


def resolve_harness_name(name: str | None) -> str:
    """DSH-first policy: use DeepSeek when a key is available, else mock."""
    if name:
        if name not in HARNESSES:
            raise SystemExit(
                f"unknown harness '{name}'; available: {', '.join(sorted(HARNESSES))}")
        return name
    return "deepseek" if os.environ.get("DEEPSEEK_API_KEY") else "mock"


def make_harness(name: str) -> Harness:
    return HARNESSES[name]()
