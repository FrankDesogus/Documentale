from django.contrib import admin

from .models import (
    FolderPermissionGrant,
    Project,
    ProjectFolder,
    ProjectFolderMembership,
    ProjectRevision,
    ProjectRevisionItem,
)


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
    fk_name = 'project'
    extra = 0
    fields = ('revision_label', 'revision_number', 'title', 'status', 'is_current', 'created_at')
    readonly_fields = ('created_at',)
    show_change_link = True


class ProjectRevisionItemInline(admin.TabularInline):
    model = ProjectRevisionItem
    extra = 0
    fields = ('item_number', 'description', 'document_version', 'notes')


class MembershipInline(admin.TabularInline):
    model = ProjectFolderMembership
    extra = 0
    fields = ('user', 'role', 'created_by', 'created_at')
    readonly_fields = ('created_at',)
    autocomplete_fields = ('user',)


@admin.register(ProjectFolder)
class ProjectFolderAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'folder_kind', 'parent', 'status', 'created_by', 'created_at')
    list_filter = ('folder_kind', 'status')
    search_fields = ('code', 'name')
    autocomplete_fields = ('parent',)
    inlines = [SubfolderInline, MembershipInline]


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'version', 'revision', 'project_type', 'root_folder', 'root_parent_folder', 'manager', 'created_at')
    list_filter = ('project_type',)
    search_fields = ('code', 'name', 'description', 'root_folder__code', 'root_folder__name')
    readonly_fields = ('created_at', 'updated_at')
    autocomplete_fields = ('root_folder', 'manager')
    inlines = [ProjectRevisionInline]

    @admin.display(description='Cartella padre', ordering='root_folder__parent__code')
    def root_parent_folder(self, obj):
        if obj.root_folder and obj.root_folder.parent:
            return obj.root_folder.parent.code
        return '—'


@admin.register(ProjectFolderMembership)
class ProjectFolderMembershipAdmin(admin.ModelAdmin):
    list_display = ('folder', 'user', 'role', 'created_at')
    list_filter = ('role', 'folder')
    search_fields = ('folder__code', 'folder__name', 'user__username', 'user__email')


@admin.register(ProjectRevision)
class ProjectRevisionAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'project', 'revision_label', 'revision_number', 'status', 'is_current', 'created_at')
    list_filter = ('status', 'is_current')
    search_fields = ('project__code', 'title', 'revision_label')
    readonly_fields = ('created_at',)
    inlines = [ProjectRevisionItemInline]


@admin.register(ProjectRevisionItem)
class ProjectRevisionItemAdmin(admin.ModelAdmin):
    list_display = ('revision', 'item_number', 'description', 'document_version')
    search_fields = ('description', 'revision__project__code')


@admin.register(FolderPermissionGrant)
class FolderPermissionGrantAdmin(admin.ModelAdmin):
    list_display = (
        'folder', 'user', 'group', 'permission_code',
        'effect', 'inherit_to_children', 'expires_at', 'created_at',
    )
    list_filter = (
        'effect',
        'permission_code',
        'inherit_to_children',
        ('expires_at', admin.EmptyFieldListFilter),
    )
    search_fields = (
        'folder__code', 'folder__name',
        'user__username', 'user__email',
        'group__name',
    )
    ordering = ('folder', 'permission_code', 'effect')
    readonly_fields = ('created_at',)
    autocomplete_fields = ('folder', 'user', 'group', 'created_by')
