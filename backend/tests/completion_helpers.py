"""In-memory provider responses shared without importing a test suite."""

from openai.types.chat import ChatCompletion


def completion(*, empty: bool = False) -> ChatCompletion:
    return ChatCompletion.model_validate(
        {
            "id": "cmpl-test",
            "object": "chat.completion",
            "created": 0,
            "model": "muse-spark-1.3-contributor",
            "choices": []
            if empty
            else [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "VERDICT: accept"},
                }
            ],
            "usage": {
                "prompt_tokens": 700,
                "completion_tokens": 100,
                "total_tokens": 800,
                "prompt_tokens_details": {"cached_tokens": 600},
            },
        }
    )
