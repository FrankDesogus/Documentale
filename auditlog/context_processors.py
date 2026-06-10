"""
Context processor per la sanatoria storica.

Aggiunge al context di ogni template:
  sanatoria_available → bool

True solo se DOCUMENTALE_DEMO_MODE=true E l'utente è supervisor_demo.
In produzione è sempre False — nessun overhead applicativo.
"""


def sanatoria_context(request):
    """Inietta sanatoria_available in ogni template."""
    try:
        from auditlog.permissions import can_use_sanatoria
        available = can_use_sanatoria(request.user)
    except Exception:
        available = False
    return {'sanatoria_available': available}
