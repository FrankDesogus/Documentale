from auditlog.models import AuditLog


def create_audit_log(
    user,
    action,
    instance,
    old_values=None,
    new_values=None,
    document=None,
    document_version=None,
    metadata=None,
):
    changes = {}
    if old_values:
        changes['old_values'] = old_values
    if new_values:
        changes['new_values'] = new_values
    if document is not None:
        changes['document_id'] = document.pk
    if document_version is not None:
        changes['document_version_id'] = document_version.pk
    if metadata:
        changes['metadata'] = metadata

    AuditLog.objects.create(
        user=user,
        action=action,
        app_label=instance._meta.app_label,
        model_name=instance._meta.model_name,
        object_id=str(instance.pk),
        object_repr=str(instance)[:255],
        changes=changes if changes else None,
    )
