"""
Test per l'app auditlog — SAN-1/SAN-2: modelli e form sanatoria storica.
"""
import datetime

from django import forms
from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from auditlog.models import (
    AuditLog,
    HistoricalEvidence,
    HistoricalImportBatch,
    HistoricalRecord,
)
from auditlog.permissions import (
    can_use_sanatoria,
    can_verify_historical_records,
    can_view_historical_records,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(username, **kwargs):
    return User.objects.create_user(username=username, password='pass', **kwargs)


def _make_supervisor_demo():
    return User.objects.create_user(
        username='supervisor_demo',
        password='demo1234',
        is_superuser=True,
        is_staff=True,
    )


def _make_batch(created_by=None, code='test-batch-01'):
    return HistoricalImportBatch.objects.create(
        code=code,
        description='Lotto test',
        created_by=created_by,
    )


# ---------------------------------------------------------------------------
# SAN-1a: HistoricalImportBatch model
# ---------------------------------------------------------------------------

class HistoricalImportBatchModelTests(TestCase):
    def setUp(self):
        self.user = _make_user('tester')

    def test_create_batch_minimal(self):
        """Un batch si crea con solo il codice."""
        batch = HistoricalImportBatch.objects.create(
            code='batch-001',
            created_by=self.user,
        )
        self.assertEqual(batch.status, HistoricalImportBatch.Status.DRAFT)
        self.assertIsNotNone(batch.created_at)

    def test_batch_default_status_is_draft(self):
        batch = _make_batch(self.user)
        self.assertEqual(batch.status, HistoricalImportBatch.Status.DRAFT)

    def test_batch_str(self):
        batch = _make_batch(self.user, code='b-02')
        self.assertIn('b-02', str(batch))
        self.assertIn('Bozza', str(batch))

    def test_batch_status_choices(self):
        batch = _make_batch(self.user, code='b-03')
        batch.status = HistoricalImportBatch.Status.COMPLETED
        batch.completed_by = self.user
        batch.completed_at = timezone.now()
        batch.save()
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'completed')

    def test_batch_code_unique(self):
        _make_batch(self.user, code='unique-code')
        with self.assertRaises(Exception):
            _make_batch(self.user, code='unique-code')


# ---------------------------------------------------------------------------
# SAN-1b: HistoricalRecord model
# ---------------------------------------------------------------------------

