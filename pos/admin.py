from django.contrib import admin

from .models import Employee, POSSale, POSSaleItem


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ["full_name", "seller", "role", "is_active"]
    list_filter = ["role", "is_active", "seller"]
    search_fields = ["full_name", "phone", "email"]


class POSSaleItemInline(admin.TabularInline):
    model = POSSaleItem
    extra = 0


@admin.register(POSSale)
class POSSaleAdmin(admin.ModelAdmin):
    list_display = ["receipt_number", "seller", "employee", "total", "payment_method", "status", "sold_at"]
    list_filter = ["status", "payment_method", "seller"]
    search_fields = ["receipt_number", "customer_name"]
    inlines = [POSSaleItemInline]
