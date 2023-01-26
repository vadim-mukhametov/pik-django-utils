from typing import Optional, Union
from uuid import UUID

from django.utils.translation import gettext_lazy as _
from django_documents_tools.api.serializers import (
    BaseChangeSerializer, BaseSnapshotSerializer,
    BaseDocumentedModelLinkSerializer, BaseChangeAttachmentSerializer,
    BaseChangeAttachmentLinkSerializer, BaseSnapshotLinkSerializer, )
from django_restql.mixins import DynamicFieldsMixin
from model_utils.managers import InheritanceManager
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.serializers import ModelSerializer

from .camelcase.serializers import CamelizeSerializerHookMixIn
from .constants import SOFT_DELETE_FIELDS
from .deprecated.serializers import UnderscorizeSerializerHookMixIn
from .lazy_field import LazyFieldHandlerMixIn
from .restql import DefaultRequestQueryParserMixin
from .validators import NewestUpdateValidator


def _normalize_label(label):
    if label:
        return label.capitalize()
    return label


class SettableNestedSerializerMixIn:
    default_error_messages = {
        'required': _('This field is required.'),
        'does_not_exist':
            _('Недопустимый guid "{uid_value}" - объект не существует.'),
        'incorrect_uid_type':
            _('Недопустимый guid. Ожидался uuid, получен "{data_type}".'),
        'incorrect_type':
            _('Некорректный тип объекта. Ожидался "{expected_object_type}". '
              'Получен "{object_type}".')}

    def run_validators(self, value):
        if not self.parent:
            return super().run_validators(value)
        return None

    def to_internal_value(self, request_data):
        if not self.parent:
            return super().to_internal_value(request_data)

        object_type = ""
        if hasattr(request_data, 'get'):  # request_data could be other types
            object_type = request_data.get('type', None)

        # We are unable to recover `CamelCase` from `lowercase`
        # so doing it here instead of DeprecatedParser
        if object_type:
            object_type = object_type.lower()

        model = self.Meta.model
        expected = [model._meta.model_name]

        # Check type through multi-table children too
        if isinstance(model.objects, InheritanceManager):
            expected.extend(model.objects.all()._get_subclasses_recurse(model))  # noqa: We just want to find all possible children

        if object_type not in expected:
            self.fail(
                'incorrect_type', expected_object_type=", ".join(expected),
                object_type=object_type)
        uid_value = request_data.get('guid')
        try:
            return model.objects.get(uid=uid_value)
        except model.DoesNotExist:
            self.fail('does_not_exist', uid_value=uid_value)
        except (TypeError, ValueError):
            self.fail('incorrect_uid_type', data_type=type(uid_value).__name__)

        return None


class StandardizedProtocolSerializer(serializers.ModelSerializer):
    guid = serializers.SerializerMethodField()
    type = serializers.SerializerMethodField()
    version = serializers.SerializerMethodField()

    def include_extra_kwargs(self, kwargs, extra_kwargs):
        """
        Override parent method to include kwargs for readonly fields
        Include any 'extra_kwargs' that have been included for this field,
        possibly removing any incompatible existing keyword arguments.
        """
        if extra_kwargs.get('default') and kwargs.get('required') is False:
            kwargs.pop('required')

        if extra_kwargs.get('read_only', kwargs.get('read_only', False)):
            # Read only fields should always omit the 'required' argument.
            extra_kwargs.pop('required', None)

        kwargs.update(extra_kwargs)

        return kwargs

    @staticmethod
    def deprecated_type_field_hook(obj):
        return obj._meta.model_name

    @staticmethod
    def get_guid(obj) -> Optional[Union[UUID, str]]:
        if not hasattr(obj, 'uid'):
            if not hasattr(obj, 'pk'):
                return None
            return str(obj.pk)
        return obj.uid

    def get_type(self, obj) -> Optional[str]:
        if 'type_field_hook' not in self.context:
            return obj._meta.model_name
        return self.context['type_field_hook'](self, obj) or (
            obj._meta.object_name)

    @staticmethod
    def get_version(obj) -> Optional[int]:
        if not hasattr(obj, 'version'):
            return None
        return obj.version