class HistoricalRecordModelTests(TestCase):
    def setUp(self):
        self.user = _make_user('tester')
        self.batch = _make_batch(self.user)

    def _make_record(self, **kwargs):
        defaults = {
            'import_batch': self.batch,
            'event_type': HistoricalRecord.EventType.DOC_APPROVED,
            'recorded_by': self.user,
        }
        defaults.update(kwargs)
        return HistoricalRecord.objects.create(**defaults)

    def test_create_record_minimal(self):
        """Un record storico si crea con i soli campi obbligatori."""
        rec = self._make_record()
        self.assertFalse(rec.is_verified)
        self.assertIsNotNone(rec.recorded_at)
        self.assertIsNone(rec.historical_date)

    def test_historical_date_nullable(self):
        """historical_date può essere None (UNKNOWN)."""
        rec = self._make_record(
            date_precision=HistoricalRecord.DatePrecision.UNKNOWN,
        )
        self.assertIsNone(rec.historical_date)

    def test_date_precision_year_without_exact_date(self):
        """Precisione YEAR con historical_date senza giorno esplicito è valida."""
        rec = self._make_record(
            historical_date=datetime.date(2018, 1, 1),
            date_precision=HistoricalRecord.DatePrecision.YEAR,
        )
        self.assertEqual(rec.get_date_display(), '2018')

    def test_date_precision_month(self):
        rec = self._make_record(
            historical_date=datetime.date(2021, 3, 1),
            date_precision=HistoricalRecord.DatePrecision.MONTH,
        )
        self.assertEqual(rec.get_date_display(), '03/2021')

    def test_date_precision_exact(self):
        rec = self._make_record(
            historical_date=datetime.date(2021, 3, 15),
            date_precision=HistoricalRecord.DatePrecision.EXACT_DATE,
        )
        self.assertEqual(rec.get_date_display(), '15/03/2021')

    def test_actor_fk_optional(self):
        """FK attore opzionale: si può usare solo il nome testuale."""
        rec = self._make_record(
            historical_actor_name='Mario Rossi',
            historical_actor_user=None,
        )
        self.assertEqual(rec.get_actor_display(), 'Mario Rossi')

    def test_actor_user_linked(self):
        """Se FK è valorizzato, get_actor_display usa il fullname."""
        actor = _make_user('mario.rossi', first_name='Mario', last_name='Rossi')
        rec = self._make_record(historical_actor_user=actor)
        self.assertEqual(rec.get_actor_display(), 'Mario Rossi')

    def test_actor_without_account_is_valid(self):
        """Un ex-dipendente senza account è valido come nome testuale."""
        rec = self._make_record(
            historical_actor_name='Giovanni Bianchi (ex dipendente 2015-2020)',
            historical_actor_user=None,
        )
        self.assertEqual(rec.historical_actor_user, None)
        self.assertIn('Bianchi', rec.historical_actor_name)

    def test_recorded_at_is_set_automatically(self):
        rec = self._make_record()
        self.assertIsNotNone(rec.recorded_at)
        # deve essere vicino al momento attuale
        delta = timezone.now() - rec.recorded_at
        self.assertLess(delta.total_seconds(), 5)

    def test_is_verified_default_false(self):
        rec = self._make_record()
        self.assertFalse(rec.is_verified)

    def test_is_verified_can_be_set(self):
        verifier = _make_user('qm_user')
        rec = self._make_record()
        rec.is_verified = True
        rec.verified_by = verifier
        rec.verified_at = timezone.now()
        rec.save()
        rec.refresh_from_db()
        self.assertTrue(rec.is_verified)
        self.assertEqual(rec.verified_by, verifier)

    def test_str_includes_event_type_and_actor(self):
        rec = self._make_record(historical_actor_name='Luisa Verdi')
        self.assertIn('Luisa Verdi', str(rec))

    def test_all_event_types_valid(self):
        """Tutti gli EventType dichiarati devono poter essere salvati."""
        for et in HistoricalRecord.EventType:
            rec = self._make_record(event_type=et.value)
            self.assertEqual(rec.event_type, et.value)

    def test_target_fields_optional(self):
        rec = self._make_record(
            target_app='documents',
            target_model='DocumentVersion',
            target_id='42',
            target_repr='DOC-001 rev.A',
        )
        self.assertEqual(rec.target_repr, 'DOC-001 rev.A')


# ---------------------------------------------------------------------------
# SAN-1c: HistoricalEvidence model
# ---------------------------------------------------------------------------

class HistoricalEvidenceModelTests(TestCase):
    def setUp(self):
        self.user = _make_user('tester')
        self.batch = _make_batch(self.user)
        self.record = HistoricalRecord.objects.create(
            import_batch=self.batch,
            event_type=HistoricalRecord.EventType.GENERIC,
            recorded_by=self.user,
        )

    def test_multiple_evidences_per_record(self):
        """Più allegati possono essere associati allo stesso record."""
        from django.core.files.base import ContentFile
        for i in range(3):
            ev = HistoricalEvidence(
                historical_record=self.record,
                description=f'Allegato {i}',
                uploaded_by=self.user,
            )
            ev.file.save(f'test_{i}.txt', ContentFile(b'test'), save=True)
        count = self.record.evidence_files.count()
        self.assertEqual(count, 3)

    def test_evidence_str(self):
        from django.core.files.base import ContentFile
        ev = HistoricalEvidence(
            historical_record=self.record,
            description='Verbale',
            uploaded_by=self.user,
        )
        ev.file.save('verbale.txt', ContentFile(b'test'), save=True)
        self.assertIn('Verbale', str(ev))


# ---------------------------------------------------------------------------
# SAN-1d: Permessi sanatoria
# ---------------------------------------------------------------------------

