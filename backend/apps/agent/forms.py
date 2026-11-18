from django import forms

from apps.agent.constants import TicketStatus

TEXT_MAX_LENGTH = 5000
PREVIEW_MAX_LENGTH = 2200


class AgentEnabledForm(forms.Form):
    """The on or off switch at the top of the agent page, saved as soon as it is flipped."""

    enabled = forms.BooleanField(required=False, label="Auto replies")


class AgentSettingsForm(forms.Form):
    knowledge = forms.CharField(
        required=False, max_length=TEXT_MAX_LENGTH, label="Facts, one per line",
        help_text="Prices, sizes, delivery, returns, how to order. Hooky looks things up here before it answers, and "
        "asks you when the answer is missing.",
        widget=forms.Textarea(attrs={
            "rows": 7,
            "placeholder": "Free shipping above Rs 999\nReturns within 7 days\nCash on delivery available\nOrder on hookd.world/shop",
        }),
    )
    voice = forms.CharField(
        required=False, max_length=TEXT_MAX_LENGTH, label="Describe the voice",
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Friendly and short, Hinglish is fine, max 1 emoji"}),
    )
    reply_to_threads = forms.BooleanField(
        required=False, label="Also answer replies inside comment threads",
    )
    approve_before_sending = forms.BooleanField(
        required=False, label="Check every reply and DM before it goes out",
    )


# One tap styles for the voice box on the agent page: (chip label, text added to the voice).
VOICE_PICKS = (
    ("Friendly", "Warm and friendly."),
    ("Playful", "Playful and fun."),
    ("Short", "Keep replies short, one or two lines."),
    ("Their language", "Reply in the language the buyer writes in."),
    ("Few emoji", "At most one emoji."),
)


class PostBrainForm(forms.Form):
    knowledge = forms.CharField(
        required=False, max_length=TEXT_MAX_LENGTH, label="Video brain",
        help_text="Facts about this post, one per line: price, sizes in stock, colours, link, launch date. Hooky looks "
        "them up before answering comments on this post.",
        widget=forms.Textarea(attrs={"rows": 5, "placeholder": "Price Rs 1499\nSizes S, M and L in stock, XL sold out"}),
    )
    agent_enabled = forms.BooleanField(required=False, initial=True, label="Reply agent on for this post")


class PreviewForm(forms.Form):
    text = forms.CharField(
        max_length=PREVIEW_MAX_LENGTH, label="Comment",
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "bhai M size available hai?"}),
    )
    post = forms.TypedChoiceField(coerce=int, required=False, empty_value=None, label="On post")
    # The agent page sends its unsaved voice and brain along, so the test uses what the creator just typed.
    voice = forms.CharField(required=False, max_length=TEXT_MAX_LENGTH)
    knowledge = forms.CharField(required=False, max_length=TEXT_MAX_LENGTH)

    def __init__(self, *args, posts=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["post"].choices = [("", "Any post (account brain only)")] + [
            (post.pk, (post.caption or "No caption")[:60]) for post in posts
        ]


class ApproveForm(forms.Form):
    text = forms.CharField(max_length=PREVIEW_MAX_LENGTH)


class AnswerForm(forms.Form):
    answer = forms.CharField(max_length=PREVIEW_MAX_LENGTH)


class TicketForm(forms.Form):
    status = forms.ChoiceField(choices=TicketStatus.choices)
    seller_note = forms.CharField(
        required=False, max_length=TEXT_MAX_LENGTH, label="Note for the buyer", widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Hooky tells the buyer this when they ask about the ticket.",
    )
