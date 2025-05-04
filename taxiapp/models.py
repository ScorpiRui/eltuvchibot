from django.db import models

class Driver(models.Model):
    tg_id     = models.BigIntegerField(primary_key=True, unique=True)    # Telegram user ID
    api_id    = models.PositiveIntegerField()
    api_hash  = models.CharField(max_length=64)
    session   = models.TextField()                          # Pyrogram string
    active    = models.BooleanField(default=True)
    created   = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.tg_id} | {self.bot_token[:6]}"




class Announcement(models.Model):
    driver = models.ForeignKey(
        Driver,
        on_delete=models.CASCADE,
        related_name="announcements"
    )
    # Store group usernames as a JSON list, e.g. ["@group1", "@group2"]
    groups = models.JSONField()
    text = models.TextField()
    interval_minutes = models.PositiveIntegerField()
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Announcement {self.id} for Driver {self.driver.tg_id}"




class ActiveUser(models.Model):
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=30)
    tg_id = models.BigIntegerField(unique=True)
    activated_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    active = models.BooleanField(default=True)
    def __str__(self):
        return f"{self.name} ({self.tg_id})"