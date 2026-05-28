"""View ECN / Varianti (ECN-B: UI minima)."""

import os

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render

from ecn.models import ChangeNotice, ChangeNoticeAttachment
from ecn.permissions import (
    can_add_ecn_attachment,
    can_close_ecn,
    can_create_ecn,
    can_review_ecn,
    can_submit_ecn,
    can_view_ecn,
)


# ---------------------------------------------------------------------------
# Lista ECN
# ---------------------------------------------------------------------------

@login_required
def ecn_list(request):
    """
    Lista degli ECN visibili all'utente corrente.

    Superuser/staff/manager/auditor/CCB vedono tutto.
    Gli altri vedono solo i propri ECN (proposed_by o created_by) più
    quelli su documenti delle cartelle dove hanno un ruolo.
    """
    from django.db.models import Q
    user = request.user

    from documents.permissions import GROUP_AUDITORS, GROUP_MANAGERS
    from ecn.permissions import GROUP_CCB, _in_group, _is_superuser_or_staff

    if _is_superuser_or_staff(user) or _in_group(user, GROUP_MANAGERS, GROUP_AUDITORS, GROUP_CCB):
        qs = ChangeNotice.objects.select_related(
            'document', 'proposed_by', 'document_version',
        ).order_by('-proposed_at')
    else:
        # Propri ECN
        own_filter = Q(proposed_by=user) | Q(created_by=user)
        # ECN su documenti in cartelle dove l'utente ha un ruolo
        from projects.permissions import get_visible_folder_ids
        visible_folder_ids = get_visible_folder_ids(user)
        folder_filter = Q(document__project_folder_id__in=visible_folder_ids)
        qs = ChangeNotice.objects.filter(
            own_filter | folder_filter
        ).select_related(
            'document', 'proposed_by', 'document_version',
        ).distinct().order_by('-proposed_at')

    # Filtro stato opzionale via GET
    status_filter = request.GET.get('status', '')
    if status_filter and status_filter in [s.value for s in ChangeNotice.Status]:
        qs = qs.filter(status=status_filter)

    return render(request, 'ecn/ecn_list.html', {
        'ecns': qs,
        'status_choices': ChangeNotice.Status.choices,
        'current_status': status_filter,
    })


# ---------------------------------------------------------------------------
# Dettaglio ECN
# ---------------------------------------------------------------------------

@login_required
def ecn_detail(request, ecn_id):
    ecn = get_object_or_404(
        ChangeNotice.objects.select_related(
            'document', 'document_version', 'project',
            'proposed_by', 'created_by',
            'ccb_reviewed_by', 'closed_by', 'executed_version',
        ),
        pk=ecn_id,
    )

    if not can_view_ecn(request.user, ecn):
        raise Http404

    attachments = ecn.attachments.select_related('uploaded_by').order_by('uploaded_at')

    return render(request, 'ecn/ecn_detail.html', {
        'ecn': ecn,
        'attachments': attachments,
        'can_submit': can_submit_ecn(request.user, ecn),
        'can_review': can_review_ecn(request.user, ecn),
        'can_close': can_close_ecn(request.user, ecn),
        'can_attach': can_add_ecn_attachment(request.user, ecn),
    })


# ---------------------------------------------------------------------------
# Crea ECN
# ---------------------------------------------------------------------------