class SanatoriaPermissionsTests(TestCase):
    def setUp(self):
        self.normal_user = _make_user('normale')
        self.superuser = _make_user('admin', is_superuser=True)
        self.supervisor_demo = _make_supervisor_demo()

        self.qm_group = Group.objects.create(name='Quality Manager')
        self.dm_group = Group.objects.create(name='Document Managers')
        self.da_group = Group.objects.create(name='Document Auditors')

        self.qm_user = _make_user('qm')
        self.qm_user.groups.add(self.qm_group)

        self.dm_user = _make_user('dm')
        self.dm_user.groups.add(self.dm_group)

        self.da_user = _make_user('da')
        self.da_user.groups.add(self.da_group)

    # --- can_use_sanatoria ---

    @override_settings(DOCUMENTALE_DEMO_MODE=False)
    def test_can_use_sanatoria_demo_mode_off(self):
        """Demo mode OFF → can_use_sanatoria sempre False."""
        self.assertFalse(can_use_sanatoria(self.supervisor_demo))
        self.assertFalse(can_use_sanatoria(self.superuser))
        self.assertFalse(can_use_sanatoria(self.normal_user))

    @override_settings(
        DOCUMENTALE_DEMO_MODE=True,
        DOCUMENTALE_DEMO_SUPERVISOR_USERNAME='supervisor_demo',
    )
    def test_can_use_sanatoria_demo_mode_on_supervisor(self):
        """Demo mode ON + supervisor_demo → True."""
        self.assertTrue(can_use_sanatoria(self.supervisor_demo))

    @override_settings(
        DOCUMENTALE_DEMO_MODE=True,
        DOCUMENTALE_DEMO_SUPERVISOR_USERNAME='supervisor_demo',
    )
    def test_can_use_sanatoria_demo_mode_on_normal_user(self):
        """Demo mode ON + utente normale → False."""
        self.assertFalse(can_use_sanatoria(self.normal_user))

    @override_settings(
        DOCUMENTALE_DEMO_MODE=True,
        DOCUMENTALE_DEMO_SUPERVISOR_USERNAME='supervisor_demo',
    )
    def test_can_use_sanatoria_demo_mode_on_superuser(self):
        """Demo mode ON + superuser ordinario (non supervisor_demo) → False."""
        self.assertFalse(can_use_sanatoria(self.superuser))

    @override_settings(
        DOCUMENTALE_DEMO_MODE=True,
        DOCUMENTALE_DEMO_SUPERVISOR_USERNAME='supervisor_demo',
    )
    def test_can_use_sanatoria_demo_mode_on_qm(self):
        """Demo mode ON + Quality Manager → False (non è supervisor_demo)."""
        self.assertFalse(can_use_sanatoria(self.qm_user))

    # --- can_view_historical_records ---

    @override_settings(DOCUMENTALE_DEMO_MODE=False)
    def test_can_view_historical_records_superuser(self):
        self.assertTrue(can_view_historical_records(self.superuser))

    @override_settings(DOCUMENTALE_DEMO_MODE=False)
    def test_can_view_historical_records_qm(self):
        self.assertTrue(can_view_historical_records(self.qm_user))

    @override_settings(DOCUMENTALE_DEMO_MODE=False)
    def test_can_view_historical_records_dm(self):
        self.assertTrue(can_view_historical_records(self.dm_user))

    @override_settings(DOCUMENTALE_DEMO_MODE=False)
    def test_can_view_historical_records_da(self):
        self.assertTrue(can_view_historical_records(self.da_user))

    @override_settings(DOCUMENTALE_DEMO_MODE=False)
    def test_can_view_historical_records_normal_user(self):
        self.assertFalse(can_view_historical_records(self.normal_user))

    # --- can_verify_historical_records ---

    @override_settings(DOCUMENTALE_DEMO_MODE=False)
    def test_can_verify_qm(self):
        self.assertTrue(can_verify_historical_records(self.qm_user))

    @override_settings(DOCUMENTALE_DEMO_MODE=False)
    def test_can_verify_superuser(self):
        self.assertTrue(can_verify_historical_records(self.superuser))

    @override_settings(DOCUMENTALE_DEMO_MODE=False)
    def test_can_verify_dm_false(self):
        """Document Manager NON può verificare (solo QM e superuser)."""
        self.assertFalse(can_verify_historical_records(self.dm_user))

    @override_settings(DOCUMENTALE_DEMO_MODE=False)
    def test_can_verify_normal_user_false(self):
        self.assertFalse(can_verify_historical_records(self.normal_user))


# ---------------------------------------------------------------------------
# SAN-2: SanatoriaFieldsMixin — form gate e validazione
# ---------------------------------------------------------------------------

from auditlog.historical_forms import SanatoriaFieldsMixin, should_send_notifications


class _TestForm(SanatoriaFieldsMixin, forms.Form):
    """Form minimo per testare il mixin."""
    title = forms.CharField()


