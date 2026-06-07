"""
Helper per creare notifiche in-app.
Le email continuano a essere inviate separatamente tramite notifications/services.py.
Questo sistema le affianca con notifiche persistenti nell'interfaccia.

URL reali del progetto:
  - Approvals: approval_detail (no namespace), args=[approval_request.pk]
  - Documents: document_detail (no namespace), args=[document.pk]
  - ECN: ecn:ecn_detail (namespace ecn), args=[change_notice.pk]
"""
from django.urls import reverse
from .models import Notification


def _create(recipient, category, notification_type, title, message='', target_url=''):
    """Crea una Notification. Non lancia eccezioni se recipient è None."""
    if recipient is None:
        return
    Notification.objects.create(
        recipient=recipient,
        category=category,
        notification_type=notification_type,
        title=title,
        message=message,
        target_url=target_url,
    )


def _create_for_many(recipients, category, notification_type, title, message='', target_url=''):
    """Crea notifiche per una lista/set di utenti, deduplicando."""
    seen = set()
    for user in recipients:
        if user and user.pk not in seen:
            seen.add(user.pk)
            _create(user, category, notification_type, title, message, target_url)


# ─── Approvazioni documento ───────────────────────────────────────────────────

def notify_approval_requested(approval_request, approver):
    """Azione richiesta: approvare una revisione documento."""
    version = approval_request.document_version
    url = reverse('approval_detail', args=[approval_request.pk])
    _create(
        recipient=approver,
        category=Notification.CATEGORY_ACTION,
        notification_type='approval_requested',
        title=f'Approvazione richiesta: {version.document.code} rev.{version.revision_label}',
        message=f'È richiesta la tua approvazione per il documento "{version.document.title}".',
        target_url=url,
    )


def notify_version_approved(approval_request):
    """Info: revisione approvata — a autore e owner documento."""
    version = approval_request.document_version
    url = reverse('document_detail', args=[version.document.pk])
    recipients = {version.created_by, version.document.owner}
    _create_for_many(
        recipients,
        category=Notification.CATEGORY_INFO,
        notification_type='version_approved',
        title=f'Revisione approvata: {version.document.code} rev.{version.revision_label}',
        message=f'La revisione "{version.revision_label}" del documento "{version.document.title}" è stata approvata.',
        target_url=url,
    )


def notify_version_rejected(approval_request):
    """Info: revisione rifiutata — a autore versione."""
    version = approval_request.document_version
    url = reverse('document_detail', args=[version.document.pk])
    _create(
        recipient=version.created_by,
        category=Notification.CATEGORY_INFO,
        notification_type='version_rejected',
        title=f'Revisione rifiutata: {version.document.code} rev.{version.revision_label}',
        message=f'La revisione "{version.revision_label}" del documento "{version.document.title}" è stata rifiutata.',
        target_url=url,
    )


# ─── ECN ──────────────────────────────────────────────────────────────────────

