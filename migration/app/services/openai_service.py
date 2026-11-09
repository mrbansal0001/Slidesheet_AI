from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor

from openai import OpenAI

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# The embeddings endpoint caps input array length (2048) and total tokens per request.
# Our chunks are ~1200 chars (~300 tokens), so 256 per batch stays comfortably under both.
_EMBED_BATCH_SIZE = 256
_MAX_CONCURRENT_BATCHES = 4


class OpenAIService:
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        self.settings = settings
        self.client = OpenAI(api_key=settings.openai_api_key, timeout=45.0, max_retries=2)

    def embed(self, texts: list[str], organization_id: str | None = None) -> list[list[float]]:
        if not texts:
            return []

        batches = [
            texts[i : i + _EMBED_BATCH_SIZE] for i in range(0, len(texts), _EMBED_BATCH_SIZE)
        ]
        started_at = time.perf_counter()
        if len(batches) == 1:
            embeddings, total_tokens = self._embed_batch(batches[0])
        else:
            with ThreadPoolExecutor(
                max_workers=min(len(batches), _MAX_CONCURRENT_BATCHES)
            ) as pool:
                batch_results = list(pool.map(self._embed_batch, batches))
            embeddings = [
                embedding for batch_embeddings, _ in batch_results for embedding in batch_embeddings
            ]
            total_tokens = sum(tokens for _, tokens in batch_results)

        self._log_usage(
            "openai_embed",
            organization_id=organization_id,
            duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
            input_count=len(texts),
            batch_count=len(batches),
            total_tokens=total_tokens,
            estimated_cost_usd=self._embedding_cost(total_tokens),
        )
        return embeddings

    def _embed_batch(self, batch: list[str]) -> tuple[list[list[float]], int]:
        response = self.client.embeddings.create(
            model=self.settings.openai_embedding_model,
            input=batch,
        )
        embeddings = [item.embedding for item in response.data]
        total_tokens = response.usage.total_tokens if response.usage else 0
        return embeddings, total_tokens

    def answer(self, question: str, context: str, organization_id: str | None = None) -> str:
        started_at = time.perf_counter()
        response = self.client.chat.completions.create(
            model=self.settings.openai_chat_model,
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Answer only from the supplied document evidence. "
                        "If the evidence is insufficient, say exactly what is missing. "
                        "Cite evidence inline using labels such as [1] and [2]. "
                        "Be concise and preserve important numbers."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Question:\n{question}\n\nEvidence:\n{context}",
                },
            ],
        )
        usage = response.usage
        prompt_tokens = usage.prompt_tokens if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0
        self._log_usage(
            "openai_chat",
            organization_id=organization_id,
            duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            estimated_cost_usd=self._chat_cost(prompt_tokens, completion_tokens),
        )
        return response.choices[0].message.content or "No answer was generated."

    def rerank(
        self,
        question: str,
        candidates: list,
        top_n: int,
        organization_id: str | None = None,
    ) -> list[int]:
        """Return up to `top_n` indices into `candidates` (each needs a `.text` attr), ordered
        most-to-least relevant to `question`.

        A "cheap LLM pass" re-ranker rather than a cross-encoder — avoids adding a new ML
        dependency (e.g. sentence-transformers/torch) for a project whose retrieval otherwise
        runs entirely inside Postgres + the OpenAI API it already calls. If the model's response
        can't be parsed, falls back to the candidates' original order rather than failing the
        chat flow — re-ranking is a quality improvement, not something that should ever be able
        to break an answer.
        """
        if len(candidates) <= top_n:
            return list(range(len(candidates)))

        started_at = time.perf_counter()
        numbered = "\n".join(
            f"[{index + 1}] {candidate.text[:400]}" for index, candidate in enumerate(candidates)
        )
        response = self.client.chat.completions.create(
            model=self.settings.openai_chat_model,
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Rank the numbered passages by relevance to the question. Return ONLY "
                        f"a JSON array of up to {top_n} passage numbers, most relevant first, "
                        "with no other text."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Question: {question}\n\nPassages:\n{numbered}",
                },
            ],
        )
        content = response.choices[0].message.content or ""
        usage = response.usage
        self._log_usage(
            "openai_rerank",
            organization_id=organization_id,
            duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
            candidate_count=len(candidates),
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
        )

        indices = self._parse_rerank_response(content, len(candidates))
        if indices is None:
            logger.warning(
                "openai_rerank_parse_failed",
                extra={"organization_id": organization_id, "raw_response": content[:500]},
            )
            return list(range(top_n))
        return indices[:top_n]

    @staticmethod
    def _parse_rerank_response(content: str, num_candidates: int) -> list[int] | None:
        match = re.search(r"\[[\d,\s]*\]", content)
        if not match:
            return None
        try:
            raw_numbers = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
        if not isinstance(raw_numbers, list):
            return None

        seen: set[int] = set()
        indices: list[int] = []
        for number in raw_numbers:
            if not isinstance(number, int):
                continue
            index = number - 1
            if 0 <= index < num_candidates and index not in seen:
                seen.add(index)
                indices.append(index)
        return indices or None

    def _embedding_cost(self, total_tokens: int) -> float | None:
        price = self.settings.openai_embedding_price_per_1k_tokens
        return round(total_tokens / 1000 * price, 6) if price else None

    def _chat_cost(self, prompt_tokens: int, completion_tokens: int) -> float | None:
        input_price = self.settings.openai_chat_input_price_per_1k_tokens
        output_price = self.settings.openai_chat_output_price_per_1k_tokens
        if not input_price and not output_price:
            return None
        cost = (prompt_tokens / 1000 * input_price) + (completion_tokens / 1000 * output_price)
        return round(cost, 6)

    def _log_usage(self, event: str, **fields) -> None:
        logger.info(event, extra=fields)

