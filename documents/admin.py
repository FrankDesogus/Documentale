from django.contrib import admin

from .models import Document, DocumentFile, DocumentVersion


class DocumentVersionInline(admin.TabularInline):
    model = DocumentVersion
    extra = 0
    fields = (
        'revision_label', 'revision_number', 'status',
        'is_current', 'created_by', 'submitted_at', 'approved_at',
    )
    readonly_fields = ('is_current', 'submitted_at', 'approved_at', 'created_at')
    show_change_link = True


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = (
        'code', 'title', 'revision_scheme', 'category', 'document_type',
        'status', 'owner', 'current_version', 'created_at',
    )
    list_filter = ('revision_scheme', 'category', 'status', 'document_type')
    search_fields = ('code', 'title', 'description')
    readonly_fields = ('current_version', 'created_by', 'created_at', 'updated_at')
    inlines = [DocumentVersionInline]


@admin.register(DocumentVersion)
class DocumentVersionAdmin(admin.ModelAdmin):
    list_display = (
        'document', 'revision_label', 'revision_number',
        'status', 'is_current', 'created_by',
        'submitted_at', 'approved_at', 'approved_by',
    )
    list_filter = ('status', 'is_current', 'submitted_at', 'approved_at')
    search_fields = (
        'document__code', 'document__title',
        'revision_label', 'change_summary',
    )
    # status è lasciato modificabile per debug, ma in produzione
    # le transizioni di stato devono passare dai service.
    readonly_fields = (
        'submitted_at', 'approved_at', 'approved_by',
        'rejected_at', 'is_current', 'replaces_version', 'created_at',
    )


@admin.register(DocumentFile)
class DocumentFileAdmin(admin.ModelAdmin):
    list_display = (
        'original_filename', 'extension', 'size',
        'sha256_hash', 'uploaded_by', 'uploaded_at',
    )
    search_fields = ('original_filename', 'sha256_hash')
    readonly_fields = ('uploaded_at',)
