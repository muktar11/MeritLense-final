from uuid import UUID

from django.shortcuts import get_object_or_404


PUBLIC_ID_OR_PK_REGEX = (
    "[0-9]+|"
    "[0-9a-fA-F]{8}-"
    "[0-9a-fA-F]{4}-"
    "[0-9a-fA-F]{4}-"
    "[0-9a-fA-F]{4}-"
    "[0-9a-fA-F]{12}"
)


def is_public_id(value):
    if value in (None, ""):
        return False
    try:
        UUID(str(value))
        return True
    except (TypeError, ValueError, AttributeError):
        return False


def is_legacy_pk(value):
    return str(value).isdigit() if value not in (None, "") else False


def build_identifier_filter(field_name, value):
    if is_public_id(value):
        return {f"{field_name}__public_id": value}
    if is_legacy_pk(value):
        return {f"{field_name}_id": value}
    raise ValueError(f"Invalid identifier: {value}")


def build_object_identifier_filter(value):
    if is_public_id(value):
        return {"public_id": value}
    if is_legacy_pk(value):
        return {"id": value}
    raise ValueError(f"Invalid identifier: {value}")


def filter_by_identifier(queryset, field_name, value):
    if value in (None, ""):
        return queryset
    try:
        return queryset.filter(**build_identifier_filter(field_name, value))
    except ValueError:
        return queryset.none()


def get_by_identifier(queryset_or_manager, value):
    try:
        return queryset_or_manager.get(**build_object_identifier_filter(value))
    except ValueError as exc:
        model = getattr(queryset_or_manager, "model", None)
        if model is None:
            raise
        raise model.DoesNotExist(str(exc)) from exc


class PublicIdLookupMixin:
    lookup_field = "public_id"
    lookup_url_kwarg = "id"
    lookup_value_regex = PUBLIC_ID_OR_PK_REGEX

    def get_object(self):
        queryset = self.filter_queryset(self.get_queryset())
        lookup_url_kwarg = self.lookup_url_kwarg or self.lookup_field
        lookup_value = self.kwargs.get(lookup_url_kwarg)

        obj = get_object_or_404(queryset, **build_object_identifier_filter(lookup_value))

        self.check_object_permissions(self.request, obj)
        return obj
