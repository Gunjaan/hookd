"""Reply engine and brain learner on a low cost Gemini text model, until Claude replaces them."""

from django.conf import settings

from apps.agent.engines import prompts
from apps.agent.constants import FactScope
from apps.agent.engines.base import AgentContext, AgentDecision, BrainLearner, LearnedFactDraft, ReplyEngine
from apps.core.gemini import generate_json

MAX_OUTPUT_TOKENS = 400
LEARN_MAX_OUTPUT_TOKENS = 1200
LABEL_MAX_LENGTH = 80
FACT_MAX_LENGTH = 300


def _confidence(value) -> float:
    try:
        return min(max(float(value), 0.0), 1.0)
    except (TypeError, ValueError):
        return 0.0


class GeminiReplyEngine(ReplyEngine):
    name = "gemini"

    def decide(self, context: AgentContext) -> AgentDecision:
        data = generate_json(
            settings.AGENT_REPLY_MODEL,
            [prompts.reply_request(context)],
            system=prompts.REPLY_SYSTEM,
            schema=prompts.REPLY_SCHEMA,
            max_output_tokens=MAX_OUTPUT_TOKENS,
        ).data
        return AgentDecision(
            intent=str(data.get("intent", "")) or "comment",
            language=str(data.get("language", "")) or "unknown",
            reply_text=str(data.get("reply_text", "")).strip(),
            confidence=_confidence(data.get("confidence")),
            reason=str(data.get("reason", "")),
        )


def _facts(data: dict) -> list[LearnedFactDraft]:
    """Well formed facts only: a known scope, a label and a text, cut to the model's field lengths."""
    facts = []
    for item in data.get("facts") or []:
        if not isinstance(item, dict):
            continue
        label = " ".join(str(item.get("label", "")).split())[:LABEL_MAX_LENGTH]
        text = " ".join(str(item.get("text", "")).split())[:FACT_MAX_LENGTH]
        scope = item.get("scope") if item.get("scope") in FactScope.values else FactScope.POST
        if label and text:
            facts.append(LearnedFactDraft(scope=scope, label=label, text=text))
    return facts


class GeminiBrainLearner(BrainLearner):
    name = "gemini"

    def _ask(self, system: str, request: str) -> list[LearnedFactDraft]:
        data = generate_json(
            settings.AGENT_REPLY_MODEL,
            [request],
            system=system,
            schema=prompts.LEARN_SCHEMA,
            max_output_tokens=LEARN_MAX_OUTPUT_TOKENS,
        ).data
        return _facts(data)

    def from_caption(self, caption: str) -> list[LearnedFactDraft]:
        return self._ask(prompts.LEARN_CAPTION_SYSTEM, caption.strip()[: prompts.CAPTION_MAX_LENGTH])

    def from_reply(self, *, question: str, answer: str, post_caption: str) -> list[LearnedFactDraft]:
        return self._ask(
            prompts.LEARN_REPLY_SYSTEM,
            prompts.learn_reply_request(question=question, answer=answer, post_caption=post_caption),
        )
