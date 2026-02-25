from rest_framework import serializers
from django.db import models
from datetime import datetime
from django.conf import settings
from django.conf.global_settings import LANGUAGES
from drf_spectacular.utils import extend_schema_field
from core.rtl_languages import RTL_LANGUAGE_CODES

from quran.models import (
    Mushaf,
    Surah,
    Ayah,
    Takhtit,
    Word,
    Translation,
    AyahTranslation,
    AyahBreaker,
    WordBreaker,
    Recitation,
    File,
    RecitationSurah,
    RecitationSurahTimestamp,
    Status,
)
from account.models import CustomUser

class MushafSerializer(serializers.ModelSerializer):
    class Meta:
        model = Mushaf
        fields = ['uuid', 'short_name', 'name', 'source', 'status']
        read_only_fields = ['creator']

    def create(self, validated_data):
        return Mushaf.objects.create(**validated_data)

class SurahNameSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=50)
    pronunciation = serializers.CharField(required=False, allow_null=True)
    translation = serializers.CharField(required=False, allow_null=True)
    transliteration = serializers.CharField(required=False, allow_null=True)

class SurahBismillahSerializer(serializers.Serializer):
    is_ayah = serializers.BooleanField()
    text = serializers.CharField()

class SurahSerializer(serializers.ModelSerializer):
    names = serializers.SerializerMethodField(read_only=True)
    mushaf = MushafSerializer(read_only=True)
    mushaf_uuid = serializers.UUIDField(write_only=True, required=True)
    name = serializers.CharField(write_only=True, required=True)
    number_of_ayahs = serializers.SerializerMethodField(read_only=True)
    bismillah = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Surah
        fields = ['uuid', 'mushaf', 'mushaf_uuid', 'name', 'names', 'number', 'period', 'search_terms', 'number_of_ayahs', 'bismillah']
        read_only_fields = ['creator']

    @extend_schema_field(SurahBismillahSerializer)
    def get_bismillah(self, instance):
        # Get the first ayah of this surah
        first_ayah = instance.ayahs.order_by('number').first()
        text = first_ayah.bismillah_text if first_ayah and first_ayah.bismillah_text is not None else ""
        is_ayah = first_ayah.is_bismillah if first_ayah else False
        return {
            'is_ayah': is_ayah,
            'text': text
        }

    def get_number_of_ayahs(self, instance):
        return instance.ayahs.count()

    @extend_schema_field(SurahNameSerializer(many=True))
    def get_names(self, instance):
        return [{
            'name': instance.name,
            'pronunciation': instance.name_pronunciation,
            'translation': instance.name_translation,
            'transliteration': instance.name_transliteration
        }]

    def create(self, validated_data):
        mushaf_uuid = validated_data.pop('mushaf_uuid')
        name = validated_data.pop('name')
        from quran.models import Mushaf
        mushaf = Mushaf.objects.get(uuid=mushaf_uuid)
        validated_data['mushaf'] = mushaf
        validated_data['name'] = name
        validated_data['creator'] = self.context['request'].user
        return super().create(validated_data)

class SurahInAyahSerializer(serializers.ModelSerializer):
    names = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Surah
        fields = ['uuid', 'names']
        read_only_fields = ['creator']

    @extend_schema_field(SurahNameSerializer(many=True))
    def get_names(self, instance):
        return [{
            'name': instance.name,
            'pronunciation': instance.name_pronunciation,
            'translation': instance.name_translation,
            'transliteration': instance.name_transliteration
        }]

