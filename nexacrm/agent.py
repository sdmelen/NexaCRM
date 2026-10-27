"""Один запрос оператора: retrieval, затем ответ с проверенными ID источников."""

from __future__ import annotations

from dataclasses import dataclass

from nexacrm.config import AppConfig
from nexacrm.llm import GroundedAnswer, OpenAIAnswerProvider, build_answer
from nexacrm.retrieval import RetrievalEngine, RetrievalResult
from nexacrm.retrieval_openai import OpenAIRetrievalProvider


@dataclass(frozen=True)
class AgentReply:
    retrieval: RetrievalResult
    grounded: GroundedAnswer

    def as_dict(self) -> dict:
        return {"retrieval": self.retrieval.diagnostic(), "response": self.grounded.as_dict()}


class SupportAgent:
    def __init__(self, config: AppConfig):
        self.config = config
        self.retrieval_provider = OpenAIRetrievalProvider(config)
        self.answer_provider = OpenAIAnswerProvider(config)

    def answer(self, question: str, history: list[dict[str, str]]) -> AgentReply:
        with RetrievalEngine(self.config) as engine:
            retrieval = engine.search(
                question, history,
                self.retrieval_provider.embed,
                self.retrieval_provider.rewrite,
            )
        grounded = build_answer(
            retrieval, history, self.answer_provider.respond, self.config.max_answer_chars
        )
        return AgentReply(retrieval, grounded)
