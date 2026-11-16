"""Reply engine on a low cost Gemini text model, until the Claude engine replaces it."""

from django.conf import settings

from apps.agent.engines import prompts
from apps.agent.engines.base import AgentContext, AgentDecision, ReplyEngine
from apps.core.gemini import generate_json

MAX_OUTPUT_TOKENS = 400


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
        )
        return AgentDecision(
            intent=str(data.get("intent", "")) or "comment",
            language=str(data.get("language", "")) or "unknown",
            reply_text=str(data.get("reply_text", "")).strip(),
            confidence=_confidence(data.get("confidence")),
            reason=str(data.get("reason", "")),
        )
