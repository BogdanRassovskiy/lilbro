from django.shortcuts import render


def home(request):
    # Search UI lives at /search/ now; home is the database list.
    return render(request, "firmy/premises.html")


def search(request):
    return render(request, "home.html")
