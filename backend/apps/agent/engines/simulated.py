"""Stand-in agent model and brain learner, used without an Anthropic key and in tests.

Deliberately naive. The agent model looks the brain up, then replies publicly with the brain line sharing the most
words with the comment. A question no line answers goes to the seller (ask_seller) and their answer becomes the reply;
anything else gets a thank you. It never DMs, saves facts, opens tickets or schedules follow-ups.
"""

import re

from apps.agent.constants import FactScope
from apps.agent.engines.base import AgentModel, BrainLearner, LearnedFactDraft, ModelStep

WORD_PATTERN = re.compile(r"\w+")
MENTION_PATTERN = re.compile(r"^@\S+\s*")
COMMENTER_PATTERN = re.compile(r"^Commenter: @(\S*)$", re.MULTILINE)
COMMENT_PATTERN = re.compile(r"^Comment: (.*)$", re.MULTILINE)
ANSWER_PREFIX = "The seller answered: "
# Words too common to tell brain lines apart.
STOP_WORDS = frozenset(
    {
        "the", "and", "for", "are", "you", "your", "this", "that", "with", "have", "has", "can", "will", "what",
        "how", "does", "kya", "hai", "bhi", "our", "all", "any", "from", "its", "was", "not",
        "is", "to", "in", "on", "of", "it", "me", "my", "we", "an", "at", "be", "do", "so", "or", "if", "no",
        "hi", "ok", "ka", "ki", "ke", "ho", "ye", "na", "se",
    }
)
THANKS_REPLY = "Thank you so much!"


def _stem(word: str) -> str:
    """Drop a plural "s" so "size" matches "sizes"."""
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def _words(text: str) -> set[str]:
    return {_stem(word) for word in WORD_PATTERN.findall(text.lower()) if len(word) > 1 and word not in STOP_WORDS}


def _best_line(comment: str, brain: str) -> str:
    comment_words = _words(comment)
    best, best_score = "", 0
    for line in brain.splitlines():
        line = line.strip()
        score = len(comment_words & _words(line))
        if line and not line.endswith(":") and score > best_score:
            best, best_score = line, score
    return best


def _first_text(messages: list[dict]) -> str:
    content = messages[0]["content"]
    return next((block["text"] for block in content if block.get("type") == "text"), "")


def _tool_calls(message: dict) -> dict[str, str]:
    """tool_use id to tool name in an assistant turn."""
    return {block["id"]: block["name"] for block in message["content"] if block.get("type") == "tool_use"}


def _call(name: str, **arguments) -> ModelStep:
    return ModelStep(content=[{"type": "tool_use", "id": f"sim_{name}", "name": name, "input": arguments}], stop_reason="tool_use")


def _done(text: str) -> ModelStep:
    return ModelStep(content=[{"type": "text", "text": text}], stop_reason="end_turn")


class SimulatedAgentModel(AgentModel):
    name = "simulated"

    def step(self, *, system: str, tools: list[dict], messages: list[dict]) -> ModelStep:
        first = _first_text(messages)
        author = (COMMENTER_PATTERN.search(first) or [None, ""])[1]
        comment = (COMMENT_PATTERN.search(first) or [None, ""])[1]
        mention = f"@{author} " if author else ""
        last = messages[-1]["content"]
        results = [block for block in last if block.get("type") == "tool_result"]
        if not results:
            if len(messages) == 1:
                return _call("search_brain", topic=comment)
            return _done("Nothing to do.")

        names = _tool_calls(messages[-2])
        result = results[-1]
        name, text = names.get(result["tool_use_id"], ""), str(result.get("content", ""))
        if name == "search_brain":
            line = _best_line(comment, text)
            if line:
                return _call("reply_to_comment", text=f"{mention}{line}")
            if "?" in comment:
                return _call("ask_seller", question=f"A buyer asked: {comment}")
            return _call("reply_to_comment", text=f"{mention}{THANKS_REPLY}")
        if name == "ask_seller" and text.startswith(ANSWER_PREFIX):
            return _call("reply_to_comment", text=f"{mention}{text.removeprefix(ANSWER_PREFIX)}")
        if name == "reply_to_comment":
            return _done(f"Could not reply: {text}" if result.get("is_error") else "Replied publicly.")
        return _done("Nothing to do.")


SENTENCE_PATTERN = re.compile(r"(?<=[.!?])\s+|\n+")
TAG_PATTERN = re.compile(r"[#@]\w+")
CAPTION_FACT_LIMIT = 8
FACT_MAX_LENGTH = 300
LABEL_MAX_LENGTH = 80


def _clean(text: str) -> str:
    return " ".join(TAG_PATTERN.sub("", text).split())


class SimulatedBrainLearner(BrainLearner):
    """Stand-in learner: caption sentences with a number in them (prices, sizes, dates) become post facts, and the
    creator's answer becomes a fact labelled with the question, so the word matching engine finds it next time."""

    name = "simulated"

    def from_caption(self, caption: str) -> list[LearnedFactDraft]:
        sentences = (_clean(part) for part in SENTENCE_PATTERN.split(caption))
        facts = [sentence for sentence in sentences if any(char.isdigit() for char in sentence)]
        return [
            LearnedFactDraft(scope=FactScope.POST, label="Caption", text=fact[:FACT_MAX_LENGTH])
            for fact in facts[:CAPTION_FACT_LIMIT]
        ]

    def from_reply(self, *, question: str, answer: str, post_caption: str) -> list[LearnedFactDraft]:
        answer = MENTION_PATTERN.sub("", answer).strip()
        question = _clean(question)
        if not answer or not question:
            return []
        return [LearnedFactDraft(scope=FactScope.POST, label=question[:LABEL_MAX_LENGTH], text=answer[:FACT_MAX_LENGTH])]