class AyahSerializer(serializers.ModelSerializer):
    text = serializers.SerializerMethodField()
    breakers = serializers.SerializerMethodField()
    bismillah = serializers.SerializerMethodField()
    surah = serializers.SerializerMethodField()

    class Meta:
        model = Ayah
        fields = ['uuid', 'number', 'sajdah', 'text', 'breakers', 'bismillah', 'surah', 'length']
        read_only_fields = ['creator']

    @extend_schema_field(SurahSerializer(allow_null=True))
    def get_surah(self, instance):
        if instance.number == 1:
            return SurahSerializer(instance.surah).data
        return None

    def get_text(self, instance):
        words = list(instance.words.all().order_by('id'))
        if not words:
            return [] if self.context.get('text_format') == 'word' else ''

        if self.context.get('text_format') == 'word':
            # Get all word breakers for these words
            word_ids = [word.id for word in words]
            word_breakers = WordBreaker.objects.filter(word_id__in=word_ids)

            # Group breakers by word_id
            breakers_by_word = {}
            for breaker in word_breakers:
                if breaker.word_id not in breakers_by_word:
                    breakers_by_word[breaker.word_id] = []
                breakers_by_word[breaker.word_id].append({
                    'name': breaker.name
                })

            # Return words with their breakers (only if they have any)
            result = []
            for word in words:
                word_data = {'uuid': word.uuid, 'text': word.text}
                if word.id in breakers_by_word:
                    word_data['breakers'] = breakers_by_word[word.id]
                result.append(word_data)
            return result

        return ' '.join(word.text for word in words)

    def get_breakers(self, instance):
        breakers = instance.breakers.all()
        if not breakers.exists():
            return None

        # Get all breakers up to current ayah across all surahs
        current_surah = instance.surah
        current_number = instance.number

        all_breakers = AyahBreaker.objects.filter(
            models.Q(
                ayah__surah__number__lt=current_surah.number
            ) | models.Q(
                ayah__surah=current_surah,
                ayah__number__lte=current_number
            )
        ).order_by('ayah__surah__number', 'ayah__number')

        # Keep running count of breakers
        breaker_counts = {}
        ayah_breakers = {}

        for breaker in all_breakers:
            # Update count for this breaker type
            if breaker.type not in breaker_counts:
                breaker_counts[breaker.type] = 1
            else:
                breaker_counts[breaker.type] += 1

            # Store current counts for this ayah
            if breaker.ayah_id not in ayah_breakers:
                ayah_breakers[breaker.ayah_id] = []

            # Only add if type not already in this ayah's breakers
            if not any(b['name'] == breaker.type for b in ayah_breakers[breaker.ayah_id]):
                ayah_breakers[breaker.ayah_id].append({
                    'name': breaker.type,
                    'number': breaker_counts[breaker.type]
                })

        # Return breakers for current ayah
        return ayah_breakers.get(instance.id, None)

    def get_bismillah(self, instance):
        # Always return a bismillah object with text (never null)
        text = instance.bismillah_text
        if text is None:
            text = ""
        return {
            'is_ayah': instance.is_bismillah,
            'text': text
        }

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        # Remove null fields safely
        for field in ['breakers', 'sajdah', 'bismillah', 'surah']:
            if field in representation and representation[field] is None:
                representation.pop(field)
            # Move bismillah into surah for the first ayah
            if instance.number == 1 and 'bismillah' in representation and 'surah' in representation:
                if representation['surah'] is not None:
                    # If surah is a dict, add bismillah to it
                    if isinstance(representation['surah'], dict):
                        representation['surah']['bismillah'] = representation['bismillah']
                    # Remove bismillah from top level
                    representation.pop('bismillah', None)
        return representation

    def create(self, validated_data):
        validated_data['creator'] = self.context['request'].user
        return super().create(validated_data)

class WordSerializer(serializers.ModelSerializer):
    ayah_uuid = serializers.UUIDField(write_only=True)

    class Meta:
        model = Word
        fields = ['uuid', 'ayah_uuid', 'text']
        read_only_fields = ['creator']

    def create(self, validated_data):
        from quran.models import Ayah
        ayah_uuid = validated_data.pop('ayah_uuid')
        ayah = Ayah.objects.get(uuid=ayah_uuid)
        validated_data['ayah'] = ayah
        validated_data['creator'] = self.context['request'].user
        return super().create(validated_data)

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        rep['ayah_uuid'] = str(instance.ayah.uuid)
        return rep

class AyahSerializerView(AyahSerializer):
    surah = SurahInAyahSerializer(read_only=True)
    mushaf = serializers.SerializerMethodField()
    words = WordSerializer(many=True, read_only=True)

    class Meta(AyahSerializer.Meta):
        fields = AyahSerializer.Meta.fields + ['surah', 'mushaf', 'words']

    def get_mushaf(self, instance):
        return MushafSerializer(instance.surah.mushaf).data


# Separate serializer for ayahs in surah
class AyahInSurahSerializer(AyahSerializer):
    class Meta(AyahSerializer.Meta):
        fields = ['uuid', 'number', 'sajdah', 'is_bismillah', 'bismillah_text', 'text']


class SurahDetailSerializer(SurahSerializer):
    ayahs = AyahInSurahSerializer(many=True, read_only=True)

    class Meta(SurahSerializer.Meta):
        fields = SurahSerializer.Meta.fields + ['ayahs']

