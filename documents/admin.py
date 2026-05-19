from django.contrib import admin

from .models import Document, DocumentVersion, DocumentFile


class DocumentVersionInline(admin.TabularInline):
    model = DocumentVersion
    extra = 0
    fields = ('revision_label', 'revision_number', 'status', 'created_by', 'is_current', 'created_at')
    readonly_fields = ('created_at',)
    show_change_link = True


@admin.register(DocumentFile)
class DocumentFileAdmin(admin.ModelAdmin):
    list_display = ('original_filename', 'extension', 'mime_type', 'size', 'sha256_hash', 'uploaded_by', 'uploaded_at')
    search_fields = ('original_filename', 'sha256_hash')
    readonly_fields = ('uploaded_at',)


@admin.register(DocumentVersion)
class DocumentVersionAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'document', 'revision_label', 'revision_number', 'status', 'is_current', 'created_by', 'created_at')
    list_filter = ('status', 'is_current')
    search_fields = ('document__code', 'document__title', 'revision_label')
    readonly_fields = ('created_at',)


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ('code', 'title', 'category', 'document_type', 'status', 'owner', 'current_version', 'created_at')
    list_filter = ('category', 'status')
    search_fields = ('code', 'title')
    inlines = [DocumentVersionInline]
