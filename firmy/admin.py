from django.contrib import admin

from .models import FirmyPremise, FirmySearchHit, FirmySearchResult, FirmySearchRun


class FirmySearchHitInline(admin.TabularInline):
    model = FirmySearchHit
    extra = 0
    readonly_fields = (
        "position",
        "premise",
    )
    can_delete = False


@admin.register(FirmySearchRun)
class FirmySearchRunAdmin(admin.ModelAdmin):
    list_display = ("id", "query", "expected_limit", "status", "results_count", "created_at")
    list_filter = ("status",)
    search_fields = ("query",)
    readonly_fields = ("created_at", "finished_at", "search_url", "error_message")
    inlines = (FirmySearchHitInline,)


@admin.register(FirmyPremise)
class FirmyPremiseAdmin(admin.ModelAdmin):
    list_display = ("premise_id", "title", "address", "website_url", "updated_at")
    search_fields = ("title", "address", "premise_id", "phones", "emails", "website_url")
    readonly_fields = ("first_seen_at", "updated_at")


@admin.register(FirmySearchHit)
class FirmySearchHitAdmin(admin.ModelAdmin):
    list_display = ("run_id", "position", "premise")
    search_fields = ("premise__premise_id", "premise__title", "premise__address")


@admin.register(FirmySearchResult)
class FirmySearchResultAdmin(admin.ModelAdmin):
    list_display = ("run_id", "position", "premise_id", "title", "address", "website_url", "phones", "emails")
    search_fields = ("title", "address", "premise_id", "phones", "emails", "website_url")