@login_required
def ecn_create(request):
    """
    Crea un nuovo ECN in stato DRAFT.

    Richiede il parametro GET ?document=<id> per individuare il documento
    di riferimento. Valida can_create_ecn(user, document).
    """
    from documents.models import Document
    from ecn.forms import ChangeNoticeForm
    from ecn.services import create_change_notice

    document_id = request.GET.get('document') or request.POST.get('document')
    if not document_id:
        raise Http404

    try:
        document_id = int(document_id)
    except (ValueError, TypeError):
        raise Http404

    document = get_object_or_404(Document, pk=document_id)

    if not can_create_ecn(request.user, document):
        raise PermissionDenied

    if request.method == 'POST':
        form = ChangeNoticeForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            # project opzionale passato come hidden input (intero pk)
            project = None
            if d.get('project'):
                from projects.models import Project
                try:
                    project = Project.objects.get(pk=d['project'])
                except Project.DoesNotExist:
                    pass

            try:
                ecn = create_change_notice(
                    document=document,
                    proposed_by=request.user,
                    title=d['title'],
                    motivation=d['motivation'],
                    description=d.get('description', ''),
                    motivation_detail=d.get('motivation_detail', ''),
                    commessa=d.get('commessa', ''),
                    project=project,
                )
                messages.success(
                    request,
                    f'ECN {ecn.code} creato come bozza.',
                )
                return redirect('ecn:ecn_detail', ecn_id=ecn.pk)
            except ValidationError as exc:
                for msg in exc.messages:
                    messages.error(request, msg)
    else:
        form = ChangeNoticeForm()

    return render(request, 'ecn/ecn_form.html', {
        'form': form,
        'document': document,
    })


# ---------------------------------------------------------------------------
# Invia ECN alla CCB  (POST only)
# ---------------------------------------------------------------------------

@login_required
def ecn_submit(request, ecn_id):
    """DRAFT → UNDER_REVIEW. Solo POST."""
    from ecn.services import submit_change_notice

    if request.method != 'POST':
        return redirect('ecn:ecn_detail', ecn_id=ecn_id)

    ecn = get_object_or_404(ChangeNotice, pk=ecn_id)

    try:
        submit_change_notice(ecn, request.user)
        messages.success(request, f'{ecn.code} inviato alla CCB per revisione.')
    except PermissionDenied:
        messages.error(request, 'Non hai il permesso di inviare questo ECN.')
    except ValidationError as exc:
        for msg in exc.messages:
            messages.error(request, msg)

    return redirect('ecn:ecn_detail', ecn_id=ecn_id)


# ---------------------------------------------------------------------------
# Revisione CCB: approva o rifiuta
# ---------------------------------------------------------------------------

@login_required
def ecn_review(request, ecn_id):
    """UNDER_REVIEW → APPROVED / REJECTED."""
    from ecn.forms import ChangeNoticeReviewForm
    from ecn.services import approve_change_notice, reject_change_notice

    ecn = get_object_or_404(
        ChangeNotice.objects.select_related('document', 'document_version', 'proposed_by'),
        pk=ecn_id,
    )

    if not can_review_ecn(request.user, ecn):
        raise PermissionDenied

    if ecn.status != ChangeNotice.Status.UNDER_REVIEW:
        messages.error(
            request,
            f'L\'ECN non è in revisione CCB (stato: {ecn.get_status_display()}).',
        )
        return redirect('ecn:ecn_detail', ecn_id=ecn_id)

    if request.method == 'POST':
        form = ChangeNoticeReviewForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            try:
                if d['action'] == ChangeNoticeReviewForm.ACTION_APPROVE:
                    approve_change_notice(
                        ecn,
                        request.user,
                        ccb_class=d['ccb_class'],
                        ccb_requirements=d.get('ccb_requirements', ''),
                        ccb_technical_impact=d.get('ccb_technical_impact', ''),
                        ccb_cost_impact=d.get('ccb_cost_impact', ''),
                        ccb_time_impact=d.get('ccb_time_impact', ''),
                        ccb_quality_impact=d.get('ccb_quality_impact', ''),
                        ccb_other_impact=d.get('ccb_other_impact', ''),
                        ccb_notes=d.get('ccb_notes', ''),
                    )
                    messages.success(request, f'{ecn.code} approvato dalla CCB.')
                else:
                    reject_change_notice(
                        ecn,
                        request.user,
                        reason=d['ccb_notes'],
                    )
                    messages.success(request, f'{ecn.code} rifiutato dalla CCB.')
                return redirect('ecn:ecn_detail', ecn_id=ecn_id)
            except (PermissionDenied, ValidationError) as exc:
                if isinstance(exc, PermissionDenied):
                    messages.error(request, str(exc))
                else:
                    for msg in exc.messages:
                        messages.error(request, msg)
    else:
        form = ChangeNoticeReviewForm()

    return render(request, 'ecn/ecn_review_form.html', {
        'form': form,
        'ecn': ecn,
    })


