"""Agent model and brain learner on a low cost Claude model (Haiku by default)."""

from django.conf import settings

from apps.agent.engines import prompts
from apps.agent.constants import FactScope
from apps.agent.engines.base import AgentModel, BrainLearner, LearnedFactDraft, ModelStep, PostLearning, PostReading
from apps.ai.claude import generate_json, image_block, run_step

# Ceilings, not charges: they leave room for the short thinking that comes before the answer.
LEARN_MAX_OUTPUT_TOKENS = 4000
FILTER_MAX_OUTPUT_TOKENS = 300
LABEL_MAX_LENGTH = 80
FACT_MAX_LENGTH = 300
BUYER_QUESTION_LIMIT = 5


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

    def _ask(self, system: str, content: str | list, schema: dict) -> dict:
        return generate_json(
            settings.AGENT_REPLY_MODEL, content, system=system, schema=schema, max_output_tokens=LEARN_MAX_OUTPUT_TOKENS
        )

    def from_post(self, post: PostReading) -> PostLearning:
        request = prompts.learn_post_request(caption=post.caption, comments=post.comments)
        content = [*(image_block(data, mime_type) for data, mime_type in post.images), {"type": "text", "text": request}]
        data = self._ask(prompts.LEARN_POST_SYSTEM, content, prompts.LEARN_POST_SCHEMA)
        questions = (" ".join(str(item).split())[:LABEL_MAX_LENGTH] for item in data.get("buyer_questions") or [])
        return PostLearning(facts=_facts(data), buyer_questions=[item for item in questions if item][:BUYER_QUESTION_LIMIT])

    def from_reply(self, *, question: str, answer: str, post_caption: str) -> list[LearnedFactDraft]:
        return _facts(
            self._ask(
                prompts.LEARN_REPLY_SYSTEM,
                prompts.learn_reply_request(question=question, answer=answer, post_caption=post_caption),
                prompts.LEARN_SCHEMA,
            )
        )
