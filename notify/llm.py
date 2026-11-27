"""Optional Chat Completions HTTP adapter. The CLI opts in explicitly; no SDK needed."""


class ChatCompletionClient:
    def __init__(self, url, model, api_key, *, timeout, max_tokens, client=None):
        if not url or not model or not api_key:
            raise ValueError("LLM URL, model and key are required")
        self.url, self.model, self.api_key = url, model, api_key
        self.timeout, self.max_tokens, self.client = timeout, max_tokens, client

    def complete(self, candidates):
        import httpx
        import json

        payload = {"model": self.model, "messages": [
            {"role": "system", "content": "알림 후보에서 한 개를 고르세요. 문구·숫자를 수정하지 마세요. JSON 객체로 message와 source_id만 반환하세요. 진단 확정·접수·전달·발송을 주장하지 마세요."},
            {"role": "user", "content": json.dumps({"candidates": candidates}, ensure_ascii=False)}],
            "max_completion_tokens": self.max_tokens, "store": False}
        # A caller-supplied transport makes real HTTP error/response handling testable.
        def call(client):
            response = client.post(self.url, headers={"Authorization": f"Bearer {self.api_key}"},
                                   json=payload, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
            choice = data["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise ValueError("LLM response was not completed")
            content = choice["message"]["content"]
            if not isinstance(content, str):
                raise ValueError("LLM response is not text")
            return content
        if self.client is not None:
            return call(self.client)
        with httpx.Client(follow_redirects=False) as client:
            return call(client)
