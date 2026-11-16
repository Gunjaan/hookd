"""Stand-in engine used until Claude is connected.

Deliberately naive: the reply is the brain line sharing the most words with the comment (video brain first). A
question no brain line answers gets a holding reply with low confidence, so it waits for the creator; anything
else gets a thank you.
"""

import re

from apps.agent.engines.base import AgentContext, AgentDecision, ReplyEngine

WORD_PATTERN = re.compile(r"\w+")
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
HOLDING_REPLY = "Great question, we will get back to you soon!"


def _stem(word: str) -> str:
    """Drop a plural "s" so "size" matches "sizes"."""
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def _words(text: str) -> set[str]:
    return {_stem(word) for word in WORD_PATTERN.findall(text.lower()) if len(word) > 1 and word not in STOP_WORDS}


def _best_fact(context: AgentContext) -> str:
    comment_words = _words(context.comment_text)
    best, best_score = "", 0
    for line in context.post_knowledge.splitlines() + context.account_knowledge.splitlines():
        line = line.strip()
        score = len(comment_words & _words(line))
        if line and score > best_score:
            best, best_score = line, score
    return best


class SimulatedReplyEngine(ReplyEngine):
    name = "simulated"

    def decide(self, context: AgentContext) -> AgentDecision:
        fact = _best_fact(context)
        is_question = "?" in context.comment_text
        if fact:
            body, confidence, reason = fact, 0.9, "Found a brain fact."
        elif is_question:
            body, confidence, reason = HOLDING_REPLY, 0.3, "No fact in the brains answers this."
        else:
            body, confidence, reason = THANKS_REPLY, 0.8, "Not a question, thanked them."

        mention = f"@{context.author_username} " if context.author_username else ""
        return AgentDecision(
            intent="question" if is_question else "comment",
            language="unknown",
            reply_text=f"{mention}{body}",
            confidence=confidence,
            reason=reason,
        )
