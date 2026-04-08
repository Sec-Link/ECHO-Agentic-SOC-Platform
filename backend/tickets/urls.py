from django.urls import path, include
from rest_framework.routers import SimpleRouter
from . import views

app_name = "tickets"

# REST API Router - use SimpleRouter to avoid duplicate format suffix converter registration
router = SimpleRouter()
# Ticket CRUD at the router root => /api/v1/tickets/
router.register(r'', views.EventTicketViewSet, basename='api-ticket')
# SLA metrics remain grouped => /api/v1/tickets/sla/
router.register(r'sla', views.TicketSLAViewSet, basename='api-sla')

urlpatterns = [
    # REST API routes (must come first)
    path('', include(router.urls)),


]