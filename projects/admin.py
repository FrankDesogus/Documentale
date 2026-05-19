from django.contrib import admin

from .models import ProjectFolder, ProjectRevision, ProjectRevisionItem


class SubfolderInline(admin.TabularInline):
    model = ProjectFolder
    fk_name = 'parent'
    extra = 0
    fields = ('code', 'name', 'folder_kind', 'status')
    show_change_link = True
    verbose_name = 'Sottocartella'
    verbose_name_plural = 'Sottocartelle'


class ProjectRevisionInline(admin.TabularInline):
    model = ProjectRevision
    extra = 0
    fields = ('revision_code', 'title', 'status', 'author', 'created_at')
    readonly_fields = ('created_at',)
    show_change_link = True


class ProjectRevisionItemInline(admin.TabularInline):
    model = ProjectRevisionItem
    extra = 0
    fields = ('item_number', 'description', 'document_version', 'notes')


@admin.register(ProjectFolder)
class ProjectFolderAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'folder_kind', 'parent', 'status', 'created_by', 'created_at')
    list_filter = ('folder_kind', 'status')
    search_fields = ('code', 'name')
    autocomplete_fields = ('parent',)
    inlines = [SubfolderInline, ProjectRevisionInline]


@admin.register(ProjectRevision)
class ProjectRevisionAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'project_folder', 'revision_code', 'status', 'author', 'created_at')
    list_filter = ('status',)
    search_fields = ('project_folder__code', 'title')
    inlines = [ProjectRevisionItemInline]


@admin.register(ProjectRevisionItem)
class ProjectRevisionItemAdmin(admin.ModelAdmin):
    list_display = ('revision', 'item_number', 'description', 'document_version')
    search_fields = ('description', 'revision__project_folder__code')