class LabeledModelSerializerMixIn:
    """
    Default DRF ModelSerializer has different nature than DRF Field

    1. DRF ModelSerializer doesn't handle labels and help_texts as expected.
    2. DRF ModelSerializer has 2 different behaviours:
        - initialized for direct use within viewset,
        - initialized and binded for use within other serializer as field.
    3. So label and help_text handling should be done during two stages:
        - `__init__()` for using within viewset,
        - `bind()` for use as field withing other serializer.

    This MixIn:
        - on `__init__()`:
            - sets label and help_text to model values if not provided,
            - saves `is_set`=True if provided;
        - on `bind()`:
            - sets parent model field fetched `label` and `help_text`
            values if `is_set`=`False`.
    """

    _label_is_set = False
    _help_text_is_set = False

    def __init__(self, *args, **kwargs):
        opts = self.Meta.model._meta

        if 'label' not in kwargs:
            kwargs['label'] = _normalize_label(opts.verbose_name)
        else:
            self._label_is_set = True

        if 'help_text' not in kwargs:
            kwargs['help_text'] = getattr(self.Meta.model, '_help_text', None)
        else:
            self._help_text_is_set = True

        self.label_plural = _normalize_label(opts.verbose_name_plural)
        if 'label_plural' in kwargs:
            self.label_plural = kwargs.pop('label_plural')

        super().__init__(*args, **kwargs)

    def bind(self, field_name, parent):
        super().bind(field_name, parent)
        if isinstance(parent, ModelSerializer):
            opts = parent.Meta.model._meta
            if not self._label_is_set:
                self.label = _normalize_label(  # noqa pylint: disable=attribute-defined-outside-init
                    opts.get_field(self.source).verbose_name)
            if not self._help_text_is_set:
                self.help_text = opts.get_field(self.source).help_text  # noqa pylint: disable=attribute-defined-outside-init


class ValidatedModelSerializerMixIn:
    """
    Allows using model defined validators due to missing DRF clean handling
    """

    def validate(self, attrs):
        if not self.parent:
            errors = []
            validators = getattr(self.Meta.model, 'validators', ())
            for validator in validators:
                try:
                    validator(
                        attrs, self.instance, self.Meta.model,
                        serializers.ValidationError)
                except serializers.ValidationError as exc:
                    errors.append(exc.detail)
            if errors:
                raise serializers.ValidationError(
                    dict(zip(range(0, len(errors)), errors)))
        return super().validate(attrs)


class NewestUpdateValidatorMixIn:
    class Meta:
        validators = [NewestUpdateValidator()]


class DynamicModelSerializerMixIn:
    """
    A ModelSerializer that takes an additional `fields` argument that
    controls which fields should be displayed.
    https://github.com/CarlosMart626/djangocon-2019/blob/master/five_minutes/five_minutes/serializers.py
    """

    def get_field_names(self, declared_fields, info) -> str:
        field_names = super().get_field_names(  # type: ignore
            declared_fields, info)
        if self.dynamic_fields is not None:
            # Drop any fields that are not specified in the `fields` argument.
            allowed = set(self.dynamic_fields)
            excluded_field_names = set(field_names) - allowed
            field_names = tuple(
                x for x in field_names if x not in excluded_field_names)
        return field_names

    def __init__(self, *args, **kwargs):
        # Don't pass the 'fields' or 'read_only_fields'
        # arg up to the superclass
        self.dynamic_fields = kwargs.pop('fields', None)
        self.read_only_fields = kwargs.pop('read_only_fields', [])

        # Instantiate the superclass normally
        super().__init__(*args, **kwargs)


class PermittedFieldsPermissionMixIn:
    use_obj_perms = False

    def has_field_permission(self, user, model, field, obj=None):
        permitted_fields = getattr(
            self, 'permitted_fields', getattr(model, 'permitted_fields', None))
        if not permitted_fields:
            return False
        for permission, _fields in permitted_fields.items():
            meta = model._meta
            permission = permission.format(
                app_label=meta.app_label.lower(),
                model_name=meta.object_name.lower())

            if self.use_obj_perms:
                has_perm = (
                    field in _fields and user.has_perm(permission, obj=obj))
            else:
                has_perm = (field in _fields and user.has_perm(permission))
            if has_perm:
                return True
        return False


