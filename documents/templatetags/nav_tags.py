"""Template tags per la navigazione sidebar.

Fornisce helper leggeri per:
- contatori badge (approvazioni pendenti, CCB, ECN, bozze)
- controlli ruolo per mostrare/nascondere voci di menu

MB1: is_staff NON concede bypass applicativo automatico.
"""
from django import template

register = template.Library()


# ---------------------------------------------------------------------------
# Controlli ruolo — usati nella sidebar per mostrare/nascondere sezioni
# ---------------------------------------------------------------------------

@register.simple_tag
def user_is_manager(user):
    """True se l'utente è Document Manager o superuser."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    from documents.permissions import is_document_manager
    return is_document_manager(user)


@register.simple_tag
def user_is_auditor(user):
    """True se l'utente è Document Auditor o superuser."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    from documents.permissions import is_document_auditor
    return is_document_auditor(user)


@register.simple_tag
def user_can_quality_workspace(user):
    """True se l'utente può accedere al workspace Qualità.
    MB1: Quality Manager, Quality Operator, Document Auditor, superuser."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    from documents.permissions import (
        is_quality_manager,
        is_quality_operator,
        is_document_auditor,
    )
    return is_quality_manager(user) or is_quality_operator(user) or is_document_auditor(user)


@register.simple_tag
def user_can_create_doc(user):
    """True se l'utente può creare nuovi documenti."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    from documents.permissions import can_create_document
    return can_create_document(user)


@register.simple_tag
def user_can_create_folder(user):
    """True se l'utente può creare cartelle. MB1: solo Document Manager e superuser."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    from documents.permissions import is_document_manager
    return is_document_manager(user)


@register.simple_tag
def user_can_create_project(user):
    """True se l'utente può creare progetti. MB1: solo Document Manager e superuser."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    from documents.permissions import is_document_manager
    return is_document_manager(user)


# ---------------------------------------------------------------------------
# Contatori badge — query leggere per sidebar
# ---------------------------------------------------------------------------

@register.simple_tag
def nav_pending_approvals(user):
    """Numero di richieste di approvazione documento pendenti per l'utente."""
    if not user or not user.is_authenticated:
        return 0
    try:
        from approvals.models import ApprovalRequest
        return ApprovalRequest.objects.filter(
            status=ApprovalRequest.Status.PENDING,
            approvers__approver=user,
        ).count()
    except Exception:
        return 0


@register.simple_tag
def nav_pending_ccb(user):
    """Numero di decisioni CCB assegnate e non ancora espresse."""
    if not user or not user.is_authenticated:
        return 0
    try:
        from ecn.models import ChangeNotice, ChangeNoticeApprover, ChangeNoticeDecision
        decided_ids = set(
            ChangeNoticeDecision.objects.filter(user=user)
            .values_list('approver_id', flat=True)
        )
        return (
            ChangeNoticeApprover.objects
            .filter(user=user, change_notice__status=ChangeNotice.Status.UNDER_REVIEW)
            .exclude(pk__in=decided_ids)
            .count()
        )
    except Exception:
        return 0


@register.simple_tag
def nav_my_ecn_open(user):
    """Numero di ECN aperti (draft/ccb_preparation/under_review/approved) proposti dall'utente."""
    if not user or not user.is_authenticated:
        return 0
    try:
        from django.db.models import Q
        from ecn.models import ChangeNotice
        return ChangeNotice.objects.filter(
            Q(proposed_by=user) | Q(created_by=user),
            status__in=[
                ChangeNotice.Status.DRAFT,
                ChangeNotice.Status.CCB_PREPARATION,
                ChangeNotice.Status.UNDER_REVIEW,
                ChangeNotice.Status.APPROVED,
            ],
        ).distinct().count()
    except Exception:
        return 0


@register.simple_tag
def nav_my_drafts(user):
    """Numero di revisioni documento in bozza/rifiutate create dall'utente."""
    if not user or not user.is_authenticated:
        return 0
    try:
        from documents.models import DocumentVersion
        return DocumentVersion.objects.filter(
            created_by=user,
            status__in=[DocumentVersion.Status.DRAFT, DocumentVersion.Status.REJECTED],
        ).count()
    except Exception:
        return 0


@register.simple_tag
def nav_ecn_to_review(user):
    """ECN draft senza CCB configurata — da valutare (solo per Quality Manager/Operator)."""
    if not user or not user.is_authenticated:
        return 0
    try:
        if not user.is_superuser:
            from documents.permissions import is_quality_manager, is_quality_operator
            if not (is_quality_manager(user) or is_quality_operator(user)):
                return 0
        from ecn.models import ChangeNotice, ChangeNoticeApprover
        draft_ids = ChangeNotice.objects.filter(
            status=ChangeNotice.Status.DRAFT
        ).values_list('pk', flat=True)
        configured_ids = ChangeNoticeApprover.objects.filter(
            change_notice_id__in=draft_ids
        ).values_list('change_notice_id', flat=True).distinct()
        return ChangeNotice.objects.filter(
            status=ChangeNotice.Status.DRAFT
        ).exclude(pk__in=configured_ids).count()
    except Exception:
        return 0


@register.simple_tag
def nav_ecn_to_close(user):
    """ECN approvati con revisione eseguita, da chiudere (solo Quality Manager)."""
    if not user or not user.is_authenticated:
        return 0
    try:
        if not user.is_superuser:
            from documents.permissions import is_quality_manager
            if not is_quality_manager(user):
                return 0
        from ecn.models import ChangeNotice
        return ChangeNotice.objects.filter(
            status=ChangeNotice.Status.APPROVED,
            executed_version__isnull=False,
        ).count()
    except Exception:
        return 0
