"""Stand-in engine used until Claude is connected.

Deliberately naive: a rule matches when one of its keywords appears in the comment, and the reply is the brain
line sharing the most words with the comment (video brain first), else the rule's reply text, else a thank you.
"""

import re

from apps.agent.engines.base import AgentContext, AgentDecision, ReplyEngine, RuleSpec

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
DEFAULT_REPLY = "Thank you so much!"


def _stem(word: str) -> str:
    """Drop a plural "s" so "size" matches "sizes"."""
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def _words(text: str) -> set[str]:
    return {_stem(word) for word in WORD_PATTERN.findall(text.lower()) if len(word) > 1 and word not in STOP_WORDS}


def _contains(text: str, keyword: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(keyword.lower())}(?!\w)", text.lower()) is not None


def _matching_rule(context: AgentContext) -> tuple[RuleSpec | None, str]:
    for rule in context.rules:
        for keyword in rule.keywords:
            if _contains(context.comment_text, keyword):
                return rule, keyword
    return None, ""


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
        rule, keyword = _matching_rule(context)
        fact = _best_fact(context)
        rule_text = rule.how_to_reply.strip() if rule else ""

        if rule and fact:
            body, confidence, reason = fact, 0.9, f'Matched "{keyword}" and found a brain fact.'
        elif rule and rule_text:
            body, confidence, reason = rule_text, 0.8, f'Matched "{keyword}", used the rule\'s reply text.'
        elif fact:
            body, confidence, reason = fact, 0.6, "No rule matched; found a brain fact."
        else:
            body, confidence = DEFAULT_REPLY, 0.5
            reason = f'Matched "{keyword}" but no reply text or fact.' if rule else "No rule matched."

        mention = f"@{context.author_username} " if context.author_username else ""
        return AgentDecision(
            rule_id=rule.id if rule else None,
            intent="question" if "?" in context.comment_text else (rule.name if rule else "comment"),
            language="unknown",
            reply_text=f"{mention}{body}",
            confidence=confidence,
            reason=reason,
        )