class AyahTranslationNestedSerializer(serializers.ModelSerializer):
    ayah_uuid = serializers.UUIDField(source='ayah.uuid', read_only=True)
    bismillah = serializers.SerializerMethodField()

    class Meta:
        model = AyahTranslation
        fields = ['uuid', 'ayah_uuid', 'text', 'bismillah']
        read_only_fields = ['creator']

    def get_bismillah(self, obj):
        # Only include bismillah for the first ayah in the surah (ayah number 1)
        if hasattr(obj, 'ayah') and getattr(obj.ayah, 'number', None) == 1:
            return obj.bismillah
        return None

class LangCodeField(serializers.ChoiceField):
    """A field for ISO 639-1 language codes using Django LANGUAGES."""
    def __init__(self, **kwargs):
        # Extract language codes from Django LANGUAGES
        language_codes = [code for code, name in LANGUAGES]
        super().__init__(choices=language_codes, **kwargs)


class TranslationSerializer(serializers.ModelSerializer):
    mushaf_uuid = serializers.SerializerMethodField()
    translator_uuid = serializers.SerializerMethodField()
    language = LangCodeField()
    language_is_rtl = serializers.SerializerMethodField()

    class Meta:
        model = Translation
        fields = ['uuid', 'mushaf_uuid', 'translator_uuid', 'language', 'language_is_rtl', 'release_date', 'source', 'status']
        read_only_fields = ['creator']

    def get_mushaf_uuid(self, obj):
        return str(obj.mushaf.uuid) if obj.mushaf else None

    def get_translator_uuid(self, obj):
        return str(obj.translator.uuid) if obj.translator else None

    def get_language_is_rtl(self, obj):
        code = (obj.language or '').strip().lower()
        base = code.split('-')[0]
        return code in RTL_LANGUAGE_CODES or base in RTL_LANGUAGE_CODES

    def to_internal_value(self, data):
        # Extract UUIDs for input
        mushaf_uuid = data.get('mushaf_uuid')
        translator_uuid = data.get('translator_uuid')
        ret = super().to_internal_value(data)
        ret['mushaf_uuid'] = mushaf_uuid
        ret['translator_uuid'] = translator_uuid
        return ret

    def create(self, validated_data):
        from quran.models import Mushaf
        from account.models import CustomUser
        mushaf_uuid = validated_data.pop('mushaf_uuid')
        translator_uuid = validated_data.pop('translator_uuid')
        mushaf = Mushaf.objects.get(uuid=mushaf_uuid)
        translator = CustomUser.objects.get(uuid=translator_uuid)
        validated_data['mushaf'] = mushaf
        validated_data['translator'] = translator
        validated_data['creator'] = self.context['request'].user
        return super().create(validated_data)

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        rep['mushaf_uuid'] = str(instance.mushaf.uuid)
        rep['translator_uuid'] = str(instance.translator.uuid)
        return rep

class AyahTranslationSerializer(serializers.ModelSerializer):
    translation_uuid = serializers.UUIDField(write_only=True)
    ayah_uuid = serializers.UUIDField(write_only=True)

    class Meta:
        model = AyahTranslation
        fields = ['uuid', 'translation_uuid', 'ayah_uuid', 'text', 'bismillah']
        read_only_fields = ['creator']

    def create(self, validated_data):
        from quran.models import Translation, Ayah
        translation_uuid = validated_data.pop('translation_uuid')
        ayah_uuid = validated_data.pop('ayah_uuid')
        translation = Translation.objects.get(uuid=translation_uuid)
        ayah = Ayah.objects.get(uuid=ayah_uuid)
        validated_data['translation'] = translation
        validated_data['ayah'] = ayah
        validated_data['creator'] = self.context['request'].user
        return super().create(validated_data)

    def to_representation(self, instance):
        rep = super().to_representation(instance)
        rep['ayah_uuid'] = str(instance.ayah.uuid)
        return rep

class AyahBreakerSerializer(serializers.ModelSerializer):
    class Meta:
        model = AyahBreaker
        fields = ['uuid', 'type']
        read_only_fields = ['creator']

    def create(self, validated_data):
        validated_data['creator'] = self.context['request'].user
        return super().create(validated_data)

class WordBreakerSerializer(serializers.ModelSerializer):
    class Meta:
        model = WordBreaker
        fields = ['uuid', 'name']
        read_only_fields = ['creator']

    def create(self, validated_data):
        validated_data['creator'] = self.context['request'].user
        return super().create(validated_data)

