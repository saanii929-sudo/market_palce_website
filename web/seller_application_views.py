from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from catalog.models import Category
from sellers.models import SellerApplication
from sellers.services import SellerApplicationError, submit_application


@login_required(login_url="web-login")
def become_seller_view(request):
    if getattr(request.user, "seller_profile", None) is not None:
        messages.info(request, "You're already a seller.")
        return redirect("web-seller-overview")

    application = SellerApplication.objects.filter(user=request.user).order_by("-submitted_at").first()
    can_apply = application is None or application.status == SellerApplication.Status.REJECTED

    if request.method == "POST" and can_apply:
        business_name = request.POST.get("business_name", "").strip()
        category_id = request.POST.get("category_id")
        phone = request.POST.get("phone", "").strip()
        id_document = request.FILES.get("id_document")
        business_certificate = request.FILES.get("business_certificate")
        category = (
            get_object_or_404(Category, id=category_id, is_active=True) if category_id else None
        )

        if not business_name or category is None or not phone:
            messages.error(request, "Please fill in your business name, category, and phone number.")
        elif id_document is None:
            messages.error(request, "Please upload a government-issued ID to apply.")
        else:
            try:
                submit_application(
                    request.user, business_name=business_name, category=category, phone=phone,
                    id_document=id_document, business_certificate=business_certificate,
                )
                messages.success(request, "Your application has been submitted! We'll review it shortly.")
                return redirect("web-become-seller")
            except SellerApplicationError as exc:
                messages.error(request, exc.message)

    return render(request, "web/become_seller.html", {
        "application": application,
        "can_apply": can_apply,
        "categories": Category.objects.filter(is_active=True),
    })