class SanatoriaFormGateTests(TestCase):
    """
    Verifica che il gate demo mode funzioni correttamente sul mixin.
    """

    def _make_supervisor(self):
        return User.objects.create_user(
            username='supervisor_demo',
            password='demo1234',
            is_superuser=True,
        )

    def _make_normal(self):
        return User.objects.create_user('user_norm', password='pass')

    # --- Test 1: demo mode OFF → nessun campo sanatoria nel form ---

    @override_settings(DOCUMENTALE_DEMO_MODE=False)
    def test_demo_mode_off_no_sanatoria_field(self):
        """Demo mode OFF → campo 'sanatoria' non presente nel form."""
        user = self._make_supervisor()
        form = _TestForm(current_user=user)
        self.assertNotIn('sanatoria', form.fields)

    # --- Test 2: demo mode ON + utente normale → no sanatoria ---

    @override_settings(
        DOCUMENTALE_DEMO_MODE=True,
        DOCUMENTALE_DEMO_SUPERVISOR_USERNAME='supervisor_demo',
    )
    def test_demo_mode_on_normal_user_no_sanatoria(self):
        """Demo mode ON + utente normale → campo sanatoria assente."""
        user = self._make_normal()
        form = _TestForm(current_user=user)
        self.assertNotIn('sanatoria', form.fields)

    # --- Test 3: demo mode ON + supervisor_demo → sanatoria presente ---

    @override_settings(
        DOCUMENTALE_DEMO_MODE=True,
        DOCUMENTALE_DEMO_SUPERVISOR_USERNAME='supervisor_demo',
    )
    def test_demo_mode_on_supervisor_has_sanatoria(self):
        """Demo mode ON + supervisor_demo → campo sanatoria presente."""
        user = self._make_supervisor()
        form = _TestForm(current_user=user)
        self.assertIn('sanatoria', form.fields)

    # --- Test 4: POST forgiato da utente normale con sanatoria=true → ignorato ---

    @override_settings(
        DOCUMENTALE_DEMO_MODE=True,
        DOCUMENTALE_DEMO_SUPERVISOR_USERNAME='supervisor_demo',
    )
    def test_forged_post_sanatoria_ignored_for_normal_user(self):
        """POST forgiato con sanatoria=true da utente normale → is_sanatoria False."""
        user = self._make_normal()
        form = _TestForm({'title': 'test', 'sanatoria': 'on'}, current_user=user)
        form.is_valid()
        self.assertFalse(form.is_sanatoria)

    # --- Test 5: default checkbox → False ---

    @override_settings(
        DOCUMENTALE_DEMO_MODE=True,
        DOCUMENTALE_DEMO_SUPERVISOR_USERNAME='supervisor_demo',
    )
    def test_default_sanatoria_false(self):
        """Senza POST, sanatoria default è False."""
        user = self._make_supervisor()
        form = _TestForm({'title': 'test'}, current_user=user)
        self.assertTrue(form.is_valid())
        self.assertFalse(form.is_sanatoria)

    # --- Validazione: data futura rifiutata ---

    @override_settings(
        DOCUMENTALE_DEMO_MODE=True,
        DOCUMENTALE_DEMO_SUPERVISOR_USERNAME='supervisor_demo',
    )
    def test_historical_date_future_rejected(self):
        """Data storica nel futuro → errore di validazione."""
        user = self._make_supervisor()
        future = (datetime.date.today() + datetime.timedelta(days=5)).isoformat()
        form = _TestForm({
            'title': 'test',
            'sanatoria': 'on',
            'historical_actor_name': 'Mario Rossi',
            'historical_date': future,
            'date_precision': 'exact_date',
        }, current_user=user)
        self.assertFalse(form.is_valid())
        self.assertIn('historical_date', form.errors)

    # --- Validazione: senza attore → errore ---

    @override_settings(
        DOCUMENTALE_DEMO_MODE=True,
        DOCUMENTALE_DEMO_SUPERVISOR_USERNAME='supervisor_demo',
    )
    def test_sanatoria_without_actor_invalid(self):
        """Sanatoria selezionata senza attore → errore."""
        user = self._make_supervisor()
        form = _TestForm({
            'title': 'test',
            'sanatoria': 'on',
            'historical_date': '2021-03-15',
            'date_precision': 'exact_date',
        }, current_user=user)
        self.assertFalse(form.is_valid())
        self.assertIn('historical_actor_name', form.errors)

    # --- Validazione: anno senza data esatta → valido con YEAR precision ---

    @override_settings(
        DOCUMENTALE_DEMO_MODE=True,
        DOCUMENTALE_DEMO_SUPERVISOR_USERNAME='supervisor_demo',
    )
    def test_year_precision_without_exact_date_valid(self):
        """Precisione YEAR senza data esatta → valido."""
        user = self._make_supervisor()
        form = _TestForm({
            'title': 'test',
            'sanatoria': 'on',
            'historical_actor_name': 'Mario Rossi',
            'date_precision': 'year',
            # historical_date assente
        }, current_user=user)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form.is_sanatoria)

    # --- maybe_create_historical_record: sanatoria false → None ---

    @override_settings(
        DOCUMENTALE_DEMO_MODE=True,
        DOCUMENTALE_DEMO_SUPERVISOR_USERNAME='supervisor_demo',
    )
    def test_maybe_create_no_record_when_sanatoria_false(self):
        """Sanatoria False → nessun HistoricalRecord creato."""
        from auditlog.models import HistoricalRecord
        user = self._make_supervisor()
        form = _TestForm({'title': 'test'}, current_user=user)
        form.is_valid()
        result = form.maybe_create_historical_record(
            event_type=HistoricalRecord.EventType.GENERIC,
            recorded_by=user,
        )
        self.assertIsNone(result)
        self.assertEqual(HistoricalRecord.objects.count(), 0)

    # --- maybe_create_historical_record: sanatoria true → HistoricalRecord creato ---

    @override_settings(
        DOCUMENTALE_DEMO_MODE=True,
        DOCUMENTALE_DEMO_SUPERVISOR_USERNAME='supervisor_demo',
    )
    def test_maybe_create_record_when_sanatoria_true(self):
        """Sanatoria True → HistoricalRecord creato."""
        from auditlog.models import HistoricalRecord
        user = self._make_supervisor()
        form = _TestForm({
            'title': 'test',
            'sanatoria': 'on',
            'historical_actor_name': 'Mario Rossi',
            'date_precision': 'year',
        }, current_user=user)
        self.assertTrue(form.is_valid(), form.errors)
        result = form.maybe_create_historical_record(
            event_type=HistoricalRecord.EventType.GENERIC,
            recorded_by=user,
        )
        self.assertIsNotNone(result)
        self.assertEqual(HistoricalRecord.objects.count(), 1)
        self.assertEqual(result.recorded_by, user)
        self.assertEqual(result.historical_actor_name, 'Mario Rossi')

    # --- recorded_by è sempre l'utente corrente ---

    @override_settings(
        DOCUMENTALE_DEMO_MODE=True,
        DOCUMENTALE_DEMO_SUPERVISOR_USERNAME='supervisor_demo',
    )
    def test_recorded_by_is_current_user(self):
        """recorded_by deve essere l'utente che esegue il backfill, non l'attore storico."""
        from auditlog.models import HistoricalRecord
        user = self._make_supervisor()
        form = _TestForm({
            'title': 'test',
            'sanatoria': 'on',
            'historical_actor_name': 'Ex Dipendente',
            'date_precision': 'unknown',
        }, current_user=user)
        form.is_valid()
        result = form.maybe_create_historical_record(
            event_type=HistoricalRecord.EventType.GENERIC,
            recorded_by=user,
        )
        self.assertEqual(result.recorded_by, user)
        self.assertEqual(result.historical_actor_name, 'Ex Dipendente')

    # --- actor testuale senza account è valido ---

    @override_settings(
        DOCUMENTALE_DEMO_MODE=True,
        DOCUMENTALE_DEMO_SUPERVISOR_USERNAME='supervisor_demo',
    )
    def test_actor_name_without_account_valid(self):
        """Nome libero senza FK User è valido."""
        user = self._make_supervisor()
        form = _TestForm({
            'title': 'test',
            'sanatoria': 'on',
            'historical_actor_name': 'Giovanni Bianchi (ex 2015)',
            'date_precision': 'unknown',
        }, current_user=user)
        self.assertTrue(form.is_valid(), form.errors)


# ---------------------------------------------------------------------------
# SAN-2b: should_send_notifications helper
# ---------------------------------------------------------------------------

class ShouldSendNotificationsTests(TestCase):
    def test_sanatoria_false_sends(self):
        """sanatoria=False → notifiche attive."""
        self.assertTrue(should_send_notifications(sanatoria=False))

    def test_sanatoria_true_no_send(self):
        """sanatoria=True → nessuna notifica."""
        self.assertFalse(should_send_notifications(sanatoria=True))
