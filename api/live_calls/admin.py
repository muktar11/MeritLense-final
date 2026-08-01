from django.contrib import admin

from .models import LiveCallParticipant, LiveCallSession

admin.site.register(LiveCallSession)
admin.site.register(LiveCallParticipant)

