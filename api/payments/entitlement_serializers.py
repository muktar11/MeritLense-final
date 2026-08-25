from rest_framework import serializers

from .entitlement_services import ADDON_POINTS_CATALOG


class AddonSpendSerializer(serializers.Serializer):
    addon_code = serializers.ChoiceField(choices=list(ADDON_POINTS_CATALOG.keys()))
