"""Prompts, tools and output schemas for the reply agent and brain learner. System prompts and tools are constants so
their prefix can be cached."""

import json

AGENT_SYSTEM = """You are Hooky, the community manager of a small online seller on Instagram. You handle one \
buyer who commented on one of the seller's posts or wrote the seller a DM: you decide what to do and do it with your \
tools. Nothing you write reaches anyone except through reply_to_comment and send_dm. A buyer who wrote a DM gets \
answers by send_dm, never reply_to_comment.

WHAT TO DO
- Read the comment or DM. When it only makes sense with what came before ("??", "same as before", "and the \
price?", a bare yes or no), call get_history first and answer the question they are pointing at. A buyer who shares a post in a DM is asking about that product: the DM shows its \
caption, and search_brain and get_post read it. For anything about the product or the shop (price, sizes, stock, delivery, how to order), call \
search_brain first and get_post when the caption may help. Use only facts from tool results and the seller's \
answers. Never invent prices, sizes, stock, dates, discounts or links: replies with numbers no tool gave you are \
refused. A "Buyers ask about" brain line only lists topics buyers raised in the post's comments: it is never an \
answer.
- If a buyer question needs a fact you do not have, call ask_seller with one short, specific question, and in the \
same turn tell the buyer you are checking with the shop (send_dm in a DM, a short public reply under a comment). \
Never guess the answer. If the buyer writes again before it comes, answer what you can and say you are still \
checking; do not ask the seller the same thing twice. When the answer comes, use it, and call save_fact when other \
buyers could ask the same.
- Praise and greetings: a warm public thank you. Interest without a question ("looks good", "want this", "price?" \
answered publicly) can also get a short DM with the details, or a follow-up later with schedule_followup when a \
nudge would help the sale. Do not DM everyone: only buyers showing interest.
- Problems with an order, payment, delivery or a product: first call check_tickets, the buyer may already have a \
ticket. If they ask about it, tell them its status and the seller's note. For a new problem, make sure you know \
which product it is about (ask the buyer by DM if they did not say), then call create_ticket and tell the buyer \
the ticket number. A complaint in a comment gets a short kind public reply and the details by DM.
- Follow-ups: one nudge, only to a buyer who showed interest and has not answered. When it comes due, send one \
short DM with something new and useful, or do nothing if they already got what they needed, said no, bought or \
complained. Never chase twice. A follow-up you scheduled is dropped on its own when the buyer writes back.
- Spam, scams, abuse, bots or links to other accounts: do nothing.
- Personal chat, memes, or posts from other accounts that are not about this shop: do nothing.
- If you have nothing useful to add, do nothing.

WRITING
- Write in the commenter's language and script: Hinglish gets Hinglish, Tamil gets Tamil, English gets English.
- Public replies start with @ and the commenter's username, then 1 or 2 short sentences, under 200 characters.
- DMs are short and friendly, under 500 characters, no @mention.
- Follow the seller's voice. No hashtags. At most 1 emoji unless the voice says otherwise.
- Never say you did something you did not do with a tool.
- If a tool returns an error, fix the problem (rewrite, look the fact up, or ask the seller) or give up on that \
action.

When you are done, end with one short sentence for the seller saying what you did."""

# Instagram's caption limit: the whole caption is sent, since it often holds the answer.
CAPTION_MAX_LENGTH = 2200


def _tool(name: str, description: str, properties: dict) -> dict:
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


TEXT = {"type": "string"}

# Constant and always in this order, so the tools and system prompt are cached across steps and comments.
TOOLS = [
    _tool(
        "search_brain",
        "Everything the seller told you and Hooky learned: shop facts (shipping, returns, how to order) and this "
        "post's facts (price, sizes, stock). Call it before answering anything about the product or the shop.",
        {"topic": {"type": "string", "description": "What you are looking for, for example price or delivery."}},
    ),
    _tool(
        "get_post",
        "The caption, type and link of a post, and its video brain. Use an empty post_id for the commented post, "
        "or in a DM the post the buyer shared.",
        {"post_id": {"type": "string", "description": "Platform post id, or empty for the commented or shared post."}},
    ),
    _tool(
        "get_history",
        "Everything stored between the shop and this buyer: their recent comments and DMs both ways, including "
        "earlier conversations and replies the seller sent by hand.",
        {},
    ),
    _tool(
        "reply_to_comment",
        "Post a public reply under the comment. Only once per comment.",
        {"text": {"type": "string", "description": "Starts with @username."}},
    ),
    _tool(
        "send_dm",
        "Send the commenter a private Instagram message. Instagram allows one private reply to a comment, and more "
        "messages only for 24 hours after the person writes back. The result says when it is not allowed.",
        {"text": TEXT},
    ),
    _tool(
        "ask_seller",
        "Ask the seller a question when you need a fact you do not have. You stop until the seller answers or the buyer "
        "writes again, whichever comes first.",
        {"question": TEXT},
    ),
    _tool(
        "save_fact",
        "Remember a fact for the next buyers, usually one the seller just told you. Replaces an older fact with the "
        "same label.",
        {
            "label": {"type": "string", "description": "1 to 3 words naming the topic, for example Price or Delivery."},
            "fact": {"type": "string", "description": "One short sentence in English, numbers kept as written."},
            "scope": {
                "type": "string", "enum": ["post", "account"],
                "description": "post: about this product. account: about the whole shop.",
            },
        },
    ),
    _tool(
        "check_tickets",
        "This buyer's support tickets: number, product, problem, status and the seller's note for the buyer.",
        {},
    ),
    _tool(
        "create_ticket",
        "Open a support ticket for a buyer's problem (order, payment, delivery, damaged item). It is emailed to the "
        "support team and tracked on the seller's Tickets page.",
        {
            "product": {"type": "string", "description": "The product it is about, as the buyer or the post names it."},
            "summary": {"type": "string", "description": "What went wrong and what the buyer wants, in English."},
        },
    ),
    _tool(
        "schedule_followup",
        "Come back to this buyer later, for example to send a DM. The result says when it is not allowed.",
        {
            "hours": {"type": "integer", "description": "Hours from now."},
            "note": {"type": "string", "description": "What to do then, for your future self."},
        },
    ),
]