class AyahAddSerializer(serializers.Serializer):
    surah_uuid = serializers.UUIDField()
    text = serializers.CharField()
    is_bismillah = serializers.BooleanField(default=False)
    bismillah_text = serializers.CharField(required=False, allow_null=True)
    sajdah = serializers.CharField(required=False, allow_null=True)

    def to_representation(self, instance):
        return {
            'uuid': str(instance.uuid),
            'number': instance.number,
            'surah_uuid': str(instance.surah.uuid),
            'is_bismillah': instance.is_bismillah,
            'bismillah_text': instance.bismillah_text,
            'sajdah': instance.sajdah,
            'length': instance.length
        }

    def create(self, validated_data):
        # Get the text and remove it from validated_data
        text = validated_data.pop('text')
        surah_uuid = validated_data.pop('surah_uuid')

        # Get the surah by uuid
        surah = Surah.objects.get(uuid=surah_uuid)

        # Get the latest ayah number in this surah and increment it
        latest_ayah = Ayah.objects.filter(surah=surah).order_by('-number').first()
        next_number = 1 if latest_ayah is None else latest_ayah.number + 1

        # Create the ayah
        ayah_data = {
            'surah': surah,
            'creator': self.context['request'].user,
            'number': next_number,
            'is_bismillah': validated_data.get('is_bismillah', False),
            'bismillah_text': validated_data.get('bismillah_text', None),
            'sajdah': validated_data.get('sajdah', None)
        }
        ayah = Ayah.objects.create(**ayah_data)

        # Create words from the text
        if text:
            # Split text into words (you might want to use a more sophisticated word splitting logic)
            words = text.split(" ")
            for word_text in words:
                Word.objects.create(
                    ayah=ayah,
                    text=word_text,
                    creator=self.context['request'].user
                )

        # Calculate and update the length after creating words
        ayah.length = ayah.calculate_length()
        ayah.save(update_fields=['length'])

        return ayah

