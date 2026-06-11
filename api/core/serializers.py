from rest_framework import serializers


class PublicIdRelatedField(serializers.PrimaryKeyRelatedField):
    def to_representation(self, value):
        public_id = getattr(value, "public_id", None)
        if public_id:
            return str(public_id)
        return super().to_representation(value)

    def to_internal_value(self, data):
        queryset = self.get_queryset()
        if queryset is not None:
            try:
                return queryset.get(public_id=data)
            except (TypeError, ValueError, queryset.model.DoesNotExist):
                pass
        return super().to_internal_value(data)


class PublicIdModelSerializer(serializers.ModelSerializer):
    serializer_related_field = PublicIdRelatedField

    id = serializers.UUIDField(source="public_id", read_only=True)
