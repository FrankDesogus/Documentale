"""
Management command: demo_full

Costruisce un database demo completo che copre tutti i casi possibili
dell'applicazione Documentale. Ideale per presentazioni e test manuali.

Chiama demo_company come base, poi aggiunge:
  - Documento con 3 revisioni (storico completo con versioni superate)
  - Documento con revisione rifiutata
  - ECN in tutti gli stati: draft, ccb_preparation, under_review,
    approved, rejected, closed
  - ECN che ha originato una revisione (mostra ECN di origine in version_detail)
  - Documento esente da ECN (approvazione diretta)
  - Approvazione con policy 'any' e policy 'sequential'
  - Record storici sanatoria (HistoricalRecord)

Uso:
    py manage.py demo_full --reset --no-email
"""

import datetime

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.test.utils import override_settings


class Command(BaseCommand):
    help = 'Crea il database demo completo con tutti gli scenari possibili.'

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true',
                            help='Svuota il database prima di ricreare.')
        parser.add_argument('--no-email', action='store_true',
                            help='Usa backend email in memoria (nessuna email reale).')

    def handle(self, *args, **options):
        if options['no_email']:
            with override_settings(
                EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'
            ):
                self._run(reset=options['reset'])
        else:
            self._run(reset=options['reset'])

    def _run(self, reset=False):
        # ── Base: utenti, gruppi, cartelle, 4 documenti fondamentali ──────
        call_command('demo_company', reset=reset, no_email=True)

        self.stdout.write(self.style.MIGRATE_HEADING(
            '\n=== DEMO FULL — SCENARI ESTESI ===\n'
        ))

        supervisor = User.objects.get(username='supervisor_demo')
        mario      = User.objects.get(username='mario.rossi')
        lucia      = User.objects.get(username='lucia.bianchi')
        anna       = User.objects.get(username='anna.neri')

        from projects.models import ProjectFolder
        folder = ProjectFolder.objects.get(code='QUA-PROC')

        # ── Scenari ────────────────────────────────────────────────────────
        self._scenario_multi_revision(supervisor, mario, lucia, folder)
        self._scenario_rejected_revision(supervisor, mario, lucia, folder)
        self._scenario_ecn_all_states(supervisor, anna, folder)
        self._scenario_ecn_executed_revision(supervisor, mario, lucia, anna, folder)
        self._scenario_ecn_exempt(supervisor, mario, lucia, folder)
        self._scenario_approval_policies(supervisor, mario, lucia, anna, folder)
        self._scenario_sanatoria(supervisor, mario)

        self.stdout.write(self.style.SUCCESS('\nDemo full completato.'))

    # ──────────────────────────────────────────────────────────────────────
    # Scenario 1 — Documento con 3 revisioni (storico completo)
    # ──────────────────────────────────────────────────────────────────────

    def _scenario_multi_revision(self, supervisor, author, approver, folder):
        from documents.models import Document
        from documents.services import create_new_revision, submit_version_for_approval
        from approvals.services import approve_version

        CODE = 'DEMO-MULTI-001'
        if Document.objects.filter(code=CODE).exists():
            self._step(f'{CODE}: già esistente, saltato.')
            return

        doc = Document.objects.create(
            code=CODE,
            title='Procedura di controllo qualità produzione — Demo multi-revisione',
            category=Document.Category.QUALITY,
            document_type='Procedura',
            project_folder=folder,
            owner=author,
            created_by=author,
            requires_ecn_for_revision=False,
        )

        for label, num, summary, reviewer in [
            ('00', 0, 'Prima emissione procedura.', supervisor),
            ('01', 1, 'Revisione sezione 3: aggiornati i criteri di accettazione.', approver),
            ('02', 2, 'Aggiunta sezione 6: gestione non conformità.', approver),
        ]:
            ver = create_new_revision(doc, author, label, num,
                                      change_summary=summary,
                                      _bypass_ecn_check=True)
            req = submit_version_for_approval(ver, author, [reviewer],
                                             send_notifications=False)
            approve_version(req, reviewer,
                            comment=f'Rev. {label} — approvazione demo.',
                            send_notifications=False)
            doc.refresh_from_db()

        self._step(f'{CODE}: 3 revisioni approvate (00 superseded, 01 superseded, 02 corrente).')

    # ──────────────────────────────────────────────────────────────────────
    # Scenario 2 — Revisione rifiutata
    # ──────────────────────────────────────────────────────────────────────

    def _scenario_rejected_revision(self, supervisor, author, approver, folder):
        from documents.models import Document
        from documents.services import create_new_revision, submit_version_for_approval
        from approvals.services import approve_version, reject_version

        CODE = 'DEMO-REJECT-001'
        if Document.objects.filter(code=CODE).exists():
            self._step(f'{CODE}: già esistente, saltato.')
            return

        doc = Document.objects.create(
            code=CODE,
            title='Istruzione operativa saldatura — Demo revisione rifiutata',
            category=Document.Category.QUALITY,
            document_type='Istruzione operativa',
            project_folder=folder,
            owner=author,
            created_by=author,
            requires_ecn_for_revision=False,
        )

        ver00 = create_new_revision(doc, author, '00', 0,
                                    change_summary='Prima emissione.',
                                    _bypass_ecn_check=True)
        req00 = submit_version_for_approval(ver00, author, [approver],
                                            send_notifications=False)
        approve_version(req00, approver,
                        comment='Prima emissione approvata.',
                        send_notifications=False)
        doc.refresh_from_db()

        ver01 = create_new_revision(doc, author, '01', 1,
                                    change_summary='Tentativo di modifica sezione 2 — rifiutato.',
                                    _bypass_ecn_check=True)
        req01 = submit_version_for_approval(ver01, author, [approver],
                                            send_notifications=False)
        reject_version(req01, approver,
                       rejection_reason='Modifiche insufficienti: mancano riferimenti normativi.',
                       send_notifications=False)

        self._step(f'{CODE}: Rev. 00 approvata (corrente) + Rev. 01 rifiutata.')

    # ──────────────────────────────────────────────────────────────────────
    # Scenario 3 — ECN in tutti gli stati
    # ──────────────────────────────────────────────────────────────────────

    def _scenario_ecn_all_states(self, supervisor, ccb_member, folder):
        from documents.models import Document
        from documents.services import create_new_revision, submit_version_for_approval
        from approvals.services import approve_version
        from ecn.models import ChangeNotice
        from ecn.services import (
            approve_change_notice, close_change_notice, configure_ccb,
            create_change_notice, reject_change_notice, submit_change_notice,
            update_ccb_dossier,
        )

        BASE_CODE = 'DEMO-ECN-BASE'
        if Document.objects.filter(code=BASE_CODE).exists():
            self._step(f'{BASE_CODE}: già esistente, saltato (ECN states).')
            return

        doc = Document.objects.create(
            code=BASE_CODE,
            title='Specifica tecnica componenti elettronici — Demo stati ECN',
            category=Document.Category.QUALITY,
            document_type='Specifica',
            project_folder=folder,
            owner=supervisor,
            created_by=supervisor,
        )
        ver00 = create_new_revision(doc, supervisor, '00', 0,
                                    change_summary='Prima emissione.',
                                    _bypass_ecn_check=True)
        req = submit_version_for_approval(ver00, supervisor, [supervisor],
                                          send_notifications=False)
        approve_version(req, supervisor,
                        comment='Prima emissione approvata.',
                        send_notifications=False)
        doc.refresh_from_db()

        def _make_ecn(code, title):
            return create_change_notice(
                document=doc,
                proposed_by=supervisor,
                title=title,
                motivation=ChangeNotice.Motivation.IMPROVEMENT,
                description=f'Descrizione demo ECN {code}.',
                motivation_detail='Adeguamento tecnico demo.',
                code=code,
            )

        def _setup_ccb(ecn):
            configure_ccb(ecn, actor=supervisor, users=[supervisor, ccb_member],
                          policy='any', coordinator=supervisor,
                          send_notifications=False)
            update_ccb_dossier(
                ecn, actor=supervisor,
                ccb_class='class2',
                ccb_requirements='Conformità normativa verificata.',
                ccb_technical_impact='Impatto limitato a sezione 3.',
                ccb_cost_impact='Nessun costo aggiuntivo.',
                ccb_time_impact='2 giorni lavorativi.',
                ccb_quality_impact='Miglioramento tracciabilità.',
                ccb_other_impact='—',
                ccb_notes='Demo automatico.',
            )

        # ECN-S-01: DRAFT
        _make_ecn('ECN-S-01', 'Aggiornamento tolleranze sezione 2 (DRAFT)')
        self._step('ECN-S-01: stato DRAFT.')

        # ECN-S-02: CCB_PREPARATION
        ecn_ccb = _make_ecn('ECN-S-02', 'Revisione criteri accettazione (CCB_PREPARATION)')
        _setup_ccb(ecn_ccb)
        self._step('ECN-S-02: stato CCB_PREPARATION (dossier compilato).')

        # ECN-S-03: UNDER_REVIEW
        ecn_review = _make_ecn('ECN-S-03', 'Aggiornamento lista materiali approvati (UNDER_REVIEW)')
        _setup_ccb(ecn_review)
        submit_change_notice(ecn_review, supervisor, send_notifications=False)
        self._step('ECN-S-03: stato UNDER_REVIEW.')

        # ECN-S-04: APPROVED (policy='any' → basta un voto)
        ecn_approved = _make_ecn('ECN-S-04', 'Nuova procedura testing (APPROVED)')
        _setup_ccb(ecn_approved)
        submit_change_notice(ecn_approved, supervisor, send_notifications=False)
        approve_change_notice(ecn_approved, supervisor,
                              ccb_class='class2',
                              ccb_requirements='Verificato.',
                              ccb_technical_impact='Minimo.',
                              comment='CCB approva — demo.',
                              send_notifications=False)
        self._step('ECN-S-04: stato APPROVED (in attesa di esecuzione).')

        # ECN-S-05: REJECTED
        ecn_rejected = _make_ecn('ECN-S-05', 'Modifica scheda dati componente (REJECTED)')
        _setup_ccb(ecn_rejected)
        submit_change_notice(ecn_rejected, supervisor, send_notifications=False)
        reject_change_notice(ecn_rejected, supervisor,
                             reason='Non conformità con requisiti normativi vigenti.',
                             comment='CCB rigetta — insufficiente analisi impatto.',
                             send_notifications=False)
        self._step('ECN-S-05: stato REJECTED.')

        # ECN-S-06: CLOSED (eseguito con Rev. 01, poi chiuso)
        ecn_closed = _make_ecn('ECN-S-06', 'Aggiornamento tabella tolleranze (CLOSED)')
        _setup_ccb(ecn_closed)
        submit_change_notice(ecn_closed, supervisor, send_notifications=False)
        approve_change_notice(ecn_closed, supervisor,
                              ccb_class='class1',
                              ccb_requirements='Approvato.',
                              ccb_technical_impact='Limitato.',
                              comment='Approvazione per chiusura demo.',
                              send_notifications=False)
        ecn_closed.refresh_from_db()
        ver01 = create_new_revision(
            doc, supervisor, '01', 1,
            ecn=ecn_closed,
            change_summary='Aggiornamento tabella tolleranze — eseguito da ECN-S-06.',
        )
        req01 = submit_version_for_approval(ver01, supervisor, [supervisor],
                                            send_notifications=False)
        approve_version(req01, supervisor,
                        comment='Rev. 01 approvata demo.',
                        send_notifications=False)
        doc.refresh_from_db()
        ecn_closed.refresh_from_db()
        close_change_notice(ecn_closed, supervisor,
                            close_notes='ECN eseguito. Rev. 01 approvata e pubblicata.',
                            send_notifications=False)
        self._step(f'ECN-S-06: stato CLOSED (Rev. 01 di {BASE_CODE} creata e approvata).')

    # ──────────────────────────────────────────────────────────────────────
    # Scenario 4 — ECN che origina una revisione (mostra "ECN di origine")
    # ──────────────────────────────────────────────────────────────────────

    def _scenario_ecn_executed_revision(self, supervisor, author, approver, ccb_member, folder):
        from documents.models import Document
        from documents.services import create_new_revision, submit_version_for_approval
        from approvals.services import approve_version
        from ecn.models import ChangeNotice
        from ecn.services import (
            approve_change_notice, close_change_notice, configure_ccb,
            create_change_notice, submit_change_notice, update_ccb_dossier,
        )

        CODE = 'DEMO-ECN-EXEC'
        if Document.objects.filter(code=CODE).exists():
            self._step(f'{CODE}: già esistente, saltato.')
            return

        doc = Document.objects.create(
            code=CODE,
            title='Procedura di calibrazione strumenti — Demo ECN eseguito',
            category=Document.Category.QUALITY,
            document_type='Procedura',
            project_folder=folder,
            owner=author,
            created_by=author,
        )
        ver00 = create_new_revision(doc, author, '00', 0,
                                    change_summary='Prima emissione.',
                                    _bypass_ecn_check=True)
        req00 = submit_version_for_approval(ver00, author, [approver],
                                            send_notifications=False)
        approve_version(req00, approver,
                        comment='Prima emissione approvata.',
                        send_notifications=False)
        doc.refresh_from_db()

        ecn = create_change_notice(
            document=doc, proposed_by=author,
            title='Aggiornamento metodo calibrazione dinamometri',
            motivation=ChangeNotice.Motivation.NON_CONFORMITY,
            description='Non conformità NC-2026-017: metodo calibrazione obsoleto.',
            motivation_detail='Adeguamento a norma ISO 9001:2015.',
            code='ECN-EXEC-001',
        )
        configure_ccb(ecn, actor=supervisor, users=[supervisor, ccb_member],
                      policy='all', coordinator=supervisor,
                      send_notifications=False)
        update_ccb_dossier(
            ecn, actor=supervisor,
            ccb_class='class2',
            ccb_requirements='Conformità ISO 9001:2015 richiesta.',
            ccb_technical_impact='Aggiornamento metodo — nessun impatto hardware.',
            ccb_cost_impact='Solo costo formazione (stima: 4h).',
            ccb_time_impact='3 giorni lavorativi.',
            ccb_quality_impact='Riduzione rischio non conformità ricorrente.',
            ccb_other_impact='Aggiornare istruzione operativa IO-STRUM-005.',
            ccb_notes='Priorità alta per audit ISO previsto il 2026-09.',
        )
        submit_change_notice(ecn, supervisor, send_notifications=False)
        approve_change_notice(ecn, supervisor,
                              ccb_class='class2',
                              ccb_requirements='Approvato.',
                              ccb_technical_impact='Limitato.',
                              comment='Supervisor approva.',
                              send_notifications=False)
        approve_change_notice(ecn, ccb_member,
                              ccb_class='class2',
                              ccb_requirements='Approvato.',
                              ccb_technical_impact='Verificato.',
                              comment='CCB member approva — policy ALL soddisfatta.',
                              send_notifications=False)
        ecn.refresh_from_db()

        ver01 = create_new_revision(
            doc, author, '01', 1,
            ecn=ecn,
            change_summary='Aggiornamento metodo calibrazione dinamometri (da ECN-EXEC-001).',
        )
        req01 = submit_version_for_approval(ver01, author, [approver],
                                            send_notifications=False)
        approve_version(req01, approver,
                        comment='Rev. 01 approvata.',
                        send_notifications=False)
        doc.refresh_from_db()
        ecn.refresh_from_db()
        close_change_notice(ecn, supervisor,
                            close_notes='ECN chiuso dopo approvazione Rev. 01.',
                            send_notifications=False)

        self._step(
            f'{CODE}: Rev. 00 + ECN-EXEC-001 (policy ALL, 2 voti) + '
            f'Rev. 01 con ECN di origine visibile in version_detail.'
        )

    # ──────────────────────────────────────────────────────────────────────
    # Scenario 5 — Documento esente da ECN (approvazione diretta)
    # ──────────────────────────────────────────────────────────────────────

    def _scenario_ecn_exempt(self, supervisor, author, approver, folder):
        from documents.models import Document
        from documents.services import create_new_revision, submit_version_for_approval
        from approvals.services import approve_version

        CODE = 'DEMO-NOSCOPE-001'
        if Document.objects.filter(code=CODE).exists():
            self._step(f'{CODE}: già esistente, saltato.')
            return

        doc = Document.objects.create(
            code=CODE,
            title='Modulo registrazione presenze — Demo senza ECN obbligatorio',
            category=Document.Category.QUALITY,
            document_type='Modulo',
            project_folder=folder,
            owner=author,
            created_by=author,
            requires_ecn_for_revision=False,
        )

        for label, num, summary in [
            ('00', 0, 'Prima emissione modulo.'),
            ('01', 1, 'Aggiunto campo firma supervisore (approvazione diretta, senza ECN).'),
        ]:
            ver = create_new_revision(doc, author, label, num,
                                      change_summary=summary,
                                      _bypass_ecn_check=True)
            req = submit_version_for_approval(ver, author, [approver],
                                             send_notifications=False)
            approve_version(req, approver,
                            comment=f'Rev. {label} approvata direttamente.',
                            send_notifications=False)
            doc.refresh_from_db()

        self._step(f'{CODE}: documento esente ECN, 2 revisioni approvate senza ECN.')

    # ──────────────────────────────────────────────────────────────────────
    # Scenario 6 — Approval policy 'any' e 'sequential'
    # ──────────────────────────────────────────────────────────────────────

    def _scenario_approval_policies(self, supervisor, author, approver1, approver2, folder):
        from documents.models import Document
        from documents.services import create_new_revision, submit_version_for_approval

        # Policy ANY: 2 approvatori, basta uno — lasciata in stato IN_APPROVAL
        CODE_ANY = 'DEMO-POL-ANY'
        if not Document.objects.filter(code=CODE_ANY).exists():
            doc_any = Document.objects.create(
                code=CODE_ANY,
                title='Specifica interfaccia — Demo policy ANY (basta un voto)',
                category=Document.Category.QUALITY,
                document_type='Specifica',
                project_folder=folder,
                owner=author,
                created_by=author,
                requires_ecn_for_revision=False,
            )
            ver_any = create_new_revision(doc_any, author, '00', 0,
                                          change_summary='Specifica iniziale.',
                                          _bypass_ecn_check=True)
            submit_version_for_approval(ver_any, author,
                                        [approver1, approver2],
                                        approval_policy='any',
                                        send_notifications=False)
            self._step(
                f'{CODE_ANY}: Rev. 00 IN_APPROVAL con policy ANY — '
                f'2 approvatori in attesa, basta uno.'
            )
        else:
            self._step(f'{CODE_ANY}: già esistente, saltato.')

        # Policy SEQUENTIAL: 2 approvatori in sequenza — lasciata in stato IN_APPROVAL
        CODE_SEQ = 'DEMO-POL-SEQ'
        if not Document.objects.filter(code=CODE_SEQ).exists():
            doc_seq = Document.objects.create(
                code=CODE_SEQ,
                title='Piano di test integrazione — Demo policy SEQUENTIAL',
                category=Document.Category.QUALITY,
                document_type='Piano',
                project_folder=folder,
                owner=author,
                created_by=author,
                requires_ecn_for_revision=False,
            )
            ver_seq = create_new_revision(doc_seq, author, '00', 0,
                                          change_summary='Prima emissione.',
                                          _bypass_ecn_check=True)
            submit_version_for_approval(ver_seq, author,
                                        [approver1, approver2],
                                        approval_policy='sequential',
                                        send_notifications=False)
            self._step(
                f'{CODE_SEQ}: Rev. 00 IN_APPROVAL con policy SEQUENTIAL — '
                f'{approver1.get_full_name()} → {approver2.get_full_name()}.'
            )
        else:
            self._step(f'{CODE_SEQ}: già esistente, saltato.')

    # ──────────────────────────────────────────────────────────────────────
    # Scenario 7 — Record storici sanatoria (HistoricalRecord)
    # ──────────────────────────────────────────────────────────────────────

    def _scenario_sanatoria(self, supervisor, author):
        from auditlog.models import HistoricalImportBatch, HistoricalRecord

        BATCH_CODE = 'DEMO-BACKFILL-2020'
        if HistoricalImportBatch.objects.filter(code=BATCH_CODE).exists():
            self._step(f'Batch {BATCH_CODE}: già esistente, saltato.')
            return

        from documents.models import Document

        try:
            doc_multi = Document.objects.get(code='DEMO-MULTI-001')
            versions = list(doc_multi.versions.order_by('revision_number'))
        except Document.DoesNotExist:
            self._step('DEMO-MULTI-001 non trovato — sanatoria saltata.')
            return

        batch = HistoricalImportBatch.objects.create(
            code=BATCH_CODE,
            description=(
                'Backfill storico procedura QUA-PROC. '
                'Dati estratti dai registri cartacei 2020-2024.'
            ),
            status=HistoricalImportBatch.Status.COMPLETED,
            created_by=supervisor,
            completed_by=supervisor,
            completed_at=datetime.datetime(2026, 1, 15, 10, 0, tzinfo=datetime.timezone.utc),
            notes='Registri digitalizzati da archivio fisico. Verbali riunioni QM.',
        )

        def _ver_id(idx):
            return str(versions[idx].pk) if len(versions) > idx else ''

        records = [
            dict(
                event_type=HistoricalRecord.EventType.DOC_CREATED,
                historical_actor_name='Ing. Roberto Ferri',
                historical_date=datetime.date(2020, 1, 15),
                date_precision=HistoricalRecord.DatePrecision.EXACT_DATE,
                target_app='documents', target_model='document',
                target_id=str(doc_multi.pk), target_repr=str(doc_multi),
                source_description='Registro emissioni documenti QM 2020, pag. 12.',
                notes='Documento creato su iniziativa del responsabile qualità.',
            ),
            dict(
                event_type=HistoricalRecord.EventType.DOC_APPROVED,
                historical_actor_name='Dr. Andrea Mele (Resp. Qualità)',
                historical_date=datetime.date(2020, 2, 10),
                date_precision=HistoricalRecord.DatePrecision.EXACT_DATE,
                target_app='documents', target_model='documentversion',
                target_id=_ver_id(0), target_repr='Rev. 00',
                source_description='Verbale riunione QM del 2020-02-10, punto 3.',
                notes='Approvazione unanime. Firmato dal direttore tecnico.',
            ),
            dict(
                event_type=HistoricalRecord.EventType.DOC_REVISION_CREATED,
                historical_actor_name='Ing. Roberto Ferri',
                historical_date=datetime.date(2022, 6, 1),
                date_precision=HistoricalRecord.DatePrecision.EXACT_DATE,
                target_app='documents', target_model='documentversion',
                target_id=_ver_id(1), target_repr='Rev. 01',
                source_description='ECN interno ECN-2022-014 approvato il 2022-05-28.',
                notes='Revisione richiesta a seguito di audit interno.',
            ),
            dict(
                event_type=HistoricalRecord.EventType.DOC_APPROVED,
                historical_actor_name='Dr. Andrea Mele (Resp. Qualità)',
                historical_date=datetime.date(2022, 6, 20),
                date_precision=HistoricalRecord.DatePrecision.EXACT_DATE,
                target_app='documents', target_model='documentversion',
                target_id=_ver_id(1), target_repr='Rev. 01',
                source_description='Verbale riunione QM del 2022-06-20, punto 2.',
                notes='Approvata con osservazione: aggiornare IO correlata entro 30 gg.',
            ),
            dict(
                event_type=HistoricalRecord.EventType.DOC_REVISION_CREATED,
                historical_actor_name='Ing. Roberto Ferri',
                historical_date=datetime.date(2024, 3, 1),
                date_precision=HistoricalRecord.DatePrecision.MONTH,
                target_app='documents', target_model='documentversion',
                target_id=_ver_id(2), target_repr='Rev. 02',
                source_description='Copia controllata n. 4 — timbro data marzo 2024.',
                notes='Data esatta non nota. Estratta da timbro sulla copia controllata.',
            ),
        ]

        for r in records:
            HistoricalRecord.objects.create(
                import_batch=batch,
                recorded_by=supervisor,
                **r,
            )

        self._step(
            f'Batch {BATCH_CODE}: {len(records)} record storici per DEMO-MULTI-001.'
        )

    # ──────────────────────────────────────────────────────────────────────

    def _step(self, message):
        self.stdout.write(f'  >> {message}')
