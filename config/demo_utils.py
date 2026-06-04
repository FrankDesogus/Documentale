"""
Utilità per la modalità demo.

DOCUMENTALE_DEMO_MODE deve essere True E l'utente deve essere il supervisore
demo designato perché le deroghe siano attive.

Con DOCUMENTALE_DEMO_MODE=False (default) is_demo_supervisor restituisce
sempre False, rendendo impossibile qualsiasi deroga anche se esiste un
utente chiamato 'supervisor_demo'.
"""

from django.conf import settings


def is_demo_supervisor(user) -> bool:
    """
    True solo se:
      - DOCUMENTALE_DEMO_MODE è attivo nelle settings (impostato via env var), E
      - l'utente è autenticato, E
      - lo username corrisponde a DOCUMENTALE_DEMO_SUPERVISOR_USERNAME.

    Con demo mode disabilitato restituisce sempre False.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if not getattr(settings, 'DOCUMENTALE_DEMO_MODE', False):
        return False
    supervisor_username = getattr(
        settings, 'DOCUMENTALE_DEMO_SUPERVISOR_USERNAME', 'supervisor_demo'
    )
    return user.username == supervisor_username
