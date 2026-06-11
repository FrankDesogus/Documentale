"""
PermissionResolver per FolderPermissionGrant.

Valuta i permessi per cartella con due layer sovrapposti:
  1. FolderPermissionGrant — grant modulari granulari (permission_code, effect ALLOW/DENY)
  2. ProjectFolderMembership — legacy, usato come fallback se include_legacy_fallback=True

Il resolver è integrato in produzione da:
  - documents/permissions.py (_resolve_folder_perm)
  - projects/permissions.py (can_view_folder, can_write_folder, get_visible_folder_ids, …)
  - projects/views.py (verifiche inline su visibilità cartella)

Strategia di migrazione: i grant modulari hanno precedenza; se nessun grant decide,
il fallback legacy legge ProjectFolderMembership con conversione _LEGACY_ROLE_PERMISSIONS.
Il management command backfill_folder_permission_grants converte i membership legacy
in grant modulari equivalenti.
"""

from __future__ import annotations

from typing import Optional

from django.utils import timezone


# ---------------------------------------------------------------------------
# Mapping legacy conservativo: ruolo → frozenset di PermissionCode
# ---------------------------------------------------------------------------
# Riproduce esattamente la semantica di projects/permissions.py senza
# aggiungere capacità che il ruolo oggi non possiede.
#
# reader   → lettura documenti pubblicati + visibilità struttura
# author   → reader + crea bozze, invia in approvazione, gestisce bozze
#             rifiutate, gestisce documenti di progetto, richiede ECN
# approver → reader + eleggibile come approvatore
# auditor  → reader + storico versioni + documenti obsoleti
# manager  → tutti i permessi sopra + gestione cartella
# ---------------------------------------------------------------------------

_LEGACY_READER_PERMISSIONS: frozenset[str] = frozenset([
    'read_published',
    'view_projects',
    'view_folder_ecns',
])

_LEGACY_AUTHOR_PERMISSIONS: frozenset[str] = _LEGACY_READER_PERMISSIONS | frozenset([
    'create_draft',
    'submit_for_approval',
    'manage_rejected_drafts',
    'manage_project_documents',
    'request_ecn',
])

_LEGACY_APPROVER_PERMISSIONS: frozenset[str] = _LEGACY_READER_PERMISSIONS | frozenset([
    'eligible_document_approver',
])

_LEGACY_AUDITOR_PERMISSIONS: frozenset[str] = _LEGACY_READER_PERMISSIONS | frozenset([
    'view_history',
    'view_obsolete_documents',
])

_LEGACY_MANAGER_PERMISSIONS: frozenset[str] = frozenset([
    'read_published',
    'view_history',
    'view_obsolete_documents',
    'view_projects',
    'view_folder_ecns',
    'create_draft',
    'submit_for_approval',
    'eligible_document_approver',
    'manage_rejected_drafts',
    'manage_project_documents',
    'request_ecn',
    'manage_folder',
])

_LEGACY_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    'reader':   _LEGACY_READER_PERMISSIONS,
    'author':   _LEGACY_AUTHOR_PERMISSIONS,
    'approver': _LEGACY_APPROVER_PERMISSIONS,
    'auditor':  _LEGACY_AUDITOR_PERMISSIONS,
    'manager':  _LEGACY_MANAGER_PERMISSIONS,
}


def _is_anonymous(user) -> bool:
    if user is None:
        return True
    return not getattr(user, 'is_authenticated', False)


