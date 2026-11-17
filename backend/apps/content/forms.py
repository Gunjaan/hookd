from django import forms

from apps.integrations.constants import PublishKind

CAPTION_MAX_LENGTH = 2200
CAROUSEL_MIN_ITEMS = 2
CAROUSEL_MAX_ITEMS = 10
IMAGE_MAX_BYTES = 8 * 1024 * 1024
VIDEO_MAX_BYTES = 300 * 1024 * 1024
IMAGE_EXTENSIONS = (".jpg", ".jpeg")
VIDEO_EXTENSIONS = (".mp4", ".mov")


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    widget = MultipleFileInput

    def clean(self, data, initial=None):
        files = data if isinstance(data, (list, tuple)) else [data] if data else []
        return [super(MultipleFileField, self).clean(item, initial) for item in files]


def _check_file(upload, extensions: tuple[str, ...], max_bytes: int, label: str) -> None:
    if not upload.name.lower().endswith(extensions):
        raise forms.ValidationError(f"{upload.name}: {label} must be {' or '.join(e.lstrip('.').upper() for e in extensions)}.")
    if upload.size > max_bytes:
        raise forms.ValidationError(f"{upload.name}: {label} must be under {max_bytes // (1024 * 1024)} MB.")


class ComposeForm(forms.Form):
    kind = forms.ChoiceField(choices=PublishKind.choices, initial=PublishKind.IMAGE, widget=forms.RadioSelect)
    media = MultipleFileField(
        label="Media",
        help_text=(
            "Image, carousel and story photo: JPEG up to 8 MB each, 2 to 10 for a carousel. "
            "Reel and story video: MP4 or MOV up to 300 MB. Stories show no caption."
        ),
    )
    caption = forms.CharField(
        required=False,
        max_length=CAPTION_MAX_LENGTH,
        widget=forms.Textarea(attrs={"rows": 6, "placeholder": "Write a caption..."}),
    )

    def clean(self):
        cleaned = super().clean()
        kind = cleaned.get("kind")
        files = cleaned.get("media") or []
        if not kind or not files:
            return cleaned

        if kind == PublishKind.CAROUSEL:
            if not CAROUSEL_MIN_ITEMS <= len(files) <= CAROUSEL_MAX_ITEMS:
                self.add_error("media", f"A carousel needs {CAROUSEL_MIN_ITEMS} to {CAROUSEL_MAX_ITEMS} images.")
                return cleaned
        elif len(files) != 1:
            self.add_error("media", "Choose exactly one file.")
            return cleaned

        try:
            for upload in files:
                if kind == PublishKind.REEL:
                    _check_file(upload, VIDEO_EXTENSIONS, VIDEO_MAX_BYTES, "a reel")
                elif kind == PublishKind.STORY_VIDEO:
                    _check_file(upload, VIDEO_EXTENSIONS, VIDEO_MAX_BYTES, "a story video")
                else:
                    _check_file(upload, IMAGE_EXTENSIONS, IMAGE_MAX_BYTES, "images")
        except forms.ValidationError as exc:
            self.add_error("media", exc)
        return cleaned
