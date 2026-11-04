from django.db import models


class Category(models.Model):

    name = models.CharField(
        max_length=100
    )

    icon = models.CharField(
        max_length=50,
        default="🍽"
    )


    def __str__(self):
        return self.name



class Food(models.Model):

    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        related_name="foods",
        null=True,
        blank=True
    )

    name = models.CharField(
        max_length=100
    )

    description = models.TextField(
        blank=True
    )

    price = models.PositiveIntegerField()

    stock = models.PositiveIntegerField(
        default=0
    )

    image = models.ImageField(
        upload_to="foods/",
        blank=True,
        null=True
    )


    def __str__(self):
        return self.name


class Order(models.Model):

    STATUS_CHOICES = [
        ("new","جدید"),
        ("accepted","تایید شده"),
        ("cooking","در حال آماده سازی"),
        ("awaiting_confirmation","در انتظار تأیید مشتری"),
        ("ready","آماده"),
        ("sent","ارسال شده"),
        ("done","تحویل شده"),
        ("rejected","رد شده"),
    ]


    table = models.CharField(
        max_length=20
    )

    # NEW: for the invoice only — not used for login/auth.
    customer_name = models.CharField(
        max_length=100,
        blank=True,
        default=""
    )

    # NEW: optional — lets a returning customer be recognized by phone
    # even on a different device / after clearing browser storage.
    customer_phone = models.CharField(
        max_length=20,
        blank=True,
        default="",
        db_index=True
    )

    # NEW: comma-separated chair keys booked for this order, e.g.
    # "T3-top,T3-right" — lets us compute REAL seat occupancy instead
    # of a random/mocked layout on the frontend.
    seats = models.CharField(
        max_length=200,
        blank=True,
        default=""
    )


    total = models.PositiveIntegerField(
        default=0
    )


    status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default="new"
    )


    created_at = models.DateTimeField(
        auto_now_add=True
    )


    cooking_started_at = models.DateTimeField(
        null=True,
        blank=True
    )

    # NEW: server-authoritative deadline for the prep/delivery countdown.
    # Clients compute their own countdown from this timestamp (immune to
    # clock drift / network lag) instead of the server ticking every second.
    eta_at = models.DateTimeField(
        null=True,
        blank=True
    )

    # NEW: set once the customer responds to the "did you receive it?"
    # prompt that appears when eta_at is reached. None = not answered yet.
    customer_confirmed = models.BooleanField(
        null=True,
        blank=True,
        default=None
    )


    def __str__(self):
        return f"Order #{self.id}"



class OrderItem(models.Model):

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items"
    )


    name = models.CharField(
        max_length=100
    )


    quantity = models.IntegerField()


    price = models.IntegerField()


    def __str__(self):
        return self.name