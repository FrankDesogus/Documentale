from django.contrib import admin

from .models import ProjectFolder, ProjectRevision, ProjectRevisionItem


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
    list_display = ('code', 'name', 'owner', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('code', 'name')
    inlines = [ProjectRevisionInline]


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
