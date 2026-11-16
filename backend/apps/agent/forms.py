from django import forms

from apps.agent.constants import FallbackAction, RuleAction

TEXT_MAX_LENGTH = 5000
RULE_TEXT_MAX_LENGTH = 1000
PREVIEW_MAX_LENGTH = 2200


class AgentSettingsForm(forms.Form):
    enabled = forms.BooleanField(
        required=False, label="Reply agent on",
        help_text="New comments are answered following your rules. Old comments are never touched.",
    )
    fallback_action = forms.ChoiceField(
        choices=FallbackAction.choices, widget=forms.RadioSelect, label="When no rule matches",
    )
    reply_to_threads = forms.BooleanField(
        required=False, label="Also answer replies inside comment threads",
    )
    voice = forms.CharField(
        required=False, max_length=TEXT_MAX_LENGTH, label="Voice",
        help_text="How replies should sound. Example: friendly, short, Hinglish is fine, call fans 'fam', max 1 emoji.",
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    knowledge = forms.CharField(
        required=False, max_length=TEXT_MAX_LENGTH, label="Account brain",
        help_text="Facts the agent may use for every post, one per line: shipping, returns, store link, timings.",
        widget=forms.Textarea(attrs={"rows": 6, "placeholder": "Free shipping above Rs 999\nReturns within 7 days\nOrder on hookd.world/shop"}),
    )


class RuleForm(forms.Form):
    name = forms.CharField(max_length=120, label="Rule name", widget=forms.TextInput(attrs={"placeholder": "Price question"}))
    when = forms.CharField(
        required=False, max_length=500, label="When",
        help_text="Describe the comments this rule is for, in plain words.",
        widget=forms.TextInput(attrs={"placeholder": "Someone asks how much it costs"}),
    )
    keywords = forms.CharField(
        required=False, max_length=500, label="Keywords",
        help_text="Comma separated. Until Claude is connected, the simulated agent matches only these words.",
        widget=forms.TextInput(attrs={"placeholder": "price, cost, how much, kitna"}),
    )
    action = forms.ChoiceField(choices=RuleAction.choices, widget=forms.RadioSelect, label="Then", initial=RuleAction.REVIEW)
    how_to_reply = forms.CharField(
        required=False, max_length=RULE_TEXT_MAX_LENGTH, label="How to reply",
        help_text="The reply itself or instructions. The simulated agent uses a matching brain fact first, else this text as is.",
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    enabled = forms.BooleanField(required=False, initial=True, label="Rule on")

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("when") and not cleaned.get("keywords"):
            raise forms.ValidationError("Add keywords or describe when the rule applies.")
        return cleaned


class PostBrainForm(forms.Form):
    knowledge = forms.CharField(
        required=False, max_length=TEXT_MAX_LENGTH, label="Video brain",
        help_text="Facts about this post, one per line: price, sizes in stock, colours, link, launch date.",
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
