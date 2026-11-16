from django import forms

TEXT_MAX_LENGTH = 5000
PREVIEW_MAX_LENGTH = 2200


class AgentSettingsForm(forms.Form):
    enabled = forms.BooleanField(
        required=False, label="Reply to new comments automatically",
        help_text="Replies go out on their own when your brains have the answer. When they do not, the draft waits "
        "in your inbox for approval.",
    )
    voice = forms.CharField(
        required=False, max_length=TEXT_MAX_LENGTH, label="How should replies sound?",
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Friendly and short, Hinglish is fine, max 1 emoji"}),
    )
    knowledge = forms.CharField(
        required=False, max_length=TEXT_MAX_LENGTH, label="What should the agent know?",
        widget=forms.Textarea(attrs={"rows": 5, "placeholder": "Free shipping above Rs 999\nReturns within 7 days\nOrder on hookd.world/shop"}),
    )
    reply_to_threads = forms.BooleanField(
        required=False, label="Also answer replies inside comment threads",
    )


class PostBrainForm(forms.Form):
    knowledge = forms.CharField(
        required=False, max_length=TEXT_MAX_LENGTH, label="Video brain",
        help_text="Facts about this post, one per line: price, sizes in stock, colours, link, launch date. When the answer "
        "to a comment is here, the agent replies on its own.",
        widget=forms.Textarea(attrs={"rows": 5, "placeholder": "Price Rs 1499\nSizes S, M and L in stock, XL sold out"}),
    )
    agent_enabled = forms.BooleanField(required=False, initial=True, label="Reply agent on for this post")


class PreviewForm(forms.Form):
    text = forms.CharField(
        max_length=PREVIEW_MAX_LENGTH, label="Comment",
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "bhai M size available hai?"}),
    )
    post = forms.TypedChoiceField(coerce=int, required=False, empty_value=None, label="On post")

    def __init__(self, *args, posts=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["post"].choices = [("", "Any post (account brain only)")] + [
            (post.pk, (post.caption or "No caption")[:60]) for post in posts
        ]


class ApproveForm(forms.Form):
    text = forms.CharField(max_length=PREVIEW_MAX_LENGTH)
