from django.urls import path

from . import views

app_name = "firmy"

urlpatterns = [
    path("search/", views.run_search, name="run_search"),
    path("runs/<int:run_id>/", views.results, name="results"),
    path("premises/", views.premises, name="premises"),
    path("processing/", views.processing, name="processing"),
    path("processing/client/<int:client_item_id>/", views.processing, name="processing_client"),
    path("processing/generate/start/", views.processing_generate_start, name="processing_generate_start"),
    path("processing/generate/status/", views.processing_generate_status, name="processing_generate_status"),
    path("processing/reply/status/", views.processing_reply_status, name="processing_reply_status"),
    path("processing/evaluate/start/", views.processing_evaluate_start, name="processing_evaluate_start"),
    path("processing/evaluate/status/", views.processing_evaluate_status, name="processing_evaluate_status"),
    path("agents/", views.agents, name="agents"),
    path("agents/new/", views.agent_new, name="agent_new"),
    path("agents/<int:agent_id>/settings/", views.agent_settings, name="agent_settings"),
]
