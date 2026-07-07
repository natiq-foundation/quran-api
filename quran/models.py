from django.db import models
from account.models import CustomUser
from core.models import File
from django.conf.global_settings import LANGUAGES
import uuid
from core.expressions import UUIDv7


class Status(models.TextChoices):
    DRAFT = "draft", "Draft"
    PENDING_REVIEW = "pending_review", "Pending review"
    PUBLISHED = "published", "Published"


class Mushaf(models.Model):
    id = models.UUIDField(
        db_default=UUIDv7(), primary_key=True, editable=False, unique=True
    )
    creator = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="mushafs"
    )
    slug = models.CharField(max_length=50, unique=True)
    name = models.TextField()
    source = models.TextField(default="")
    status = models.CharField(
        max_length=50, choices=Status.choices, default=Status.DRAFT
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class Surah(models.Model):
    PERIOD_CHOICES = [
        ("makki", "Makki"),
        ("madani", "Madani"),
    ]

    id = models.UUIDField(
        db_default=UUIDv7(), primary_key=True, editable=False, unique=True
    )
    creator = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="surahs"
    )
    mushaf = models.ForeignKey(Mushaf, on_delete=models.CASCADE, related_name="surahs")
    number = models.IntegerField()
    period = models.CharField(
        max_length=50, choices=PERIOD_CHOICES, blank=True, null=True
    )
    has_bismillah = models.BooleanField(default=True)
    bismillah_text = models.TextField(blank=True, null=True)
    search_terms = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["number"]
        unique_together = ["mushaf", "number"]

    def __str__(self):
        return f"{self.number}"


class Ayah(models.Model):
    SAJDAH_CHOICES = [
        ("none", "None"),
    ]

    id = models.UUIDField(
        db_default=UUIDv7(), primary_key=True, editable=False, unique=True
    )
    creator = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="ayahs"
    )
    surah = models.ForeignKey(Surah, on_delete=models.CASCADE, related_name="ayahs")
    number = models.IntegerField()
    sajdah = models.CharField(
        max_length=20, choices=SAJDAH_CHOICES, default="none", null=True
    )
    is_bismillah = models.BooleanField(default=False)
    length = models.IntegerField(
        default=0, help_text="Character count of the ayah text (joined words)"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["number"]
        unique_together = ["surah", "number"]

    def __str__(self):
        return f"{self.number}"

    def calculate_length(self):
        """Calculate the character count of the ayah text by joining all words."""
        words = self.words.all().order_by("id")
        if not words.exists():
            return 0
        text = " ".join(word.text for word in words)
        return len(text)

    def save(self, *args, **kwargs):
        """Override save to automatically calculate and update length."""
        # Only calculate length if the ayah already has a primary key (is saved)
        if self.pk:
            self.length = self.calculate_length()
        super().save(*args, **kwargs)


class Word(models.Model):
    id = models.UUIDField(
        db_default=UUIDv7(), primary_key=True, editable=False, unique=True
    )
    creator = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="words"
    )
    ayah = models.ForeignKey(Ayah, on_delete=models.CASCADE, related_name="words")
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.text

    def save(self, *args, **kwargs):
        """Override save to update the parent ayah's length."""
        super().save(*args, **kwargs)
        # Update the parent ayah's length
        self.ayah.length = self.ayah.calculate_length()
        self.ayah.save(update_fields=["length"])

    def delete(self, *args, **kwargs):
        """Override delete to update the parent ayah's length."""
        ayah = self.ayah
        super().delete(*args, **kwargs)
        # Update the parent ayah's length
        ayah.length = ayah.calculate_length()
        ayah.save(update_fields=["length"])


class Translation(models.Model):
    id = models.UUIDField(
        db_default=UUIDv7(), primary_key=True, editable=False, unique=True
    )
    creator = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="translations"
    )
    mushaf = models.ForeignKey(
        Mushaf, on_delete=models.CASCADE, related_name="translations"
    )
    translator = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="translated_works"
    )
    language = models.CharField(
        max_length=7, choices=LANGUAGES
    )  # ISO 639-1 language code
    release_date = models.DateField(blank=True, null=True)
    source = models.CharField(max_length=300, blank=True, null=True)
    # approved = models.BooleanField(default=False)
    status = models.CharField(
        max_length=50, choices=Status.choices, default=Status.DRAFT
    )
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ["mushaf", "translator", "language"]

    def __str__(self):
        return f"{self.mushaf.name} - {self.language} by {self.translator.username}"


class AyahTranslation(models.Model):
    id = models.UUIDField(
        db_default=UUIDv7(), primary_key=True, editable=False, unique=True
    )
    creator = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="ayah_translations"
    )
    translation = models.ForeignKey(
        Translation, on_delete=models.CASCADE, related_name="ayah_translations"
    )
    ayah = models.ForeignKey(
        Ayah, on_delete=models.CASCADE, related_name="translations"
    )
    text = models.TextField()
    bismillah = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # class Meta:
    #    unique_together = ['translation', 'ayah']

    def __str__(self):
        return f"{self.translation.language} - {self.ayah}"