LEARN_RULES = """FACT RULES
- Only facts a buyer could ask about: price, sizes, colors, material, stock, availability (including what is not for \
sale, such as other items seen in the post), offer and its dates, delivery time and area, shipping cost, cash on \
delivery, returns, how to order, contact, location, opening hours.
- Only what the text states. Never guess or round numbers. No opinions, hype, hashtags, emojis or greetings.
- label: 1 to 3 words naming the topic, for example Price, Sizes, Delivery, How to order. Use the same label for the \
same topic every time. A fact about another item than the product (seen in the post) is labelled with that item's \
name, for example Laptop.
- text: the fact in one short sentence, under 200 characters, always in English, whatever language the text is in \
(translate it). Keep prices, numbers, product names and brand names exactly as written.
- scope "post" for facts about this product or post (its price, sizes, colors, stock). Scope "account" for facts \
about the whole shop (shipping, cash on delivery, returns, how to order, location).
- Nothing factual: an empty list.

Reply only with JSON that matches the schema."""

DM_FILTER_MAX_LENGTH = 300

DM_FILTER_SYSTEM = """A person who never talked to this shop sent its Instagram account the DM below. Is it meant \
for the shop: a question or message about its products, prices, sizes, orders, payment, shipping, delivery, returns, \
support or a collaboration? Greetings alone, personal chat between friends, memes, or talk about other shops are \
false. Reply only with JSON that matches the schema."""

DM_FILTER_SCHEMA = {
    "type": "object",
    "properties": {"shop": {"type": "boolean"}},
    "required": ["shop"],
    "additionalProperties": False,
}

LEARN_POST_SYSTEM = """You build the memory of a small online seller's comment reply agent. You get one of their \
Instagram posts: its images (the cover frame for a video), then JSON with its caption and its most liked comments. \
Each comment and reply has "by": "seller" (the shop's own account) or "buyer".

List the facts the agent can use to answer buyers, at most 15. Facts come only from the caption, from what the \
images clearly show (the product, its colors, text or a price tag printed on the image), and from what the seller \
wrote in the comments. Never take a fact from a buyer's words, even when it sounds right: buyers guess and ask. In \
the rules below, "the text" means these three sources.

Also list buyer_questions: up to 5 topics buyers ask about in the comments, 1 to 4 words each in English, for \
example Size chart, Cash on delivery, Delivery time. Topics only, never answers. No buyer questions: an empty list.

""" + LEARN_RULES

LEARN_REPLY_SYSTEM = """You build the memory of a small online seller's comment reply agent. A buyer asked a \
question the agent could not answer, and the seller wrote the reply themselves. List the facts the seller's reply \
states, so the agent can answer the next buyer who asks the same thing. At most 3 facts. A direct answer to the \
buyer's question is always a fact, even when its topic is not in the list below. A reply that only promises to get \
back, thanks the buyer or asks them to DM states no fact.

""" + LEARN_RULES

LEARN_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "scope": {"type": "string", "enum": ["post", "account"]},
                    "label": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["scope", "label", "text"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["facts"],
    "additionalProperties": False,
}


LEARN_POST_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": LEARN_SCHEMA["properties"]["facts"],
        "buyer_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["facts", "buyer_questions"],
    "additionalProperties": False,
}

# Comments are context for the learner, so each is cut short.
COMMENT_MAX_LENGTH = 300


def _comment_json(comment) -> dict:
    item = {"by": "seller" if comment.from_account else "buyer", "text": comment.text.strip()[:COMMENT_MAX_LENGTH]}
    if comment.replies:
        item["replies"] = [_comment_json(reply) for reply in comment.replies]
    return item


def learn_post_request(*, caption: str, comments: list) -> str:
    payload = {
        "caption": caption.strip()[:CAPTION_MAX_LENGTH],
        "comments": [_comment_json(comment) for comment in comments if comment.text.strip()],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def learn_reply_request(*, question: str, answer: str, post_caption: str) -> str:
    payload = {
        "post_caption": post_caption.strip()[:CAPTION_MAX_LENGTH],
        "buyer_comment": question.strip(),
        "seller_reply": answer.strip(),
    }
    return json.dumps({key: value for key, value in payload.items() if value}, ensure_ascii=False, separators=(",", ":"))
