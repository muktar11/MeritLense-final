from django.contrib import admin

# Deliberately not registered for editing here. A plain admin.ModelAdmin
# would let a row be saved in place, bypassing
# KnowledgeContentService.create_new_version (the versioning/audit-trail
# guarantee this app exists to provide) - content changes must go through
# the authenticated KnowledgeEntryViewSet API instead.
