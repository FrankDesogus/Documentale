"""
Permessi per la sanatoria storica (DEMO-SANATORIA-MODE).

Regole fondamentali:
  can_use_sanatoria          → SOLO is_demo_supervisor(user)
                               (richiede DOCUMENTALE_DEMO_MODE=true)
  can_view_historical_records → supervisor_demo, superuser, QM, DM, DA
  can_verify_historical_records → Quality Manager, superuser

Con DOCUMENTALE_DEMO_MODE=false:
  - can_use_sanatoria restituisce sempre False
  - la UI non mostra checkbox né campi storici
  - i POST con sanatoria=true vengono rifiutati
"""


def can_use_sanatoria(user) -> bool:
    """
    True SOLO se DOCUMENTALE_DEMO_MODE è attivo E l'utente è supervisor_demo.
    Qualsiasi altro utente — inclusi superuser ordinari e Quality Manager — ottiene False.
    """
    from config.demo_utils import is_demo_supervisor
    return is_demo_supervisor(user)


def can_view_historical_records(user) -> bool:
    """
    True per: supervisor_demo, superuser, Quality Manager,
    Document Manager, Document Auditor.
    """
    if not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser:
        return True
    from config.demo_utils import is_demo_supervisor
    if is_demo_supervisor(user):
        return True
    from documents.permissions import (
        GROUP_QUALITY_MANAGER,
        GROUP_MANAGERS,
        GROUP_AUDITORS,
    )
    return user.groups.filter(
        name__in=[GROUP_QUALITY_MANAGER, GROUP_MANAGERS, GROUP_AUDITORS]
    ).exists()


def can_verify_historical_records(user) -> bool:
    """
    True per: Quality Manager, superuser.
    """
    if not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser:
        return True
    from documents.permissions import GROUP_QUALITY_MANAGER
    return user.groups.filter(name=GROUP_QUALITY_MANAGER).exists()
