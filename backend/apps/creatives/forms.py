from django import forms
from PIL import Image, UnidentifiedImageError

from apps.content.forms import MultipleFileField
from apps.creatives.models import FormatChoice, Look

MAX_PHOTOS = 3
PHOTO_MAX_BYTES = 10 * 1024 * 1024
PHOTO_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
NOTES_MAX_LENGTH = 1000
CAPTION_MAX_LENGTH = 2200
HASHTAGS_MAX_LENGTH = 1000
INSTRUCTION_MAX_LENGTH = 300
ANSWER_MAX_LENGTH = 300
MAX_QUESTIONS = 6

LOOK_HINTS = {
    Look.MODEL: "A person wears or holds it. Best for clothing, jewellery and bags.",
    Look.STUDIO: "Clean background and soft light, like a catalog.",
    Look.LIFESTYLE: "In a real place where it is used: a cafe, a room, a street.",
    Look.FLATLAY: "Shot from above with a few simple props.",
}

FORMAT_HINTS = {
    FormatChoice.AUTO: "Hookd picks one photo or a 3 photo carousel.",
    FormatChoice.SINGLE: "One strong hero photo.",
    FormatChoice.CAROUSEL: "3 to 5 photos: hero, close up, in use and more.",
}


class CreativeForm(forms.Form):
    photos = MultipleFileField(
        label="Product photos",
        help_text="1 to 3 photos, JPEG, PNG or WebP, up to 10 MB each.",
    )
    look = forms.ChoiceField(choices=Look.choices, initial=Look.MODEL, widget=forms.RadioSelect)
    format_choice = forms.ChoiceField(choices=FormatChoice.choices, initial=FormatChoice.AUTO, widget=forms.RadioSelect)
    notes = forms.CharField(
        required=False,
        max_length=NOTES_MAX_LENGTH,
        label="Anything special?",
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "placeholder": "A woman in her 20s, Diwali mood, warm light. Mention free shipping above Rs 999.",
            }
        ),
    )

    def clean_photos(self):
        photos = self.cleaned_data["photos"]
        if not 1 <= len(photos) <= MAX_PHOTOS:
            raise forms.ValidationError(f"Add 1 to {MAX_PHOTOS} photos.")
        for photo in photos:
            if not photo.name.lower().endswith(PHOTO_EXTENSIONS):
                raise forms.ValidationError(f"{photo.name}: use a JPEG, PNG or WebP photo.")
            if photo.size > PHOTO_MAX_BYTES:
                raise forms.ValidationError(f"{photo.name}: photos must be under 10 MB.")
            try:
                Image.open(photo).verify()
            except (UnidentifiedImageError, OSError) as exc:
                raise forms.ValidationError(f"{photo.name}: this file is not a readable photo.") from exc
            photo.seek(0)
        return photos

    def look_options(self):
        return [(choice, LOOK_HINTS[choice.data["value"]]) for choice in self["look"]]

    def format_options(self):
        return [(choice, FORMAT_HINTS[choice.data["value"]]) for choice in self["format_choice"]]


class CopyForm(forms.Form):
    caption = forms.CharField(required=False, max_length=CAPTION_MAX_LENGTH, widget=forms.Textarea(attrs={"rows": 9}))
    hashtags = forms.CharField(required=False, max_length=HASHTAGS_MAX_LENGTH, widget=forms.Textarea(attrs={"rows": 3}))


def brain_answers(data) -> dict[int, str]:
    """The reply agent answers posted as answer_0 .. answer_5, trimmed to ANSWER_MAX_LENGTH."""
    return {
        index: data[f"answer_{index}"][:ANSWER_MAX_LENGTH]
        for index in range(MAX_QUESTIONS)
        if f"answer_{index}" in data
    }


class RewriteForm(forms.Form):
    instruction = forms.CharField(
        required=False,
        max_length=INSTRUCTION_MAX_LENGTH,
        widget=forms.TextInput(attrs={"placeholder": "Shorter, in Hinglish, more playful..."}),
    )


class RedoSlideForm(forms.Form):
    tweak = forms.CharField(
        required=False,
        max_length=INSTRUCTION_MAX_LENGTH,
        widget=forms.TextInput(attrs={"placeholder": "Outdoor light, model smiling..."}),
    )


class PublishForm(forms.Form):
    account = forms.TypedChoiceField(coerce=int, label="Post to")

    def __init__(self, *args, accounts=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].choices = [(account.pk, f"@{account.username}") for account in accounts]
