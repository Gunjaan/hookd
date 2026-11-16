from django import forms

from apps.agent.constants import FallbackAction, RuleAction

TEXT_MAX_LENGTH = 5000
RULE_TEXT_MAX_LENGTH = 1000
PREVIEW_MAX_LENGTH = 2200


AGENT_OFF = "off"


class AgentSettingsForm(forms.Form):
    """Folds `enabled` and `fallback_action` into one `mode` choice. "Only handle my special cases" (fallback ignore)
    is offered only when the account has rules; without rules it would do the same as turning the agent off."""

    mode = forms.ChoiceField(widget=forms.RadioSelect, label="What should Hookd do with a new comment?")
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

    def __init__(self, *args, has_rules: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [(FallbackAction.REVIEW, "Draft a reply for me to approve")]
        if has_rules:
            choices.append((FallbackAction.IGNORE, "Only handle my special cases"))
        choices.append((AGENT_OFF, "Turn off auto replies"))
        self.fields["mode"].choices = choices

    @staticmethod
    def initial_for(profile, *, has_rules: bool) -> dict:
        only_rules_without_rules = profile.fallback_action == FallbackAction.IGNORE and not has_rules
        return {
            "mode": AGENT_OFF if not profile.enabled or only_rules_without_rules else profile.fallback_action,
            "voice": profile.voice,
            "knowledge": profile.knowledge,
            "reply_to_threads": profile.reply_to_threads,
        }

    def profile_fields(self) -> dict:
        data = dict(self.cleaned_data)
        mode = data.pop("mode")
        data["enabled"] = mode != AGENT_OFF
        if mode != AGENT_OFF:
            data["fallback_action"] = mode
        return data


class RuleForm(forms.Form):
    keywords = forms.CharField(
        required=False, max_length=500, label="If a comment mentions",
        help_text="Words separated by commas. Any one of them is enough.",
        widget=forms.TextInput(attrs={"placeholder": "price, cost, how much, kitna"}),
    )
    when = forms.CharField(
        required=False, max_length=500, label="Or describe the comment",
        help_text="In plain words. Until Claude is connected, only the words above are matched.",
        widget=forms.TextInput(attrs={"placeholder": "Someone asks how much it costs"}),
    )
    action = forms.ChoiceField(choices=RuleAction.choices, widget=forms.RadioSelect, label="Then", initial=RuleAction.REVIEW)
    how_to_reply = forms.CharField(
        required=False, max_length=RULE_TEXT_MAX_LENGTH, label="Reply with",
        help_text="The reply itself or instructions. A matching fact the agent knows is used first, else this text as is.",
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    name = forms.CharField(max_length=120, label="Give this rule a name", widget=forms.TextInput(attrs={"placeholder": "Price question"}))
    enabled = forms.BooleanField(required=False, initial=True, label="Rule on")

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("when") and not cleaned.get("keywords"):
            raise forms.ValidationError("Add words to look for or describe the comment.")
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
