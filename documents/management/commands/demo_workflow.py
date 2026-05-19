from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from approvals.models import ApprovalDecision, ApprovalRequest
from approvals.services import approve_version, reject_version
from auditlog.models import AuditLog
from documents.models import Document, DocumentVersion
from documents.services import (
    create_new_revision,
    reopen_rejected_version_as_draft,
    submit_version_for_approval,
)

DEMO_DOCUMENT_CODE = 'QUA-DEMO-001'


class Command(BaseCommand):
    help = 'Popola il database con un flusso documentale demo completo.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Cancella i dati demo relativi a QUA-DEMO-001 prima di ricrearli.',
        )

    def handle(self, *args, **options):
        if options['reset']:
            self._reset()
        self._run()

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------

    def _reset(self):
        try:
            doc = Document.objects.get(code=DEMO_DOCUMENT_CODE)
            # Pulisce i log prima di eliminare il documento
            deleted_logs, _ = AuditLog.objects.filter(changes__document_id=doc.pk).delete()
            doc.delete()  # cascade: DocumentVersion → ApprovalRequest → ApprovalRequestApprover, ApprovalDecision
            self.stdout.write(
                self.style.SUCCESS(
                    f'Dati demo eliminati ({deleted_logs} voci AuditLog rimosse).'
                )
            )
        except Document.DoesNotExist:
            self.stdout.write('Nessun dato demo da eliminare.')

    # ------------------------------------------------------------------
    # Flusso demo
    # ------------------------------------------------------------------

    def _run(self):
        self.stdout.write(self.style.MIGRATE_HEADING('\n=== AVVIO DEMO WORKFLOW ===\n'))

        # 1. Utenti demo
        autore = self._get_or_create_user('autore_demo', 'Autore', 'Demo')
        approvatore = self._get_or_create_user('approvatore_demo', 'Approvatore', 'Demo')
        self._get_or_create_user('lettore_demo', 'Lettore', 'Demo')

        # 2. Documento demo
        if Document.objects.filter(code=DEMO_DOCUMENT_CODE).exists():
            self.stdout.write(
                self.style.WARNING(
                    f'Il documento {DEMO_DOCUMENT_CODE} esiste già. '
                    'Usa --reset per ricrearlo da zero.'
                )
            )
            return

        doc = Document.objects.create(
            code=DEMO_DOCUMENT_CODE,
            title='Procedura demo gestione qualità',
            category=Document.Category.QUALITY,
            document_type='Procedura',
            owner=autore,
            created_by=autore,
        )
        self._step(f'Documento creato: {doc}')

        # 3. Crea Rev.00
        rev00 = create_new_revision(
            doc, autore,
            revision_label='00',
            revision_number=0,
            change_summary='Prima emissione.',
        )
        self._step(f'Rev.00 creata in bozza: {rev00}')

        # 4. Invia Rev.00 in approvazione
        req00 = submit_version_for_approval(rev00, autore, [approvatore])
        self._step(f'Rev.00 inviata in approvazione (ApprovalRequest #{req00.pk})')

        # 5. Approva Rev.00
        approve_version(req00, approvatore, comment='Prima emissione approvata.')
        rev00.refresh_from_db()
        self._step(f'Rev.00 approvata → {rev00.get_status_display()}, is_current={rev00.is_current}')

        # 6. Crea Rev.01
        doc.refresh_from_db()
        rev01 = create_new_revision(
            doc, autore,
            revision_label='01',
            revision_number=1,
            change_summary='Aggiornamento procedure operative sezione 3.',
        )
        self._step(f'Rev.01 creata in bozza: {rev01}')

        # 7. Invia Rev.01 in approvazione
        req01 = submit_version_for_approval(rev01, autore, [approvatore])
        self._step(f'Rev.01 inviata in approvazione (ApprovalRequest #{req01.pk})')

        # 8. Rifiuta Rev.01
        reject_version(
            req01, approvatore,
            rejection_reason='Sezione 3.2 incompleta: aggiornare le procedure operative.',
            comment='Rivedere interamente la sezione 3.2.',
        )
        rev01.refresh_from_db()
        self._step(f'Rev.01 rifiutata → {rev01.get_status_display()}')

        # 9. Riapri Rev.01 come bozza
        reopen_rejected_version_as_draft(rev01, autore)
        rev01.refresh_from_db()
        self._step(f'Rev.01 riaperta come bozza → {rev01.get_status_display()}')

        # 10. Reinvia Rev.01 in approvazione
        req01b = submit_version_for_approval(rev01, autore, [approvatore])
        self._step(f'Rev.01 reinviata in approvazione (ApprovalRequest #{req01b.pk})')

        # 11. Approva Rev.01
        approve_version(req01b, approvatore, comment='Approvato dopo correzioni sezione 3.2.')
        rev01.refresh_from_db()
        doc.refresh_from_db()
        self._step(f'Rev.01 approvata → {rev01.get_status_display()}, is_current={rev01.is_current}')

        # 12. Riepilogo finale
        rev00.refresh_from_db()
        audit_count = AuditLog.objects.filter(changes__document_id=doc.pk).count()
        req_count = ApprovalRequest.objects.filter(
            document_version__document=doc
        ).count()
        dec_count = ApprovalDecision.objects.filter(
            approval_request__document_version__document=doc
        ).count()

        self.stdout.write(self.style.MIGRATE_HEADING('\n=== RIEPILOGO ===\n'))
        self.stdout.write(f'  Documento          : {doc}')
        self.stdout.write(f'  Versione corrente  : {doc.current_version}')
        self.stdout.write(f'  Stato Rev.00       : {rev00.get_status_display()}  (is_current={rev00.is_current})')
        self.stdout.write(f'  Stato Rev.01       : {rev01.get_status_display()}  (is_current={rev01.is_current})')
        self.stdout.write(f'  AuditLog creati    : {audit_count}')
        self.stdout.write(f'  ApprovalRequest    : {req_count}')
        self.stdout.write(f'  ApprovalDecision   : {dec_count}')
        self.stdout.write(self.style.SUCCESS('\nDemo completata con successo.'))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_or_create_user(self, username, first_name, last_name):
        user, created = User.objects.get_or_create(
            username=username,
            defaults={'first_name': first_name, 'last_name': last_name},
        )
        label = 'creato' if created else 'già esistente'
        self.stdout.write(f'  Utente {username}: {label}')
        return user

    def _step(self, message):
        self.stdout.write(f'  → {message}')
