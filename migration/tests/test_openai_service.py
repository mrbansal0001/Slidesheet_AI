import logging

from app.services.openai_service import OpenAIService, _EMBED_BATCH_SIZE


class _FakeEmbeddingItem:
    def __init__(self, embedding):
        self.embedding = embedding


class _FakeUsage:
    def __init__(self, total_tokens):
        self.total_tokens = total_tokens


class _FakeEmbeddingResponse:
    def __init__(self, embeddings, total_tokens=0):
        self.data = [_FakeEmbeddingItem(embedding) for embedding in embeddings]
        self.usage = _FakeUsage(total_tokens)


def test_embed_returns_empty_list_for_no_texts():
    service = OpenAIService()
    assert service.embed([]) == []


def test_embed_sends_single_batch_when_under_limit():
    service = OpenAIService()
    calls = []

    def fake_create(*, model, input):
        calls.append(list(input))
        return _FakeEmbeddingResponse([[float(len(text))] for text in input])

    service.client.embeddings.create = fake_create

    texts = ["alpha", "beta", "gamma"]
    embeddings = service.embed(texts)

    assert len(calls) == 1
    assert embeddings == [[5.0], [4.0], [5.0]]


def test_embed_splits_large_input_into_batches_preserving_order():
    service = OpenAIService()
    calls = []

    def fake_create(*, model, input):
        calls.append(list(input))
        return _FakeEmbeddingResponse([[float(index)] for index, _ in enumerate(input)])

    service.client.embeddings.create = fake_create

    texts = [f"chunk-{i}" for i in range(_EMBED_BATCH_SIZE + 10)]
    embeddings = service.embed(texts)

    assert len(calls) == 2
    assert len(calls[0]) == _EMBED_BATCH_SIZE
    assert len(calls[1]) == 10
    assert len(embeddings) == len(texts)


def test_embed_logs_token_usage_and_organization_id(caplog):
    service = OpenAIService()
    service.client.embeddings.create = lambda *, model, input: _FakeEmbeddingResponse(
        [[1.0] for _ in input], total_tokens=len(input) * 3
    )

    with caplog.at_level(logging.INFO, logger="app.services.openai_service"):
        service.embed(["a", "b"], organization_id="org-abc")

    record = next(r for r in caplog.records if r.message == "openai_embed")
    assert record.organization_id == "org-abc"
    assert record.total_tokens == 6
    assert record.input_count == 2
    assert record.estimated_cost_usd is None  # pricing settings default to 0 (disabled)


class _FakeChatUsage:
    def __init__(self, prompt_tokens, completion_tokens):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _FakeChatResponse:
    def __init__(self, content, prompt_tokens, completion_tokens):
        message = type("_Message", (), {"content": content})()
        choice = type("_Choice", (), {"message": message})()
        self.choices = [choice]
        self.usage = _FakeChatUsage(prompt_tokens, completion_tokens)


def test_answer_returns_content_and_logs_usage(caplog):
    service = OpenAIService()
    service.client.chat.completions.create = lambda **_kwargs: _FakeChatResponse(
        "The answer.", prompt_tokens=42, completion_tokens=7
    )

    with caplog.at_level(logging.INFO, logger="app.services.openai_service"):
        answer = service.answer("What is X?", "some context", organization_id="org-123")

    assert answer == "The answer."
    record = next(r for r in caplog.records if r.message == "openai_chat")
    assert record.organization_id == "org-123"
    assert record.prompt_tokens == 42
    assert record.completion_tokens == 7
    assert record.total_tokens == 49


class _Candidate:
    def __init__(self, text):
        self.text = text


def test_rerank_returns_identity_order_when_at_or_under_top_n():
    service = OpenAIService()
    candidates = [_Candidate("a"), _Candidate("b")]

    # No API call should even be attempted — len(candidates) <= top_n short-circuits.
    service.client.chat.completions.create = None

    assert service.rerank("q", candidates, top_n=2) == [0, 1]
    assert service.rerank("q", candidates, top_n=5) == [0, 1]


def test_rerank_parses_model_output_and_reorders():
    service = OpenAIService()
    candidates = [_Candidate("alpha"), _Candidate("beta"), _Candidate("gamma")]
    service.client.chat.completions.create = lambda **_kwargs: _FakeChatResponse(
        "[3, 1]", prompt_tokens=10, completion_tokens=2
    )

    order = service.rerank("q", candidates, top_n=2, organization_id="org-1")

    assert order == [2, 0]  # 1-based [3, 1] -> 0-based [2, 0]


def test_rerank_falls_back_to_original_order_on_unparseable_response(caplog):
    service = OpenAIService()
    candidates = [_Candidate("alpha"), _Candidate("beta"), _Candidate("gamma")]
    service.client.chat.completions.create = lambda **_kwargs: _FakeChatResponse(
        "I refuse to answer in JSON.", prompt_tokens=10, completion_tokens=2
    )

    with caplog.at_level(logging.WARNING, logger="app.services.openai_service"):
        order = service.rerank("q", candidates, top_n=2)

    assert order == [0, 1]
    assert any(r.message == "openai_rerank_parse_failed" for r in caplog.records)


def test_rerank_ignores_out_of_range_and_duplicate_indices():
    service = OpenAIService()
    candidates = [_Candidate("alpha"), _Candidate("beta"), _Candidate("gamma")]
    # 99 is out of range, 1 is duplicated — both should be dropped, not crash.
    service.client.chat.completions.create = lambda **_kwargs: _FakeChatResponse(
        "[99, 1, 1, 2]", prompt_tokens=10, completion_tokens=2
    )

    order = service.rerank("q", candidates, top_n=2)

    assert order == [0, 1]