class AyahBreakerType(models.TextChoices):
    PAGE = "page", "Page"
    JUZ = "juz", "Juz"
    HIZB = "hizb", "Hizb"
    RUB = "rub", "Rub"
    MANZIL = "manzil", "Manzil"
    RUKU = "ruku", "Ruku"


class Takhtit(models.Model):
    id = models.UUIDField(
        db_default=UUIDv7(), primary_key=True, editable=False, unique=True
    )
    creator = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="takhtits"
    )
    mushaf = models.ForeignKey(
        Mushaf, on_delete=models.CASCADE, related_name="takhtits"
    )
    account = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="takhtit_accounts"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return str(self.account)


class AyahBreaker(models.Model):
    id = models.UUIDField(
        db_default=UUIDv7(), primary_key=True, editable=False, unique=True
    )
    creator = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="ayah_breakers"
    )
    ayah = models.ForeignKey(Ayah, on_delete=models.CASCADE, related_name="breakers")
    owner = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name="owned_ayah_breakers",
        null=True,
        blank=True,
    )
    takhtit = models.ForeignKey(
        Takhtit,
        on_delete=models.CASCADE,
        related_name="ayah_breakers",
        null=True,
        blank=True,
    )
    type = models.CharField(max_length=20, choices=AyahBreakerType.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.type} - {self.ayah}"


class WordBreaker(models.Model):
    id = models.UUIDField(
        db_default=UUIDv7(), primary_key=True, editable=False, unique=True
    )
    creator = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="word_breakers"
    )
    word = models.ForeignKey(Word, on_delete=models.CASCADE, related_name="breakers")
    owner = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name="owned_word_breakers",
        null=True,
        blank=True,
    )
    takhtit = models.ForeignKey(
        Takhtit,
        on_delete=models.CASCADE,
        related_name="word_breakers",
        null=True,
        blank=True,
    )
    TYPE_CHOICES = [
        ("line", "Line"),
    ]
    type = models.CharField(max_length=20, choices=TYPE_CHOICES, default="line")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.type} - {self.word}"


class Recitation(models.Model):
    id = models.UUIDField(
        db_default=UUIDv7(), primary_key=True, editable=False, unique=True
    )
    creator = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="recitations"
    )
    mushaf = models.ForeignKey(
        Mushaf, on_delete=models.CASCADE, related_name="recitations"
    )
    reciter_account = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="recited_works"
    )
    recitation_date = models.DateField(blank=True, null=True)
    recitation_location = models.TextField(blank=True, null=True)
    recitation_type = models.TextField()
    status = models.CharField(
        max_length=50, choices=Status.choices, default=Status.DRAFT
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return (
            f"Recitation by {self.reciter_account.username} on {self.recitation_date}"
        )


class RecitationSurah(models.Model):
    """Associates a Recitation with a specific Surah and the corresponding audio file."""

    id = models.UUIDField(
        db_default=UUIDv7(), primary_key=True, editable=False, unique=True
    )
    recitation = models.ForeignKey(
        Recitation, on_delete=models.CASCADE, related_name="recitation_surahs"
    )
    surah = models.ForeignKey(
        Surah, on_delete=models.CASCADE, related_name="recitation_surahs"
    )
    file = models.ForeignKey(
        File, on_delete=models.CASCADE, related_name="recitation_surahs"
    )
    duration = models.DurationField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ["recitation", "surah"]

    def __str__(self):
        return f"{self.recitation} - Surah {self.surah.number}"


class RecitationSurahTimestamp(models.Model):
    id = models.UUIDField(
        db_default=UUIDv7(), primary_key=True, editable=False, unique=True
    )
    recitation_surah = models.ForeignKey(
        RecitationSurah, on_delete=models.CASCADE, related_name="timestamps"
    )
    start_time = models.TimeField()
    end_time = models.TimeField(null=True, blank=True)
    word = models.ForeignKey(
        Word,
        on_delete=models.CASCADE,
        related_name="recitation_timestamps",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["start_time"]

    def __str__(self):
        return f"Timestamp for {self.recitation_surah} at {self.start_time}"


class SurahName(models.Model):
    id = models.UUIDField(
        db_default=UUIDv7(), primary_key=True, editable=False, unique=True
    )
    surah = models.ForeignKey(Surah, on_delete=models.CASCADE, related_name="names")
    name = models.CharField(max_length=50)
    pronunciation = models.TextField(blank=True, null=True)
    translation = models.TextField(blank=True, null=True)
    transliteration = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class Provenance(models.Model):
    id = models.UUIDField(
        db_default=UUIDv7(), primary_key=True, editable=False, unique=True
    )
    creator = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="provenances_creator"
    )
    account = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="provenances_acc"
    )
    child_provenance = models.OneToOneField(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="parent_provenance",
    )
    role = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
