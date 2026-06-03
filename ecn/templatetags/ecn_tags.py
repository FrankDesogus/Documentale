"""Template tags per ECN / Varianti."""
from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def user_can_view_ecn_dashboard(context):
    """Restituisce True se l'utente può accedere al cruscotto ECN operativo."""
    user = context.get('user')
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    from ecn.permissions import _can_consult_all_ecn
    return _can_consult_all_ecn(user)