def notify_ecn_created_inapp(change_notice):
    """Info a owner documento (se diverso da proponente) e Quality Manager."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    url = reverse('ecn:ecn_detail', args=[change_notice.pk])
    doc_owner = change_notice.document.owner
    if doc_owner and doc_owner != change_notice.proposed_by:
        _create(
            recipient=doc_owner,
            category=Notification.CATEGORY_INFO,
            notification_type='ecn_created',
            title=f'ECN proposta sul tuo documento: {change_notice.code}',
            message=f'È stata proposta la variante "{change_notice.title}" sul documento "{change_notice.document.title}".',
            target_url=url,
        )
    # Quality Manager
    qm_users = User.objects.filter(groups__name='Quality Manager', is_active=True)
    for u in qm_users:
        if u != change_notice.proposed_by:
            _create(
                recipient=u,
                category=Notification.CATEGORY_INFO,
                notification_type='ecn_created',
                title=f'Nuova ECN da prendere in carico: {change_notice.code}',
                message=f'È stata proposta la variante "{change_notice.title}" da {change_notice.proposed_by.get_full_name() or change_notice.proposed_by.username}.',
                target_url=url,
            )


def notify_ccb_dossier_assigned(change_notice):
    """Azione richiesta: compilare il dossier CCB."""
    coordinator = change_notice.ccb_coordinator
    if not coordinator:
        return
    url = reverse('ecn:ecn_detail', args=[change_notice.pk])
    _create(
        recipient=coordinator,
        category=Notification.CATEGORY_ACTION,
        notification_type='ccb_dossier_assigned',
        title=f'Dossier CCB da compilare: {change_notice.code}',
        message=f"Sei stato nominato coordinatore dell'istruttoria per la variante \"{change_notice.title}\".",
        target_url=url,
    )


def notify_ccb_vote_required(change_notice, approver_user):
    """Azione richiesta: votare in CCB."""
    url = reverse('ecn:ecn_detail', args=[change_notice.pk])
    _create(
        recipient=approver_user,
        category=Notification.CATEGORY_ACTION,
        notification_type='ccb_vote_required',
        title=f'Voto CCB richiesto: {change_notice.code}',
        message=f'La variante "{change_notice.title}" richiede la tua decisione in CCB.',
        target_url=url,
    )


def notify_ecn_outcome_inapp(change_notice, approved: bool):
    """Info: esito ECN — a tutti gli interessati."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    url = reverse('ecn:ecn_detail', args=[change_notice.pk])
    status_label = 'approvata' if approved else 'rifiutata'
    recipients = set()
    recipients.add(change_notice.proposed_by)
    doc_owner = change_notice.document.owner
    if doc_owner:
        recipients.add(doc_owner)
    if change_notice.ccb_coordinator:
        recipients.add(change_notice.ccb_coordinator)
    for a in change_notice.changenoticeapprover_set.select_related('user').all():
        recipients.add(a.user)
    for u in User.objects.filter(groups__name='Quality Manager', is_active=True):
        recipients.add(u)

    for recipient in recipients:
        if recipient is None:
            continue
        extra = ''
        if approved and doc_owner and recipient.pk == doc_owner.pk:
            extra = ' Puoi ora creare la nuova revisione autorizzata.'
        _create(
            recipient=recipient,
            category=Notification.CATEGORY_INFO,
            notification_type='ecn_approved' if approved else 'ecn_rejected',
            title=f'ECN {status_label}: {change_notice.code}',
            message=f'La variante "{change_notice.title}" è stata {status_label}.{extra}',
            target_url=url,
        )


def notify_ecn_executed_inapp(change_notice):
    """Info: ECN eseguita (nuova revisione creata)."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    url = reverse('ecn:ecn_detail', args=[change_notice.pk])
    recipients = set()
    recipients.add(change_notice.proposed_by)
    if change_notice.ccb_coordinator:
        recipients.add(change_notice.ccb_coordinator)
    for u in User.objects.filter(groups__name='Quality Manager', is_active=True):
        recipients.add(u)
    _create_for_many(
        recipients,
        category=Notification.CATEGORY_INFO,
        notification_type='ecn_executed',
        title=f'ECN eseguita: {change_notice.code}',
        message=f'La nuova revisione per la variante "{change_notice.title}" è stata creata.',
        target_url=url,
    )


def notify_ecn_closed_inapp(change_notice):
    """Info: ECN chiusa."""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    url = reverse('ecn:ecn_detail', args=[change_notice.pk])
    recipients = set()
    recipients.add(change_notice.proposed_by)
    if change_notice.document.owner:
        recipients.add(change_notice.document.owner)
    if change_notice.ccb_coordinator:
        recipients.add(change_notice.ccb_coordinator)
    for a in change_notice.changenoticeapprover_set.select_related('user').all():
        recipients.add(a.user)
    for u in User.objects.filter(groups__name='Quality Manager', is_active=True):
        recipients.add(u)
    _create_for_many(
        recipients,
        category=Notification.CATEGORY_INFO,
        notification_type='ecn_closed',
        title=f'ECN chiusa: {change_notice.code}',
        message=f'La variante "{change_notice.title}" è stata chiusa.',
        target_url=url,
    )
