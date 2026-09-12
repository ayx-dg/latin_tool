from django.contrib import admin
from index.models import Works, Contents, GlossCache

admin.site.register(Works)
admin.site.register(GlossCache)

@admin.register(Contents)
class ContentsAdmin(admin.ModelAdmin):
    list_display = ('work', 'path', 'global_order')
    list_filter = ('work', 'path')
    search_fields = ('text',)