class PermissionResolver:
    """
    Resolver granulare per FolderPermissionGrant.

    Regole di risoluzione:
      - is_superuser → allow totale
      - is_staff senza grant → deny (nessun bypass applicativo)
      - Valuta grant da cartella corrente verso root
      - Cartella corrente: tutti i grant (anche inherit_to_children=False)
      - Antenati: solo grant con inherit_to_children=True
      - Parità di livello: user_deny > user_allow > group_deny > group_allow
      - Grant scaduti ignorati (expires_at <= now)
      - Default: deny
    """

    def __init__(
        self,
        user,
        *,
        include_legacy_fallback: bool = False,
    ) -> None:
        self._user = user
        self._include_legacy_fallback = include_legacy_fallback
        self._cache: dict[tuple[int, str], bool] = {}
        self._group_ids: Optional[list[int]] = None

    @classmethod
    def for_request(
        cls,
        request,
        *,
        include_legacy_fallback: bool = False,
    ) -> "PermissionResolver":
        """
        Restituisce un resolver cached sulla request.

        Cache key: include_legacy_fallback.
        La stessa configurazione restituisce la stessa istanza per tutta la request,
        evitando query ripetute tra chiamate successive nella stessa view.
        """
        attr = '_folder_permission_resolvers'
        if not hasattr(request, attr):
            setattr(request, attr, {})
        resolvers: dict = getattr(request, attr)
        cache_key = include_legacy_fallback
        if cache_key not in resolvers:
            resolvers[cache_key] = cls(
                request.user,
                include_legacy_fallback=include_legacy_fallback,
            )
        return resolvers[cache_key]

    # ------------------------------------------------------------------
    # API pubblica
    # ------------------------------------------------------------------

    def has_permission(
        self,
        folder,
        permission_code: str,
    ) -> bool:
        """Verifica se l'utente ha il permesso sulla cartella indicata."""
        # folder None → deny (tranne superuser)
        if folder is None:
            return bool(getattr(self._user, 'is_superuser', False))

        if _is_anonymous(self._user):
            return False

        if self._user.is_superuser:
            return True

        cache_key = (folder.pk, permission_code)
        if cache_key in self._cache:
            return self._cache[cache_key]

        result = self._resolve(folder, permission_code)
        self._cache[cache_key] = result
        return result

    # ------------------------------------------------------------------
    # Risoluzione interna
    # ------------------------------------------------------------------

    def _get_group_ids(self) -> list[int]:
        """Carica gli ID gruppo dell'utente una sola volta per istanza."""
        if self._group_ids is None:
            self._group_ids = list(
                self._user.groups.values_list('id', flat=True)
            )
        return self._group_ids

    def _get_ancestor_pks_near_to_far(self, folder) -> list[int]:
        """
        Restituisce i pk degli antenati ordinati da parent diretto verso root,
        estratti dal materialized path della cartella.
        """
        if not folder.path:
            return []
        parts = [p for p in folder.path.split('/') if p]
        # parts[-1] è la cartella stessa; tutto il resto sono antenati
        ancestor_pks = [int(p) for p in parts[:-1]]
        return list(reversed(ancestor_pks))  # parent diretto → root

    def _resolve(self, folder, permission_code: str) -> bool:
        """
        Logica di risoluzione senza cache.

        Valuta dal livello più specifico (cartella corrente) verso il meno
        specifico (root). Alla prima decisione (allow o deny) si ferma.
        """
        from projects.models import FolderPermissionGrant
        from django.db.models import Q

        now = timezone.now()
        group_ids = self._get_group_ids()

        current_pk = folder.pk
        ancestor_pks = self._get_ancestor_pks_near_to_far(folder)
        all_folder_pks = [current_pk] + ancestor_pks

        # Query unica per tutti i grant rilevanti
        user_q = Q(user=self._user)
        group_q = Q(group_id__in=group_ids) if group_ids else Q(pk__in=[])

        grants = list(
            FolderPermissionGrant.objects.filter(
                (user_q | group_q),
                folder_id__in=all_folder_pks,
                permission_code=permission_code,
            ).filter(
                Q(expires_at__isnull=True) | Q(expires_at__gt=now)
            ).values(
                'folder_id', 'user_id', 'group_id', 'effect', 'inherit_to_children'
            )
        )

        # Raggruppa per folder_pk
        grants_by_folder: dict[int, list[dict]] = {pk: [] for pk in all_folder_pks}
        for g in grants:
            grants_by_folder[g['folder_id']].append(g)

        # Valuta dal più specifico verso il meno specifico
        for i, folder_pk in enumerate(all_folder_pks):
            level_grants = grants_by_folder.get(folder_pk, [])
            if not level_grants:
                continue

            # Per gli antenati (i > 0): considera solo grant ereditabili
            if i > 0:
                level_grants = [g for g in level_grants if g['inherit_to_children']]
                if not level_grants:
                    continue

            decision = self._evaluate_grants_at_level(level_grants)
            if decision is not None:
                return decision

        # Nessuna decisione modulare → fallback legacy opzionale
        if self._include_legacy_fallback:
            return self._legacy_fallback(folder, permission_code)

        return False  # default deny

    def _evaluate_grants_at_level(self, grants: list[dict]) -> Optional[bool]:
        """
        Valuta i grant di un singolo livello.

        Precedenza: user_deny > user_allow > group_deny > group_allow
        Restituisce True (allow), False (deny) o None (nessuna decisione).
        """
        has_user_deny = any(
            g['user_id'] is not None and g['effect'] == 'deny'
            for g in grants
        )
        if has_user_deny:
            return False

        has_user_allow = any(
            g['user_id'] is not None and g['effect'] == 'allow'
            for g in grants
        )
        if has_user_allow:
            return True

        has_group_deny = any(
            g['group_id'] is not None and g['effect'] == 'deny'
            for g in grants
        )
        if has_group_deny:
            return False

        has_group_allow = any(
            g['group_id'] is not None and g['effect'] == 'allow'
            for g in grants
        )
        if has_group_allow:
            return True

        return None

    def _legacy_fallback(self, folder, permission_code: str) -> bool:
        """
        Fallback conservativo basato su ProjectFolderMembership (singola cartella).
        Invocato solo se include_legacy_fallback=True e nessun grant modulare
        ha prodotto una decisione. Non sovrascrive un deny modulare.
        """
        from projects.models import ProjectFolderMembership
        try:
            membership = ProjectFolderMembership.objects.get(
                folder=folder, user=self._user,
            )
        except ProjectFolderMembership.DoesNotExist:
            return False
        role = membership.role
        allowed = _LEGACY_ROLE_PERMISSIONS.get(role, frozenset())
        return permission_code in allowed

    def _legacy_fallback_bulk(
        self,
        folders: list,
        permission_code: str,
    ) -> dict[int, bool]:
        """
        Fallback legacy per più cartelle in una singola query.
        Usato da resolve_bulk per minimizzare le query DB.
        """
        from projects.models import ProjectFolderMembership
        folder_ids = [f.pk for f in folders]
        memberships = {
            m['folder_id']: m['role']
            for m in ProjectFolderMembership.objects.filter(
                folder_id__in=folder_ids,
                user=self._user,
            ).values('folder_id', 'role')
        }
        return {
            f.pk: permission_code in _LEGACY_ROLE_PERMISSIONS.get(
                memberships.get(f.pk, ''), frozenset()
            )
            for f in folders
        }

    # ------------------------------------------------------------------
    # Bulk API
    # ------------------------------------------------------------------

    def resolve_bulk(
        self,
        folders: list,
        permission_code: str,
    ) -> dict[int, bool]:
        """
        Risolve il permesso per una lista di cartelle in modo efficiente.

        Usa al massimo 3 query DB totali:
          1. group_ids (cached dopo la prima chiamata)
          2. FolderPermissionGrant (tutti i folder + antenati in una sola query)
          3. ProjectFolderMembership (solo per cartelle senza decisione modulare,
             e solo se include_legacy_fallback=True)

        Rispetta le stesse regole del resolver singolo:
          - specificità child > parent
          - user_deny > user_allow > group_deny > group_allow
          - ereditarietà (inherit_to_children)
          - grant scaduti ignorati
          - default deny
          - superuser bypass
          - staff senza bypass

        Returns: dict {folder_pk: bool}
        """
        from django.db.models import Q

        if not folders:
            return {}

        # Fast path: anonimo
        if _is_anonymous(self._user):
            return {f.pk: False for f in folders}

        # Fast path: superuser
        if self._user.is_superuser:
            return {f.pk: True for f in folders}

        result: dict[int, bool] = {}
        needs_resolution: list = []

        # Recupera risultati già in cache
        for f in folders:
            cache_key = (f.pk, permission_code)
            if cache_key in self._cache:
                result[f.pk] = self._cache[cache_key]
            else:
                needs_resolution.append(f)

        if not needs_resolution:
            return result

        # Per ogni folder che richiede risoluzione, costruisce la catena
        # folder_pk → [folder_pk, parent_pk, grandparent_pk, ...]
        folder_chain_map: dict[int, list[int]] = {}
        all_pks_needed: set[int] = set()

        for f in needs_resolution:
            ancestor_pks = self._get_ancestor_pks_near_to_far(f)
            chain = [f.pk] + ancestor_pks
            folder_chain_map[f.pk] = chain
            all_pks_needed.update(chain)

        # UNA query per tutti i grant rilevanti (folder correnti + antenati)
        now = timezone.now()
        group_ids = self._get_group_ids()
        user_q = Q(user=self._user)
        group_q = Q(group_id__in=group_ids) if group_ids else Q(pk__in=[])

        from projects.models import FolderPermissionGrant
        grants_raw = list(
            FolderPermissionGrant.objects.filter(
                (user_q | group_q),
                folder_id__in=all_pks_needed,
                permission_code=permission_code,
            ).filter(
                Q(expires_at__isnull=True) | Q(expires_at__gt=now)
            ).values(
                'folder_id', 'user_id', 'group_id', 'effect', 'inherit_to_children'
            )
        )

        # Raggruppa per folder_pk
        grants_by_folder: dict[int, list[dict]] = {}
        for g in grants_raw:
            grants_by_folder.setdefault(g['folder_id'], []).append(g)

        # Risolve ogni folder usando i grant caricati
        needs_legacy: list = []

        for f in needs_resolution:
            chain = folder_chain_map[f.pk]
            decision: Optional[bool] = None

            for i, folder_pk in enumerate(chain):
                level_grants = grants_by_folder.get(folder_pk, [])
                if not level_grants:
                    continue
                if i > 0:  # antenato: solo grant ereditabili
                    level_grants = [g for g in level_grants if g['inherit_to_children']]
                    if not level_grants:
                        continue
                level_decision = self._evaluate_grants_at_level(level_grants)
                if level_decision is not None:
                    decision = level_decision
                    break

            if decision is not None:
                result[f.pk] = decision
                self._cache[(f.pk, permission_code)] = decision
            elif self._include_legacy_fallback:
                needs_legacy.append(f)
            else:
                result[f.pk] = False
                self._cache[(f.pk, permission_code)] = False

        # UNA query per il fallback legacy (solo per folder senza decisione modulare)
        if needs_legacy:
            legacy_results = self._legacy_fallback_bulk(needs_legacy, permission_code)
            for pk, decision in legacy_results.items():
                result[pk] = decision
                self._cache[(pk, permission_code)] = decision

        return result


# ---------------------------------------------------------------------------
# API funzionale conveniente
# ---------------------------------------------------------------------------

def has_folder_permission(
    user,
    folder,
    permission_code: str,
    *,
    include_legacy_fallback: bool = False,
) -> bool:
    """Verifica il permesso di una singola cartella senza istanziare esplicitamente il resolver."""
    resolver = PermissionResolver(
        user,
        include_legacy_fallback=include_legacy_fallback,
    )
    return resolver.has_permission(folder, permission_code)


def resolve_folder_permissions(
    user,
    folders: list,
    permission_code: str,
    *,
    include_legacy_fallback: bool = False,
) -> dict[int, bool]:
    """Risolve il permesso per una lista di cartelle in un'unica chiamata efficiente."""
    resolver = PermissionResolver(user, include_legacy_fallback=include_legacy_fallback)
    return resolver.resolve_bulk(folders, permission_code)
