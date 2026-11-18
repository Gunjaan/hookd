"""Agent model and brain learner on a low cost Claude model (Haiku by default)."""

from django.conf import settings

from apps.agent.engines import prompts
from apps.agent.constants import FactScope
from apps.agent.engines.base import AgentModel, BrainLearner, LearnedFactDraft, ModelStep
from apps.ai.claude import generate_json, run_step

# Ceilings, not charges: they leave room for the short thinking that comes before the answer.
LEARN_MAX_OUTPUT_TOKENS = 4000
FILTER_MAX_OUTPUT_TOKENS = 300
LABEL_MAX_LENGTH = 80
FACT_MAX_LENGTH = 300


class ClaudeAgentModel(AgentModel):
    name = "claude"

    def step(self, *, system: str, tools: list[dict], messages: list[dict]) -> ModelStep:
        content, stop_reason = run_step(
            settings.AGENT_REPLY_MODEL,
            system=system,
            tools=tools,
            messages=messages,
            max_output_tokens=settings.AGENT_LOOP_LIMITS["MAX_OUTPUT_TOKENS"],
        )
        return ModelStep(content=content, stop_reason=stop_reason)

    def is_shop_message(self, text: str) -> bool:
        data = generate_json(
            settings.AGENT_REPLY_MODEL,
            text.strip()[: prompts.DM_FILTER_MAX_LENGTH],
            system=prompts.DM_FILTER_SYSTEM,
            schema=prompts.DM_FILTER_SCHEMA,
            max_output_tokens=FILTER_MAX_OUTPUT_TOKENS,
        )
        return bool(data.get("shop"))


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


class ClaudeBrainLearner(BrainLearner):
    name = "claude"

    def _ask(self, system: str, request: str) -> list[LearnedFactDraft]:
        data = generate_json(
            settings.AGENT_REPLY_MODEL,
            request,
            system=system,
            schema=prompts.LEARN_SCHEMA,
            max_output_tokens=LEARN_MAX_OUTPUT_TOKENS,
        )
        return _facts(data)

    def from_caption(self, caption: str) -> list[LearnedFactDraft]:
        return self._ask(prompts.LEARN_CAPTION_SYSTEM, caption.strip()[: prompts.CAPTION_MAX_LENGTH])

    def from_reply(self, *, question: str, answer: str, post_caption: str) -> list[LearnedFactDraft]:
        return self._ask(
            prompts.LEARN_REPLY_SYSTEM,
            prompts.learn_reply_request(question=question, answer=answer, post_caption=post_caption),
        )