class PermittedFieldsSerializerMixIn(PermittedFieldsPermissionMixIn):
    default_error_messages = {
        'field_permission_denied': _(
            'У вас нет прав для редактирования этого поля.')}

    def to_internal_value(self, request_data):
        errors = {}
        ret = super().to_internal_value(request_data)

        if 'request' not in self.context:
            return ret
        user = self.context['request'].user
        model = self.Meta.model

        for field in ret.keys():
            if self.has_field_permission(user, model, field, self.instance):
                continue
            errors[field] = [self.error_messages['field_permission_denied']]

        if errors:
            raise ValidationError(errors)

        return ret


class StandardizedModelSerializer(
        DefaultRequestQueryParserMixin,
        DynamicFieldsMixin,
        LazyFieldHandlerMixIn,
        LabeledModelSerializerMixIn,
        SettableNestedSerializerMixIn,
        ValidatedModelSerializerMixIn,
        NewestUpdateValidatorMixIn,
        DynamicModelSerializerMixIn,
        PermittedFieldsSerializerMixIn,
        StandardizedProtocolSerializer,
):

    # we pass soft deleted logic here because drf-yasg can't find type of
    # `is_deleted` if it declared on parent's parent
    is_deleted = serializers.SerializerMethodField()

    @staticmethod
    def get_is_deleted(obj) -> bool:
        return bool(obj.deleted)


class StandardizedChangeSerializer(
        CamelizeSerializerHookMixIn, UnderscorizeSerializerHookMixIn,
        BaseChangeSerializer, StandardizedModelSerializer):

    class Meta(BaseChangeSerializer.Meta):
        fields = BaseChangeSerializer.Meta.fields + SOFT_DELETE_FIELDS
        extra_kwargs: dict = {}


class StandardizedSnapshotSerializer(
        CamelizeSerializerHookMixIn, UnderscorizeSerializerHookMixIn,
        BaseSnapshotSerializer, StandardizedModelSerializer):

    class Meta(BaseSnapshotSerializer.Meta):
        fields = BaseSnapshotSerializer.Meta.fields + SOFT_DELETE_FIELDS


class StandardizedSnapshotLinkSerializer(
        CamelizeSerializerHookMixIn, UnderscorizeSerializerHookMixIn,
        BaseSnapshotLinkSerializer, StandardizedModelSerializer):

    class Meta(BaseSnapshotLinkSerializer.Meta):
        fields = BaseSnapshotLinkSerializer.Meta.fields + SOFT_DELETE_FIELDS


class StandardizedDocumentedModelLinkSerializer(
        CamelizeSerializerHookMixIn, UnderscorizeSerializerHookMixIn,
        BaseDocumentedModelLinkSerializer, StandardizedModelSerializer):

    class Meta(BaseDocumentedModelLinkSerializer.Meta):
        fields = (
            BaseDocumentedModelLinkSerializer.Meta.fields + SOFT_DELETE_FIELDS)


class StandardizedChangeAttachmentSerializer(
        CamelizeSerializerHookMixIn, UnderscorizeSerializerHookMixIn,
        BaseChangeAttachmentSerializer, StandardizedModelSerializer):

    class Meta(BaseChangeAttachmentSerializer.Meta):
        fields = (
            BaseChangeAttachmentSerializer.Meta.fields + SOFT_DELETE_FIELDS)


class StandardizedChangeAttachmentLinkSerializer(
        CamelizeSerializerHookMixIn, UnderscorizeSerializerHookMixIn,
        BaseChangeAttachmentLinkSerializer, StandardizedModelSerializer):

    class Meta(BaseChangeAttachmentLinkSerializer.Meta):
        fields = (
            BaseChangeAttachmentLinkSerializer.Meta.fields + ('file',)
            + SOFT_DELETE_FIELDS)