# ---------------------------------------------------------------------------
# Chiusura ECN
# ---------------------------------------------------------------------------

@login_required
def ecn_close(request, ecn_id):
    """APPROVED → CLOSED."""
    from ecn.forms import ChangeNoticeCloseForm
    from ecn.services import close_change_notice

    ecn = get_object_or_404(
        ChangeNotice.objects.select_related('document', 'document_version', 'executed_version'),
        pk=ecn_id,
    )

    if not can_close_ecn(request.user, ecn):
        raise PermissionDenied

    if ecn.status != ChangeNotice.Status.APPROVED:
        messages.error(
            request,
            f'L\'ECN non è nello stato approvato (stato: {ecn.get_status_display()}).',
        )
        return redirect('ecn:ecn_detail', ecn_id=ecn_id)

    warn_no_version = ecn.executed_version is None

    if request.method == 'POST':
        form = ChangeNoticeCloseForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            try:
                close_change_notice(ecn, request.user, close_notes=d.get('close_notes', ''))
                messages.success(request, f'{ecn.code} chiuso.')
                return redirect('ecn:ecn_detail', ecn_id=ecn_id)
            except (PermissionDenied, ValidationError) as exc:
                if isinstance(exc, PermissionDenied):
                    messages.error(request, str(exc))
                else:
                    for msg in exc.messages:
                        messages.error(request, msg)
    else:
        form = ChangeNoticeCloseForm()

    return render(request, 'ecn/ecn_close_form.html', {
        'form': form,
        'ecn': ecn,
        'warn_no_version': warn_no_version,
    })


# ---------------------------------------------------------------------------
# Aggiungi allegato
# ---------------------------------------------------------------------------

@login_required
def ecn_add_attachment(request, ecn_id):
    """Carica un file allegato all'ECN."""
    from ecn.forms import ChangeNoticeAttachmentForm

    ecn = get_object_or_404(ChangeNotice.objects.select_related('document'), pk=ecn_id)

    if not can_add_ecn_attachment(request.user, ecn):
        raise PermissionDenied

    if request.method == 'POST':
        form = ChangeNoticeAttachmentForm(request.POST, request.FILES)
        if form.is_valid():
            d = form.cleaned_data
            uploaded_file = d['file']
            original_filename = uploaded_file.name
            _, ext = os.path.splitext(original_filename)

            attachment = ChangeNoticeAttachment(
                change_notice=ecn,
                file=uploaded_file,
                original_filename=original_filename,
                title=d.get('title', ''),
                description=d.get('description', ''),
                uploaded_by=request.user,
                size=uploaded_file.size,
                extension=ext.lstrip('.').lower() if ext else '',
            )
            attachment.save()
            messages.success(request, f'Allegato "{original_filename}" caricato.')
            return redirect('ecn:ecn_detail', ecn_id=ecn_id)
    else:
        form = ChangeNoticeAttachmentForm()

    return render(request, 'ecn/ecn_attachment_form.html', {
        'form': form,
        'ecn': ecn,
    })


# ---------------------------------------------------------------------------
# Download allegato
# ---------------------------------------------------------------------------

@login_required
def ecn_attachment_download(request, attachment_id):
    """Download di un allegato ECN."""
    attachment = get_object_or_404(
        ChangeNoticeAttachment.objects.select_related('change_notice'),
        pk=attachment_id,
    )
    if not can_view_ecn(request.user, attachment.change_notice):
        raise PermissionDenied

    if not attachment.file:
        raise Http404

    file_path = attachment.file.path
    if not os.path.exists(file_path):
        raise Http404

    return FileResponse(
        open(file_path, 'rb'),
        as_attachment=True,
        filename=attachment.original_filename or attachment.file.name,
    )
