from django.contrib import admin

from .models import Document, DocumentVersion, DocumentFile


class DocumentVersionInline(admin.TabularInline):
    model = DocumentVersion
    extra = 0
    fields = ('version_number', 'status', 'author', 'effective_date', 'created_at')
    readonly_fields = ('created_at',)
    show_change_link = True


class DocumentFileInline(admin.TabularInline):
    model = DocumentFile
    extra = 0
    fields = ('file', 'original_filename', 'mime_type', 'file_size', 'uploaded_by', 'uploaded_at')
    readonly_fields = ('uploaded_at',)


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ('code', 'title', 'category', 'owner', 'is_active', 'created_at')
    list_filter = ('category', 'is_active')
    search_fields = ('code', 'title')
    inlines = [DocumentVersionInline]


@admin.register(DocumentVersion)
class DocumentVersionAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'document', 'version_number', 'status', 'author', 'effective_date', 'created_at')
    list_filter = ('status',)
    search_fields = ('document__code', 'document__title', 'version_number')
    inlines = [DocumentFileInline]


@admin.register(DocumentFile)
class DocumentFileAdmin(admin.ModelAdmin):
    list_display = ('original_filename', 'version', 'mime_type', 'file_size', 'uploaded_by', 'uploaded_at')
    search_fields = ('original_filename', 'version__document__code')
