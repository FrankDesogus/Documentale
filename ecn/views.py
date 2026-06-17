"""View ECN / Varianti (ECN-B: UI minima)."""

import os

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render

from auditlog.historical_forms import should_send_notifications
from auditlog.models import HistoricalRecord
from auditlog.permissions import can_use_sanatoria

from ecn.models import ChangeNotice, ChangeNoticeApprover, ChangeNoticeAttachment, ChangeNoticeDecision
from ecn.permissions import (
    can_add_ecn_attachment,
    can_close_ecn,
    can_compile_dossier,
    can_configure_ccb,
    can_create_ecn,
    can_download_ecn_attachment,
    can_edit_ecn,
    can_reconfigure_ccb,
    can_reopen_ccb,
    can_review_ecn,
    can_submit_ecn,
    can_view_ecn,
    _can_consult_all_ecn,
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

    # Quality Manager / Quality Operator / Direction / superuser → vedono tutto
    # is_staff, Document Manager, Document Auditor, CCB globale → NON vedono tutto
    if _can_consult_all_ecn(user):
        qs = ChangeNotice.objects.select_related(
            'document', 'proposed_by', 'document_version',
        ).order_by('-proposed_at')
    else:
        # Propri ECN (proposed_by / created_by)
        own_filter = Q(proposed_by=user) | Q(created_by=user)
        # ECN dove l'utente è approvatore CCB assegnato
        assigned_ecn_ids = list(
            ChangeNoticeApprover.objects.filter(user=user)
            .values_list('change_notice_id', flat=True)
        )
        assigned_filter = Q(pk__in=assigned_ecn_ids)
        # ECN dove l'utente è coordinatore CCB
        coordinator_filter = Q(ccb_coordinator=user)
        qs = ChangeNotice.objects.filter(
            own_filter | assigned_filter | coordinator_filter
        ).select_related(
            'document', 'proposed_by', 'document_version',
        ).distinct().order_by('-proposed_at')

    # ── Ricerca e filtri (post-authorization) ──
    from django.core.paginator import Paginator

    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(
            Q(code__icontains=q)
            | Q(title__icontains=q)
            | Q(description__icontains=q)
            | Q(motivation_detail__icontains=q)
            | Q(document__code__icontains=q)
            | Q(document__title__icontains=q)
            | Q(proposed_by__username__icontains=q)
            | Q(proposed_by__first_name__icontains=q)
            | Q(proposed_by__last_name__icontains=q)
        )

    status_filter = request.GET.get('status', '')
    if status_filter and status_filter in [s.value for s in ChangeNotice.Status]:
        qs = qs.filter(status=status_filter)

    motivation_filter = request.GET.get('motivation', '')
    if motivation_filter:
        qs = qs.filter(motivation=motivation_filter)

    proposer_filter = request.GET.get('proposer', '').strip()
    if proposer_filter:
        try:
            qs = qs.filter(proposed_by_id=int(proposer_filter))
        except (ValueError, TypeError):
            pass

    coordinator_filter = request.GET.get('coordinator', '').strip()
    if coordinator_filter:
        try:
            qs = qs.filter(ccb_coordinator_id=int(coordinator_filter))
        except (ValueError, TypeError):
            pass

    ccb_member_filter = request.GET.get('ccb_member', '').strip()
    if ccb_member_filter:
        try:
            qs = qs.filter(approvers__user_id=int(ccb_member_filter))
        except (ValueError, TypeError):
            pass

    # "Solo le ECN che richiedono una mia azione"
    my_action = request.GET.get('my_action', '')
    if my_action:
        decided_ids = set(
            ChangeNoticeDecision.objects.filter(user=user).values_list('approver_id', flat=True)
        )
        my_action_ecn_ids = list(
            ChangeNoticeApprover.objects
            .filter(user=user, change_notice__status=ChangeNotice.Status.UNDER_REVIEW)
            .exclude(pk__in=decided_ids)
            .values_list('change_notice_id', flat=True)
        )
        qs = qs.filter(pk__in=my_action_ecn_ids)

    paginator = Paginator(qs.distinct(), 20)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    return render(request, 'ecn/ecn_list.html', {
        'ecns': page_obj,
        'page_obj': page_obj,
        'q': q,
        'status_choices': ChangeNotice.Status.choices,
        'current_status': status_filter,
        'motivation_choices': ChangeNotice.Motivation.choices,
        'motivation_filter': motivation_filter,
        'proposer_filter': proposer_filter,
        'coordinator_filter': coordinator_filter,
        'ccb_member_filter': ccb_member_filter,
        'my_action': my_action,
        'total_count': paginator.count,
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
    approvers = ecn.approvers.select_related('user').order_by('order', 'id')
    decisions = ecn.decisions.select_related('user', 'approver').order_by('decided_at')

    # Pulsante "Crea revisione da ECN": visibile se ECN approvato, non ancora eseguito,
    # documento ha current_version approvata e l'utente può creare revisioni.
    can_create_rev_from_ecn = False
    if (
        ecn.status == ChangeNotice.Status.APPROVED
        and ecn.executed_version_id is None
        and ecn.document.current_version is not None
        and ecn.document.current_version.status == 'approved'
    ):
        from documents.permissions import can_create_revision
        can_create_rev_from_ecn = can_create_revision(request.user, ecn.document)

    has_ccb_configured = ecn.approvers.exists()
    has_decisions = ecn.decisions.exists()

    historical_records = []
    if can_use_sanatoria(request.user):
        from auditlog.models import HistoricalRecord
        historical_records = list(
            HistoricalRecord.objects.filter(
                target_app='ecn',
                target_model='changenotice',
                target_id=str(ecn.pk),
            ).select_related('recorded_by').order_by('-historical_date')
        )

    return render(request, 'ecn/ecn_detail.html', {
        'ecn': ecn,
        'attachments': attachments,
        'approvers': approvers,
        'decisions': decisions,
        'can_submit': can_submit_ecn(request.user, ecn),
        'can_configure_ccb': can_configure_ccb(request.user, ecn),
        'has_ccb_configured': has_ccb_configured,
        'can_review': can_review_ecn(request.user, ecn),
        'can_close': can_close_ecn(request.user, ecn),
        'can_attach': can_add_ecn_attachment(request.user, ecn),
        'can_create_rev_from_ecn': can_create_rev_from_ecn,
        'can_edit': can_edit_ecn(request.user, ecn),
        'can_reconfigure': can_reconfigure_ccb(request.user, ecn),
        'can_reopen': can_reopen_ccb(request.user, ecn),
        'has_decisions': has_decisions,
        'can_compile_dossier': can_compile_dossier(request.user, ecn),
        'historical_records': historical_records,
        'sanatoria_available': can_use_sanatoria(request.user),
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
        form = ChangeNoticeForm(request.POST, current_user=request.user)
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
                from ecn.services import create_change_notice
                ecn = create_change_notice(
                    document=document,
                    proposed_by=request.user,
                    title=d['title'],
                    motivation=d['motivation'],
                    description=d.get('description', ''),
                    motivation_detail=d.get('motivation_detail', ''),
                    commessa=d.get('commessa', ''),
                    project=project,
                    send_notifications=should_send_notifications(sanatoria=form.is_sanatoria),
                )
                form.maybe_create_historical_record(
                    event_type=HistoricalRecord.EventType.ECN_CREATED,
                    target_instance=ecn,
                    recorded_by=request.user,
                )
                messages.success(
                    request,
                    f'ECN {ecn.code} creato come bozza. '
                    f'Il Responsabile Qualità configurerà gli approvatori CCB.',
                )
                return redirect('ecn:ecn_detail', ecn_id=ecn.pk)
            except ValidationError as exc:
                for msg in exc.messages:
                    messages.error(request, msg)
    else:
        form = ChangeNoticeForm(current_user=request.user)

    return render(request, 'ecn/ecn_form.html', {
        'form': form,
        'document': document,
        'sanatoria_available': can_use_sanatoria(request.user),
    })


# ---------------------------------------------------------------------------
# Configura CCB: seleziona approvatori e policy (solo Manager / staff)
# ---------------------------------------------------------------------------

@login_required
def ecn_configure_ccb(request, ecn_id):
    """
    Configura CCB: responsabile istruttoria, componenti ordinati, policy.
    Usa formset dinamico (pattern identico agli approvatori documentali).
    Transizione DRAFT → CCB_PREPARATION.
    """
    from ecn.forms import CCBMemberFormSet, ChangeNoticeCCBConfigForm
    from ecn.services import configure_ccb

    ecn = get_object_or_404(
        ChangeNotice.objects.select_related('document', 'proposed_by'),
        pk=ecn_id,
    )

    if not can_reconfigure_ccb(request.user, ecn):
        raise PermissionDenied

    allowed_statuses = (
        ChangeNotice.Status.DRAFT,
        ChangeNotice.Status.CCB_PREPARATION,
        ChangeNotice.Status.UNDER_REVIEW,
    )
    if ecn.status not in allowed_statuses:
        messages.error(
            request,
            f'La configurazione CCB non è possibile in stato {ecn.get_status_display()}.',
        )
        return redirect('ecn:ecn_detail', ecn_id=ecn_id)

    existing_approvers = list(ecn.approvers.order_by('order', 'id').select_related('user'))

    if request.method == 'POST':
        form = ChangeNoticeCCBConfigForm(request.POST, current_user=request.user)
        formset = CCBMemberFormSet(
            request.POST, prefix='ccb', current_user=request.user,
        )
        if form.is_valid() and formset.is_valid():
            selected_users = [
                f.cleaned_data['user']
                for f in formset.forms
                if f.cleaned_data and f.cleaned_data.get('user')
            ]
            if not selected_users:
                messages.error(request, 'Devi selezionare almeno un componente CCB.')
            else:
                try:
                    coordinator = form.cleaned_data.get('coordinator')
                    configure_ccb(
                        ecn,
                        actor=request.user,
                        users=selected_users,
                        policy=form.cleaned_data['ccb_policy'],
                        coordinator=coordinator,
                        send_notifications=should_send_notifications(sanatoria=form.is_sanatoria),
                    )
                    form.maybe_create_historical_record(
                        event_type=HistoricalRecord.EventType.ECN_CCB_CONFIGURED,
                        target_instance=ecn,
                        recorded_by=request.user,
                    )
                    messages.success(
                        request,
                        f'CCB configurata per {ecn.code}: {len(selected_users)} componenti, '
                        f'policy {ecn.get_ccb_policy_display()}.',
                    )
                    return redirect('ecn:ecn_detail', ecn_id=ecn_id)
                except ValidationError as exc:
                    for msg in exc.messages:
                        messages.error(request, msg)
    else:
        form = ChangeNoticeCCBConfigForm(
            initial={
                'ccb_policy': ecn.ccb_policy,
                'coordinator': ecn.ccb_coordinator_id,
            },
            current_user=request.user,
        )
        # Pre-popola il formset con i componenti esistenti
        initial_rows = [{'user': app.user_id} for app in existing_approvers]
        if not initial_rows:
            initial_rows = [{}]  # almeno una riga vuota
        formset = CCBMemberFormSet(
            prefix='ccb',
            initial=initial_rows,
            current_user=request.user,
        )

    return render(request, 'ecn/ecn_configure_ccb.html', {
        'form': form,
        'formset': formset,
        'ecn': ecn,
        'is_under_review': ecn.status == ChangeNotice.Status.UNDER_REVIEW,
        'is_ccb_preparation': ecn.status == ChangeNotice.Status.CCB_PREPARATION,
        'sanatoria_available': can_use_sanatoria(request.user),
    })


# ---------------------------------------------------------------------------
# Istruttoria CCB — compila dossier (STEP I)
# ---------------------------------------------------------------------------

@login_required
def ecn_ccb_dossier(request, ecn_id):
    """
    Pagina di compilazione del dossier istruttorio CCB.
    Accessibile al responsabile istruttoria, al Quality Manager e ai superuser.
    Il dossier è modificabile solo in DRAFT o CCB_PREPARATION.
    Pulsanti:
      - Salva bozza istruttoria → salva senza inviare
      - Invia alla CCB → salva + transizione a UNDER_REVIEW
    """
    from ecn.forms import ChangeNoticeDossierForm
    from ecn.services import update_ccb_dossier, submit_change_notice

    ecn = get_object_or_404(
        ChangeNotice.objects.select_related(
            'document', 'document_version', 'proposed_by',
            'ccb_coordinator',
        ),
        pk=ecn_id,
    )

    if not can_view_ecn(request.user, ecn):
        raise PermissionDenied

    dossier_editable = can_compile_dossier(request.user, ecn)

    if request.method == 'POST':
        if not dossier_editable:
            raise PermissionDenied

        form = ChangeNoticeDossierForm(request.POST, current_user=request.user)
        action = request.POST.get('dossier_action', 'save')  # 'save' | 'submit'

        if form.is_valid():
            d = form.cleaned_data

            # Valida campi obbligatori se si sta inviando
            if action == 'submit':
                try:
                    form.validate_for_submit()
                except Exception:
                    # validate_for_submit popola gli errori nel form
                    pass

            if not form.errors:
                try:
                    update_ccb_dossier(
                        ecn,
                        actor=request.user,
                        ccb_class=d.get('ccb_class') or None,
                        ccb_requirements=d.get('ccb_requirements', ''),
                        ccb_technical_impact=d.get('ccb_technical_impact', ''),
                        ccb_cost_impact=d.get('ccb_cost_impact', ''),
                        ccb_time_impact=d.get('ccb_time_impact', ''),
                        ccb_quality_impact=d.get('ccb_quality_impact', ''),
                        ccb_other_impact=d.get('ccb_other_impact', ''),
                        ccb_notes=d.get('ccb_notes', ''),
                    )
                    form.maybe_create_historical_record(
                        event_type=HistoricalRecord.EventType.ECN_DOSSIER_COMPILED,
                        target_instance=ecn,
                        recorded_by=request.user,
                    )

                    if action == 'submit':
                        submit_change_notice(
                            ecn,
                            request.user,
                            send_notifications=should_send_notifications(sanatoria=form.is_sanatoria),
                        )
                        messages.success(
                            request,
                            f'{ecn.code}: dossier inviato alla CCB. La votazione è avviata.',
                        )
                    else:
                        messages.success(request, f'{ecn.code}: bozza dossier salvata.')

                    return redirect('ecn:ecn_detail', ecn_id=ecn_id)
                except PermissionDenied as exc:
                    messages.error(request, str(exc))
                except ValidationError as exc:
                    for msg in exc.messages:
                        messages.error(request, msg)
    else:
        # Pre-popola con i dati esistenti
        form = ChangeNoticeDossierForm(
            initial={
                'ccb_class':           ecn.ccb_class or '',
                'ccb_requirements':    ecn.ccb_requirements,
                'ccb_technical_impact': ecn.ccb_technical_impact,
                'ccb_cost_impact':     ecn.ccb_cost_impact,
                'ccb_time_impact':     ecn.ccb_time_impact,
                'ccb_quality_impact':  ecn.ccb_quality_impact,
                'ccb_other_impact':    ecn.ccb_other_impact,
                'ccb_notes':           ecn.ccb_notes,
            },
            current_user=request.user,
        )

    approvers = ecn.approvers.order_by('order', 'id').select_related('user')

    return render(request, 'ecn/ecn_ccb_dossier.html', {
        'form': form,
        'ecn': ecn,
        'dossier_editable': dossier_editable,
        'approvers': approvers,
        'can_submit': can_submit_ecn(request.user, ecn),
        'sanatoria_available': can_use_sanatoria(request.user),
    })


# ---------------------------------------------------------------------------
# Invia ECN alla CCB  (POST only)
# ---------------------------------------------------------------------------

@login_required
def ecn_submit(request, ecn_id):
    """DRAFT/CCB_PREPARATION → UNDER_REVIEW. Solo POST."""
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
    """
    Decisione individuale del membro CCB (STEP I).
    Mostra il dossier in sola lettura + form semplificato (approva/rifiuta + commento).
    UNDER_REVIEW → APPROVED / REJECTED.
    """
    from ecn.forms import ChangeNoticeReviewForm
    from ecn.services import approve_change_notice, reject_change_notice

    ecn = get_object_or_404(
        ChangeNotice.objects.select_related(
            'document', 'document_version', 'proposed_by', 'ccb_coordinator',
        ),
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
        form = ChangeNoticeReviewForm(request.POST, current_user=request.user)
        if form.is_valid():
            d = form.cleaned_data
            try:
                if d['action'] == ChangeNoticeReviewForm.ACTION_APPROVE:
                    # Il dossier è già compilato: non passiamo ccb_class/requirements
                    # perché il servizio usa quello già salvato sull'ECN.
                    approve_change_notice(
                        ecn,
                        request.user,
                        comment=d.get('comment', ''),
                        send_notifications=should_send_notifications(sanatoria=form.is_sanatoria),
                    )
                    form.maybe_create_historical_record(
                        event_type=HistoricalRecord.EventType.ECN_VOTE_APPROVED,
                        target_instance=ecn,
                        recorded_by=request.user,
                    )
                    ecn.refresh_from_db()
                    if ecn.status == ChangeNotice.Status.APPROVED:
                        messages.success(request, f'{ecn.code} approvato dalla CCB.')
                    else:
                        messages.success(
                            request,
                            'La tua approvazione è stata registrata. '
                            'In attesa degli altri approvatori.',
                        )
                else:
                    reject_change_notice(
                        ecn,
                        request.user,
                        reason=d['ccb_notes'],
                        comment=d.get('comment', ''),
                        send_notifications=should_send_notifications(sanatoria=form.is_sanatoria),
                    )
                    form.maybe_create_historical_record(
                        event_type=HistoricalRecord.EventType.ECN_VOTE_REJECTED,
                        target_instance=ecn,
                        recorded_by=request.user,
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
        form = ChangeNoticeReviewForm(current_user=request.user)

    approvers = ecn.approvers.order_by('order', 'id').select_related('user')

    return render(request, 'ecn/ecn_review_form.html', {
        'form': form,
        'ecn': ecn,
        'approvers': approvers,
        'sanatoria_available': can_use_sanatoria(request.user),
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

    # Avvisa se non c'è ancora una revisione eseguita (il service blocca la chiusura)
    warn_no_version = ecn.executed_version is None

    if request.method == 'POST':
        form = ChangeNoticeCloseForm(request.POST, current_user=request.user)
        if form.is_valid():
            d = form.cleaned_data
            try:
                close_change_notice(
                    ecn,
                    request.user,
                    close_notes=d.get('close_notes', ''),
                    send_notifications=should_send_notifications(sanatoria=form.is_sanatoria),
                )
                form.maybe_create_historical_record(
                    event_type=HistoricalRecord.EventType.ECN_CLOSED,
                    target_instance=ecn,
                    recorded_by=request.user,
                )
                messages.success(request, f'{ecn.code} chiuso.')
                return redirect('ecn:ecn_detail', ecn_id=ecn_id)
            except (PermissionDenied, ValidationError) as exc:
                if isinstance(exc, PermissionDenied):
                    messages.error(request, str(exc))
                else:
                    for msg in exc.messages:
                        messages.error(request, msg)
    else:
        form = ChangeNoticeCloseForm(current_user=request.user)

    return render(request, 'ecn/ecn_close_form.html', {
        'form': form,
        'ecn': ecn,
        'warn_no_version': warn_no_version,
        'sanatoria_available': can_use_sanatoria(request.user),
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
        ChangeNoticeAttachment.objects.select_related(
            'change_notice__proposed_by', 'change_notice__created_by',
        ),
        pk=attachment_id,
    )
    if not can_download_ecn_attachment(request.user, attachment):
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


# ---------------------------------------------------------------------------
# Modifica ECN (dati base — solo DRAFT)
# ---------------------------------------------------------------------------

@login_required
def ecn_edit(request, ecn_id):
    """
    Modifica i dati base di un ECN in bozza:
    titolo, motivazione, descrizione, commessa, progetto.

    Accessibile a: proponente / created_by / Manager / staff, solo se DRAFT.
    """
    from ecn.forms import ChangeNoticeEditForm
    from ecn.services import update_change_notice

    ecn = get_object_or_404(
        ChangeNotice.objects.select_related('document', 'proposed_by', 'project'),
        pk=ecn_id,
    )

    if not can_edit_ecn(request.user, ecn):
        raise PermissionDenied

    if request.method == 'POST':
        form = ChangeNoticeEditForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            try:
                update_change_notice(
                    ecn,
                    actor=request.user,
                    title=d['title'],
                    motivation=d['motivation'],
                    description=d.get('description', ''),
                    motivation_detail=d.get('motivation_detail', ''),
                    commessa=d.get('commessa', ''),
                    project=d.get('project'),
                )
                messages.success(request, f'{ecn.code} aggiornato.')
                return redirect('ecn:ecn_detail', ecn_id=ecn_id)
            except ValidationError as exc:
                for msg in exc.messages:
                    messages.error(request, msg)
    else:
        form = ChangeNoticeEditForm(initial={
            'title': ecn.title,
            'motivation': ecn.motivation,
            'motivation_detail': ecn.motivation_detail,
            'description': ecn.description,
            'commessa': ecn.commessa,
            'project': ecn.project,
        })

    return render(request, 'ecn/ecn_edit_form.html', {
        'form': form,
        'ecn': ecn,
    })


# ---------------------------------------------------------------------------
# Riapri configurazione CCB (under_review con decisioni → DRAFT)
# ---------------------------------------------------------------------------

@login_required
def ecn_reopen_ccb(request, ecn_id):
    """
    Riapre la configurazione CCB di un ECN in revisione.

    Cancella tutte le decisioni esistenti e riporta l'ECN a DRAFT.
    Accessibile solo a Manager / staff quando lo stato è UNDER_REVIEW e
    ci sono decisioni già espresse.
    """
    from ecn.forms import ChangeNoticeReopenCCBForm
    from ecn.services import reopen_ccb_configuration

    ecn = get_object_or_404(
        ChangeNotice.objects.select_related('document', 'proposed_by'),
        pk=ecn_id,
    )

    if not can_reopen_ccb(request.user, ecn):
        raise PermissionDenied

    if request.method == 'POST':
        form = ChangeNoticeReopenCCBForm(request.POST)
        if form.is_valid():
            reason = form.cleaned_data.get('reason', '')
            try:
                reopen_ccb_configuration(ecn, actor=request.user, reason=reason)
                messages.success(
                    request,
                    f'{ecn.code} riportato in bozza. Le decisioni CCB sono state annullate. '
                    f'Riconfigura gli approvatori e reinvia alla CCB.',
                )
                return redirect('ecn:ecn_detail', ecn_id=ecn_id)
            except (PermissionDenied, ValidationError) as exc:
                if isinstance(exc, PermissionDenied):
                    messages.error(request, str(exc))
                else:
                    for msg in exc.messages:
                        messages.error(request, msg)
    else:
        form = ChangeNoticeReopenCCBForm()

    return render(request, 'ecn/ecn_reopen_ccb_form.html', {
        'form': form,
        'ecn': ecn,
    })


# ---------------------------------------------------------------------------
# Cruscotto ECN operativo  (Manager / Auditor / Staff)
# ---------------------------------------------------------------------------

@login_required
def ecn_dashboard(request):
    """
    Vista riepilogativa di tutti gli ECN attivi, raggruppati per stato operativo.

    Accesso: superuser, staff, Document Managers, Document Auditors.
    """
    user = request.user
    if not _can_consult_all_ecn(user):
        raise PermissionDenied

    # 1 & 2: DRAFT — divisi per CCB configurata o meno
    all_draft = (
        ChangeNotice.objects
        .filter(status=ChangeNotice.Status.DRAFT)
        .select_related('document', 'proposed_by')
        .prefetch_related('approvers')
        .order_by('-proposed_at')
    )
    draft_no_ccb = [e for e in all_draft if not e.approvers.exists()]
    draft_ccb_ready = [e for e in all_draft if e.approvers.exists()]

    # 3: IN REVISIONE CCB — con progresso approvatori
    under_review_raw = (
        ChangeNotice.objects
        .filter(status=ChangeNotice.Status.UNDER_REVIEW)
        .select_related('document', 'proposed_by')
        .prefetch_related('approvers__user', 'decisions')
        .order_by('-proposed_at')
    )
    under_review_data = []
    for ecn in under_review_raw:
        approvers = list(ecn.approvers.all())
        decisions = list(ecn.decisions.all())
        decided_ids = {d.approver_id for d in decisions}
        pending = [a for a in approvers if a.pk not in decided_ids]
        under_review_data.append({
            'ecn': ecn,
            'total': len(approvers),
            'decided': len(decisions),
            'pending': pending,
        })

    # 4: APPROVATE senza revisione eseguita → da eseguire
    approved_no_exec = (
        ChangeNotice.objects
        .filter(status=ChangeNotice.Status.APPROVED, executed_version__isnull=True)
        .select_related('document', 'proposed_by')
        .order_by('-proposed_at')
    )

    # 5: APPROVATE con revisione eseguita → da chiudere formalmente
    approved_exec = (
        ChangeNotice.objects
        .filter(status=ChangeNotice.Status.APPROVED, executed_version__isnull=False)
        .select_related('document', 'proposed_by', 'executed_version')
        .order_by('-proposed_at')
    )

    rejected_count = ChangeNotice.objects.filter(status=ChangeNotice.Status.REJECTED).count()
    closed_count = ChangeNotice.objects.filter(status=ChangeNotice.Status.CLOSED).count()

    return render(request, 'ecn/ecn_dashboard.html', {
        'draft_no_ccb': draft_no_ccb,
        'draft_ccb_ready': draft_ccb_ready,
        'under_review_data': under_review_data,
        'approved_no_exec': approved_no_exec,
        'approved_exec': approved_exec,
        'rejected_count': rejected_count,
        'closed_count': closed_count,
    })


# ---------------------------------------------------------------------------
# Le mie ECN  (vista personale — tutti gli utenti autenticati)
# ---------------------------------------------------------------------------

@login_required
def ecn_my(request):
    """
    Vista personale ECN:
      - ECN proposti o creati dall'utente corrente
      - Decisioni CCB assegnate e non ancora espresse
      - Storico decisioni già espresse
    """
    from django.db.models import Q

    user = request.user

    # Sezione 1: le mie richieste ECN
    my_ecns = (
        ChangeNotice.objects
        .filter(Q(proposed_by=user) | Q(created_by=user))
        .distinct()
        .select_related('document')
        .order_by('-proposed_at')
    )

    # Sezione 2: decisioni CCB in attesa
    decided_approver_ids = set(
        ChangeNoticeDecision.objects
        .filter(user=user)
        .values_list('approver_id', flat=True)
    )
    pending_approvals = (
        ChangeNoticeApprover.objects
        .filter(
            user=user,
            change_notice__status=ChangeNotice.Status.UNDER_REVIEW,
        )
        .exclude(pk__in=decided_approver_ids)
        .select_related('change_notice__document', 'change_notice__proposed_by')
    )

    # Sezione 3: storico decisioni già espresse
    my_decisions = (
        ChangeNoticeDecision.objects
        .filter(user=user)
        .select_related('change_notice__document')
        .order_by('-decided_at')
    )

    return render(request, 'ecn/ecn_my.html', {
        'my_ecns': my_ecns,
        'pending_approvals': pending_approvals,
        'my_decisions': my_decisions,
    })