class RecitationSerializer(serializers.ModelSerializer):
    mushaf_uuid = serializers.UUIDField(write_only=True)
    reciter_account_uuid = serializers.UUIDField(write_only=True)

    # Add read-only fields for output
    get_mushaf_uuid = serializers.SerializerMethodField(read_only=True)
    # reciter_account_uuid is accepted in the request body (write-only). We manually
    # add it back in `to_representation` so it also appears in responses.

    class Meta:
        model = Recitation
        fields = [
            'uuid',
            'mushaf_uuid',
            'get_mushaf_uuid',
            'status',
            'reciter_account_uuid',
            'recitation_date',
            'recitation_location',
            'duration',
            'recitation_type',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['creator', 'get_mushaf_uuid']

    def get_get_mushaf_uuid(self, obj):
        return str(obj.mushaf.uuid) if obj.mushaf else None

    def get_reciter_account_uuid(self, obj):
        return str(obj.reciter_account.uuid) if obj.reciter_account else None

    def to_internal_value(self, data):
        mushaf_uuid = data.get('mushaf_uuid')
        ret = super().to_internal_value(data)
        ret['mushaf_uuid'] = mushaf_uuid
        return ret

    def create(self, validated_data):
        from quran.models import Mushaf, RecitationSurah
        from account.models import CustomUser

        mushaf_uuid = validated_data.pop('mushaf_uuid')

        mushaf = Mushaf.objects.get(uuid=mushaf_uuid)

        validated_data['mushaf'] = mushaf
        # Use the requesting user as the reciter by default (can be adjusted later via recitation_surah upload).
        reciter_account_uuid = validated_data.pop('reciter_account_uuid')
        try:
            reciter_user = CustomUser.objects.get(uuid=reciter_account_uuid)
        except CustomUser.DoesNotExist:
            raise serializers.ValidationError({'reciter_account_uuid': 'User not found'})
        validated_data['reciter_account'] = reciter_user
        # Initial RecitationSurah association is created via dedicated endpoints (e.g., upload).
        validated_data['creator'] = self.context['request'].user
        # Remove word_timestamps handling – they will be provided via the upload endpoint
        recitation = super().create(validated_data)

        return recitation

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        # Remove write-only fields from output
        representation.pop('mushaf_uuid', None)
        # Echo back the reciter_account_uuid for client convenience
        representation['reciter_account_uuid'] = str(instance.reciter_account.uuid) if getattr(instance, 'reciter_account', None) else None
        # Always show UUIDs using the read-only methods
        representation['mushaf_uuid'] = representation.pop('get_mushaf_uuid', None)
        # Remove reciter_account (int id) from output if present
        representation.pop('reciter_account', None)

        # Add recitation_surahs with file_url for each (filtered by surah_uuid on retrieve when provided)
        from quran.models import RecitationSurah
        recitation_surahs = RecitationSurah.objects.filter(recitation=instance)
        request = self.context.get('request')
        view = self.context.get('view')
        if request and view and view.action == 'retrieve':
            surah_uuid = request.query_params.get('surah_uuid')
            if surah_uuid:
                recitation_surahs = recitation_surahs.filter(surah__uuid=surah_uuid)
        representation['recitation_surahs'] = RecitationSurahSerializer(recitation_surahs, many=True, context=self.context).data

        # No timestamps are returned at the recitation level; they are exposed via track endpoints only.
        return representation

class TranslatorDetailSerializer(serializers.Serializer):
    uuid = serializers.UUIDField()
    name = serializers.CharField()

class ReciterDetailSerializer(serializers.Serializer):
    uuid = serializers.UUIDField()
    name = serializers.CharField()

class TranslationListSerializer(serializers.ModelSerializer):
    mushaf_uuid = serializers.SerializerMethodField()
    translator = serializers.SerializerMethodField()
    language_is_rtl = serializers.SerializerMethodField()

    class Meta:
        model = Translation
        fields = ['uuid', 'mushaf_uuid', 'translator', 'language', 'language_is_rtl', 'release_date', 'source', 'status']
        read_only_fields = ['creator']

    def get_mushaf_uuid(self, obj):
        return str(obj.mushaf.uuid) if obj.mushaf else None

    @extend_schema_field(TranslatorDetailSerializer(allow_null=True))
    def get_translator(self, obj):
        if not obj.translator:
            return None
        # Construct name from first_name and last_name, or fallback to username
        name_parts = []
        if obj.translator.first_name:
            name_parts.append(obj.translator.first_name)
        if obj.translator.last_name:
            name_parts.append(obj.translator.last_name)
        name = ' '.join(name_parts) if name_parts else obj.translator.username
        return {
            'uuid': str(obj.translator.uuid),
            'name': name
        }

    def get_language_is_rtl(self, obj):
        code = (obj.language or '').strip().lower()
        base = code.split('-')[0]
        return code in RTL_LANGUAGE_CODES or base in RTL_LANGUAGE_CODES

class RecitationSurahSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()
    surah_uuid = serializers.SerializerMethodField()

    class Meta:
        model = RecitationSurah
        fields = ['uuid', 'surah_uuid', 'file_url']

    def get_file_url(self, obj):
        if obj.file and hasattr(obj.file, 'get_absolute_url'):
            return obj.file.get_absolute_url()
        return None

    def get_surah_uuid(self, obj):
        return str(obj.surah.uuid) if obj.surah else None


class TrackDetailSerializer(serializers.ModelSerializer):
    """Detailed serializer for a RecitationSurah (track), including timestamps."""

    file_url = serializers.SerializerMethodField()
    surah_uuid = serializers.SerializerMethodField()
    words_timestamps = serializers.SerializerMethodField()
    ayahs_timestamps = serializers.SerializerMethodField()

    class Meta:
        model = RecitationSurah
        fields = [
            "uuid",
            "surah_uuid",
            "file_url",
            "words_timestamps",
            "ayahs_timestamps",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "uuid",
            "surah_uuid",
            "file_url",
            "words_timestamps",
            "ayahs_timestamps",
            "created_at",
            "updated_at",
        ]

    def get_file_url(self, obj):
        if obj.file and hasattr(obj.file, "get_absolute_url"):
            return obj.file.get_absolute_url()
        return None

    def get_surah_uuid(self, obj):
        return str(obj.surah.uuid) if obj.surah else None

    def get_words_timestamps(self, obj):
        """Return word-level timestamps for this track."""
        from quran.models import RecitationSurahTimestamp

        result = []
        qs = (
            RecitationSurahTimestamp.objects.filter(recitation_surah=obj)
            .select_related("word")
            .order_by("start_time")
        )
        for timestamp in qs:
            start_time = timestamp.start_time.strftime("%H:%M:%S.%f")[:-3]
            end_time = (
                timestamp.end_time.strftime("%H:%M:%S.%f")[:-3]
                if timestamp.end_time
                else None
            )
            result.append(
                {
                    "start": start_time,
                    "end": end_time,
                    "word_uuid": str(timestamp.word.uuid) if timestamp.word else None,
                }
            )
        return result

    def get_ayahs_timestamps(self, obj):
        """Return ayah start times for this track."""
        from quran.models import RecitationSurahTimestamp, Ayah

        timestamps = list(
            RecitationSurahTimestamp.objects.filter(recitation_surah=obj).order_by(
                "start_time"
            )
        )
        if not timestamps:
            return []

        ayahs = Ayah.objects.filter(surah=obj.surah).prefetch_related("words")
        ayahs_first_words_as_id = set()
        for ayah in ayahs:
            first_word = ayah.words.values("id").first()
            if first_word:
                ayahs_first_words_as_id.add(first_word["id"])

        ayah_start_times = []
        for timestamp in timestamps[1:]:  # Skip first timestamp
            start_time = timestamp.start_time.strftime("%H:%M:%S.%f")[:-3]
            if timestamp.word_id in ayahs_first_words_as_id:
                ayah_start_times.append(start_time)
        return ayah_start_times

# Recitation list serializer (no recitation_surahs)
class RecitationListSerializer(serializers.ModelSerializer):
    reciter = serializers.SerializerMethodField()
    mushaf_uuid = serializers.UUIDField(source="mushaf.uuid", read_only=True)

    class Meta:
        model = Recitation
        fields = [
            "uuid",
            "status",
            "recitation_date",
            "recitation_location",
            "duration",
            "recitation_type",
            "created_at",
            "updated_at",
            "reciter",
            "mushaf_uuid",
        ]

    @extend_schema_field(ReciterDetailSerializer(allow_null=True))
    def get_reciter(self, obj):
        if not obj.reciter_account:
            return None
        # Construct name from first_name and last_name, or fallback to username
        name_parts = []
        if obj.reciter_account.first_name:
            name_parts.append(obj.reciter_account.first_name)
        if obj.reciter_account.last_name:
            name_parts.append(obj.reciter_account.last_name)
        name = ' '.join(name_parts) if name_parts else obj.reciter_account.username
        return {
            'uuid': str(obj.reciter_account.uuid),
            'name': name
        }

class TakhtitSerializer(serializers.ModelSerializer):
    mushaf_uuid = serializers.UUIDField(write_only=True, required=True)
    account_uuid = serializers.UUIDField(write_only=True, required=True)

    class Meta:
        model = Takhtit
        fields = [
            'uuid',
            'creator',
            'mushaf_uuid',
            'account_uuid',
            'created_at',
        ]
        read_only_fields = ['uuid', 'creator', 'created_at', 'updated_at']

    def create(self, validated_data):
        # Remove the UUID fields before creating the model instance
        validated_data.pop('mushaf_uuid', None)
        validated_data.pop('account_uuid', None)
        return super().create(validated_data)

class AyahBreakersResponseSerializer(serializers.Serializer):
    """Serializer for the ayahs_breakers endpoint response"""
    uuid = serializers.UUIDField(help_text="UUID of the ayah")
    surah = serializers.IntegerField(help_text="Surah number")
    ayah = serializers.IntegerField(help_text="Ayah number")
    length = serializers.IntegerField(help_text="Ayah text length")
    juz = serializers.IntegerField(allow_null=True, help_text="Juz number (null if not a juz breaker)")
    hizb = serializers.IntegerField(allow_null=True, help_text="Hizb number (null if not a hizb breaker)")
    ruku = serializers.IntegerField(allow_null=True, help_text="Ruku number (null if not a ruku breaker)")
    page = serializers.IntegerField(allow_null=True, help_text="Page number (null if not a page breaker)")
    rub = serializers.IntegerField(allow_null=True, help_text="Rub number (null if not a rub breaker)")
    manzil = serializers.IntegerField(allow_null=True, help_text="Manzil number (null if not a manzil breaker)")

class WordBreakersResponseSerializer(serializers.Serializer):
    """Serializer for the words_breakers endpoint response"""
    word_uuid = serializers.UUIDField(help_text="UUID of the word")
    line = serializers.IntegerField(help_text="Line number counter")

class WordBreakerDetailResponseSerializer(serializers.Serializer):
    """Serializer for individual word breaker responses"""
    word_uuid = serializers.UUIDField(help_text="UUID of the word")
    type = serializers.CharField(help_text="Breaker type (always 'line')")
